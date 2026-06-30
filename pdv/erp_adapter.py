"""PDV — integração com o ERP app.pabloagro.cloud."""
import logging
import sys, os

logger = logging.getLogger(__name__)

# Garante que .env da raiz seja carregado quando rodando como .exe
def _carregar_env():
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS
    else:
        base = os.path.join(os.path.dirname(__file__), "..")
    env_path = os.path.join(base, ".env")
    if os.path.exists(env_path):
        from dotenv import load_dotenv
        load_dotenv(env_path, override=False)

_carregar_env()

import erp_client as erp

_FORMA_ERP_MAP = {
    "dinheiro": "dinheiro",
    "pix":      "pix",
    "credito":  "cartao",
    "debito":   "cartao",
}


# ── Sincronização de produtos ─────────────────────────────────────────────────

def sincronizar_produtos() -> dict:
    """Importa produtos ativos do ERP e desativa localmente os que sumiram."""
    from pdv.database import upsert_produto, get_conn

    try:
        produtos = erp.get("/api/produtos", params={"situacao": "Ativo", "limit": 5000})
    except Exception as e:
        msg = f"Erro ao buscar produtos do ERP: {e}"
        logger.error(msg)
        return {"importados": 0, "erro": msg}

    if not isinstance(produtos, list):
        msg = f"Resposta inesperada do ERP: {type(produtos)}"
        logger.error(msg)
        return {"importados": 0, "erro": msg}

    codigos_ativos = set()
    total = 0
    for p in produtos:
        try:
            codigo = str(p.get("codigo", "") or "").strip()
            cod_bal = str(p.get("cod_balanca") or "").strip().lower() or None
            upsert_produto({
                "erp_id":            p["id"],
                "codigo":            codigo,
                "codigo_barras":     str(p.get("ean", "") or "").strip(),
                "nome":              str(p.get("nome", "")).strip(),
                "unidade":           str(p.get("unidade", "UN") or "UN"),
                "preco_base":        float(p.get("preco_venda") or 0),
                "total_vendido_erp": float(p.get("total_vendido") or 0),
                "codigo_balanca":    cod_bal,
                "ativo":             True,
            })
            if codigo:
                codigos_ativos.add(codigo)
            else:
                codigos_ativos.add(f"__erp_id__{p['id']}")
            total += 1
        except Exception as e:
            logger.warning(f"[ERP/Sync] erro no produto {p.get('codigo')}: {e}")

    # Desativa localmente produtos que não vieram no sync (inativados no ERP)
    desativados = 0
    with get_conn() as conn:
        locais = conn.execute(
            "SELECT id, codigo, erp_id FROM pdv_produtos WHERE ativo = 1"
        ).fetchall()
        for row in locais:
            chave = row["codigo"] if row["codigo"] else f"__erp_id__{row['erp_id']}"
            if chave not in codigos_ativos:
                conn.execute("UPDATE pdv_produtos SET ativo = 0 WHERE id = ?", (row["id"],))
                desativados += 1

    if desativados:
        logger.info(f"[PDV/Sync] {desativados} produto(s) desativado(s) localmente (inativo no ERP)")

    logger.info(f"[PDV/Sync] {total} produtos importados do ERP")
    return {"importados": total, "erro": None}


# ── Criar Venda no ERP ────────────────────────────────────────────────────────

def criar_venda_balcao(venda_id: int, itens: list[dict], pagamentos: list[dict],
                       total: float, desconto: float) -> tuple[int | None, str | None]:
    """
    Registra a venda no ERP via POST /api/pdv/sync-venda.
    Retorna (venda_id_erp, None) em sucesso ou (None, msg_erro) em falha.
    """
    from pdv.database import get_produto

    itens_erp = []
    sem_codigo = []
    for item in itens:
        vid = item.get("produto_id")
        if not vid:
            sem_codigo.append(item.get("nome", "?"))
            continue
        prod = get_produto(vid)
        if not prod or not prod.get("codigo"):
            sem_codigo.append(item.get("nome", f"id={vid}"))
            continue
        itens_erp.append({
            "produto_id":     prod["codigo"],
            "nome":           prod["nome"],
            "quantidade":     float(item["quantidade"]),
            "preco_unitario": float(item["preco_unitario"]),
        })

    if sem_codigo:
        logger.warning(f"[PDV/ERP venda {venda_id}] itens sem código ignorados: {sem_codigo}")

    if not itens_erp:
        return None, "Nenhum item com código ERP — venda não registrada"

    pagamentos_erp = [
        {"tipo": _FORMA_ERP_MAP.get(p["tipo"], p["tipo"]), "valor": float(p["valor"])}
        for p in pagamentos
    ]

    payload = {
        "pdv_venda_id": venda_id,
        "itens":        itens_erp,
        "pagamentos":   pagamentos_erp,
        "total":        total,
        "desconto":     desconto,
    }

    try:
        resp = erp.post("/api/pdv/sync-venda", payload)
        erp_id = resp.get("id") or resp.get("pedido_id") or venda_id
        return erp_id, None
    except Exception as e:
        return None, f"Erro ao registrar venda no ERP: {e}"


# ── Sync completo pós-venda ───────────────────────────────────────────────────

def sincronizar_venda(venda_id: int):
    """Chamado em background thread após criar a venda."""
    from pdv.database import get_itens_venda, get_pagamentos_venda, atualizar_sync_venda, get_conn

    itens      = get_itens_venda(venda_id)
    pagamentos = get_pagamentos_venda(venda_id)

    with get_conn() as conn:
        row = conn.execute("SELECT desconto, total FROM pdv_vendas WHERE id = ?", (venda_id,)).fetchone()
    desconto = float(row["desconto"]) if row else 0.0
    total    = float(row["total"])    if row else sum(p["valor"] for p in pagamentos)

    erp_id, erro = criar_venda_balcao(venda_id, itens, pagamentos, total, desconto)

    if erro:
        logger.warning(f"[PDV/Sync venda {venda_id}] {erro}")
        atualizar_sync_venda(venda_id, "erro", erro)
    else:
        logger.info(f"[PDV/Sync venda {venda_id}] OK — erp_id={erp_id}")
        atualizar_sync_venda(venda_id, "ok")
