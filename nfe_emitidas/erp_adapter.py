"""Adapter nfe_emitidas → ERP app.pabloagro.cloud (substitui vhsys_adapter.py)."""
import logging
import re

import erp_client as erp

logger = logging.getLogger(__name__)


def buscar_nfe_por_chave(chave: str) -> dict | None:
    """Busca NF-e emitida no ERP pela chave de 44 dígitos."""
    try:
        resultado = erp.get("/api/nfe-emitidas", params={"chave": chave, "limit": 1})
        if isinstance(resultado, list) and resultado:
            return resultado[0]
        if isinstance(resultado, dict):
            itens = resultado.get("notas") or resultado.get("data", [])
            if itens:
                return itens[0]
    except Exception as e:
        logger.warning("[ERPAdapter/nfe] Erro ao buscar NF-e %s: %s", chave[:10], e)
    return None


def buscar_cliente_por_id(cnpj_cpf: str) -> dict | None:
    """Busca cliente no ERP por CNPJ/CPF."""
    cnpj = re.sub(r"\D", "", str(cnpj_cpf or ""))
    if not cnpj:
        return None
    try:
        c = erp.get("/api/clientes/detalhe", params={"cnpj": cnpj})
        if c:
            c["nome_cliente"]     = c.get("razao_social") or c.get("fantasia") or ""
            c["cpf_cnpj_cliente"] = c.get("cnpj_cpf", "")
            return c
    except Exception as e:
        logger.warning("[ERPAdapter/nfe] Erro ao buscar cliente %s: %s", cnpj, e)
    return None


def buscar_clientes(razao_social: str = "", cpf_cnpj: str = "") -> list[dict]:
    """Busca clientes no ERP."""
    busca = razao_social or cpf_cnpj
    if not busca.strip():
        return []
    try:
        clientes = erp.get("/api/clientes", params={"busca": busca.strip(), "limit": 20})
        if not isinstance(clientes, list):
            return []
        for c in clientes:
            c["nome_cliente"] = c.get("razao_social") or c.get("fantasia") or ""
        return clientes
    except Exception as e:
        logger.error("[ERPAdapter/nfe] Erro ao buscar clientes: %s", e)
        return []
