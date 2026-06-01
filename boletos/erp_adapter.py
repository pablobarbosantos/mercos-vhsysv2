"""Adapter boletos → ERP app.pabloagro.cloud (substitui vhsys_adapter.py)."""
import logging
import re

import erp_client as erp
from boletos import database as db

logger = logging.getLogger(__name__)


def _so_digitos(s: str) -> str:
    return re.sub(r"\D", "", str(s or ""))


def _normalizar_cliente(c: dict) -> dict:
    """Adiciona aliases VHSys → ERP para que construir_payload encontre os campos."""
    c["nome_cliente"]     = c.get("razao_social") or c.get("fantasia") or ""
    c["cpf_cnpj_cliente"] = c.get("cnpj_cpf", "")
    return c


def buscar_contas_abertas() -> list[dict]:
    """
    Retorna pedidos ativos do ERP como 'contas a receber' para emissão de boleto.
    Cada item segue o formato esperado por construir_payload.
    """
    try:
        pedidos = erp.get("/api/pedidos", params={
            "limit": 500,
        })
    except Exception as e:
        logger.error("[ERPAdapter/boletos] Erro ao buscar pedidos: %s", e)
        return []

    if not isinstance(pedidos, list):
        pedidos = pedidos.get("pedidos", []) if isinstance(pedidos, dict) else []

    emitidos = db.listar_conta_ids_emitidos()

    resultado = []
    for p in pedidos:
        status = p.get("status_fluxo", "")
        if status in ("cancelado", "finalizado"):
            continue
        conta_id = str(p.get("mercos_id", ""))
        resultado.append({
            "id_conta_rec":    conta_id,
            "n_documento_rec": str(p.get("numero") or conta_id),
            "valor_rec":       p.get("valor", 0),
            "data_vencimento": "",
            "id_cliente":      p.get("cliente_cnpj", ""),
            "nome_cliente":    p.get("cliente_nome_fantasia") or p.get("cliente", ""),
            "cpf_cnpj_cliente": _so_digitos(p.get("cliente_cnpj", "")),
            "cidade_cliente":  p.get("cidade", ""),
            "uf_cliente":      p.get("estado", "MG"),
            "boleto_ja_emitido": conta_id in emitidos or bool(p.get("boleto_emitido")),
        })

    resultado.sort(
        key=lambda x: int(x["n_documento_rec"].split("-")[0]) if x["n_documento_rec"].split("-")[0].isdigit() else 0,
        reverse=True,
    )
    logger.info("[ERPAdapter] %d contas abertas, %d com boleto", len(resultado), sum(1 for i in resultado if i["boleto_ja_emitido"]))
    return resultado


def buscar_conta_por_id(mercos_id: str) -> dict | None:
    """
    Busca pedido ERP pelo mercos_id e mescla dados do cliente.
    Retorna dict no formato esperado por construir_payload.
    """
    try:
        pedido = erp.get(f"/api/pedidos/{mercos_id}")
    except Exception as e:
        logger.error("[ERPAdapter] Erro ao buscar pedido %s: %s", mercos_id, e)
        return None

    if not pedido:
        return None

    cnpj = _so_digitos(pedido.get("cliente_cnpj", ""))
    cliente: dict = {}
    if cnpj:
        try:
            cliente = erp.get("/api/clientes/detalhe", params={"cnpj": cnpj}) or {}
        except Exception:
            pass

    return {
        "id_conta_rec":    str(pedido.get("mercos_id", mercos_id)),
        "n_documento_rec": str(pedido.get("numero", mercos_id)),
        "valor_rec":       pedido.get("valor", 0),
        "data_vencimento": "",
        # campos do cliente — construir_payload já tem fallbacks para nomes ERP
        "nome_cliente":    cliente.get("razao_social") or pedido.get("cliente", ""),
        "cpf_cnpj_cliente": cnpj or _so_digitos(cliente.get("cnpj_cpf", "")),
        "endereco":        cliente.get("endereco", ""),
        "numero":          cliente.get("numero", ""),
        "bairro":          cliente.get("bairro", ""),
        "cidade":          cliente.get("cidade") or pedido.get("cidade", ""),
        "uf":              cliente.get("uf") or pedido.get("estado", "MG"),
        "cep":             cliente.get("cep", ""),
    }


def buscar_cliente_por_id(cnpj_cpf: str) -> dict | None:
    """Busca cliente no ERP por CNPJ/CPF."""
    cnpj = _so_digitos(cnpj_cpf)
    if not cnpj:
        return None
    try:
        c = erp.get("/api/clientes/detalhe", params={"cnpj": cnpj})
        if c:
            return _normalizar_cliente(c)
    except Exception as e:
        logger.error("[ERPAdapter] Erro ao buscar cliente %s: %s", cnpj, e)
    return None


def buscar_clientes(q: str) -> list[dict]:
    """Busca clientes no ERP por nome ou CNPJ (autocomplete)."""
    q = q.strip()
    if not q:
        return []
    try:
        clientes = erp.get("/api/clientes", params={"busca": q, "limit": 30})
        if not isinstance(clientes, list):
            return []
        return [_normalizar_cliente(c) for c in clientes]
    except Exception as e:
        logger.error("[ERPAdapter/clientes] %s", e)
        return []


def buscar_pedidos_recentes(limit: int = 100) -> list[dict]:
    """Lista pedidos recentes do ERP no formato esperado pelo admin de boletos."""
    try:
        pedidos = erp.get("/api/pedidos", params={"limit": limit})
        if not isinstance(pedidos, list):
            return []
        return [
            {
                "id_ped":            p.get("mercos_id"),
                "id_pedido":         p.get("mercos_id"),
                "numero":            p.get("numero", ""),
                "id_cliente":        _so_digitos(p.get("cliente_cnpj", "")),
                "nome_cliente":      p.get("cliente_nome_fantasia") or p.get("cliente", ""),
                "valor_total_nota":  str(p.get("valor", 0)),
                "data_pedido":       p.get("recebido_em", "")[:10] if p.get("recebido_em") else "",
                "status_pedido":     p.get("status_fluxo", ""),
            }
            for p in pedidos
        ]
    except Exception as e:
        logger.error("[ERPAdapter/pedidos] %s", e)
        return []
