"""
Relatório matinal de boletos via WhatsApp.
Enviado de segunda a sexta às 7:30 pelo APScheduler em main.py.
"""
import logging
from datetime import datetime
from boletos import database as db

logger = logging.getLogger(__name__)


def relatorio_boletos():
    """Compõe e envia o relatório de boletos pelo WhatsApp."""
    try:
        from src.whatsapp import get_whatsapp
        wa = get_whatsapp()
    except Exception as e:
        logger.error("[BoletoReport] Não foi possível carregar WhatsApp: %s", e)
        return False

    try:
        stats = db.stats_relatorio()
    except Exception as e:
        logger.error("[BoletoReport] Erro ao buscar stats: %s", e)
        return False

    pagos    = stats["pagos"]
    abertos  = stats["abertos"]
    vencidos = stats["vencidos"]
    total_pago   = stats["total_pago"]
    total_aberto = stats["total_aberto"]
    total_vencido = stats["total_vencido"]

    hoje = datetime.now().strftime("%d/%m/%Y")

    # Linhas de pagos (até 5)
    linhas_pagos = ""
    for b in pagos[:5]:
        linhas_pagos += f"  • {b['cliente_nome'][:25]} — R$ {b['valor_pago'] or b['valor_nominal']:,.2f}\n"
    if len(pagos) > 5:
        linhas_pagos += f"  ... e mais {len(pagos) - 5}\n"

    # Linhas de vencidos (até 5)
    linhas_vencidos = ""
    for b in vencidos[:5]:
        linhas_vencidos += f"  • {b['cliente_nome'][:25]} — R$ {b['valor_nominal']:,.2f} (venc. {b['data_vencimento']})\n"
    if len(vencidos) > 5:
        linhas_vencidos += f"  ... e mais {len(vencidos) - 5}\n"

    msg = (
        f"🏦 *Boletos — {hoje}*\n"
        f"━━━━━━━━━━━━━━━━\n"
    )

    if pagos:
        msg += (
            f"💰 *Pagos (noite/manhã): {len(pagos)}*\n"
            f"{linhas_pagos}"
            f"   Total: *R$ {total_pago:,.2f}*\n"
            f"━━━━━━━━━━━━━━━━\n"
        )
    else:
        msg += "💰 Nenhum pagamento registrado desde ontem 18h\n━━━━━━━━━━━━━━━━\n"

    msg += f"📋 Em aberto: *{len(abertos)}* → R$ {total_aberto:,.2f}\n"

    if vencidos:
        msg += (
            f"⚠️ *Vencidos: {len(vencidos)}* → R$ {total_vencido:,.2f}\n"
            f"{linhas_vencidos}"
        )
    else:
        msg += "✅ Nenhum boleto vencido\n"

    msg += (
        f"━━━━━━━━━━━━━━━━\n"
        f"🕐 {datetime.now().strftime('%d/%m %H:%M')}\n"
        f"👉 http://localhost:8000/boletos"
    )

    ok = wa._enviar(wa.notify_to, msg)
    if ok:
        logger.info("[BoletoReport] Relatório matinal enviado com sucesso")
    else:
        logger.warning("[BoletoReport] Falha ao enviar relatório")
    return ok
