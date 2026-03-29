import logging
from consulta_vhsys.database.database import (
    get_produto_by_ean,
    get_produto_by_vhsys_id,
    buscar_por_nome as _db_buscar_por_nome,
    set_ean,
    set_preco_estoque,
    marcar_dirty,
    log,
)

logger = logging.getLogger("consulta_vhsys.product_lookup")


def buscar_por_ean(ean: str) -> dict | None:
    """
    Busca produto por EAN no SQLite local.
    NÃO consulta API.

    Retorna produto ou None se não encontrado.
    """
    produto = get_produto_by_ean(ean.strip())
    if produto:
        logger.debug("[LOOKUP] EAN '%s' → vhsys_id=%d", ean, produto["vhsys_id"])
    else:
        logger.debug("[LOOKUP] EAN '%s' não encontrado", ean)
    return produto


def buscar_por_nome(termo: str) -> list[dict]:
    """
    Busca produtos por nome (parcial, case-insensitive) no SQLite local.
    NÃO consulta API.

    Retorna lista de produtos para o operador selecionar.
    """
    resultados = _db_buscar_por_nome(termo.strip())
    logger.debug("[LOOKUP] Nome '%s' → %d resultados", termo, len(resultados))
    return resultados


def vincular_ean(vhsys_id: int, ean: str) -> dict:
    """
    Vincula EAN a um produto.
    Verifica duplicidade antes de salvar — BLOQUEIA se EAN já existe em outro produto.
    Marca dirty=1.

    Retorna {"ok": True} ou {"ok": False, "erro": "..."}
    """
    ean = ean.strip()

    # Verificar duplicidade
    existente = get_produto_by_ean(ean)
    if existente and existente["vhsys_id"] != vhsys_id:
        msg = f"EAN '{ean}' já vinculado ao produto '{existente['nome']}' (vhsys_id={existente['vhsys_id']})"
        logger.warning("[LOOKUP] Bloqueado: %s", msg)
        return {"ok": False, "erro": msg}

    # Verificar se produto existe
    produto = get_produto_by_vhsys_id(vhsys_id)
    if not produto:
        msg = f"Produto vhsys_id={vhsys_id} não encontrado na base local"
        logger.warning("[LOOKUP] %s", msg)
        return {"ok": False, "erro": msg}

    set_ean(vhsys_id, ean)
    marcar_dirty(vhsys_id)
    log("vincular_ean", vhsys_id, f"EAN='{ean}' vinculado")
    logger.info("[LOOKUP] EAN '%s' vinculado a vhsys_id=%d", ean, vhsys_id)

    return {"ok": True}


def editar_produto(
    vhsys_id: int,
    preco: float,
    estoque: float,
) -> dict:
    """
    Altera preço e estoque localmente.
    Marca dirty=1.

    Retorna {"ok": True, "produto": {...}} ou {"ok": False, "erro": "..."}
    """
    produto = get_produto_by_vhsys_id(vhsys_id)
    if not produto:
        msg = f"Produto vhsys_id={vhsys_id} não encontrado na base local"
        logger.warning("[LOOKUP] %s", msg)
        return {"ok": False, "erro": msg}

    set_preco_estoque(vhsys_id, preco, estoque)
    marcar_dirty(vhsys_id)

    detalhes = [
        f"preco={preco:.2f} (antes={produto.get('preco')})",
        f"estoque={estoque} (antes={produto.get('estoque')})",
    ]

    log("editar_produto", vhsys_id, ", ".join(detalhes))
    logger.info("[LOOKUP] vhsys_id=%d editado: %s", vhsys_id, ", ".join(detalhes))

    produto_atualizado = get_produto_by_vhsys_id(vhsys_id)
    return {"ok": True, "produto": produto_atualizado}
