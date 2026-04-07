"""
Handler de webhooks de pagamento recebidos do SICOOB.
O SICOOB chama POST /boletos/webhook/sicoob quando um boleto é pago/baixado.
"""
import json
import logging
from boletos import database as db

logger = logging.getLogger(__name__)

# Eventos conhecidos do SICOOB Cobrança v3
_EVENTOS_PAGAMENTO = {"PAGAMENTO_BOLETO", "LIQUIDACAO", "LIQUIDADO"}
_EVENTOS_BAIXA = {"BAIXA_BOLETO", "BAIXA", "BAIXADO"}


async def processar(payload: dict) -> dict:
    """
    Processa um evento de webhook do SICOOB.
    Retorna dict com resultado do processamento.
    """
    evento = payload.get("codigoEvento") or payload.get("evento") or "DESCONHECIDO"
    nosso_numero = payload.get("nossoNumero") or payload.get("nosso_numero")
    payload_str = json.dumps(payload, ensure_ascii=False)

    # Logar sempre
    db.log_webhook(evento, nosso_numero, payload_str)
    logger.info("[Webhook] Evento=%s nossoNumero=%s", evento, nosso_numero)

    if not nosso_numero:
        logger.warning("[Webhook] Payload sem nossoNumero: %s", payload_str[:200])
        return {"processado": False, "motivo": "nossoNumero ausente"}

    evento_upper = evento.upper()

    if evento_upper in _EVENTOS_PAGAMENTO:
        data_pag = payload.get("dataPagamento") or payload.get("data_pagamento")
        valor_pago = payload.get("valorPago") or payload.get("valor_pago") or payload.get("valorNominal")
        db.atualizar_status(int(nosso_numero), "pago", pago_em=data_pag, valor_pago=valor_pago)
        logger.info("[Webhook] ✅ Boleto %s marcado como PAGO (R$ %s)", nosso_numero, valor_pago)
        return {"processado": True, "acao": "pago", "nossoNumero": nosso_numero}

    if evento_upper in _EVENTOS_BAIXA:
        db.atualizar_status(int(nosso_numero), "baixado")
        logger.info("[Webhook] Boleto %s marcado como BAIXADO", nosso_numero)
        return {"processado": True, "acao": "baixado", "nossoNumero": nosso_numero}

    logger.info("[Webhook] Evento '%s' registrado mas não processado (sem ação local)", evento)
    return {"processado": True, "acao": "registrado", "evento": evento}
