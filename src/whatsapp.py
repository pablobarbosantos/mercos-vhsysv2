"""
WhatsApp — Evolution API client
================================
Envia notificações via Evolution API (self-hosted).

Variáveis de ambiente necessárias:
  EVOLUTION_API_URL      ex: http://147.15.95.71:8080
  EVOLUTION_API_KEY      chave de autenticação
  EVOLUTION_INSTANCE     nome da instância (ex: pablo-agro)
  WHATSAPP_NOTIFY_NUMBER número que RECEBE alertas internos (ex: 5534999999999)

Opcional:
  WHATSAPP_ENABLED       true/false (default: true)
"""

import logging
import os
import requests
from datetime import datetime

logger = logging.getLogger(__name__)


class WhatsAppClient:

    def __init__(self):
        self.base_url     = os.getenv("EVOLUTION_API_URL", "").rstrip("/")
        self.api_key      = os.getenv("EVOLUTION_API_KEY", "")
        self.instance     = os.getenv("EVOLUTION_INSTANCE", "pablo-agro")
        self.notify_to    = os.getenv("WHATSAPP_NOTIFY_NUMBER", "")
        self.enabled      = os.getenv("WHATSAPP_ENABLED", "true").lower() == "true"

        if self.enabled and not all([self.base_url, self.api_key, self.notify_to]):
            logger.warning(
                "[WhatsApp] Variáveis incompletas — notificações DESATIVADAS. "
                "Configure EVOLUTION_API_URL, EVOLUTION_API_KEY e WHATSAPP_NOTIFY_NUMBER no .env"
            )
            self.enabled = False

    # ──────────────────────────────────────────────────────────────────────────
    # Envio base
    # ──────────────────────────────────────────────────────────────────────────

    def _enviar(self, numero: str, mensagem: str) -> bool:
        if not self.enabled:
            logger.debug(f"[WhatsApp] (desativado) Mensagem para {numero}: {mensagem[:60]}...")
            return False

        url = f"{self.base_url}/message/sendText/{self.instance}"
        headers = {
            "apikey": self.api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "number": numero,
            "text": mensagem,
        }

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=10)
            if resp.status_code in (200, 201):
                logger.info(f"[WhatsApp] ✅ Mensagem enviada para {numero}")
                return True
            else:
                logger.error(f"[WhatsApp] ❌ HTTP {resp.status_code}: {resp.text[:200]}")
                return False
        except Exception as e:
            logger.error(f"[WhatsApp] Erro ao enviar: {e}")
            return False

    # ──────────────────────────────────────────────────────────────────────────
    # Notificações internas (para você, Pablo)
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
            f"👉 Acesse o painel: http://localhost:8000/admin"
        )
        return self._enviar(self.notify_to, msg)

    # ──────────────────────────────────────────────────────────────────────────
    # Confirmação pro cliente
    # ──────────────────────────────────────────────────────────────────────────

    def confirmar_pedido_cliente(self, telefone: str, nome_cliente: str,
                                  numero_pedido, valor: float, condicao: str):
        if not telefone:
            logger.debug(f"[WhatsApp] Cliente {nome_cliente} sem telefone — confirmação não enviada.")
            return False

        # Normalizar telefone — remover caracteres especiais
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
    # Resumo diário (chamado pelo script scripts/resumo_diario.py)
    # ──────────────────────────────────────────────────────────────────────────

    def enviar_resumo_diario(self, stats: dict, pedidos: list):
        hoje = datetime.now().strftime("%d/%m/%Y")

        linhas_pedidos = ""
        for p in pedidos[:10]:  # máx 10 linhas
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


# Instância global (singleton)
_client: WhatsAppClient | None = None

def get_whatsapp() -> WhatsAppClient:
    global _client
    if _client is None:
        _client = WhatsAppClient()
    return _client
