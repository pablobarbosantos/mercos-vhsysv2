"""
Adapter para buscar dados de NF-e no VHSys por chave de acesso (44 dígitos).
Complementa a consulta SEFAZ para geração de DANFE sem o XML original.
"""
import logging
import os
import requests

logger = logging.getLogger(__name__)

_BASE_URL = os.getenv("VHSYS_BASE_URL", "https://api.vhsys.com.br/v2")
_HEADERS = {
    "access-token": os.getenv("VHSYS_ACCESS_TOKEN", ""),
    "secret-access-token": os.getenv("VHSYS_SECRET_TOKEN", ""),
    "cache-control": "no-cache",
}
_TIMEOUT = 20


def _get_list(path: str, params: dict = None) -> list[dict]:
    resultados = []
    pagina = 1
    while True:
        p = {**(params or {}), "limit": 100, "offset": (pagina - 1) * 100}
        try:
            resp = requests.get(
                f"{_BASE_URL}{path}", headers=_HEADERS, params=p, timeout=_TIMEOUT
            )
        except Exception as e:
            logger.error("[VHSysNFe] Erro de rede: %s", e)
            break
        if resp.status_code != 200:
            logger.warning("[VHSysNFe] HTTP %s em %s", resp.status_code, path)
            break
        data = resp.json().get("data", [])
        itens = data if isinstance(data, list) else ([data] if data else [])
        if not itens:
            break
        resultados.extend(itens)
        if len(itens) < 100:
            break
        pagina += 1
    return resultados


def buscar_nfe_por_chave(chave: str) -> dict | None:
    """
    Busca nota fiscal no VHSys filtrando pelo campo nota_chave (44 dígitos).
    Retorna o primeiro registro que bate, ou None.
    """
    try:
        notas = _get_list("/notas-fiscais", {"nota_chave": chave})
        for nf in notas:
            if str(nf.get("nota_chave", "")) == chave:
                return nf
        # Se a API não filtra, pagina até achar
        if notas and notas[0].get("nota_chave") != chave:
            logger.debug("[VHSysNFe] Filtro por nota_chave ignorado pela API, busca paginada")
            notas = _get_list("/notas-fiscais", {})
            for nf in notas:
                if str(nf.get("nota_chave", "")) == chave:
                    return nf
        return None
    except Exception as e:
        logger.warning("[VHSysNFe] Erro ao buscar NF-e por chave: %s", e)
        return None


def buscar_cliente_por_id(id_cliente) -> dict:
    """Retorna dados do cliente VHSys ou dict vazio."""
    try:
        resp = requests.get(
            f"{_BASE_URL}/clientes/{id_cliente}",
            headers=_HEADERS,
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning("[VHSysNFe] Erro ao buscar cliente %s: %s", id_cliente, e)
    return {}


def buscar_clientes(razao_social: str = "", cpf_cnpj: str = "") -> list[dict]:
    """Busca clientes por nome ou CNPJ/CPF."""
    params = {}
    if razao_social:
        params["razao_social"] = razao_social
    if cpf_cnpj:
        params["cpf_cnpj"] = cpf_cnpj
    try:
        return _get_list("/clientes", params)
    except Exception as e:
        logger.warning("[VHSysNFe] Erro ao buscar clientes: %s", e)
        return []
