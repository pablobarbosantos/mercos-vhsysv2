"""
Sincroniza boletos do banco local → Sicoob.
Estratégia: a API Sicoob V3 requer nossoNumero para cada consulta,
não expõe listagem por período. O sync itera sobre os boletos locais
em status aberto e atualiza o status consultando o Sicoob um a um.
"""
import logging

from services import boleto_service, database
from services.exceptions import BoletoError, BoletoNaoEncontrado

logger = logging.getLogger(__name__)

# Status Sicoob → status interno
_SITUACAO_PARA_STATUS = {
    "LIQUIDADO":         "LIQUIDADO",
    "BAIXADO":           "BAIXADO",
    "VENCIDO":           "VENCIDO",
    "EM_ABERTO":         "EMITIDO",
    "REGISTRADO":        "EMITIDO",
    "AGUARDANDO_ACEITE": "EMITIDO",
    "RECUSADO":          "BAIXADO",
}

# Status que ainda estão "em aberto" e precisam ser verificados
_STATUS_ABERTOS = ("EMITIDO", "VENCIDO")


def sincronizar(dias: int = 60) -> dict:
    """
    Para cada boleto local com status em aberto, consulta o Sicoob
    e atualiza status se houver mudança.
    Retorna: {"verificados": N, "atualizados": M, "erros": K}
    """
    logger.info("Iniciando sincronização: consultando boletos em aberto no Sicoob...")

    boletos_abertos = database.listar_boletos(
        status=list(_STATUS_ABERTOS),
        limit=500,
    )

    if not boletos_abertos:
        logger.info("Nenhum boleto em aberto para sincronizar.")
        return {"verificados": 0, "atualizados": 0, "erros": 0}

    verificados = atualizados = erros = 0

    for b in boletos_abertos:
        nosso_numero = b["nosso_numero"]
        # Pular registros de teste/webhook sem nosso_numero real
        if not nosso_numero or len(nosso_numero) < 4:
            continue

        try:
            verificados += 1
            dados_sicoob = boleto_service.consultar(nosso_numero)

            # Extrair situação do retorno (estrutura pode variar)
            situacao_raw = (
                dados_sicoob.get("situacaoBoleto", {}).get("codigo")
                or dados_sicoob.get("codigoSituacao")
                or dados_sicoob.get("situacao", "")
            ).upper()

            novo_status = _SITUACAO_PARA_STATUS.get(situacao_raw)

            if novo_status and novo_status != b["status_atual"]:
                # Status mudou → registrar evento
                tipo_evento = _evento_para_status(novo_status)
                campos = {"status_atual": novo_status}
                if novo_status == "LIQUIDADO":
                    campos["origem_pagamento"] = "SICOOB"

                # Atualizar linha digitável se veio na resposta
                linha = dados_sicoob.get("linhaDigitavel") or dados_sicoob.get("codigoLinhaDigitavel")
                if linha:
                    campos["linha_digitavel"] = linha

                bid = database.upsert_boleto(nosso_numero, campos)
                database.registrar_evento(
                    bid,
                    tipo_evento,
                    "SICOOB",
                    dados={"status_sicoob": situacao_raw, "status_anterior": b["status_atual"]},
                )
                atualizados += 1
                logger.info(
                    "Boleto %s: %s → %s", nosso_numero, b["status_atual"], novo_status
                )

        except BoletoNaoEncontrado:
            # Boleto emitido localmente mas não encontrado no Sicoob (sandbox? erro?)
            logger.warning("Boleto %s não encontrado no Sicoob — mantendo status local.", nosso_numero)
        except Exception as e:
            logger.error("Erro ao sincronizar boleto %s: %s", nosso_numero, e)
            erros += 1

    logger.info(
        "Sincronização concluída: verificados=%d atualizados=%d erros=%d",
        verificados, atualizados, erros,
    )
    return {"verificados": verificados, "atualizados": atualizados, "erros": erros}


def _evento_para_status(status: str) -> str:
    m = {
        "LIQUIDADO":    "PAGO_SICOOB",
        "BAIXADO":      "BAIXADO",
        "VENCIDO":      "VENCIDO",
    }
    return m.get(status, "SINCRONIZADO")
