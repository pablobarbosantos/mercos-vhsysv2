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


@router.post("/api/reconciliacao/verificar-agora")
async def api_reconciliacao_agora():
    """Força execução imediata da reconciliação fim de dia (não bloqueia a resposta)."""
    from src.auditoria import reconciliar_fim_de_dia
    threading.Thread(target=reconciliar_fim_de_dia, daemon=True).start()
    return {"ok": True, "mensagem": "Reconciliação iniciada em background — verifique logs e WhatsApp."}


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


# ──────────────────────────────────────────────────────────────────────────────
# Diagnóstico e Correção de dados históricos
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/api/dados/diagnostico")
async def api_diagnostico():
    """Retorna estado do banco para entender por que analytics mostra zero."""
    with db.get_conn() as conn:
        fila_total = conn.execute("SELECT COUNT(*) FROM fila_eventos").fetchone()[0]
        fila_por_evento = conn.execute(
            "SELECT evento, COUNT(*) AS qtd FROM fila_eventos GROUP BY evento"
        ).fetchall()
        fluxo_total = conn.execute("SELECT COUNT(*) FROM pedidos_fluxo").fetchone()[0]
        fluxo_valor_zero = conn.execute(
            "SELECT COUNT(*) FROM pedidos_fluxo WHERE valor = 0 OR valor IS NULL"
        ).fetchone()[0]
        fluxo_com_valor = conn.execute(
            "SELECT COUNT(*) FROM pedidos_fluxo WHERE valor > 0"
        ).fetchone()[0]
        processados_total = conn.execute("SELECT COUNT(*) FROM pedidos_processados").fetchone()[0]
        itens_total = conn.execute("SELECT COUNT(*) FROM itens_pedido").fetchone()[0]
        sample_fluxo = conn.execute(
            "SELECT mercos_id, numero, cliente, valor, cidade, status_fluxo FROM pedidos_fluxo ORDER BY recebido_em DESC LIMIT 5"
        ).fetchall()
        sample_fila = conn.execute(
            "SELECT id, evento, mercos_id, status, SUBSTR(payload_json,1,100) as payload_preview FROM fila_eventos ORDER BY id DESC LIMIT 5"
        ).fetchall()
    return {
        "fila_eventos": {
            "total": fila_total,
            "por_evento": {r["evento"]: r["qtd"] for r in fila_por_evento},
        },
        "pedidos_fluxo": {
            "total": fluxo_total,
            "com_valor_zero": fluxo_valor_zero,
            "com_valor_preenchido": fluxo_com_valor,
        },
        "pedidos_processados": {"total": processados_total},
        "itens_pedido": {"total": itens_total},
        "sample_fluxo_recentes": [dict(r) for r in sample_fluxo],
        "sample_fila_recentes": [dict(r) for r in sample_fila],
    }

@router.post("/api/dados/corrigir-valores")
async def api_corrigir_valores(request: Request):
    """
    Retroativamente:
    - Atualiza pedidos_fluxo.valor, cidade e bairro lendo o payload_json da fila
    - Popula itens_pedido para pedidos que ainda não têm itens registrados
    """
    import json as _json
    from datetime import datetime, timezone as _tz

    ip = request.client.host if request.client else ""
    atualizados = 0
    itens_preenchidos = 0

    with db.get_conn() as conn:
        # Busca o payload mais completo por mercos_id (prefere pedido.gerado)
        rows = conn.execute("""
            SELECT mercos_id,
                   MAX(CASE WHEN evento='pedido.gerado' THEN payload_json END)
                   AS payload_gerado,
                   MAX(CASE WHEN evento='pedido.faturado' THEN payload_json END)
                   AS payload_faturado,
                   MAX(criado_em) AS criado_em
            FROM fila_eventos
            WHERE evento IN ('pedido.gerado','pedido.faturado')
              AND mercos_id IS NOT NULL
            GROUP BY mercos_id
        """).fetchall()

        # Resolve qual payload usar: prefere pedido.gerado
        class _Row:
            def __init__(self, mid, pj, em):
                self.mercos_id   = mid
                self.payload_json = pj
                self.criado_em   = em
            def __getitem__(self, k): return getattr(self, k)

        rows = [_Row(r["mercos_id"],
                     r["payload_gerado"] or r["payload_faturado"],
                     r["criado_em"]) for r in rows if (r["payload_gerado"] or r["payload_faturado"])]

        for row in rows:
            try:
                dados  = _json.loads(row["payload_json"])
                valor  = float(dados.get("valor_total", 0) or 0)
                cidade = dados.get("cliente_cidade", "") or ""
                bairro = dados.get("cliente_bairro", "") or ""

                # Atualiza fluxo
                conn.execute("""
                    UPDATE pedidos_fluxo
                    SET valor  = CASE WHEN ? > 0 THEN ? ELSE valor END,
                        cidade = CASE WHEN ? != '' THEN ? ELSE cidade END,
                        bairro = CASE WHEN ? != '' THEN ? ELSE bairro END
                    WHERE mercos_id = ?
                """, (valor, valor, cidade, cidade, bairro, bairro, row["mercos_id"]))

                atualizados += 1

                # Popula itens se ainda não existem
                tem_itens = conn.execute(
                    "SELECT 1 FROM itens_pedido WHERE mercos_id = ? LIMIT 1", (row["mercos_id"],)
                ).fetchone()
                if not tem_itens:
                    itens_raw = dados.get("itens", [])
                    ts = row["criado_em"] or datetime.now(_tz.utc).isoformat()
                    for it in itens_raw:
                        if it.get("excluido"):
                            continue
                        qtd   = float(it.get("quantidade", 0) or 0)
                        preco = float(it.get("preco_liquido", 0) or 0)
                        conn.execute("""
                            INSERT INTO itens_pedido
                                (mercos_id, sku, nome_produto, quantidade, valor_unit, valor_total, processado_em)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (
                            row["mercos_id"],
                            str(it.get("produto_codigo", "") or "").strip(),
                            it.get("produto_nome", ""),
                            qtd, preco, qtd * preco, ts,
                        ))
                    if itens_raw:
                        itens_preenchidos += 1
            except Exception:
                pass

    db.admin_registrar_acao("corrigir_valores", None,
                            f"{atualizados} pedidos corrigidos, {itens_preenchidos} com itens restaurados", ip)
    return {"ok": True, "atualizados": atualizados, "itens_preenchidos": itens_preenchidos}


# ──────────────────────────────────────────────────────────────────────────────
# Auditoria — resolver todos os buracos
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/api/auditoria/sequencia/resolver-todos")
async def api_resolver_todos_buracos(request: Request):
    from src.auditoria import marcar_todos_buracos_resolvidos
    ip = request.client.host if request.client else ""
    qtd = marcar_todos_buracos_resolvidos("verificado_em_lote")
    db.admin_registrar_acao("resolver_todos_buracos", None,
                            f"{qtd} buracos resolvidos em lote", ip)
    return {"ok": True, "resolvidos": qtd}


# ──────────────────────────────────────────────────────────────────────────────
# Relatórios ABC
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/api/relatorios/abc-produtos")
async def api_abc_produtos(data_inicio: str = None, data_fim: str = None, top: int = 100):
    """Classificação ABC de produtos por receita (A=80%, B=95%, C=100%)."""
    filtros = []
    params = []
    if data_inicio:
        filtros.append("i.processado_em >= ?")
        params.append(data_inicio)
    if data_fim:
        filtros.append("i.processado_em <= ?")
        params.append(data_fim + "T23:59:59")
    where = ("WHERE " + " AND ".join(filtros)) if filtros else ""

    with db.get_conn() as conn:
        rows = conn.execute(f"""
            SELECT
                COALESCE(NULLIF(i.sku,''), i.nome_produto) AS produto,
                i.nome_produto,
                SUM(i.quantidade)  AS qtd_total,
                SUM(i.valor_total) AS valor_total,
                COUNT(DISTINCT i.mercos_id) AS num_pedidos
            FROM itens_pedido i
            {where}
            GROUP BY COALESCE(NULLIF(i.sku,''), i.nome_produto)
            ORDER BY valor_total DESC
            LIMIT ?
        """, (*params, top)).fetchall()

    total_geral = sum(r["valor_total"] or 0 for r in rows)
    acumulado = 0
    resultado = []
    for r in rows:
        v = r["valor_total"] or 0
        acumulado += v
        pct_acum = (acumulado / total_geral * 100) if total_geral > 0 else 0
        classe = "A" if pct_acum <= 80 else ("B" if pct_acum <= 95 else "C")
        resultado.append({
            **dict(r),
            "pct_receita": round(v / total_geral * 100, 2) if total_geral > 0 else 0,
            "pct_acumulado": round(pct_acum, 2),
            "classe": classe,
        })
    return {"itens": resultado, "total_geral": round(total_geral, 2)}


@router.get("/api/relatorios/abc-clientes")
async def api_abc_clientes(data_inicio: str = None, data_fim: str = None, top: int = 100):
    """Classificação ABC de clientes por receita."""
    filtros = ["status_fluxo NOT IN ('cancelado','erro')"]
    params = []
    if data_inicio:
        filtros.append("recebido_em >= ?")
        params.append(data_inicio)
    if data_fim:
        filtros.append("recebido_em <= ?")
        params.append(data_fim + "T23:59:59")
    where = "WHERE " + " AND ".join(filtros)

    with db.get_conn() as conn:
        rows = conn.execute(f"""
            SELECT
                cliente,
                COUNT(*) AS num_pedidos,
                SUM(valor) AS valor_total,
                AVG(valor) AS ticket_medio,
                MAX(recebido_em) AS ultima_compra
            FROM pedidos_fluxo
            {where}
            GROUP BY cliente
            ORDER BY valor_total DESC
            LIMIT ?
        """, (*params, top)).fetchall()

    total_geral = sum(r["valor_total"] or 0 for r in rows)
    acumulado = 0
    resultado = []
    for r in rows:
        v = r["valor_total"] or 0
        acumulado += v
        pct_acum = (acumulado / total_geral * 100) if total_geral > 0 else 0
        classe = "A" if pct_acum <= 80 else ("B" if pct_acum <= 95 else "C")
        resultado.append({
            **dict(r),
            "ticket_medio": round(r["ticket_medio"] or 0, 2),
            "pct_receita": round(v / total_geral * 100, 2) if total_geral > 0 else 0,
            "pct_acumulado": round(pct_acum, 2),
            "classe": classe,
        })
    return {"itens": resultado, "total_geral": round(total_geral, 2)}


# ──────────────────────────────────────────────────────────────────────────────
# Analytics com filtros
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/api/analytics/resumo-filtrado")
async def api_analytics_resumo_filtrado(
    data_inicio: str = None, data_fim: str = None,
    cliente: str = None, cidade: str = None, bairro: str = None
):
    """Resumo de faturamento com filtros opcionais."""
    filtros = ["status_fluxo NOT IN ('cancelado','erro')"]
    params: list = []

    if data_inicio:
        filtros.append("recebido_em >= ?")
        params.append(data_inicio)
    if data_fim:
        filtros.append("recebido_em <= ?")
        params.append(data_fim + "T23:59:59")
    if cliente:
        filtros.append("cliente LIKE ?")
        params.append(f"%{cliente}%")
    if cidade:
        filtros.append("cidade LIKE ?")
        params.append(f"%{cidade}%")
    if bairro:
        filtros.append("bairro LIKE ?")
        params.append(f"%{bairro}%")

    where = "WHERE " + " AND ".join(filtros)

    with db.get_conn() as conn:
        faturamento = conn.execute(
            f"SELECT COALESCE(SUM(valor),0) FROM pedidos_fluxo {where}", params
        ).fetchone()[0]
        num_pedidos = conn.execute(
            f"SELECT COUNT(*) FROM pedidos_fluxo {where}", params
        ).fetchone()[0]
        ticket_medio = round(faturamento / num_pedidos, 2) if num_pedidos > 0 else 0

        # Top clientes no período filtrado
        top_clientes = conn.execute(f"""
            SELECT cliente, COUNT(*) AS num_pedidos, SUM(valor) AS valor_total,
                   AVG(valor) AS ticket_medio, MAX(recebido_em) AS ultima_compra
            FROM pedidos_fluxo {where}
            GROUP BY cliente ORDER BY valor_total DESC LIMIT 10
        """, params).fetchall()

        # Bairros no período
        por_bairro = conn.execute(f"""
            SELECT COALESCE(NULLIF(bairro,''),'(sem bairro)') AS bairro,
                   COUNT(*) AS num_pedidos, SUM(valor) AS faturamento
            FROM pedidos_fluxo {where}
            GROUP BY bairro ORDER BY faturamento DESC LIMIT 20
        """, params).fetchall()

    return {
        "faturamento_total": round(faturamento, 2),
        "num_pedidos": num_pedidos,
        "ticket_medio": ticket_medio,
        "top_clientes": [dict(r) for r in top_clientes],
        "por_bairro": [dict(r) for r in por_bairro],
    }


# ──────────────────────────────────────────────────────────────────────────────
# Módulo Separação
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/api/separacao/fila")
async def api_separacao_fila():
    """Pedidos em status 'processado' prontos para separação, com seus itens."""
    with db.get_conn() as conn:
        pedidos = conn.execute("""
            SELECT f.mercos_id, f.numero, f.cliente, f.valor,
                   f.cidade, f.bairro, f.processado_em, f.recebido_em,
                   p.vhsys_id
            FROM pedidos_fluxo f
            LEFT JOIN pedidos_processados p ON p.mercos_id = f.mercos_id
            WHERE f.status_fluxo = 'processado'
            ORDER BY f.processado_em ASC
        """).fetchall()

        resultado = []
        for p in pedidos:
            itens = conn.execute("""
                SELECT sku, nome_produto, quantidade, valor_unit, valor_total
                FROM itens_pedido WHERE mercos_id = ?
            """, (p["mercos_id"],)).fetchall()
            resultado.append({
                **dict(p),
                "itens": [dict(i) for i in itens],
                "total_itens": len(itens),
            })

    return {"pedidos": resultado, "total": len(resultado)}


@router.get("/api/separacao/guia/{mercos_id}")
async def api_separacao_guia(mercos_id: int):
    """Guia de separação de um pedido específico."""
    with db.get_conn() as conn:
        pedido = conn.execute(
            "SELECT * FROM pedidos_fluxo WHERE mercos_id = ?", (mercos_id,)
        ).fetchone()
        if not pedido:
            raise HTTPException(status_code=404, detail="Pedido não encontrado")
        itens = conn.execute(
            "SELECT * FROM itens_pedido WHERE mercos_id = ?", (mercos_id,)
        ).fetchall()
        vhsys = conn.execute(
            "SELECT vhsys_id FROM pedidos_processados WHERE mercos_id = ?", (mercos_id,)
        ).fetchone()
    return {
        "pedido": dict(pedido),
        "vhsys_id": vhsys["vhsys_id"] if vhsys else None,
        "itens": [dict(i) for i in itens],
    }


@router.get("/api/separacao/guia-lote")
async def api_separacao_guia_lote(ids: str):
    """
    Guia consolidado de múltiplos pedidos.
    ids = '101,102,103' (comma-separated)
    Retorna itens agrupados por produto com lista de pedidos que os contêm.
    """
    try:
        mercos_ids = [int(x.strip()) for x in ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="IDs inválidos")

    if not mercos_ids:
        raise HTTPException(status_code=400, detail="Nenhum ID fornecido")

    placeholders = ",".join("?" * len(mercos_ids))
    with db.get_conn() as conn:
        pedidos = conn.execute(
            f"SELECT mercos_id, numero, cliente, cidade, bairro, valor FROM pedidos_fluxo WHERE mercos_id IN ({placeholders})",
            mercos_ids
        ).fetchall()
        itens = conn.execute(
            f"SELECT * FROM itens_pedido WHERE mercos_id IN ({placeholders}) ORDER BY nome_produto",
            mercos_ids
        ).fetchall()

    # Agrupa itens por produto
    agrupado: dict = {}
    for item in itens:
        chave = item["sku"] or item["nome_produto"] or "?"
        if chave not in agrupado:
            agrupado[chave] = {
                "sku": item["sku"],
                "nome_produto": item["nome_produto"],
                "qtd_total": 0,
                "pedidos": [],
            }
        agrupado[chave]["qtd_total"] += item["quantidade"] or 0
        agrupado[chave]["pedidos"].append({
            "mercos_id": item["mercos_id"],
            "quantidade": item["quantidade"],
        })

    return {
        "pedidos": [dict(p) for p in pedidos],
        "itens_consolidados": list(agrupado.values()),
        "total_pedidos": len(pedidos),
    }


@router.get("/api/separacao/em-separacao")
async def api_separacao_em_separacao():
    """Pedidos em status 'separado' aguardando envio."""
    with db.get_conn() as conn:
        pedidos = conn.execute("""
            SELECT f.mercos_id, f.numero, f.cliente, f.valor,
                   f.cidade, f.bairro, f.separado_em, f.processado_em,
                   p.vhsys_id
            FROM pedidos_fluxo f
            LEFT JOIN pedidos_processados p ON p.mercos_id = f.mercos_id
            WHERE f.status_fluxo = 'separado'
            ORDER BY f.separado_em ASC
        """).fetchall()
        resultado = []
        for p in pedidos:
            itens = conn.execute(
                "SELECT sku, nome_produto, quantidade FROM itens_pedido WHERE mercos_id = ?",
                (p["mercos_id"],)
            ).fetchall()
            resultado.append({**dict(p), "itens": [dict(i) for i in itens]})
    return {"pedidos": resultado, "total": len(resultado)}
