"""
Sincroniza boletos do Sicoob → banco local.
Roda periodicamente (APScheduler) e pode ser disparado manualmente via endpoint.
"""
import logging
from datetime import date, timedelta

from services import boleto_service, database
from services.exceptions import BoletoError

logger = logging.getLogger(__name__)

# Mapeamento status Sicoob → status interno
_STATUS_MAP = {
    "LIQUIDADO":         "LIQUIDADO",
    "BAIXADO":           "BAIXADO",
    "VENCIDO":           "VENCIDO",
    "EM_ABERTO":         "EMITIDO",
    "REGISTRADO":        "EMITIDO",
    "AGUARDANDO_ACEITE": "EMITIDO",
    "RECUSADO":          "BAIXADO",
}

# Mapeamento status → tipo de evento a registrar
_EVENTO_MAP = {
    "LIQUIDADO": "PAGO_SICOOB",
    "BAIXADO":   "BAIXADO",
    "VENCIDO":   "VENCIDO",
}


def sincronizar(dias: int = 60) -> dict:
    """
    Busca boletos no Sicoob dos últimos `dias` dias, atualiza banco local.
    Sempre mantém em aberto (EMITIDO/VENCIDO) independente de data.
    Retorna: {"novos": N, "atualizados": M, "erros": K}
    """
    logger.info("Iniciando sincronização Sicoob (últimos %d dias)...", dias)
    novos = atualizados = erros = 0

    try:
        boletos_sicoob = boleto_service.listar(dias=dias)
    except BoletoError as e:
        logger.error("Falha ao listar boletos no Sicoob: %s", e)
        return {"novos": 0, "atualizados": 0, "erros": 1, "detalhe": str(e)}

    for item in boletos_sicoob:
        try:
            nosso_numero = str(item.get("nossoNumero") or item.get("nosso_numero", ""))
            if not nosso_numero:
                continue

            status_sicoob = (
                item.get("situacaoBoleto", {}).get("codigo")
                or item.get("codigoSituacao")
                or item.get("situacao", "")
            ).upper()

            status_interno = _STATUS_MAP.get(status_sicoob, "EMITIDO")

            pagador = item.get("pagador", {})
            campos = {
                "nosso_numero":    nosso_numero,
                "seu_numero":      item.get("seuNumero") or item.get("seu_numero"),
                "cliente_nome":    pagador.get("nome"),
                "cliente_doc":     pagador.get("numeroCpfCnpj"),
                "valor":           item.get("valor"),
                "vencimento":      _normalizar_data(item.get("dataVencimento")),
                "linha_digitavel": item.get("linhaDigitavel") or item.get("codigoLinhaDigitavel"),
                "codigo_barras":   item.get("codigoBarras"),
            }
            campos = {k: v for k, v in campos.items() if v is not None}

            existia = database.get_boleto(nosso_numero)
            boleto_id = database.upsert_boleto(nosso_numero, campos)

            if existia is None:
                novos += 1
                database.registrar_evento(boleto_id, "EMITIDO", "SICOOB", dados=item)
            else:
                status_anterior = existia["status_atual"]
                if status_interno != status_anterior:
                    tipo_evento = _EVENTO_MAP.get(status_interno, "SINCRONIZADO")
                    database.registrar_evento(
                        boleto_id, tipo_evento, "SICOOB",
                        dados={"status_sicoob": status_sicoob, "status_anterior": status_anterior},
                    )
                    atualizados += 1

        except Exception as e:
            logger.error("Erro ao sincronizar boleto %s: %s", item.get("nossoNumero"), e)
            erros += 1

    logger.info(
        "Sincronização concluída: novos=%d atualizados=%d erros=%d",
        novos, atualizados, erros,
    )
    return {"novos": novos, "atualizados": atualizados, "erros": erros}


def _normalizar_data(valor) -> str | None:
    """Converte datas ISO com fuso ou datetime para YYYY-MM-DD."""
    if not valor:
        return None
    s = str(valor)
    return s[:10]  # pega só YYYY-MM-DD independente do formato
