#!/bin/bash
# Liga todos os serviços manualmente.
# Nota: após o setup, os serviços sobem automaticamente no boot via systemd.
# Use este script apenas se precisar reiniciar manualmente.

cd "$(dirname "$0")"
source venv/bin/activate
mkdir -p logs

# Para processos anteriores se existirem
pkill -f "node whatsapp_server/server.js" 2>/dev/null || true
pkill -f "ngrok http 8000"               2>/dev/null || true
pkill -f "python main.py"                2>/dev/null || true
sleep 1

echo "→ Iniciando WhatsApp server..."
node whatsapp_server/server.js >> logs/node.log 2>&1 &
sleep 3

echo "→ Iniciando ngrok..."
./ngrok http 8000 >> logs/ngrok.log 2>&1 &
sleep 5

echo "→ Iniciando servidor principal..."
python main.py >> logs/python.log 2>&1 &
sleep 3

echo ""
echo "Serviços iniciados!"
echo "  WhatsApp QR: http://localhost:3000/qr"
echo "  Admin:       http://localhost:8000/admin"
echo ""
bash mostrar_webhook.sh
