"""
Adapter VHSys — somente leitura.
Usado para pré-preencher formulário de emissão de boleto a partir de um pedido VHSys.
"""
import logging

import requests

import config

logger = logging.getLogger(__name__)

_TIMEOUT = 20


def _headers() -> dict:
    return {
        "access-token":        config.VHSYS_ACCESS_TOKEN,
        "secret-access-token": config.VHSYS_SECRET_TOKEN,
        "Content-Type":        "application/json",
    }


def _disponivel() -> bool:
    return bool(config.VHSYS_ACCESS_TOKEN and config.VHSYS_SECRET_TOKEN)


def buscar_pedidos(
    situacao: str | None = None,
    data_inicio: str | None = None,
    limite: int = 50,
) -> list[dict]:
    """Retorna pedidos VHSys para pré-preencher form. Retorna [] se não configurado."""
    if not _disponivel():
        return []

    params: dict = {"limit": limite, "offset": 0}
    if situacao:
        params["situacao_pedido"] = situacao
    if data_inicio:
        params["data_pedido_ini"] = data_inicio

    resultados = []
    try:
        while True:
            resp = requests.get(
                f"{config.VHSYS_BASE_URL}/pedidos",
                headers=_headers(),
                params=params,
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            itens = data.get("data", [])
            for p in itens:
                resultados.append(_normalizar_pedido(p))
            if len(itens) < params["limit"]:
                break
            params["offset"] += params["limit"]
            if len(resultados) >= 500:
                break
    except Exception as e:
        logger.error("VHSys buscar_pedidos falhou: %s", e)

    return resultados


def buscar_pedido(pedido_id: int) -> dict | None:
    """Busca um pedido específico pelo ID."""
    if not _disponivel():
        return None
    try:
        resp = requests.get(
            f"{config.VHSYS_BASE_URL}/pedidos/{pedido_id}",
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        pedidos = data.get("data", [])
        if pedidos:
            return _normalizar_pedido(pedidos[0])
        return None
    except Exception as e:
        logger.error("VHSys buscar_pedido(%s) falhou: %s", pedido_id, e)
        return None


def buscar_cliente(cnpj_cpf: str) -> dict | None:
    """Busca cliente por CPF/CNPJ. Retorna nome e doc, ou None."""
    if not _disponivel():
        return None
    cnpj_cpf = "".join(c for c in cnpj_cpf if c.isdigit())
    try:
        resp = requests.get(
            f"{config.VHSYS_BASE_URL}/clientes",
            headers=_headers(),
            params={"cpf_cnpj": cnpj_cpf, "limit": 1},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        clientes = data.get("data", [])
        if clientes:
            c = clientes[0]
            return {
                "id":   c.get("id_cliente"),
                "nome": c.get("razao_social") or c.get("nome_cliente"),
                "doc":  cnpj_cpf,
            }
        return None
    except Exception as e:
        logger.error("VHSys buscar_cliente(%s) falhou: %s", cnpj_cpf, e)
        return None


def listar_clientes_docs() -> list[str]:
    """Retorna todos os CPF/CNPJs de clientes cadastrados no VHSys (somente dígitos)."""
    if not _disponivel():
        return []

    docs = set()
    params = {"limit": 100, "offset": 0}
    try:
        while True:
            resp = requests.get(
                f"{config.VHSYS_BASE_URL}/clientes",
                headers=_headers(),
                params=params,
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            clientes = data.get("data", [])
            for c in clientes:
                doc = (
                    c.get("cnpj_cliente")
                    or c.get("cpf_cliente")
                    or c.get("cpf_cnpj_cliente")
                    or ""
                )
                doc = "".join(d for d in doc if d.isdigit())
                if len(doc) in (11, 14):
                    docs.add(doc)
            if len(clientes) < params["limit"]:
                break
            params["offset"] += params["limit"]
    except Exception as e:
        logger.error("VHSys listar_clientes_docs falhou: %s", e)

    logger.info("VHSys: %d CPF/CNPJs de clientes carregados.", len(docs))
    return list(docs)


def _normalizar_pedido(p: dict) -> dict:
    return {
        "id":           p.get("id_pedido") or p.get("id"),
        "numero":       p.get("numero_pedido") or p.get("id_pedido"),
        "cliente_nome": p.get("nome_cliente") or p.get("razao_social"),
        "cliente_doc":  p.get("cpf_cnpj_cliente") or p.get("cnpj_cpf"),
        "valor_total":  p.get("total_pedido") or p.get("valor_total") or 0,
        "data":         p.get("data_pedido"),
        "situacao":     p.get("situacao_pedido"),
    }
