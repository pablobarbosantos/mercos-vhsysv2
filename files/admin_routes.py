"""
Painel Admin — rotas FastAPI
Serve o painel HTML e os endpoints JSON usados por ele.
Inclui endpoints de Auditoria de Sequência e Fluxo.
"""

import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import os

from src import database as db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin")

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "..", "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers DB (não poluem database.py)
# ──────────────────────────────────────────────────────────────────────────────

def _listar_pedidos(limit: int = 200) -> list[dict]:
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT
                p.mercos_id,
                p.vhsys_id,
                p.processado_em,
                p.status,
                e.erro
            FROM pedidos_processados p
            LEFT JOIN (
                SELECT referencia_id, erro,
                       ROW_NUMBER() OVER (PARTITION BY referencia_id ORDER BY ocorrido_em DESC) AS rn
                FROM erros_log
                WHERE entidade = 'pedidos'
            ) e ON e.referencia_id = CAST(p.mercos_id AS TEXT) AND e.rn = 1
            ORDER BY p.processado_em DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def _pedido_payload_raw(mercos_id: int) -> dict | None:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM pedidos_processados WHERE mercos_id = ?", (mercos_id,)
        ).fetchone()
    return dict(row) if row else None


def _reprocessar_pedido(mercos_id: int):
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE pedidos_processados SET status = 'pendente_reprocessamento' WHERE mercos_id = ?",
            (mercos_id,)
        )
    logger.info(f"[Admin] Pedido {mercos_id} marcado para reprocessamento.")


def _stats() -> dict:
    with db.get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM pedidos_processados").fetchone()[0]
        ok    = conn.execute("SELECT COUNT(*) FROM pedidos_processados WHERE status = 'ok'").fetchone()[0]
        erro  = conn.execute("SELECT COUNT(*) FROM pedidos_processados WHERE status = 'erro'").fetchone()[0]
        hoje  = conn.execute(
            "SELECT COUNT(*) FROM pedidos_processados WHERE DATE(processado_em) = DATE('now')"
        ).fetchone()[0]
        # Buracos abertos
        buracos = conn.execute(
            "SELECT COUNT(*) FROM auditoria_sequencia WHERE resolvido = 0"
        ).fetchone()[0]
    return {"total": total, "ok": ok, "erro": erro, "hoje": hoje, "buracos_abertos": buracos}


# ──────────────────────────────────────────────────────────────────────────────
# Rotas principais (existentes)
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def painel(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request})


@router.get("/api/pedidos")
async def api_pedidos(limit: int = 200):
    pedidos = _listar_pedidos(limit)
    stats   = _stats()
    return {"pedidos": pedidos, "stats": stats}


@router.post("/api/reprocessar/{mercos_id}")
async def api_reprocessar(mercos_id: int):
    row = _pedido_payload_raw(mercos_id)
    if not row:
        raise HTTPException(status_code=404, detail="Pedido não encontrado.")
    if row["status"] not in ("erro", "pendente_reprocessamento"):
        raise HTTPException(
            status_code=400,
            detail=f"Pedido está com status '{row['status']}' — só pedidos com erro podem ser reprocessados."
        )
    _reprocessar_pedido(mercos_id)
    return {"ok": True, "mensagem": f"Pedido {mercos_id} marcado para reprocessamento."}


# ──────────────────────────────────────────────────────────────────────────────
# NOVO: Rotas de Auditoria
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/auditoria", response_class=HTMLResponse)
async def painel_auditoria(request: Request):
    """Mesma página admin — o frontend decide o que renderizar."""
    return templates.TemplateResponse("admin.html", {"request": request})


@router.get("/api/auditoria/sequencia")
async def api_auditoria_sequencia(apenas_abertos: bool = True):
    """Lista buracos na sequência de pedidos."""
    buracos = db.auditoria_listar_buracos(apenas_abertos=apenas_abertos)
    return {"buracos": buracos, "total": len(buracos)}


@router.get("/api/auditoria/fluxo")
async def api_auditoria_fluxo(limit: int = 200):
    """Lista pedidos com informações de fluxo operacional."""
    pedidos = db.fluxo_listar(limit=limit)
    # Agrupa estatísticas
    stats = {
        "total":           len(pedidos),
        "recebidos":       sum(1 for p in pedidos if p["status_fluxo"] == "recebido"),
        "processados":     sum(1 for p in pedidos if p["status_fluxo"] == "processado"),
        "separados":       sum(1 for p in pedidos if p["status_fluxo"] == "separado"),
        "enviados":        sum(1 for p in pedidos if p["status_fluxo"] == "enviado"),
        "cancelados":      sum(1 for p in pedidos if p["status_fluxo"] == "cancelado"),
        "com_erro":        sum(1 for p in pedidos if p["status_fluxo"] == "erro"),
    }
    return {"pedidos": pedidos, "stats": stats}


@router.post("/api/auditoria/verificar-agora")
async def api_verificar_agora():
    """Dispara as duas auditorias manualmente (útil para testes)."""
    from src.auditoria import verificar_sequencia, verificar_fluxo
    buracos = verificar_sequencia()
    alertas = verificar_fluxo()
    return {
        "ok": True,
        "buracos_encontrados": len(buracos),
        "alertas_fluxo":       len(alertas),
    }


@router.post("/api/auditoria/fluxo/{mercos_id}/separado")
async def api_marcar_separado(mercos_id: int):
    """Marca pedido como separado manualmente via painel."""
    pedido = db.fluxo_get_pedido(mercos_id)
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido não encontrado no fluxo.")
    db.fluxo_marcar_separado(mercos_id)
    logger.info(f"[Admin] Pedido {mercos_id} marcado como SEPARADO manualmente.")
    return {"ok": True, "mercos_id": mercos_id, "novo_status": "separado"}


@router.post("/api/auditoria/fluxo/{mercos_id}/enviado")
async def api_marcar_enviado(mercos_id: int):
    """Marca pedido como enviado manualmente via painel."""
    pedido = db.fluxo_get_pedido(mercos_id)
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido não encontrado no fluxo.")
    db.fluxo_marcar_enviado(mercos_id)
    logger.info(f"[Admin] Pedido {mercos_id} marcado como ENVIADO manualmente.")
    return {"ok": True, "mercos_id": mercos_id, "novo_status": "enviado"}


@router.post("/api/auditoria/sequencia/{mercos_id}/resolver")
async def api_resolver_buraco(mercos_id: int, resolucao: str = "verificado_manualmente"):
    """Marca um buraco de sequência como resolvido."""
    from src.auditoria import marcar_buraco_resolvido
    marcar_buraco_resolvido(mercos_id, resolucao)
    return {"ok": True, "mercos_id": mercos_id, "resolucao": resolucao}


@router.get("/api/auditoria/fechamento")
async def api_fechamento():
    """Gera e retorna o fechamento do dia (sem enviar WhatsApp)."""
    from src.auditoria import fechamento_do_dia
    stats = fechamento_do_dia()
    return stats
