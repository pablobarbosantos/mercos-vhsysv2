"""
WhatsApp — cliente local via whatsapp-web.js
=============================================
Envia notificações via servidor Node.js local (porta 3000).

Variáveis de ambiente necessárias:
  WHATSAPP_API_URL       ex: http://localhost:3000
  WHATSAPP_NOTIFY_NUMBER número que RECEBE alertas internos (ex: 5534991027738)

Opcional:
  WHATSAPP_ENABLED       true/false (default: true)
"""

import logging
import os
import time
import requests
from datetime import datetime

logger = logging.getLogger(__name__)


class WhatsAppClient:

    def __init__(self):
        self.base_url  = os.getenv("WHATSAPP_API_URL", "http://localhost:3000").rstrip("/")
        self.notify_to = os.getenv("WHATSAPP_NOTIFY_NUMBER", "")
        self.enabled   = os.getenv("WHATSAPP_ENABLED", "true").lower() == "true"

        if self.enabled and not self.notify_to:
            logger.warning(
                "[WhatsApp] WHATSAPP_NOTIFY_NUMBER não configurado — notificações DESATIVADAS."
            )
            self.enabled = False

    # ──────────────────────────────────────────────────────────────────────────
    # Envio base
    # ──────────────────────────────────────────────────────────────────────────

    def _enviar(self, numero: str, mensagem: str, max_tentativas: int = 3) -> bool:
        if not self.enabled:
            logger.debug(f"[WhatsApp] (desativado) Para {numero}: {mensagem[:60]}...")
            return False

        for tentativa in range(1, max_tentativas + 1):
            try:
                resp = requests.post(
                    f"{self.base_url}/send",
                    json={"numero": numero, "mensagem": mensagem},
                    timeout=10,
                )
                if resp.status_code == 200:
                    logger.info(f"[WhatsApp] ✅ Enviado para {numero} (tentativa {tentativa})")
                    return True
                else:
                    logger.warning(
                        f"[WhatsApp] HTTP {resp.status_code} "
                        f"(tentativa {tentativa}/{max_tentativas}): {resp.text[:200]}"
                    )
            except Exception as e:
                logger.warning(f"[WhatsApp] Erro (tentativa {tentativa}/{max_tentativas}): {e}")

            if tentativa < max_tentativas:
                time.sleep(2 * tentativa)  # 2s, 4s

        logger.error(f"[WhatsApp] Falha definitiva após {max_tentativas} tentativas para {numero}")
        return False

    # ──────────────────────────────────────────────────────────────────────────
    # Pedido OK / Erro
    # ──────────────────────────────────────────────────────────────────────────

    def notificar_pedido_ok(self, numero_pedido, mercos_id: int, vhsys_id: str,
                             cliente: str, valor: float, condicao: str):
        msg = (
            f"✅ *Pedido processado!*\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📋 Mercos: *#{numero_pedido}*\n"
            f"🔗 VHSys: *{vhsys_id}*\n"
            f"👤 Cliente: {cliente}\n"
            f"💰 Valor: *R$ {valor:,.2f}*\n"
            f"📅 Cond.: {condicao}\n"
            f"🕐 {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        )
        return self._enviar(self.notify_to, msg)

    def notificar_pedido_erro(self, numero_pedido, mercos_id: int,
                               cliente: str, motivo: str):
        msg = (
            f"❌ *ERRO no pedido!*\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📋 Mercos: *#{numero_pedido}*\n"
            f"👤 Cliente: {cliente}\n"
            f"⚠️ Motivo: {motivo}\n"
            f"🕐 {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
            f"👉 Painel: http://localhost:8000/admin"
        )
        return self._enviar(self.notify_to, msg)

    # ──────────────────────────────────────────────────────────────────────────
    # Confirmação pro cliente
    # ──────────────────────────────────────────────────────────────────────────

    def confirmar_pedido_cliente(self, telefone: str, nome_cliente: str,
                                  numero_pedido, valor: float, condicao: str):
        if not telefone:
            return False
        fone = "".join(c for c in str(telefone) if c.isdigit())
        if not fone.startswith("55"):
            fone = "55" + fone

        msg = (
            f"Olá, *{nome_cliente}*! 👋\n\n"
            f"Recebemos seu pedido *#{numero_pedido}* com sucesso!\n\n"
            f"💰 Valor total: *R$ {valor:,.2f}*\n"
            f"📅 Condição: {condicao}\n\n"
            f"Em breve nossa equipe entrará em contato.\n\n"
            f"*Pablo Agro* 🌱"
        )
        return self._enviar(fone, msg)

    # ──────────────────────────────────────────────────────────────────────────
    # Alertas de Auditoria de Sequência
    # ──────────────────────────────────────────────────────────────────────────

    def alertar_sequencia_quebrada(self, buracos: list[dict]):
        ids = [str(b["mercos_id"]) for b in buracos]
        titulo = f"⚠️ *{len(ids)} pedido(s) faltando na sequência!*"
        lista = "\n".join(f"  • Pedido #{i}" for i in ids[:10])
        if len(ids) > 10:
            lista += f"\n  ... e mais {len(ids) - 10}"
        msg = (
            f"{titulo}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"*Números não recebidos:*\n"
            f"{lista}\n\n"
            f"🔍 Pode ser: pedido cancelado antes de confirmar ou falha de rede.\n\n"
            f"👉 http://localhost:8000/admin\n"
            f"🕐 {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        )
        return self._enviar(self.notify_to, msg)

    # ──────────────────────────────────────────────────────────────────────────
    # Alertas de Auditoria de Fluxo
    # ──────────────────────────────────────────────────────────────────────────

    def alertar_fluxo_travado(self, alertas: list[dict]):
        por_tipo: dict[str, list] = {}
        for a in alertas:
            por_tipo.setdefault(a["tipo"], []).append(a)

        linhas = []
        if "nao_processado" in por_tipo:
            grupo = por_tipo["nao_processado"]
            linhas.append(f"🔴 *{len(grupo)} sem processar (>30min):*")
            for a in grupo[:5]:
                linhas.append(f"  • #{a['numero']} — {a['cliente']}")
            if len(grupo) > 5:
                linhas.append(f"  ... e mais {len(grupo) - 5}")
        if "parado_separacao" in por_tipo:
            grupo = por_tipo["parado_separacao"]
            linhas.append(f"\n🟡 *{len(grupo)} sem separação (>2h):*")
            for a in grupo[:5]:
                linhas.append(f"  • #{a['numero']} — {a['cliente']}")
            if len(grupo) > 5:
                linhas.append(f"  ... e mais {len(grupo) - 5}")
        if "parado_envio" in por_tipo:
            grupo = por_tipo["parado_envio"]
            linhas.append(f"\n🟠 *{len(grupo)} separados sem envio (>4h):*")
            for a in grupo[:5]:
                linhas.append(f"  • #{a['numero']} — {a['cliente']}")
            if len(grupo) > 5:
                linhas.append(f"  ... e mais {len(grupo) - 5}")

        msg = (
            f"📦 *Auditoria de Fluxo — {len(alertas)} alerta(s)*\n"
            f"━━━━━━━━━━━━━━━━\n"
            + "\n".join(linhas) +
            f"\n\n👉 http://localhost:8000/admin\n"
            f"🕐 {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        )
        return self._enviar(self.notify_to, msg)

    # ──────────────────────────────────────────────────────────────────────────
    # Fechamento do dia
    # ──────────────────────────────────────────────────────────────────────────

    def enviar_fechamento_dia(self, stats: dict):
        hoje = datetime.now().strftime("%d/%m/%Y")
        total = stats.get("total", 0)
        enviados = stats.get("enviados", 0)
        taxa = f"{(enviados/total*100):.0f}%" if total > 0 else "—"

        if stats.get("com_erro", 0) == 0 and stats.get("buracos", 0) == 0:
            saude = "🟢 Operação sem problemas"
        elif stats.get("com_erro", 0) > 0 or stats.get("buracos", 0) > 0:
            saude = "🟡 Atenção necessária"
        else:
            saude = "🔴 Problemas detectados"

        msg = (
            f"📊 *Fechamento do dia — {hoje}*\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📦 Pedidos recebidos: *{total}*\n"
            f"⚙️  Processados (VHSys): *{stats.get('processados', 0)}*\n"
            f"📋 Separados: *{stats.get('separados', 0)}*\n"
            f"🚚 Enviados: *{enviados}*\n"
            f"📈 Taxa de conclusão: *{taxa}*\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"❌ Com erro: *{stats.get('com_erro', 0)}*\n"
            f"🔍 Buracos de sequência: *{stats.get('buracos', 0)}*\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"{saude}\n"
            f"👉 http://localhost:8000/admin"
        )
        return self._enviar(self.notify_to, msg)

    # ──────────────────────────────────────────────────────────────────────────
    # Resumo diário
    # ──────────────────────────────────────────────────────────────────────────

    def enviar_resumo_diario(self, stats: dict, pedidos: list):
        hoje = datetime.now().strftime("%d/%m/%Y")
        linhas_pedidos = ""
        for p in pedidos[:10]:
            status_emoji = "✅" if p["status"] == "ok" else "❌"
            linhas_pedidos += f"  {status_emoji} #{p['mercos_id']} → VHSys {p['vhsys_id']}\n"
        if len(pedidos) > 10:
            linhas_pedidos += f"  ... e mais {len(pedidos) - 10} pedidos\n"
        msg = (
            f"📊 *Resumo do dia — {hoje}*\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📦 Pedidos hoje: *{stats.get('hoje', 0)}*\n"
            f"✅ Processados OK: *{stats.get('ok_hoje', 0)}*\n"
            f"❌ Com erro: *{stats.get('erro_hoje', 0)}*\n"
            f"📈 Total acumulado: {stats.get('total', 0)}\n"
        )
        if linhas_pedidos:
            msg += f"\n*Pedidos de hoje:*\n{linhas_pedidos}"
        if stats.get("erro_hoje", 0) > 0:
            msg += f"\n⚠️ Há pedidos com erro! Acesse o painel para reprocessar."
        return self._enviar(self.notify_to, msg)

    # ──────────────────────────────────────────────────────────────────────────
    # Lembrete de boleto
    # ──────────────────────────────────────────────────────────────────────────

    def lembrete_boleto(self, telefone: str, nome_cliente: str,
                         numero_pedido, valor: float, vencimento: str):
        if not telefone:
            return False
        fone = "".join(c for c in str(telefone) if c.isdigit())
        if not fone.startswith("55"):
            fone = "55" + fone
        msg = (
            f"Olá, *{nome_cliente}*! 👋\n\n"
            f"🔔 Lembrete: você tem um boleto vencendo em breve.\n\n"
            f"📋 Pedido: *#{numero_pedido}*\n"
            f"💰 Valor: *R$ {valor:,.2f}*\n"
            f"📅 Vencimento: *{vencimento}*\n\n"
            f"Em caso de dúvidas, entre em contato conosco.\n\n"
            f"*Pablo Agro* 🌱"
        )
        return self._enviar(fone, msg)

    # ──────────────────────────────────────────────────────────────────────────
    # Expedição automática
    # ──────────────────────────────────────────────────────────────────────────

    def notificar_separado_automatico(self, numero: str, mercos_id: int,
                                       cliente: str, vhsys_id: str):
        """Notifica que expedição foi criada no VHSys (pedido em separação)."""
        msg = (
            f"📦 *Pedido em separação!*\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📋 Mercos: *#{numero}*\n"
            f"🔗 VHSys: *{vhsys_id}*\n"
            f"👤 Cliente: {cliente}\n"
            f"⚙️  Expedição criada no VHSys\n"
            f"🕐 {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        )
        return self._enviar(self.notify_to, msg)

    def notificar_enviado_automatico(self, numero: str, mercos_id: int,
                                      cliente: str, vhsys_id: str):
        """Notifica que expedição foi concluída no VHSys (pedido enviado)."""
        msg = (
            f"🚚 *Pedido enviado!*\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📋 Mercos: *#{numero}*\n"
            f"🔗 VHSys: *{vhsys_id}*\n"
            f"👤 Cliente: {cliente}\n"
            f"✅ Expedição concluída no VHSys\n"
            f"🕐 {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        )
        return self._enviar(self.notify_to, msg)

    # ──────────────────────────────────────────────────────────────────────────
    # Reconciliação fim de dia
    # ──────────────────────────────────────────────────────────────────────────

    def notificar_reconciliacao(self, stats: dict) -> bool:
        reenf     = len(stats["reenfileirados"])
        andamento = len(stats["em_andamento"])
        incons    = len(stats["inconsistentes"])
        total     = stats["total"]

        if total == 0:
            msg = (
                f"✅ *Reconciliação fim de dia*\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"Todos os pedidos de hoje foram processados com sucesso.\n"
                f"🕐 {datetime.now().strftime('%d/%m/%Y %H:%M')}"
            )
        else:
            linhas = [f"⚠️ *Reconciliação fim de dia*\n━━━━━━━━━━━━━━━━"]
            if reenf:
                linhas.append(f"🔄 {reenf} pedido(s) reenfileirado(s):")
                for p in stats["reenfileirados"][:5]:
                    linhas.append(f"  • #{p['numero']} — {str(p.get('cliente',''))[:25]}")
            if andamento:
                linhas.append(f"⏳ {andamento} pedido(s) ainda em processamento")
            if incons:
                linhas.append(f"❗ {incons} pedido(s) com inconsistência — ver logs")
            linhas.append(f"🕐 {datetime.now().strftime('%d/%m/%Y %H:%M')}")
            linhas.append("http://localhost:8000/admin")
            msg = "\n".join(linhas)

        return self._enviar(self.notify_to, msg)


# Instância global (singleton)
_client: WhatsAppClient | None = None

def get_whatsapp() -> WhatsAppClient:
    global _client
    if _client is None:
        _client = WhatsAppClient()
    return _client
