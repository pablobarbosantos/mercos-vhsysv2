import logging
from consulta_vhsys.database.database import get_conn, set_ativo, set_ean, marcar_dirty, log

logger = logging.getLogger("consulta_vhsys.duplicidade")


def verificar_duplicidades() -> list[dict]:
    """
    Identifica duplicidades de EAN e nome (normalizado) na base local.
    Nunca toma decisão automática.

    Retorna lista de conflitos:
    [
      {
        "tipo": "ean" | "nome",
        "valor": "<valor duplicado>",
        "produtos": [{"id": int, "vhsys_id": int, "nome": str, "ean": str}, ...]
      },
      ...
    ]
    """
    conflitos = []

    with get_conn() as conn:
        # EAN duplicado (ignora NULL/vazio)
        eans = conn.execute("""
            SELECT ean, COUNT(*) AS qtd
            FROM produtos
            WHERE ean IS NOT NULL AND trim(ean) != ''
            GROUP BY ean
            HAVING qtd > 1
        """).fetchall()

        for row in eans:
            ean = row["ean"]
            produtos = conn.execute(
                "SELECT id, vhsys_id, nome, ean FROM produtos WHERE ean = ?", (ean,)
            ).fetchall()
            conflitos.append({
                "tipo":    "ean",
                "valor":   ean,
                "produtos": [dict(p) for p in produtos],
            })
            logger.info("[DUPLICIDADE] EAN '%s' em %d produtos", ean, len(produtos))

        # Nome duplicado (normalizado: lower + trim) — apenas ativos
        nomes = conn.execute("""
            SELECT lower(trim(nome)) AS nome_norm, COUNT(*) AS qtd
            FROM produtos
            WHERE nome IS NOT NULL AND trim(nome) != '' AND ativo = 1
            GROUP BY nome_norm
            HAVING qtd > 1
        """).fetchall()

        for row in nomes:
            nome_norm = row["nome_norm"]
            produtos = conn.execute(
                "SELECT id, vhsys_id, nome, ean FROM produtos WHERE lower(trim(nome)) = ? AND ativo = 1",
                (nome_norm,)
            ).fetchall()
            conflitos.append({
                "tipo":    "nome",
                "valor":   nome_norm,
                "produtos": [dict(p) for p in produtos],
            })
            logger.info("[DUPLICIDADE] Nome '%s' em %d produtos", nome_norm, len(produtos))

    if not conflitos:
        logger.info("[DUPLICIDADE] Nenhuma duplicidade encontrada")
    else:
        logger.warning("[DUPLICIDADE] %d grupos de duplicidade encontrados", len(conflitos))

    return conflitos


def resolver_duplicidade_ean(
    vhsys_id_manter: int,
    vhsys_ids_remover_ean: list[int],
) -> dict:
    """
    Remove EAN dos produtos em vhsys_ids_remover_ean.
    Mantém EAN em vhsys_id_manter.
    Marca dirty=1 nos produtos alterados.
    Decisão vem SEMPRE do operador.

    Retorna {"ok": True, "alterados": [vhsys_id, ...]}
    """
    alterados = []
    for vid in vhsys_ids_remover_ean:
        if vid == vhsys_id_manter:
            continue
        set_ean(vid, None)
        marcar_dirty(vid)
        log("resolver_ean", vid, f"EAN removido. Mantido em vhsys_id={vhsys_id_manter}")
        alterados.append(vid)
        logger.info("[DUPLICIDADE] EAN removido de vhsys_id=%d (mantido em %d)", vid, vhsys_id_manter)

    return {"ok": True, "alterados": alterados}


def resolver_duplicidade_nome(vhsys_id_inativar: int) -> dict:
    """
    Marca produto como inativo (ativo=0).
    Decisão vem SEMPRE do operador.

    Retorna {"ok": True}
    """
    set_ativo(vhsys_id_inativar, 0)
    log("resolver_nome", vhsys_id_inativar, "Produto marcado como inativo pelo operador")
    logger.info("[DUPLICIDADE] vhsys_id=%d marcado como inativo", vhsys_id_inativar)
    return {"ok": True}
