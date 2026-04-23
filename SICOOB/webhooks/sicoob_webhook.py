"""
Receptor de notificações de pagamento do Sicoob.

O Sicoob V3 não tem push webhook documentado publicamente — este endpoint fica
pronto para quando for configurado no portal do cooperado (Developers Sicoob).

Enquanto não configurado, o sync periódico (APScheduler, 15min) garante que
mudanças de status sejam detectadas via polling.

Formato de payload esperado (Sicoob Cobrança Bancária V3):
{
  "nossoNumero":    123456,
  "codigoSituacao": "LIQUIDADO",
  "dataPagamento":  "2025-05-15",
  "valorPago":      1250.00,
  "numeroCliente":  123456
}
"""
import hashlib
import hmac
import json
import logging
from typing import Any

import config
from fastapi import APIRouter, Header, HTTPException, Request
from services import database
from services.exceptions import BoletoError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook/sicoob", tags=["webhook"])

# Sicoob → tipo de evento interno
_SITUACAO_MAP = {
    "LIQUIDADO":         ("PAGO_SICOOB",  "LIQUIDADO"),
    "BAIXADO":           ("BAIXADO",      "BAIXADO"),
    "VENCIDO":           ("VENCIDO",      "VENCIDO"),
    "EM_ABERTO":         ("SINCRONIZADO", "EMITIDO"),
    "REGISTRADO":        ("SINCRONIZADO", "EMITIDO"),
    "AGUARDANDO_ACEITE": ("SINCRONIZADO", "EMITIDO"),
}


@router.post("/pagamento")
async def receber_pagamento(
    request: Request,
    x_sicoob_signature: str | None = Header(default=None),
) -> dict[str, Any]:
    """
    Recebe notificação de pagamento/situação do Sicoob.
    Sempre retorna 200 — o Sicoob reenvia se receber != 200.
    """
    body = await request.body()

    # Validação de assinatura (só se SICOOB_WEBHOOK_SECRET estiver configurado)
    if config.SICOOB_WEBHOOK_SECRET:
        if not x_sicoob_signature:
            logger.warning("Webhook Sicoob recebido sem assinatura — rejeitado.")
            raise HTTPException(status_code=401, detail="Assinatura ausente")
        esperada = hmac.new(
            config.SICOOB_WEBHOOK_SECRET.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(x_sicoob_signature, esperada):
            logger.warning("Webhook Sicoob com assinatura inválida — rejeitado.")
            raise HTTPException(status_code=401, detail="Assinatura inválida")

    try:
        payload = json.loads(body)
    except Exception:
        logger.error("Webhook Sicoob: payload não é JSON válido.")
        return {"ok": False, "erro": "payload inválido"}

    nosso_numero = str(payload.get("nossoNumero", ""))
    situacao     = str(payload.get("codigoSituacao", "")).upper()
    num_cliente  = payload.get("numeroCliente")

    logger.info("Webhook Sicoob: nossoNumero=%s situacao=%s", nosso_numero, situacao)

    # Rejeitar notificações de outro convênio
    if num_cliente and int(num_cliente) != config.NUMERO_CLIENTE:
        logger.warning(
            "Webhook Sicoob para numeroCliente=%s (esperado %s) — ignorado.",
            num_cliente, config.NUMERO_CLIENTE,
        )
        return {"ok": True, "aviso": "numeroCliente não corresponde"}

    if not nosso_numero:
        return {"ok": True, "aviso": "nossoNumero ausente"}

    tipo_evento, novo_status = _SITUACAO_MAP.get(situacao, (None, None))
    if tipo_evento is None:
        logger.info("Webhook Sicoob: situação '%s' não mapeada — ignorado.", situacao)
        return {"ok": True, "aviso": f"situação '{situacao}' não processada"}

    try:
        boleto_id = database.upsert_boleto(
            nosso_numero,
            {
                "status_atual":      novo_status,
                "origem_pagamento":  "SICOOB" if situacao == "LIQUIDADO" else None,
                "valor":             payload.get("valorPago") or payload.get("valor", 0),
            },
        )
        database.registrar_evento(
            boleto_id,
            tipo_evento,
            "SICOOB",
            dados={
                "situacao_sicoob": situacao,
                "data_pagamento":  payload.get("dataPagamento"),
                "valor_pago":      payload.get("valorPago"),
            },
        )
        logger.info(
            "Webhook Sicoob processado: boleto=%s evento=%s status=%s",
            nosso_numero, tipo_evento, novo_status,
        )
    except BoletoError as e:
        logger.error("Webhook Sicoob: erro ao processar boleto %s: %s", nosso_numero, e)
        return {"ok": False, "erro": str(e)}

    return {"ok": True}
