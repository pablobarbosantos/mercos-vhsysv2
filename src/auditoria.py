"""
Auditoria de Sequência + Auditoria de Fluxo
============================================
Detecta:
  1. Buracos na sequência numérica de IDs de pedidos Mercos
  2. Pedidos travados em etapas do fluxo operacional

Rodado via APScheduler (veja main.py).

Tempos configuráveis via .env:
  AUDIT_LIMITE_PROCESSAMENTO_MIN   (padrão: 30)
  AUDIT_LIMITE_SEPARACAO_MIN       (padrão: 120)
  AUDIT_LIMITE_ENVIO_MIN           (padrão: 240)
"""

import logging
import os
from datetime import datetime, timezone

from src import database as db
from src.whatsapp import get_whatsapp

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# Limites de tempo (em minutos) antes de alertar
# ──────────────────────────────────────────────────────────────
LIMITE_PROCESSAMENTO = int(os.getenv("AUDIT_LIMITE_PROCESSAMENTO_MIN", 30))
LIMITE_SEPARACAO     = int(os.getenv("AUDIT_LIMITE_SEPARACAO_MIN", 120))
LIMITE_ENVIO         = int(os.getenv("AUDIT_LIMITE_ENVIO_MIN", 4320))  # padrão: 3 dias (era 4h)

# Evita flood de alertas: só reenvia o mesmo buraco de sequência após X horas
COOLDOWN_ALERTA_HORAS = int(os.getenv("AUDIT_COOLDOWN_HORAS", 4))

# Cooldown de re-alerta para pedidos travados no fluxo (parado_separacao / parado_envio)
COOLDOWN_FLUXO_HORAS = int(os.getenv("AUDIT_COOLDOWN_FLUXO_HORAS", 24))


# ══════════════════════════════════════════════════════════════
# 1. AUDITORIA DE SEQUÊNCIA
# ══════════════════════════════════════════════════════════════

def verificar_sequencia() -> list[dict]:
    """
    Detecta buracos na sequência de NÚMEROS de pedido da empresa (campo 'numero'
    em pedidos_fluxo, ex: 2876). Ignora mercos_id que é global entre todas as
    empresas Mercos e gera falsos positivos.
    Retorna lista de buracos novos (não alertados recentemente).
    """
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT CAST(numero AS INTEGER) as num
               FROM pedidos_fluxo
               WHERE numero IS NOT NULL AND numero != ''
               ORDER BY CAST(numero AS INTEGER)"""
        ).fetchall()

    if len(rows) < 2:
        logger.debug("[Auditoria/Seq] Menos de 2 pedidos — verificação ignorada.")
        return []

    nums_ordenados = sorted(r["num"] for r in rows if r["num"])

    # Detecta buracos comparando números consecutivos (O(n))
    buracos_novos = []
    for i in range(len(nums_ordenados) - 1):
        atual = nums_ordenados[i]
        proximo = nums_ordenados[i + 1]
        gap = proximo - atual - 1
        if gap <= 0:
            continue
        # Limita a 50 buracos por gap para não explodir memória/alertas
        for faltando in range(atual + 1, min(atual + 1 + gap, atual + 51)):
            if not _buraco_ja_alertado(faltando):
                buracos_novos.append({
                    "mercos_id":     faltando,  # armazena numero do pedido
                    "classificacao": "nao_recebido",
                    "descricao":     "Nunca chegou via webhook",
                })
        if gap > 50:
            logger.warning(
                f"[Auditoria/Seq] Gap de {gap} pedidos entre #{atual} e #{proximo} "
                f"— reportando apenas os primeiros 50."
            )

    if not buracos_novos:
        logger.info("[Auditoria/Seq] ✅ Sequência OK — nenhum buraco novo.")
        return []

    logger.warning(
        f"[Auditoria/Seq] ⚠️ {len(buracos_novos)} buraco(s) novo(s): "
        f"{[b['mercos_id'] for b in buracos_novos]}"
    )

    _registrar_buracos(buracos_novos)

    try:
        get_whatsapp().alertar_sequencia_quebrada(buracos_novos)
    except Exception as e:
        logger.warning(f"[Auditoria/Seq] Falha no alerta WhatsApp: {e}")

    return buracos_novos


def _buraco_ja_alertado(mercos_id: int) -> bool:
    """Retorna True se este buraco não deve gerar novo alerta.
    - Se já foi marcado como resolvido: nunca mais alerta.
    - Caso contrário: suprime re-alerta dentro do cooldown de X horas.
    """
    with db.get_conn() as conn:
        row = conn.execute(
            """SELECT detectado_em, resolvido_em, resolvido FROM auditoria_sequencia
               WHERE mercos_id = ?
               ORDER BY detectado_em DESC LIMIT 1""",
            (mercos_id,)
        ).fetchone()
    if not row:
        return False
    # Buraco marcado como resolvido → nunca mais alerta
    if row["resolvido"]:
        return True
    ultima = datetime.fromisoformat(row["detectado_em"])
    if ultima.tzinfo is None:
        ultima = ultima.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ultima).total_seconds() / 3600 < COOLDOWN_ALERTA_HORAS


def _registrar_buracos(buracos: list[dict]):
    with db.get_conn() as conn:
        for b in buracos:
            conn.execute(
                """INSERT INTO auditoria_sequencia (mercos_id, classificacao, detectado_em)
                   VALUES (?, ?, ?)""",
                (b["mercos_id"], b["classificacao"], datetime.now(timezone.utc).isoformat())
            )


def marcar_buraco_resolvido(mercos_id: int, resolucao: str = "processado_manualmente"):
    """Chamado quando um pedido que estava faltando chega ou é explicado."""
    with db.get_conn() as conn:
        conn.execute(
            """UPDATE auditoria_sequencia
               SET resolvido = 1, resolucao = ?, resolvido_em = ?
               WHERE mercos_id = ?""",
            (resolucao, datetime.now(timezone.utc).isoformat(), mercos_id)
        )
    logger.info(f"[Auditoria/Seq] Buraco {mercos_id} marcado como resolvido: {resolucao}")


def marcar_todos_buracos_resolvidos(resolucao: str = "verificado_em_lote") -> int:
    """Marca todos os buracos abertos como resolvidos de uma vez. Retorna qtd resolvida."""
    agora = datetime.now(timezone.utc).isoformat()
    with db.get_conn() as conn:
        cur = conn.execute(
            """UPDATE auditoria_sequencia
               SET resolvido = 1, resolucao = ?, resolvido_em = ?
               WHERE resolvido = 0""",
            (resolucao, agora)
        )
        qtd = cur.rowcount
    logger.info(f"[Auditoria/Seq] {qtd} buracos resolvidos em lote.")
    return qtd


# ══════════════════════════════════════════════════════════════
# 2. AUDITORIA DE FLUXO
# ══════════════════════════════════════════════════════════════

def verificar_fluxo() -> list[dict]:
    """
    Detecta pedidos travados em etapas do fluxo operacional.
    Retorna lista de alertas com severidade.
    """
    alertas = []

    with db.get_conn() as conn:

        # ── Recebidos mas não processados (erro ou travado)
        nao_processados = conn.execute(f"""
            SELECT mercos_id, numero, cliente, valor, recebido_em, status_fluxo
            FROM pedidos_fluxo
            WHERE status_fluxo IN ('recebido', 'erro')
              AND recebido_em < datetime('now', '-{LIMITE_PROCESSAMENTO} minutes')
        """).fetchall()

        for r in nao_processados:
            alertas.append({
                "mercos_id":  r["mercos_id"],
                "numero":     r["numero"],
                "cliente":    r["cliente"],
                "tipo":       "nao_processado",
                "status":     r["status_fluxo"],
                "desde":      r["recebido_em"],
                "mensagem":   f"Pedido #{r['numero']} recebido há >{LIMITE_PROCESSAMENTO}min sem processar",
                "severidade": "alta",
            })

        # ── Processados mas sem separação
        sem_separacao = conn.execute(f"""
            SELECT mercos_id, numero, cliente, valor, processado_em
            FROM pedidos_fluxo
            WHERE status_fluxo = 'processado'
              AND processado_em < datetime('now', '-{LIMITE_SEPARACAO} minutes')
              AND separado_em IS NULL
              AND (ultimo_alerta_fluxo_em IS NULL
                   OR ultimo_alerta_fluxo_em < datetime('now', '-{COOLDOWN_FLUXO_HORAS} hours'))
        """).fetchall()

        for r in sem_separacao:
            alertas.append({
                "mercos_id":  r["mercos_id"],
                "numero":     r["numero"],
                "cliente":    r["cliente"],
                "tipo":       "parado_separacao",
                "status":     "aguardando_separacao",
                "desde":      r["processado_em"],
                "mensagem":   f"Pedido #{r['numero']} há >{LIMITE_SEPARACAO//60}h sem separação",
                "severidade": "media",
            })

        # ── Separados mas não enviados
        sem_envio = conn.execute(f"""
            SELECT mercos_id, numero, cliente, valor, separado_em
            FROM pedidos_fluxo
            WHERE status_fluxo = 'separado'
              AND separado_em < datetime('now', '-{LIMITE_ENVIO} minutes')
              AND enviado_em IS NULL
              AND (ultimo_alerta_fluxo_em IS NULL
                   OR ultimo_alerta_fluxo_em < datetime('now', '-{COOLDOWN_FLUXO_HORAS} hours'))
        """).fetchall()

        for r in sem_envio:
            alertas.append({
                "mercos_id":  r["mercos_id"],
                "numero":     r["numero"],
                "cliente":    r["cliente"],
                "tipo":       "parado_envio",
                "status":     "aguardando_envio",
                "desde":      r["separado_em"],
                "mensagem":   f"Pedido #{r['numero']} separado há >{LIMITE_ENVIO//60}h sem envio",
                "severidade": "media",
            })

    if not alertas:
        logger.info("[Auditoria/Fluxo] ✅ Todos os pedidos dentro do prazo.")
        return []

    logger.warning(f"[Auditoria/Fluxo] ⚠️ {len(alertas)} pedido(s) travado(s).")

    try:
        get_whatsapp().alertar_fluxo_travado(alertas)
    except Exception as e:
        logger.warning(f"[Auditoria/Fluxo] Falha no alerta WhatsApp: {e}")

    # Registra timestamp do alerta para evitar re-envio dentro do cooldown
    ids_alertados = [a["mercos_id"] for a in alertas if a.get("tipo") in ("parado_separacao", "parado_envio")]
    if ids_alertados:
        placeholders = ",".join("?" * len(ids_alertados))
        with db.get_conn() as conn:
            conn.execute(
                f"UPDATE pedidos_fluxo SET ultimo_alerta_fluxo_em = datetime('now') WHERE mercos_id IN ({placeholders})",
                ids_alertados,
            )

    return alertas


# ══════════════════════════════════════════════════════════════
# 3. FECHAMENTO DO DIA
# ══════════════════════════════════════════════════════════════

def fechamento_do_dia():
    """
    Consolida os dados do dia e envia resumo via WhatsApp.
    Agendado para rodar às 20h (configurável no main.py).
    """
    with db.get_conn() as conn:
        total        = conn.execute("SELECT COUNT(*) FROM pedidos_fluxo WHERE DATE(recebido_em) = DATE('now')").fetchone()[0]
        processados  = conn.execute("SELECT COUNT(*) FROM pedidos_fluxo WHERE DATE(recebido_em) = DATE('now') AND status_fluxo != 'recebido'").fetchone()[0]
        separados    = conn.execute("SELECT COUNT(*) FROM pedidos_fluxo WHERE DATE(recebido_em) = DATE('now') AND separado_em IS NOT NULL").fetchone()[0]
        enviados     = conn.execute("SELECT COUNT(*) FROM pedidos_fluxo WHERE DATE(recebido_em) = DATE('now') AND status_fluxo = 'enviado'").fetchone()[0]
        com_erro     = conn.execute("SELECT COUNT(*) FROM pedidos_processados WHERE DATE(processado_em) = DATE('now') AND status = 'erro'").fetchone()[0]
        buracos_dia  = conn.execute("SELECT COUNT(*) FROM auditoria_sequencia WHERE DATE(detectado_em) = DATE('now') AND resolvido = 0").fetchone()[0]

    stats = {
        "total":       total,
        "processados": processados,
        "separados":   separados,
        "enviados":    enviados,
        "com_erro":    com_erro,
        "buracos":     buracos_dia,
    }

    logger.info(f"[Auditoria] Fechamento do dia: {stats}")

    try:
        get_whatsapp().enviar_fechamento_dia(stats)
    except Exception as e:
        logger.warning(f"[Auditoria] Falha no fechamento do dia: {e}")

    return stats


# ══════════════════════════════════════════════════════════════
# 4. BOLETOS VENCIDOS
# ══════════════════════════════════════════════════════════════

def verificar_boletos_vencidos():
    """
    Consulta VHSys por contas a receber em aberto com vencimento vencido.
    Envia alerta WhatsApp se houver boletos vencidos.
    Chamado diariamente às 09h pelo APScheduler.
    """
    try:
        from vhsys_service import VhsysService
        vhsys = VhsysService()
        boletos = vhsys.buscar_boletos_vencidos()
    except Exception as e:
        logger.error(f"[Auditoria/Boletos] Erro ao consultar VHSys: {e}")
        return []

    if not boletos:
        logger.info("[Auditoria/Boletos] ✅ Nenhum boleto vencido.")
        return []

    logger.warning(f"[Auditoria/Boletos] ⚠️ {len(boletos)} boleto(s) vencido(s).")

    try:
        wa = get_whatsapp()
        linhas = []
        for b in boletos[:5]:
            nome    = b.get("nome_cliente") or b.get("cliente") or "?"
            venc    = b.get("vencimento_rec") or b.get("vencimento") or "?"
            valor   = b.get("valor_rec") or b.get("valor") or "?"
            doc     = b.get("n_documento_rec") or b.get("identificacao") or "?"
            linhas.append(f"  • {nome} | Doc: {doc} | R$ {valor} | Venc: {venc}")
        resto = f"\n  ... e mais {len(boletos) - 5}" if len(boletos) > 5 else ""
        msg = (
            f"💰 *{len(boletos)} boleto(s) VENCIDO(S)*\n"
            f"━━━━━━━━━━━━━━━━\n"
            + "\n".join(linhas) + resto +
            f"\n\n👉 Verifique o financeiro no VHSys.\n"
            f"🕐 {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        )
        wa._enviar(wa.notify_to, msg)
    except Exception as e:
        logger.warning(f"[Auditoria/Boletos] Falha no alerta WhatsApp: {e}")

    return boletos


# ──────────────────────────────────────────────────────────────
# Monitor da fila de eventos
# ──────────────────────────────────────────────────────────────

def verificar_fila_eventos() -> dict:
    """
    Alerta se houver eventos em erro_permanente ou fila com backlog alto.
    Chamado a cada 15 minutos pelo APScheduler.
    """
    from src import database as db_mod

    stats   = db_mod.fila_stats()
    alertas = []

    erro_permanente = stats.get("erro_permanente", 0)
    pendentes       = stats.get("pendente", 0)

    if erro_permanente > 0:
        msg = (
            f"⛔ FILA: {erro_permanente} pedido(s) em ERRO PERMANENTE — "
            f"intervenção manual necessária. Verifique /admin/api/fila"
        )
        logger.error(f"[Auditoria/Fila] {msg}")
        alertas.append(msg)
        try:
            get_whatsapp().notificar_pedido_erro(
                numero_pedido="FILA",
                mercos_id=0,
                cliente="Sistema",
                motivo=msg,
            )
        except Exception as e:
            logger.warning(f"[Auditoria/Fila] Falha ao enviar alerta WhatsApp: {e}")

    if pendentes > 50:
        msg = f"⚠️ FILA: {pendentes} eventos pendentes (backlog alto)."
        logger.warning(f"[Auditoria/Fila] {msg}")
        alertas.append(msg)

    return {"stats": stats, "alertas": alertas}


# ══════════════════════════════════════════════════════════════
# 6. RECONCILIAÇÃO FIM DE DIA
# ══════════════════════════════════════════════════════════════

def reconciliar_fim_de_dia():
    """
    Job fim de dia (19:55): detecta pedidos recebidos hoje que não foram
    processados com sucesso, reinicia os que estão em erro_permanente,
    e notifica via WhatsApp com resumo.
    """
    logger.info("[Reconciliacao] Iniciando reconciliação fim de dia...")
    stats = db.reconciliar_pendentes_hoje()

    reenf     = len(stats["reenfileirados"])
    andamento = len(stats["em_andamento"])
    incons    = len(stats["inconsistentes"])

    logger.info(
        f"[Reconciliacao] Total pendentes: {stats['total']} | "
        f"Reenfileirados: {reenf} | Em andamento: {andamento} | "
        f"Inconsistentes: {incons}"
    )
    for p in stats["reenfileirados"]:
        logger.warning(
            f"[Reconciliacao] Reenfileirado mercos_id={p['mercos_id']} "
            f"#{p['numero']} — era erro_permanente (tentativas={p['tentativas']})"
        )
    for p in stats["inconsistentes"]:
        logger.error(
            f"[Reconciliacao] Inconsistência mercos_id={p['mercos_id']} "
            f"#{p['numero']} — fila_status={p.get('fila_status')}"
        )

    get_whatsapp().notificar_reconciliacao(stats)


# ══════════════════════════════════════════════════════════════
# 5. VERIFICAÇÃO DE SYNC VHSYS (via referencia_pedido)
# ══════════════════════════════════════════════════════════════

def verificar_sync_vhsys(vhsys_service) -> None:
    """
    Cruza os pedidos do VHSys (campo referencia_pedido = número Mercos)
    com os pedidos recebidos localmente (pedidos_fluxo).

    Detecta dois tipos de problema:
      1. Pedido recebido via webhook mas ausente no VHSys (falha no processamento).
      2. Gap na sequência dos números Mercos no VHSys (webhook nunca chegou).

    Só analisa pedidos com referencia_pedido preenchida — field adicionado em
    06/04/2026; pedidos anteriores são ignorados.

    Roda a cada 30 min via APScheduler.
    """
    from datetime import date, timedelta

    hoje = date.today().isoformat()
    ontem = (date.today() - timedelta(days=1)).isoformat()

    # ── 1. Pedidos no VHSys com referencia_pedido preenchida (últimos 2 dias) ─
    try:
        pedidos_vhsys = vhsys_service.buscar_pedidos_recentes(dias=2)
    except Exception as e:
        logger.error(f"[Sync/VHSys] Erro ao buscar pedidos VHSys: {e}")
        return

    refs_vhsys: set[int] = set()
    for p in pedidos_vhsys:
        ref = str(p.get("referencia_pedido") or "").strip()
        if ref.isdigit() and int(ref) > 0:
            refs_vhsys.add(int(ref))

    if not refs_vhsys:
        logger.debug("[Sync/VHSys] Nenhum pedido VHSys com referencia_pedido numérica — ainda sem dados pós-deploy.")
        return

    # ── 2. Pedidos recebidos localmente nos últimos 2 dias ────────────────────
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT CAST(numero AS INTEGER) as num, cliente
            FROM pedidos_fluxo
            WHERE (recebido_em LIKE ? OR recebido_em LIKE ?)
              AND numero IS NOT NULL AND numero != ''
              AND CAST(numero AS INTEGER) > 0
        """, (f"{hoje}%", f"{ontem}%")).fetchall()

    nums_fluxo: dict[int, str] = {
        row["num"]: row["cliente"]
        for row in rows
        if row["num"]
    }

    # ── 3. Pedidos no fluxo local mas fora do VHSys (falha de processamento) ─
    # Só verifica pedidos cujo número está na faixa coberta pelo VHSys
    min_ref = min(refs_vhsys)
    max_ref = max(refs_vhsys)

    faltando_vhsys = {
        n: c for n, c in nums_fluxo.items()
        if n >= min_ref and n not in refs_vhsys
    }

    # ── 4. Gaps na sequência VHSys (webhook nunca chegou) ────────────────────
    seq_ordenada = sorted(refs_vhsys)
    gaps_seq: list[int] = []
    for i in range(len(seq_ordenada) - 1):
        atual  = seq_ordenada[i]
        proximo = seq_ordenada[i + 1]
        for gap in range(atual + 1, proximo):
            # Só reporta se também não está no fluxo local (webhook realmente perdido)
            if gap not in nums_fluxo:
                gaps_seq.append(gap)

    # ── 5. Log e alerta ───────────────────────────────────────────────────────
    problemas: list[str] = []

    if faltando_vhsys:
        for num, cliente in sorted(faltando_vhsys.items()):
            logger.warning(f"[Sync/VHSys] Pedido #{num} ({cliente}) recebido mas ausente no VHSys.")
        nomes = ", ".join(f"#{n}" for n in sorted(faltando_vhsys))
        problemas.append(f"Recebidos mas não criados no VHSys: {nomes}")

    if gaps_seq:
        for gap in gaps_seq:
            logger.warning(f"[Sync/VHSys] Gap na sequência VHSys: pedido #{gap} não encontrado.")
        nomes = ", ".join(f"#{n}" for n in gaps_seq)
        problemas.append(f"Webhook não recebido (gap de sequência): {nomes}")

    if problemas:
        msg = "⚠️ *Sync VHSys/Mercos*\n" + "\n".join(f"• {p}" for p in problemas)
        msg += "\nVerifique o painel admin e reprocesse se necessário."
        try:
            get_whatsapp().enviar_mensagem(msg)
        except Exception as e:
            logger.warning(f"[Sync/VHSys] Falha ao enviar WhatsApp: {e}")
    else:
        logger.info(
            f"[Sync/VHSys] ✅ {len(refs_vhsys)} pedido(s) VHSys verificados "
            f"(#{min_ref}–#{max_ref}) — nenhum problema encontrado."
        )
