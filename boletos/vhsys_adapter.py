"""
Adapter para buscar contas a receber do VHSys que precisam de boleto.
Usa as mesmas credenciais e padrão de paginação do vhsys_service.py.
"""
import logging
import os
import requests
from boletos import database as db

logger = logging.getLogger(__name__)

_BASE_URL = os.getenv("VHSYS_BASE_URL", "https://api.vhsys.com.br/v2")
_HEADERS = {
    "access-token":  os.getenv("VHSYS_ACCESS_TOKEN", ""),
    "secret-access-token": os.getenv("VHSYS_SECRET_TOKEN", ""),
    "cache-control": "no-cache",
}
_TIMEOUT = 20


def _get(path: str, params: dict) -> list[dict]:
    """Paginação idêntica à de vhsys_service.py:buscar_boletos_vencidos()."""
    resultados = []
    pagina = 1
    while True:
        p = {**params, "limit": 100, "offset": (pagina - 1) * 100}
        try:
            resp = requests.get(f"{_BASE_URL}{path}", headers=_HEADERS, params=p, timeout=_TIMEOUT)
        except Exception as e:
            logger.error("[VHSysAdapter] Erro de rede: %s", e)
            break
        if resp.status_code != 200:
            logger.warning("[VHSysAdapter] HTTP %s em %s", resp.status_code, path)
            break
        itens = resp.json().get("data", [])
        if not itens:
            break
        resultados.extend(itens)
        if len(itens) < 100:
            break
        pagina += 1
    return resultados


def buscar_contas_abertas() -> list[dict]:
    """
    Retorna contas a receber do VHSys (liquidado_rec=Nao) que ainda não têm
    boleto emitido no banco local. Cada item inclui todos os campos retornados
    pela API VHSys + campo extra 'boleto_ja_emitido'.
    """
    itens = _get("/contas-receber", {"liquidado_rec": "Nao"})
    emitidos = db.listar_conta_ids_emitidos()

    resultado = []
    for item in itens:
        conta_id = str(item.get("id_conta_rec", ""))
        item["boleto_ja_emitido"] = conta_id in emitidos
        resultado.append(item)

    logger.info(
        "[VHSysAdapter] %d contas abertas, %d já com boleto emitido",
        len(resultado),
        sum(1 for i in resultado if i["boleto_ja_emitido"]),
    )
    return resultado


def buscar_conta_por_id(id_conta_rec: str) -> dict | None:
    """
    Busca uma conta específica pelo ID e enriquece com dados do cliente
    (CNPJ/CPF, endereço) necessários para o payload do boleto SICOOB.
    """
    try:
        resp = requests.get(
            f"{_BASE_URL}/contas-receber/{id_conta_rec}",
            headers=_HEADERS,
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        data = resp.json().get("data", {})
        if isinstance(data, list):
            data = data[0] if data else {}

        # Enriquecer com dados do cliente (CNPJ/CPF, endereço)
        id_cliente = data.get("id_cliente")
        if id_cliente:
            cliente = _buscar_cliente(str(id_cliente))
            if cliente:
                # Injeta campos do cliente que o boleto precisa
                data.setdefault("cpf_cnpj_cliente", cliente.get("cnpj_cliente") or cliente.get("cpf_cliente", ""))
                data.setdefault("endereco_cliente", cliente.get("endereco_cliente", ""))
                data.setdefault("numero_end",       cliente.get("numero_cliente", ""))
                data.setdefault("bairro_cliente",   cliente.get("bairro_cliente", ""))
                data.setdefault("cidade_cliente",   cliente.get("cidade_cliente", ""))
                data.setdefault("uf_cliente",       cliente.get("uf_cliente", "MG"))
                data.setdefault("cep_cliente",      cliente.get("cep_cliente", ""))
        return data
    except Exception as e:
        logger.error("[VHSysAdapter] Erro ao buscar conta %s: %s", id_conta_rec, e)
    return None


def _buscar_cliente(id_cliente: str) -> dict | None:
    """Busca dados completos de um cliente no VHSys pelo ID."""
    try:
        resp = requests.get(
            f"{_BASE_URL}/clientes/{id_cliente}",
            headers=_HEADERS,
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            return data[0] if isinstance(data, list) and data else data
    except Exception as e:
        logger.debug("[VHSysAdapter] Erro ao buscar cliente %s: %s", id_cliente, e)
    return None
