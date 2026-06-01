"""Adapter SICOOB → ERP app.pabloagro.cloud (substitui vhsys_adapter.py)."""
import logging
import re
import sys
import os

logger = logging.getLogger(__name__)

# Garante que erp_client (raiz do projeto) seja encontrado
_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import erp_client as erp


def _so_digitos(s: str) -> str:
    return re.sub(r"\D", "", str(s or ""))


def _normalizar_pedido(p: dict) -> dict:
    return {
        "id":           p.get("mercos_id"),
        "numero":       p.get("numero") or str(p.get("mercos_id", "")),
        "cliente_nome": p.get("cliente_nome_fantasia") or p.get("cliente", ""),
        "cliente_doc":  _so_digitos(p.get("cliente_cnpj", "")),
        "valor_total":  float(p.get("valor") or 0),
        "data":         (p.get("recebido_em") or "")[:10],
        "situacao":     p.get("status_fluxo", ""),
    }


def buscar_pedidos(situacao: str | None = None, data_inicio: str | None = None, limite: int = 50) -> list[dict]:
    params: dict = {"limit": limite}
    if situacao:
        params["status"] = situacao
    if data_inicio:
        params["data_de"] = data_inicio
    try:
        pedidos = erp.get("/api/pedidos", params=params)
        if not isinstance(pedidos, list):
            return []
        return [_normalizar_pedido(p) for p in pedidos]
    except Exception as e:
        logger.error("[ERPAdapter/SICOOB] Erro ao buscar pedidos: %s", e)
        return []


def buscar_pedido(pedido_id: int | str) -> dict | None:
    try:
        p = erp.get(f"/api/pedidos/{pedido_id}")
        return _normalizar_pedido(p) if p else None
    except Exception as e:
        logger.error("[ERPAdapter/SICOOB] Erro ao buscar pedido %s: %s", pedido_id, e)
        return None


def buscar_cliente(cnpj_cpf: str) -> dict | None:
    cnpj = _so_digitos(cnpj_cpf)
    if not cnpj:
        return None
    try:
        c = erp.get("/api/clientes/detalhe", params={"cnpj": cnpj})
        if c:
            c["nome_cliente"]     = c.get("razao_social") or c.get("fantasia") or ""
            c["cpf_cnpj_cliente"] = c.get("cnpj_cpf", "")
        return c or None
    except Exception as e:
        logger.error("[ERPAdapter/SICOOB] Erro ao buscar cliente %s: %s", cnpj, e)
        return None


def listar_clientes_docs() -> list[str]:
    """Retorna lista de todos os CNPJs/CPFs de clientes no ERP."""
    try:
        clientes = erp.get("/api/clientes", params={"limit": 2000})
        if not isinstance(clientes, list):
            return []
        return [_so_digitos(c.get("cnpj_cpf", "")) for c in clientes if c.get("cnpj_cpf")]
    except Exception as e:
        logger.error("[ERPAdapter/SICOOB] Erro ao listar clientes: %s", e)
        return []
