#!/bin/bash
# Mostra a URL atual do ngrok (para configurar no painel Mercos).
# Copia automaticamente para o clipboard.

URL=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null | \
    python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for t in data.get('tunnels', []):
        if t.get('proto') == 'https':
            print(t['public_url'] + '/webhook/mercos')
            break
except Exception:
    pass
" 2>/dev/null)

if [ -z "$URL" ]; then
    MSG="ngrok não está rodando ou ainda está iniciando.\n\nVerifique:\n  systemctl status mercos-ngrok\n\nSe recém iniciou, aguarde ~10 segundos e tente novamente."
    if command -v zenity &>/dev/null; then
        zenity --error --title="ngrok indisponível" --text="$MSG" --width=400
    else
        echo -e "$MSG"
    fi
    exit 1
fi

# Copia para clipboard (silencioso se xclip não estiver disponível)
echo "$URL" | xclip -selection clipboard 2>/dev/null || true

echo "URL do webhook: $URL"

if command -v zenity &>/dev/null; then
    zenity --info \
        --title="URL do Webhook — Mercos" \
        --width=520 \
        --text="URL copiada para o clipboard:\n\n<b>${URL}</b>\n\nCole no painel Mercos:\nConfigurações → Webhooks → URL do Webhook"
fi
