"""
Sincroniza boletos do banco local → Sicoob.

Limitação da API V3: não há endpoint de listagem geral por período.
O sync verifica um a um os boletos locais em status aberto (EMITIDO/VENCIDO).

Para importar boletos históricos emitidos fora deste sistema, use
sincronizar_por_pagador(cpf_cnpj) que consulta via /pagadores/:doc/boletos.
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


def _extrair_situacao(dados: dict) -> str:
    sit = dados.get("situacaoBoleto", "")
    # Pode ser string direta ("LIQUIDADO") ou dict {"codigo": "LIQUIDADO"}
    if isinstance(sit, dict):
        sit = sit.get("codigo", "")
    return (sit or dados.get("codigoSituacao") or dados.get("situacao", "")).upper()


def sincronizar(dias: int = 60) -> dict:
    """
    Para cada boleto local com status em aberto, consulta o Sicoob
    e atualiza status se houver mudança.
    Retorna: {"verificados": N, "atualizados": M, "erros": K}
    """
    logger.info("Iniciando sincronização: verificando boletos em aberto no Sicoob...")

    boletos_abertos = database.listar_boletos(status=list(_STATUS_ABERTOS), limit=500)

    if not boletos_abertos:
        logger.info("Nenhum boleto em aberto para sincronizar.")
        return {"verificados": 0, "atualizados": 0, "erros": 0}

    verificados = atualizados = erros = 0

    for b in boletos_abertos:
        nosso_numero = b["nosso_numero"]
        if not nosso_numero or not nosso_numero.isdigit():
            continue

        try:
            verificados += 1
            dados_sicoob = boleto_service.consultar(nosso_numero)
            situacao_raw = _extrair_situacao(dados_sicoob)
            novo_status = _SITUACAO_PARA_STATUS.get(situacao_raw)

            if novo_status and novo_status != b["status_atual"]:
                tipo_evento = _evento_para_status(novo_status)
                campos = {"status_atual": novo_status}
                if novo_status == "LIQUIDADO":
                    campos["origem_pagamento"] = "SICOOB"

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
                logger.info("Boleto %s: %s → %s", nosso_numero, b["status_atual"], novo_status)

        except BoletoNaoEncontrado:
            logger.warning("Boleto %s não encontrado no Sicoob — mantendo status local.", nosso_numero)
        except Exception as e:
            logger.error("Erro ao verificar boleto %s: %s", nosso_numero, e)
            erros += 1

    logger.info(
        "Sincronização concluída: verificados=%d atualizados=%d erros=%d",
        verificados, atualizados, erros,
    )
    return {"verificados": verificados, "atualizados": atualizados, "erros": erros}


def sincronizar_por_pagador(cpf_cnpj: str, dias: int = 365) -> dict:
    """
    Importa boletos de um pagador específico via /pagadores/:doc/boletos.
    A API limita a 35 dias por chamada — pagina automaticamente.
    codigoSituacao: 1=Em Aberto, 2=Baixado, 3=Liquidado (3 chamadas por janela).
    """
    import config
    from datetime import date, timedelta

    scope = "boletos_consulta"
    session, token = boleto_service._session_e_token(scope)
    doc = cpf_cnpj.replace(".", "").replace("-", "").replace("/", "")
    url = f"https://api.sicoob.com.br/cobranca-bancaria/v3/pagadores/{doc}/boletos"
    headers = {"Authorization": f"Bearer {token}"}

    # Gera janelas de 35 dias (limite da API)
    hoje = date.today()
    inicio_total = hoje - timedelta(days=dias)
    janelas = []
    cursor = inicio_total
    while cursor < hoje:
        fim_janela = min(cursor + timedelta(days=34), hoje)
        janelas.append((cursor.isoformat(), fim_janela.isoformat()))
        cursor = fim_janela + timedelta(days=1)

    importados = erros = 0
    for data_inicio, data_fim in janelas:
        for codigo_situacao in (1, 2, 3):
            try:
                params = {
                    "numeroCliente":  config.NUMERO_CLIENTE,
                    "numeroCpfCnpj":  doc,
                    "dataInicio":     data_inicio,
                    "dataFim":        data_fim,
                    "codigoSituacao": codigo_situacao,
                }
                resp = session.get(url, params=params, headers=headers, timeout=config.TIMEOUT)
                resp.raise_for_status()
                if not resp.content or not resp.text.strip():
                    continue
                data = resp.json()
                boletos = data.get("resultado", data)
                if isinstance(boletos, dict):
                    boletos = boletos.get("itens", boletos.get("boletos", []))

                for b in (boletos or []):
                    nosso_numero = str(b.get("nossoNumero", "")).strip()
                    if not nosso_numero or not nosso_numero.isdigit():
                        continue
                    try:
                        pagador = b.get("pagador") or {}
                        situacao_raw = _extrair_situacao(b)
                        status = _SITUACAO_PARA_STATUS.get(situacao_raw, "EMITIDO")
                        campos = {
                            "nosso_numero":    nosso_numero,
                            "seu_numero":      b.get("seuNumero") or b.get("numeroDocumento"),
                            "valor":           b.get("valor") or b.get("valorNominal") or 0.0,
                            "vencimento":      b.get("dataVencimento", ""),
                            "cliente_nome":    pagador.get("nome"),
                            "cliente_doc":     pagador.get("numeroInscricao") or pagador.get("cpfCnpj"),
                            "linha_digitavel": b.get("linhaDigitavel") or b.get("codigoLinhaDigitavel"),
                            "codigo_barras":   b.get("codigoBarras"),
                            "status_atual":    status,
                        }
                        database.upsert_boleto(nosso_numero, {k: v for k, v in campos.items() if v is not None})
                        importados += 1
                    except Exception as e:
                        logger.error("Erro ao importar boleto %s: %s", nosso_numero, e)
                        erros += 1

            except Exception as e:
                logger.error("Erro pagador %s janela=%s/%s situacao=%d: %s",
                             doc, data_inicio, data_fim, codigo_situacao, e)
                erros += 1

    logger.info("Pagador %s: %d boleto(s) importados, %d erros.", doc, importados, erros)
    return {"importados": importados, "erros": erros}


def sincronizar_todos(dias: int = 60) -> dict:
    """
    Importa boletos de todos os clientes VHSys via /pagadores/:doc/boletos.
    Itera sobre todos os CPF/CNPJs cadastrados no VHSys.
    """
    from services import erp_adapter

    docs = erp_adapter.listar_clientes_docs()
    if not docs:
        logger.warning("Nenhum CPF/CNPJ encontrado no VHSys — abortando sync total.")
        return {"clientes": 0, "importados": 0, "erros": 0}

    logger.info("Sincronizando boletos de %d clientes (últimos %d dias)...", len(docs), dias)
    total_importados = total_erros = 0

    for doc in docs:
        resultado = sincronizar_por_pagador(doc, dias=dias)
        total_importados += resultado["importados"]
        total_erros += resultado["erros"]

    logger.info(
        "Sync total concluído: clientes=%d importados=%d erros=%d",
        len(docs), total_importados, total_erros,
    )
    return {"clientes": len(docs), "importados": total_importados, "erros": total_erros}


def _evento_para_status(status: str) -> str:
    m = {
        "LIQUIDADO": "PAGO_SICOOB",
        "BAIXADO":   "BAIXADO",
        "VENCIDO":   "VENCIDO",
    }
    return m.get(status, "SINCRONIZADO")
