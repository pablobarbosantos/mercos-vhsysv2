"""
Serviço de Movimentações SICOOB — fluxo assíncrono em 3 etapas:

  1. POST /boletos/movimentacoes  → codigoSolicitacao
  2. GET  /boletos/movimentacoes  (polling) → idArquivos quando pronto
  3. GET  /boletos/movimentacao-download  → ZIP/JSON com os registros

Para 120 dias com tipos 1/5/6: 60 janelas × 3 tipos = 180 POSTs.
Executa em ThreadPoolExecutor(max_workers=5) para não bloquear.
"""
import io
import json
import logging
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Any

import config
from services import database
from services.boleto_service import _request_with_retry, _session_e_token

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.sicoob.com.br/cobranca-bancaria/v3/boletos"

_DESCRICAO_TIPO = {
    1: "Entrada",
    2: "Prorrogação",
    3: "A Vencer",
    4: "Vencido",
    5: "Liquidação",
    6: "Baixa",
}


def _janelas(data_inicio: date, data_fim: date) -> list[tuple[str, str]]:
    """Gera janelas de até 2 dias entre data_inicio e data_fim."""
    janelas = []
    cursor = data_inicio
    while cursor <= data_fim:
        fim = min(cursor + timedelta(days=1), data_fim)
        janelas.append((cursor.isoformat(), fim.isoformat()))
        cursor = fim + timedelta(days=1)
    return janelas


def _solicitar_um(session, token: str, numero_cliente: int, tipo: int, inicio: str, fim: str) -> int | None:
    """POST para solicitar uma janela. Retorna codigoSolicitacao ou None em falha."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {
        "numeroCliente": numero_cliente,
        "tipoMovimento": tipo,
        "dataInicial": inicio,
        "dataFinal": fim,
    }
    try:
        resp = _request_with_retry(session, "post", f"{_BASE_URL}/movimentacoes",
                                   json=body, headers=headers, timeout=config.TIMEOUT)
        if resp.status_code not in (200, 201):
            logger.warning("movimentacoes POST %s/%s tipo=%d → HTTP %d: %s",
                           inicio, fim, tipo, resp.status_code, resp.text[:200])
            return None
        data = resp.json()
        resultado = data.get("resultado", data)
        cod = resultado.get("codigoSolicitacao")
        logger.debug("Solicitação criada: tipo=%d %s/%s → cod=%s", tipo, inicio, fim, cod)
        return cod
    except Exception as e:
        logger.error("Erro ao solicitar movimentacao tipo=%d %s/%s: %s", tipo, inicio, fim, e)
        return None


def _aguardar_e_baixar(session, token: str, numero_cliente: int,
                       cod: int, tipo: int, inicio: str, fim: str,
                       max_tentativas: int = 12, intervalo: int = 5) -> list[dict]:
    """Polling do status e download dos arquivos. Retorna lista de registros normalizados."""
    headers = {"Authorization": f"Bearer {token}"}
    url_status = f"{_BASE_URL}/movimentacoes"
    url_download = f"{_BASE_URL}/movimentacoes/download"

    for tentativa in range(max_tentativas):
        time.sleep(intervalo)
        try:
            resp = _request_with_retry(
                session, "get", url_status,
                params={"numeroCliente": numero_cliente, "codigoSolicitacao": cod},
                headers=headers, timeout=config.TIMEOUT,
            )
            # Ainda processando: 404 ou corpo vazio
            if resp.status_code == 404 or not resp.content or not resp.text.strip():
                logger.debug("Solicitacao %d ainda não processada (tentativa %d)", cod, tentativa + 1)
                continue
            data = resp.json()
            resultado = data.get("resultado", data)
            id_arquivos = resultado.get("idArquivos", [])

            # Resposta completa recebida — mesmo que sem arquivos (sem dados para esse período/tipo)
            if not id_arquivos:
                logger.debug("Solicitacao %d tipo=%d %s/%s: sem dados no período.", cod, tipo, inicio, fim)
                return []

            registros = []
            for id_arquivo in id_arquivos:
                registros.extend(_baixar_arquivo(session, headers, url_download,
                                                  numero_cliente, cod, id_arquivo, tipo, inicio, fim))
            return registros

        except Exception as e:
            logger.error("Erro polling solicitacao %d tentativa %d: %s", cod, tentativa + 1, e)

    logger.warning("Solicitacao %d tipo=%d %s/%s esgotou tentativas sem resposta.", cod, tipo, inicio, fim)
    return []


def _baixar_arquivo(session, headers: dict, url: str,
                    numero_cliente: int, cod: int, id_arquivo: int,
                    tipo: int, inicio: str, fim: str) -> list[dict]:
    """Baixa e parseia um arquivo de movimentação."""
    headers_dl = {**headers, "client_id": config.CLIENT_ID}
    params = {"numeroCliente": numero_cliente, "codigoSolicitacao": cod, "idArquivo": id_arquivo}
    try:
        resp = _request_with_retry(
            session, "get", url,
            params=params,
            headers=headers_dl, timeout=max(config.TIMEOUT, 60),
        )
        if resp.status_code != 200:
            logger.error(
                "Download HTTP %d — url=%s params=%s body=%s",
                resp.status_code, url, params, resp.text[:300],
            )
            return []
        return _parsear_resposta(resp.content, tipo, inicio, fim)
    except Exception as e:
        logger.error("Erro download arquivo=%d cod=%d: %s", id_arquivo, cod, e)
        return []


def _parsear_resposta(content: bytes, tipo: int, inicio: str, fim: str) -> list[dict]:
    """Parseia conteúdo da resposta.

    SICOOB retorna JSON: {"resultado": {"arquivo": "<base64_zip>", "nomeArquivo": "..."}}
    O campo `arquivo` é um ZIP base64 contendo JSON com os registros.
    """
    import base64
    registros: list[dict] = []
    itens: list[dict] = []

    try:
        # Resposta JSON com campo arquivo (base64 do ZIP)
        data = json.loads(content)
        resultado = data.get("resultado", data)
        arquivo_b64 = resultado.get("arquivo")

        if arquivo_b64:
            zip_bytes = base64.b64decode(arquivo_b64)
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                for nome in zf.namelist():
                    with zf.open(nome) as f:
                        itens.extend(_extrair_itens(json.load(f)))
        else:
            # Fallback: JSON direto com os itens (sem envelope ZIP)
            itens.extend(_extrair_itens(data))

    except json.JSONDecodeError:
        # Fallback: bytes crus são um ZIP
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                for nome in zf.namelist():
                    with zf.open(nome) as f:
                        itens.extend(_extrair_itens(json.load(f)))
        except Exception as e:
            logger.error("Erro ao parsear resposta tipo=%d: %s — raw(50)=%s", tipo, e, content[:50])
            return []
    except Exception as e:
        logger.error("Erro ao parsear resposta tipo=%d: %s — raw(50)=%s", tipo, e, content[:50])
        return []

    for item in itens:
        registros.append(_normalizar(item, tipo, inicio, fim))
    return registros


def _extrair_itens(data: Any) -> list[dict]:
    """Extrai lista de registros independente da estrutura do JSON."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for chave in ("resultado", "itens", "boletos", "movimentacoes", "registros", "data"):
            val = data.get(chave)
            if isinstance(val, list):
                return val
            if isinstance(val, dict):
                return _extrair_itens(val)
    return []


def _normalizar(item: dict, tipo: int, inicio: str, fim: str) -> dict:
    """Normaliza um registro de movimentação para o schema local.

    Campos reais da API SICOOB v3 (movimentações):
      numeroTitulo  → nosso número no banco
      seuNumero     → referência do cliente
      valorTitulo   → valor nominal do boleto
      valorLiquido  → valor efetivamente liquidado (Liquidação)
      dataMovimentoEntrada / dataMovimentoLiquidacao / dataVencimentoTitulo → data evento
    """
    nosso_numero = str(item.get("numeroTitulo") or item.get("nossoNumero") or item.get("nosso_numero") or "")
    seu_numero = item.get("seuNumero") or ""

    # Valor: para Liquidação, usar valorLiquido; para Entrada, usar valorTitulo
    valor = (
        item.get("valorLiquido") or
        item.get("valorTitulo") or
        item.get("valor") or
        item.get("valorNominal") or
        item.get("valorPago")
    )

    # Data do evento: campo varia por tipo de movimento
    data_evento = (
        item.get("dataMovimentoLiquidacao") or
        item.get("dataMovimentoEntrada") or
        item.get("dataMovimentoBaixa") or
        item.get("dataMovimento") or
        item.get("dataVencimentoTitulo") or
        item.get("dataOcorrencia") or
        inicio
    )

    return {
        "data_evento":    str(data_evento)[:10],
        "tipo_movimento": tipo,
        "descricao":      _DESCRICAO_TIPO.get(tipo, str(tipo)),
        "nosso_numero":   nosso_numero,
        "cliente_nome":   seu_numero,  # movimentações não trazem nome; seuNumero é ref. do cliente
        "cliente_doc":    "",
        "valor":          float(valor) if valor else None,
        "dados_raw":      json.dumps(item, ensure_ascii=False),
        "periodo_inicio": inicio,
        "periodo_fim":    fim,
    }


def solicitar_e_salvar(
    data_inicio: str,
    data_fim: str,
    tipos: list[int] | None = None,
) -> dict:
    """
    Busca movimentações no SICOOB para o período e tipos informados.
    Salva no DB local. Retorna estatísticas da operação.
    """
    if tipos is None:
        tipos = [1, 5, 6]

    d_inicio = date.fromisoformat(data_inicio)
    d_fim = date.fromisoformat(data_fim)
    janelas = _janelas(d_inicio, d_fim)

    scope = "boletos_consulta"
    session, token = _session_e_token(scope)
    numero_cliente = int(config.NUMERO_CLIENTE)

    tarefas = [(tipo, ini, fim) for ini, fim in janelas for tipo in tipos]
    logger.info(
        "Movimentacoes: %d janelas × %d tipos = %d solicitacoes | periodo %s→%s",
        len(janelas), len(tipos), len(tarefas), data_inicio, data_fim,
    )

    # Fase 1: solicitar tudo em paralelo
    solicitacoes: list[dict] = []
    erros_solicitacao = 0

    def _solicitar(args):
        tipo, ini, fim = args
        cod = _solicitar_um(session, token, numero_cliente, tipo, ini, fim)
        return {"cod": cod, "tipo": tipo, "inicio": ini, "fim": fim}

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(_solicitar, t): t for t in tarefas}
        for future in as_completed(futures):
            resultado = future.result()
            if resultado["cod"] is not None:
                solicitacoes.append(resultado)
            else:
                erros_solicitacao += 1

    logger.info("Fase 1 concluída: %d solicitações aceitas, %d erros.", len(solicitacoes), erros_solicitacao)

    # Fase 2+3: polling e download em paralelo
    todos_registros: list[dict] = []
    erros_download = 0

    def _baixar(s):
        return _aguardar_e_baixar(session, token, numero_cliente,
                                   s["cod"], s["tipo"], s["inicio"], s["fim"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(_baixar, s): s for s in solicitacoes}
        for future in as_completed(futures):
            try:
                regs = future.result()
                todos_registros.extend(regs)
            except Exception as e:
                logger.error("Erro download: %s", e)
                erros_download += 1

    logger.info("Fase 2/3 concluída: %d registros baixados.", len(todos_registros))

    # Persiste no DB
    inseridos = database.salvar_movimentacao(todos_registros)

    return {
        "periodo": f"{data_inicio} → {data_fim}",
        "tipos": tipos,
        "janelas": len(janelas),
        "solicitacoes": len(solicitacoes),
        "registros_brutos": len(todos_registros),
        "inseridos_db": inseridos,
        "erros_solicitacao": erros_solicitacao,
        "erros_download": erros_download,
    }
