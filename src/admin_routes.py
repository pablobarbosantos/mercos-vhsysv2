"""
Painel Admin — rotas FastAPI
Serve o painel HTML e os endpoints JSON usados por ele.
Inclui endpoints de Auditoria de Sequência e Fluxo.
"""

import logging
import threading
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
async def api_reprocessar(request: Request, mercos_id: int):
    row = _pedido_payload_raw(mercos_id)
    if not row:
        raise HTTPException(status_code=404, detail="Pedido não encontrado.")
    if row["status"] not in ("erro", "pendente_reprocessamento"):
        raise HTTPException(
            status_code=400,
            detail=f"Pedido está com status '{row['status']}' — só pedidos com erro podem ser reprocessados."
        )
    _reprocessar_pedido(mercos_id)
    db.admin_registrar_acao("reprocessar", mercos_id, ip=request.client.host if request.client else "")
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


@router.post("/api/expedicao/verificar-agora")
async def api_expedicao_verificar_agora():
    """Dispara o job de sync de expedição em background (não bloqueia a resposta)."""
    from src.expedicao import job_sync_expedicao
    threading.Thread(target=job_sync_expedicao, daemon=True).start()
    return {"ok": True, "mensagem": "Job iniciado em background — verifique os logs."}


@router.post("/api/auditoria/fluxo/{mercos_id}/separado")
async def api_marcar_separado(request: Request, mercos_id: int):
    """Marca pedido como separado manualmente via painel."""
    pedido = db.fluxo_get_pedido(mercos_id)
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido não encontrado no fluxo.")
    db.fluxo_marcar_separado(mercos_id)
    db.admin_registrar_acao("separado", mercos_id, ip=request.client.host if request.client else "")
    logger.info(f"[Admin] Pedido {mercos_id} marcado como SEPARADO manualmente.")
    return {"ok": True, "mercos_id": mercos_id, "novo_status": "separado"}


@router.post("/api/auditoria/fluxo/{mercos_id}/enviado")
async def api_marcar_enviado(request: Request, mercos_id: int):
    """Marca pedido como enviado manualmente via painel."""
    pedido = db.fluxo_get_pedido(mercos_id)
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido não encontrado no fluxo.")
    db.fluxo_marcar_enviado(mercos_id)
    db.admin_registrar_acao("enviado", mercos_id, ip=request.client.host if request.client else "")
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


@router.get("/api/acoes")
async def api_acoes_admin(limit: int = 100):
    """Lista ações manuais realizadas no painel admin (audit trail)."""
    acoes = db.admin_listar_acoes(limit=limit)
    return {"acoes": acoes, "total": len(acoes)}


@router.get("/api/fila")
async def api_fila_stats():
    """Retorna estatísticas da fila de eventos."""
    return {"stats": db.fila_stats()}


# ──────────────────────────────────────────────────────────────────────────────
# Analytics
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/api/analytics/resumo")
async def api_analytics_resumo():
    """Faturamento hoje/semana/mês, ticket médio, taxa de sucesso, tempo médio."""
    with db.get_conn() as conn:
        fat_hoje = conn.execute(
            "SELECT COALESCE(SUM(valor),0) FROM pedidos_fluxo WHERE DATE(recebido_em)=DATE('now') AND status_fluxo NOT IN ('cancelado','erro')"
        ).fetchone()[0]
        fat_semana = conn.execute(
            "SELECT COALESCE(SUM(valor),0) FROM pedidos_fluxo WHERE recebido_em >= datetime('now','-7 days') AND status_fluxo NOT IN ('cancelado','erro')"
        ).fetchone()[0]
        fat_mes = conn.execute(
            "SELECT COALESCE(SUM(valor),0) FROM pedidos_fluxo WHERE strftime('%Y-%m',recebido_em)=strftime('%Y-%m','now') AND status_fluxo NOT IN ('cancelado','erro')"
        ).fetchone()[0]
        pedidos_hoje = conn.execute(
            "SELECT COUNT(*) FROM pedidos_fluxo WHERE DATE(recebido_em)=DATE('now')"
        ).fetchone()[0]
        ok_hoje = conn.execute(
            "SELECT COUNT(*) FROM pedidos_fluxo WHERE DATE(recebido_em)=DATE('now') AND status_fluxo NOT IN ('cancelado','erro','recebido')"
        ).fetchone()[0]
        ticket_medio = round(fat_hoje / ok_hoje, 2) if ok_hoje > 0 else 0
        taxa_sucesso = round(ok_hoje / pedidos_hoje * 100, 1) if pedidos_hoje > 0 else 100.0
        # Tempo médio de processamento (segundos) hoje
        tempo_medio = conn.execute("""
            SELECT AVG((julianday(processado_em) - julianday(recebido_em)) * 86400)
            FROM pedidos_fluxo
            WHERE DATE(recebido_em) = DATE('now')
              AND processado_em IS NOT NULL
        """).fetchone()[0]
    return {
        "faturamento_hoje":   round(fat_hoje, 2),
        "faturamento_semana": round(fat_semana, 2),
        "faturamento_mes":    round(fat_mes, 2),
        "pedidos_hoje":       pedidos_hoje,
        "ticket_medio":       ticket_medio,
        "taxa_sucesso":       taxa_sucesso,
        "tempo_medio_seg":    round(tempo_medio or 0, 1),
    }


@router.get("/api/analytics/produtos")
async def api_analytics_produtos(dias_parado: int = 30, top: int = 10):
    """Top produtos mais vendidos + produtos parados (sem venda há X dias)."""
    with db.get_conn() as conn:
        mais_vendidos = conn.execute(f"""
            SELECT
                COALESCE(NULLIF(sku,''), nome_produto) AS produto,
                nome_produto,
                SUM(quantidade) AS qtd_total,
                SUM(valor_total) AS valor_total,
                COUNT(DISTINCT mercos_id) AS num_pedidos
            FROM itens_pedido
            GROUP BY COALESCE(NULLIF(sku,''), nome_produto)
            ORDER BY valor_total DESC
            LIMIT ?
        """, (top,)).fetchall()

        parados = conn.execute(f"""
            SELECT
                COALESCE(NULLIF(sku,''), nome_produto) AS produto,
                nome_produto,
                SUM(quantidade) AS qtd_total,
                MAX(processado_em) AS ultima_venda,
                CAST(julianday('now') - julianday(MAX(processado_em)) AS INTEGER) AS dias_sem_venda
            FROM itens_pedido
            GROUP BY COALESCE(NULLIF(sku,''), nome_produto)
            HAVING dias_sem_venda >= ?
            ORDER BY dias_sem_venda DESC
            LIMIT ?
        """, (dias_parado, top)).fetchall()

    return {
        "mais_vendidos": [dict(r) for r in mais_vendidos],
        "parados":       [dict(r) for r in parados],
    }


@router.get("/api/analytics/clientes")
async def api_analytics_clientes(top: int = 10):
    """Top clientes por faturamento total + frequência de compra."""
    with db.get_conn() as conn:
        rows = conn.execute(f"""
            SELECT
                cliente,
                COUNT(*) AS num_pedidos,
                SUM(valor) AS valor_total,
                MAX(recebido_em) AS ultima_compra,
                ROUND(SUM(valor)/COUNT(*), 2) AS ticket_medio
            FROM pedidos_fluxo
            WHERE status_fluxo NOT IN ('cancelado','erro')
            GROUP BY cliente
            ORDER BY valor_total DESC
            LIMIT ?
        """, (top,)).fetchall()

        # Alerta de concentração: cliente dominante hoje
        fat_hoje_total = conn.execute(
            "SELECT COALESCE(SUM(valor),0) FROM pedidos_fluxo WHERE DATE(recebido_em)=DATE('now') AND status_fluxo NOT IN ('cancelado','erro')"
        ).fetchone()[0]
        concentracao = None
        if fat_hoje_total > 0:
            top_hoje = conn.execute("""
                SELECT cliente, SUM(valor) as val
                FROM pedidos_fluxo
                WHERE DATE(recebido_em)=DATE('now') AND status_fluxo NOT IN ('cancelado','erro')
                GROUP BY cliente ORDER BY val DESC LIMIT 1
            """).fetchone()
            if top_hoje:
                pct = round(top_hoje["val"] / fat_hoje_total * 100, 1)
                if pct >= 60:
                    concentracao = {"cliente": top_hoje["cliente"], "percentual": pct}

    return {
        "top_clientes": [dict(r) for r in rows],
        "alerta_concentracao": concentracao,
    }


@router.get("/api/analytics/score")
async def api_analytics_score():
    """Score 0-100 da saúde da operação."""
    score = 100
    detalhes = []

    with db.get_conn() as conn:
        erro_permanente = conn.execute(
            "SELECT COUNT(*) FROM fila_eventos WHERE status='erro_permanente'"
        ).fetchone()[0]
        travados = conn.execute(f"""
            SELECT COUNT(*) FROM pedidos_fluxo
            WHERE status_fluxo IN ('recebido','erro')
              AND recebido_em < datetime('now','-30 minutes')
        """).fetchone()[0]
        pedidos_hoje = conn.execute(
            "SELECT COUNT(*) FROM pedidos_fluxo WHERE DATE(recebido_em)=DATE('now')"
        ).fetchone()[0]
        erros_hoje = conn.execute(
            "SELECT COUNT(*) FROM pedidos_fluxo WHERE DATE(recebido_em)=DATE('now') AND status_fluxo='erro'"
        ).fetchone()[0]
        pendentes = conn.execute(
            "SELECT COUNT(*) FROM fila_eventos WHERE status='pendente'"
        ).fetchone()[0]
        buracos_hoje = conn.execute(
            "SELECT COUNT(*) FROM auditoria_sequencia WHERE DATE(detectado_em)=DATE('now') AND resolvido=0"
        ).fetchone()[0]

    if erro_permanente > 0:
        score -= 20
        detalhes.append(f"-20: {erro_permanente} pedido(s) em erro permanente")
    desconto_travados = min(travados * 10, 30)
    if desconto_travados > 0:
        score -= desconto_travados
        detalhes.append(f"-{desconto_travados}: {travados} pedido(s) travado(s)")
    taxa = (erros_hoje / pedidos_hoje * 100) if pedidos_hoje > 0 else 0
    if taxa > 5:
        score -= 10
        detalhes.append(f"-10: taxa de erro hoje {taxa:.1f}%")
    if pendentes > 10:
        score -= 5
        detalhes.append(f"-5: {pendentes} eventos pendentes na fila")
    if buracos_hoje > 0:
        score -= 5
        detalhes.append(f"-5: {buracos_hoje} buraco(s) de sequência hoje")

    score = max(0, score)
    if score >= 80:
        cor = "verde"
    elif score >= 60:
        cor = "amarelo"
    else:
        cor = "vermelho"

    return {"score": score, "cor": cor, "detalhes": detalhes}
