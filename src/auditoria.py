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
LIMITE_ENVIO         = int(os.getenv("AUDIT_LIMITE_ENVIO_MIN", 240))

# Evita flood de alertas: só reenvia o mesmo buraco após X horas
COOLDOWN_ALERTA_HORAS = int(os.getenv("AUDIT_COOLDOWN_HORAS", 4))


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
    """Retorna True se este buraco foi alertado dentro do cooldown."""
    with db.get_conn() as conn:
        row = conn.execute(
            """SELECT detectado_em FROM auditoria_sequencia
               WHERE mercos_id = ?
               ORDER BY detectado_em DESC LIMIT 1""",
            (mercos_id,)
        ).fetchone()
    if not row:
        return False
    ultima = datetime.fromisoformat(row["detectado_em"])
    if ultima.tzinfo is None:
        ultima = ultima.replace(tzinfo=timezone.utc)
    delta_horas = (datetime.now(timezone.utc) - ultima).total_seconds() / 3600
    return delta_horas < COOLDOWN_ALERTA_HORAS


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
