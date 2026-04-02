#!/bin/bash
# Para todos os serviços manualmente.

pkill -f "node whatsapp_server/server.js" 2>/dev/null || true
pkill -f "ngrok http 8000"               2>/dev/null || true
pkill -f "python main.py"                2>/dev/null || true

echo "Serviços parados."
