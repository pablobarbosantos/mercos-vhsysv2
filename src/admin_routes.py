"""
Painel Admin — rotas FastAPI
Serve o painel HTML e os endpoints JSON usados por ele.
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
# Helpers DB extras (não poluem database.py)
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
    """Retorna dados brutos do pedido na tabela, se existir."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM pedidos_processados WHERE mercos_id = ?", (mercos_id,)
        ).fetchone()
    return dict(row) if row else None


def _reprocessar_pedido(mercos_id: int):
    """
    Remove o registro do pedido para permitir reprocessamento
    quando o webhook o reenviar, ou marca como 'pendente'.
    """
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
    return {"total": total, "ok": ok, "erro": erro, "hoje": hoje}


# ──────────────────────────────────────────────────────────────────────────────
# Rotas
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
