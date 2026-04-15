#!/bin/bash
# Mostra a URL estável do Tailscale Funnel para configurar no painel Mercos.
# A URL nunca muda entre reinicializações.

HOSTNAME=$(tailscale status --json 2>/dev/null | \
    python3 -c "import sys,json; d=json.load(sys.stdin); print(d['Self']['DNSName'].rstrip('.'))" 2>/dev/null)

if [ -z "$HOSTNAME" ]; then
    MSG="Tailscale não está rodando ou não está autenticado.\n\nVerifique:\n  tailscale status\n\nSe recém iniciou, aguarde alguns segundos e tente novamente."
    if command -v zenity &>/dev/null; then
        zenity --error --title="Tailscale indisponível" --text="$MSG" --width=400
    else
        echo -e "$MSG"
    fi
    exit 1
fi

URL="https://${HOSTNAME}/webhook/mercos"

# Copia para clipboard (silencioso se xclip não estiver disponível)
echo "$URL" | xclip -selection clipboard 2>/dev/null || true

echo "URL do webhook: $URL"

if command -v zenity &>/dev/null; then
    zenity --info \
        --title="URL do Webhook — Mercos" \
        --width=520 \
        --text="URL copiada para o clipboard:\n\n<b>${URL}</b>\n\nCole no painel Mercos:\nConfigurações → Webhooks → URL do Webhook\n\n⚠ Esta URL é estável — não muda entre reinicializações."
fi
