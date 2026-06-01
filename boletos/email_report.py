"""
Relatório matinal por e-mail: boletos + previsão de cartão.
Dispara no mesmo horário do relatório WhatsApp (7:30 seg-sex).
"""
import logging
import os
import smtplib
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from boletos import database as db

logger = logging.getLogger(__name__)

# ── Config via env ──────────────────────────────────────────────
SMTP_HOST     = os.getenv("SMTP_HOST",     "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER     = os.getenv("SMTP_USER",     "")
SMTP_PASS     = os.getenv("SMTP_PASS",     "")
EMAIL_DEST    = os.getenv("EMAIL_RELATORIO", "pablobarbosantos@gmail.com")
CARTAO_DESC   = float(os.getenv("CARTAO_DESCONTO_PCT", "2.5"))


def _brl(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _data_recebimento_cartao(ref: date) -> date:
    """Sexta/Sáb/Dom → segunda; demais → dia seguinte."""
    wd = ref.weekday()  # 0=seg … 6=dom
    if wd == 4:   # sexta
        return ref + timedelta(days=3)
    if wd == 5:   # sábado
        return ref + timedelta(days=2)
    if wd == 6:   # domingo
        return ref + timedelta(days=1)
    return ref + timedelta(days=1)


def _vendas_cartao_hoje() -> float:
    """Soma vendas de cartão registradas hoje (romaneio retornos + tabela vendas_cartao)."""
    hoje = date.today().isoformat()
    from src.database import get_conn as sync_conn
    total = 0.0
    try:
        with sync_conn() as conn:
            # Entregas pagas no cartão hoje
            row = conn.execute(
                """SELECT COALESCE(SUM(pf.valor), 0) FROM pedidos_fluxo pf
                   JOIN romaneio_pedidos rp ON rp.mercos_id = pf.mercos_id
                   WHERE rp.forma_pgto = 'cartao'
                     AND DATE(pf.entregue_em) = ?""",
                (hoje,)
            ).fetchone()
            total += float(row[0])
            # Tabela de vendas avulsas no cartão
            row2 = conn.execute(
                "SELECT COALESCE(SUM(valor), 0) FROM vendas_cartao WHERE data = ?",
                (hoje,)
            ).fetchone()
            total += float(row2[0])
    except Exception as e:
        logger.warning("[EmailReport] Erro ao buscar vendas cartão: %s", e)
    return total


def _html_report(stats: dict, hoje: date) -> str:
    pagos    = stats["pagos"]
    abertos  = stats["abertos"]
    vencidos = stats["vencidos"]
    total_pago    = stats["total_pago"]
    total_aberto  = stats["total_aberto"]
    total_vencido = stats["total_vencido"]

    # Card settlement
    cartao_hoje  = _vendas_cartao_hoje()
    recebe_em    = _data_recebimento_cartao(hoje)
    liquido_cart = cartao_hoje * (1 - CARTAO_DESC / 100)
    recebe_str   = recebe_em.strftime("%d/%m")
    hoje_str     = hoje.strftime("%d/%m/%Y")

    def linhas_boletos(rows, campo_valor="valor_nominal", campo_extra=None):
        if not rows:
            return "<tr><td colspan='3' style='color:#94a3b8;font-style:italic'>Nenhum</td></tr>"
        html = ""
        for r in rows[:10]:
            nome = (r.get("cliente_nome") or "—")[:35]
            val  = r.get(campo_valor) or 0
            extra = r.get(campo_extra, "") if campo_extra else ""
            extra_html = f"<td style='color:#94a3b8'>{extra}</td>" if campo_extra else ""
            html += f"<tr><td>{nome}</td><td style='text-align:right'>{_brl(val)}</td>{extra_html}</tr>"
        if len(rows) > 10:
            html += f"<tr><td colspan='3' style='color:#94a3b8'>… e mais {len(rows)-10}</td></tr>"
        return html

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head><meta charset="UTF-8">
<style>
  body{{font-family:sans-serif;background:#f8fafc;color:#1e293b;margin:0;padding:20px}}
  .wrap{{max-width:620px;margin:0 auto;background:#fff;border-radius:12px;
         border:1px solid #e2e8f0;overflow:hidden}}
  .hdr{{background:#0f1117;color:#fff;padding:20px 24px}}
  .hdr h1{{margin:0;font-size:1.2rem}}
  .hdr small{{color:#94a3b8;font-size:.8rem}}
  .body{{padding:24px}}
  h2{{font-size:.9rem;text-transform:uppercase;letter-spacing:.05em;color:#64748b;margin:20px 0 8px}}
  table{{width:100%;border-collapse:collapse;font-size:.9rem}}
  th{{background:#f1f5f9;padding:6px 10px;text-align:left;font-size:.75rem;color:#64748b}}
  td{{padding:6px 10px;border-bottom:1px solid #f1f5f9}}
  .kpi{{display:flex;gap:12px;margin:16px 0}}
  .kpi-box{{flex:1;background:#f8fafc;border-radius:8px;padding:12px;border:1px solid #e2e8f0}}
  .kpi-val{{font-size:1.2rem;font-weight:700;color:#1e293b}}
  .kpi-lbl{{font-size:.75rem;color:#64748b;margin-top:2px}}
  .green{{color:#10b981}} .red{{color:#ef4444}} .yellow{{color:#f59e0b}}
  .footer{{padding:16px 24px;background:#f8fafc;font-size:.75rem;color:#94a3b8;border-top:1px solid #e2e8f0}}
</style></head>
<body>
<div class="wrap">
  <div class="hdr">
    <h1>Relatório Financeiro — {hoje_str}</h1>
    <small>Pablo Agro · Gerado às {datetime.now().strftime('%H:%M')}</small>
  </div>
  <div class="body">

    <div class="kpi">
      <div class="kpi-box">
        <div class="kpi-val green">{_brl(total_pago)}</div>
        <div class="kpi-lbl">💰 Recebido (noite/manhã)</div>
      </div>
      <div class="kpi-box">
        <div class="kpi-val">{_brl(total_aberto)}</div>
        <div class="kpi-lbl">📋 Em Aberto ({len(abertos)} boletos)</div>
      </div>
      <div class="kpi-box">
        <div class="kpi-val {'red' if vencidos else 'green'}">{_brl(total_vencido)}</div>
        <div class="kpi-lbl">⚠️ Vencidos ({len(vencidos)})</div>
      </div>
    </div>

    <h2>Pagos desde ontem 18h</h2>
    <table>
      <thead><tr><th>Cliente</th><th style="text-align:right">Valor Pago</th></tr></thead>
      <tbody>{linhas_boletos(pagos, campo_valor='valor_pago')}</tbody>
    </table>

    <h2>Vence Hoje — Em Aberto</h2>
    <table>
      <thead><tr><th>Cliente</th><th style="text-align:right">Valor</th></tr></thead>
      <tbody>{linhas_boletos([b for b in abertos if b.get('data_vencimento') == hoje.isoformat()])}</tbody>
    </table>

    {f'''<h2>⚠️ Boletos Vencidos</h2>
    <table>
      <thead><tr><th>Cliente</th><th style="text-align:right">Valor</th><th>Vencimento</th></tr></thead>
      <tbody>{linhas_boletos(vencidos, campo_extra='data_vencimento')}</tbody>
    </table>''' if vencidos else ''}

    <h2>Cartão — Previsão de Liquidação</h2>
    <table>
      <thead><tr><th>Vendas hoje (cartão)</th><th style="text-align:right">Previsão líquida</th><th>Recebimento</th></tr></thead>
      <tbody><tr>
        <td>{_brl(cartao_hoje)}</td>
        <td style="text-align:right">{_brl(liquido_cart)} <small style="color:#94a3b8">(-{CARTAO_DESC}%)</small></td>
        <td><strong>{recebe_str}</strong></td>
      </tr></tbody>
    </table>

  </div>
  <div class="footer">
    Pablo ERP · <a href="http://localhost:2525/financeiro-receber.html">Ver detalhes</a>
  </div>
</div>
</body></html>"""


def relatorio_email():
    """Envia o relatório matinal por e-mail. Retorna True se enviou com sucesso."""
    if not SMTP_USER or not SMTP_PASS:
        logger.info("[EmailReport] SMTP não configurado (SMTP_USER/SMTP_PASS ausentes) — pulando.")
        return False

    try:
        stats = db.stats_relatorio()
    except Exception as e:
        logger.error("[EmailReport] Erro ao buscar stats: %s", e)
        return False

    hoje = date.today()
    html = _html_report(stats, hoje)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Relatório Financeiro Pablo Agro — {hoje.strftime('%d/%m/%Y')}"
    msg["From"]    = SMTP_USER
    msg["To"]      = EMAIL_DEST
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as s:
            s.ehlo()
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, [EMAIL_DEST], msg.as_bytes())
        logger.info("[EmailReport] Relatório enviado para %s", EMAIL_DEST)
        return True
    except Exception as e:
        logger.error("[EmailReport] Falha ao enviar e-mail: %s", e)
        return False
