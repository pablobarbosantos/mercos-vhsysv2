"""Adapter compras → ERP app.pabloagro.cloud (substitui vhsys_adapter.py)."""
import logging
import time
import difflib

import erp_client as erp

logger = logging.getLogger(__name__)

# Cache: erp_id (int) → codigo (str) — populado uma vez por hora
_id_to_codigo: dict[int, str] = {}
_cache_ts: float = 0


def _get_codigo(erp_id: int) -> str | None:
    global _id_to_codigo, _cache_ts
    if not _id_to_codigo or (time.time() - _cache_ts) > 3600:
        try:
            produtos = erp.get("/api/produtos", params={"limit": 5000})
            if isinstance(produtos, list):
                _id_to_codigo = {p["id"]: p["codigo"] for p in produtos if p.get("codigo")}
                _cache_ts = time.time()
        except Exception as e:
            logger.warning("[ERPAdapter/compras] Falha ao atualizar cache de produtos: %s", e)
    return _id_to_codigo.get(erp_id)


def atualizar_custo_produto(erp_id: int, valor_unitario: float) -> bool:
    """Atualiza preco_custo do produto no ERP."""
    codigo = _get_codigo(erp_id)
    if not codigo:
        logger.warning("[ERPAdapter/compras] erp_id=%d sem codigo — custo não atualizado", erp_id)
        return False
    try:
        erp.patch(f"/api/produtos/{codigo}", {"preco_custo": float(valor_unitario)})
        logger.info("[ERPAdapter/compras] custo atualizado erp_id=%d codigo=%s → %.4f", erp_id, codigo, valor_unitario)
        return True
    except Exception as e:
        logger.error("[ERPAdapter/compras] Erro ao atualizar custo erp_id=%d: %s", erp_id, e)
        return False


def lancar_entrada_compra(erp_id: int, quantidade: float, chave_nfe: str, descricao: str) -> bool:
    """Lança entrada de estoque no ERP."""
    codigo = _get_codigo(erp_id)
    if not codigo:
        logger.warning("[ERPAdapter/compras] erp_id=%d sem codigo — estoque não lançado", erp_id)
        return False
    try:
        erp.post(f"/api/produtos/{codigo}/estoque", {
            "tipo": "Entrada",
            "quantidade": float(quantidade),
            "obs": f"NF-e {chave_nfe[:20]}",
        })
        logger.info("[ERPAdapter/compras] estoque +%.2f erp_id=%d codigo=%s", quantidade, erp_id, codigo)
        return True
    except Exception as e:
        logger.error("[ERPAdapter/compras] Erro ao lançar estoque erp_id=%d: %s", erp_id, e)
        return False


def atualizar_ean_produto(erp_id: int, ean: str) -> bool:
    """Atualiza EAN do produto no ERP."""
    codigo = _get_codigo(erp_id)
    if not codigo:
        return False
    try:
        erp.patch(f"/api/produtos/{codigo}", {"ean": ean})
        return True
    except Exception as e:
        logger.warning("[ERPAdapter/compras] Erro ao atualizar EAN erp_id=%d: %s", erp_id, e)
        return False


def atualizar_unidade_produto(erp_id: int, unidade: str) -> bool:
    """Atualiza unidade do produto no ERP."""
    codigo = _get_codigo(erp_id)
    if not codigo:
        return False
    try:
        erp.patch(f"/api/produtos/{codigo}", {"unidade": unidade})
        return True
    except Exception as e:
        logger.warning("[ERPAdapter/compras] Erro ao atualizar unidade erp_id=%d: %s", erp_id, e)
        return False


def criar_produto_erp(dados: dict) -> dict | None:
    """Cria produto no ERP. Retorna o produto criado ou None em falha."""
    try:
        return erp.post("/api/produtos", dados)
    except Exception as e:
        logger.error("[ERPAdapter/compras] Erro ao criar produto: %s", e)
        return None


def buscar_produtos_erp(q: str) -> list[dict]:
    """Busca produtos no ERP por nome/código/EAN."""
    try:
        produtos = erp.get("/api/produtos", params={"busca": q, "limit": 20})
        if not isinstance(produtos, list):
            return []
        return [
            {
                "vhsys_id": p["id"],
                "nome":     p.get("nome", ""),
                "ean":      p.get("ean", ""),
                "preco":    p.get("preco_venda", 0),
                "codigo":   p.get("codigo", ""),
            }
            for p in produtos
        ]
    except Exception as e:
        logger.error("[ERPAdapter/compras] Erro ao buscar produtos: %s", e)
        return []


def auto_match_produto(codigo_fornecedor: str, descricao: str) -> dict | None:
    """Auto-match: tenta encontrar produto no ERP por EAN, código ou nome fuzzy."""
    # 1. Busca por EAN/código direto
    if codigo_fornecedor:
        resultados = buscar_produtos_erp(codigo_fornecedor)
        if resultados:
            for r in resultados:
                if r.get("ean") == codigo_fornecedor or r.get("codigo") == codigo_fornecedor:
                    return {**r, "via": "EAN"}
            if resultados:
                return {**resultados[0], "via": "SKU"}

    # 2. Busca por nome (traz candidatos)
    if not descricao:
        return None
    candidatos = buscar_produtos_erp(descricao[:30])
    if not candidatos:
        return None

    desc_lower = descricao.lower()
    scored = [
        (difflib.SequenceMatcher(None, desc_lower, c["nome"].lower()).ratio(), c)
        for c in candidatos
    ]
    scored.sort(key=lambda x: -x[0])
    bons = [(s, c) for s, c in scored if s >= 0.60]
    if not bons:
        return None

    best_score, best = bons[0]
    alternativas = [{**c, "similaridade": round(s, 2)} for s, c in bons[1:3]]
    if best_score >= 0.70:
        return {**best, "via": "nome_similar", "similaridade": round(best_score, 2), "alternativas": alternativas}

    return None
