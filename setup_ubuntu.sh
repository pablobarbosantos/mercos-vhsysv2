#!/bin/bash
# =============================================================================
# Pablo Agro — Instalação no Servidor Ubuntu
# =============================================================================
# Execute UMA VEZ após copiar o projeto do pen drive:
#
#   cd /caminho/do/pendrive/mercos_vhsys_git
#   bash setup_ubuntu.sh
#
# O script instala tudo, copia o projeto para ~/mercos_vhsys e cria
# 3 atalhos na área de trabalho: Admin, PDV e Consulta.
# =============================================================================

set -e

# --- Cores para output ---
VERDE='\033[0;32m'
AMARELO='\033[1;33m'
VERMELHO='\033[0;31m'
NC='\033[0m'

ok()  { echo -e "${VERDE}✔ $1${NC}"; }
msg() { echo -e "${AMARELO}→ $1${NC}"; }
err() { echo -e "${VERMELHO}✘ $1${NC}"; }

INSTALL_DIR="$HOME/mercos_vhsys"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USUARIO="$USER"

echo ""
echo "=========================================="
echo "  Pablo Agro — Instalação Ubuntu Server   "
echo "=========================================="
echo ""
msg "Diretório de instalação: $INSTALL_DIR"
msg "Usuário: $USUARIO"
echo ""

# =============================================================================
# 1. Detectar versão Ubuntu
# =============================================================================
UBUNTU_VER=$(lsb_release -rs 2>/dev/null || echo "22.04")
msg "Ubuntu detectado: $UBUNTU_VER"

# =============================================================================
# 2. Atualizar repositórios e instalar dependências de sistema
# =============================================================================
msg "Atualizando repositórios..."
sudo apt-get update -q

msg "Instalando Python3 e ferramentas de build..."
sudo apt-get install -y \
    python3 python3-pip python3-venv python3-dev \
    build-essential libffi-dev libssl-dev

msg "Instalando dependências GTK/WebKit para pywebview (janelas desktop)..."
if [[ "$UBUNTU_VER" == "24."* ]]; then
    sudo apt-get install -y \
        python3-gi python3-gi-cairo gir1.2-gtk-3.0 \
        gir1.2-webkit2-4.1 libwebkit2gtk-4.1-dev
else
    # Ubuntu 22.04 e anteriores
    sudo apt-get install -y \
        python3-gi python3-gi-cairo gir1.2-gtk-3.0 \
        gir1.2-webkit2-4.0 libwebkit2gtk-4.0-dev
fi

msg "Instalando dependências do Chromium/Puppeteer (para WhatsApp Web)..."
sudo apt-get install -y \
    libasound2t64 libatk-bridge2.0-0 libatk1.0-0 libcairo2 libcups2 \
    libdbus-1-3 libdrm2 libexpat1 libgbm1 libglib2.0-0 libnspr4 \
    libnss3 libpango-1.0-0 libpangocairo-1.0-0 libx11-6 libxcb1 \
    libxcomposite1 libxdamage1 libxext6 libxfixes3 libxrandr2 \
    libxrender1 libxss1 libxtst6 libgtk-3-0 fonts-liberation \
    ca-certificates wget xdg-utils

msg "Instalando utilitários (xclip, zenity, rsync, curl, unzip)..."
sudo apt-get install -y xclip zenity rsync curl wget unzip

ok "Dependências de sistema instaladas."

# =============================================================================
# 3. Instalar Node.js 20.x
# =============================================================================
msg "Instalando Node.js 20.x..."
if ! command -v node &>/dev/null || [[ "$(node --version | cut -d'.' -f1 | tr -d 'v')" -lt 18 ]]; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y nodejs
fi
ok "Node.js $(node --version) instalado."

# =============================================================================
# 4. Copiar projeto para local fixo
# =============================================================================
msg "Copiando projeto para $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR/logs"
rsync -a \
    --exclude='venv/' \
    --exclude='node_modules/' \
    --exclude='*.pyc' \
    --exclude='__pycache__/' \
    --exclude='pdv/PDV_PabloAgro/' \
    --exclude='pdv/build/' \
    --exclude='ngrok.exe' \
    "$SCRIPT_DIR/" "$INSTALL_DIR/"
ok "Projeto copiado."

# =============================================================================
# 5. Python venv + pip install
# =============================================================================
msg "Criando ambiente virtual Python..."
cd "$INSTALL_DIR"
python3 -m venv venv
source venv/bin/activate

msg "Instalando dependências Python (requirements.txt)..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

msg "Instalando pywebview (janelas desktop para Admin, PDV e Consulta)..."
pip install pywebview -q

if [ -f "pdv/requirements.txt" ]; then
    msg "Instalando dependências do PDV..."
    pip install -r pdv/requirements.txt -q
fi

ok "Dependências Python instaladas."

# =============================================================================
# 6. Node.js — WhatsApp server
# =============================================================================
msg "Instalando dependências Node.js (WhatsApp server)..."
cd "$INSTALL_DIR/whatsapp_server"
npm install --silent
ok "Node.js dependencies instaladas."

# =============================================================================
# 7. Instalar Tailscale
# =============================================================================
msg "Instalando Tailscale..."
if command -v tailscale &>/dev/null; then
    ok "Tailscale já instalado, pulando."
else
    curl -fsSL https://tailscale.com/install.sh | sh
    ok "Tailscale instalado."
fi

# =============================================================================
# 8. Autenticar Tailscale e ativar Funnel
# =============================================================================
echo ""
echo "=========================================="
echo "     CONFIGURAÇÃO DO TAILSCALE FUNNEL     "
echo "=========================================="
echo ""
echo "O Tailscale Funnel cria um endereço público ESTÁVEL para o webhook."
echo "A URL nunca muda entre reinicializações."
echo ""
echo "Passos:"
echo "  1. Execute:  sudo tailscale up"
echo "     → Abra o link mostrado no browser e faça login"
echo "  2. No painel admin.tailscale.com:"
echo "     → Ative 'HTTPS Certificates' (DNS → Enable HTTPS)"
echo "     → Ative 'Funnel' para esta máquina"
echo "  3. Execute:  tailscale funnel 8000"
echo "     → Anote a URL exibida (ex: https://meupc.meuperfil.ts.net)"
echo "  4. Configure essa URL no painel Mercos (Configurações → Webhooks)"
echo "     → Adicione /webhook/mercos ao final da URL"
echo ""
read -rp "Pressione Enter quando o Funnel estiver ativo e a URL configurada no Mercos..."
ok "Tailscale Funnel configurado."

# =============================================================================
# 9. Criar systemd services
# =============================================================================
msg "Criando serviços systemd (auto-start no boot)..."

# --- mercos-whatsapp.service ---
sudo tee /etc/systemd/system/mercos-whatsapp.service > /dev/null <<EOF
[Unit]
Description=Mercos WhatsApp Server
After=network.target

[Service]
Type=simple
User=${USUARIO}
WorkingDirectory=${INSTALL_DIR}/whatsapp_server
ExecStart=/usr/bin/node server.js
Restart=on-failure
RestartSec=5
StandardOutput=append:${INSTALL_DIR}/logs/node.log
StandardError=append:${INSTALL_DIR}/logs/node.log

[Install]
WantedBy=multi-user.target
EOF

# --- mercos-main.service ---
sudo tee /etc/systemd/system/mercos-main.service > /dev/null <<EOF
[Unit]
Description=Mercos VHSys Main Server
After=network.target tailscaled.service
Wants=tailscaled.service

[Service]
Type=simple
User=${USUARIO}
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/venv/bin/python main.py
Restart=on-failure
RestartSec=10
StandardOutput=append:${INSTALL_DIR}/logs/python.log
StandardError=append:${INSTALL_DIR}/logs/python.log

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable mercos-whatsapp mercos-main
ok "Serviços systemd criados e habilitados."

# =============================================================================
# 10. Criar atalhos de desktop
# =============================================================================
msg "Criando atalhos na área de trabalho..."

DESKTOP_DIR="$HOME/Desktop"
mkdir -p "$DESKTOP_DIR"

ICON="$INSTALL_DIR/pdv/assets/logo.png"
# Fallback caso logo não exista
[ ! -f "$ICON" ] && ICON="utilities-terminal"

# Admin
cat > "$DESKTOP_DIR/Admin.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Admin — Pablo Agro
Comment=Painel de administração e monitoramento de pedidos
Exec=bash -c "cd ${INSTALL_DIR} && source venv/bin/activate && python admin_launcher.py"
Icon=${ICON}
Terminal=false
Categories=Application;
EOF

# PDV
cat > "$DESKTOP_DIR/PDV.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=PDV — Pablo Agro
Comment=Ponto de Venda
Exec=bash -c "cd ${INSTALL_DIR} && source venv/bin/activate && python pdv/main.py"
Icon=${ICON}
Terminal=false
Categories=Application;
EOF

# Consulta VHSys
cat > "$DESKTOP_DIR/Consulta.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Consulta VHSys
Comment=Consulta e gestão de produtos VHSys
Exec=bash -c "cd ${INSTALL_DIR} && source venv/bin/activate && python consulta_vhsys/main.py"
Icon=${ICON}
Terminal=false
Categories=Application;
EOF

# Webhook (script utilitário — opcional como atalho)
cat > "$DESKTOP_DIR/Webhook.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=URL do Webhook
Comment=Mostra a URL estável do Tailscale Funnel para configurar no Mercos
Exec=bash -c "cd ${INSTALL_DIR} && bash mostrar_webhook.sh"
Icon=network-transmit-receive
Terminal=false
Categories=Application;
EOF

chmod +x "$DESKTOP_DIR/Admin.desktop" "$DESKTOP_DIR/PDV.desktop" \
         "$DESKTOP_DIR/Consulta.desktop" "$DESKTOP_DIR/Webhook.desktop"

# Tornar confiáveis no GNOME (evita o aviso "arquivo executável não confiável")
if command -v gio &>/dev/null; then
    gio set "$DESKTOP_DIR/Admin.desktop"    metadata::trusted true 2>/dev/null || true
    gio set "$DESKTOP_DIR/PDV.desktop"      metadata::trusted true 2>/dev/null || true
    gio set "$DESKTOP_DIR/Consulta.desktop" metadata::trusted true 2>/dev/null || true
    gio set "$DESKTOP_DIR/Webhook.desktop"  metadata::trusted true 2>/dev/null || true
fi

ok "4 atalhos criados na área de trabalho (Admin, PDV, Consulta, Webhook)."

# =============================================================================
# 11. Iniciar serviços agora
# =============================================================================
msg "Iniciando serviços..."
sudo systemctl start mercos-whatsapp || true
sleep 3
sudo systemctl start mercos-main || true
ok "Serviços iniciados."

# =============================================================================
# 12. Resumo final
# =============================================================================
echo ""
echo "=========================================="
echo "        INSTALAÇÃO CONCLUÍDA!             "
echo "=========================================="
echo ""
ok "Python venv criado com todas as dependências"
ok "Node.js e WhatsApp server instalados"
ok "Tailscale Funnel configurado (URL estável)"
ok "Serviços de boot habilitados (auto-start)"
ok "4 atalhos criados na área de trabalho"
echo ""
echo -e "${AMARELO}PRÓXIMOS PASSOS:${NC}"
echo ""
echo "  1. WhatsApp — Escaneie o QR Code:"
echo "     Abra o Firefox → http://localhost:3000/qr"
echo "     Escaneie com o celular para conectar"
echo ""
echo "  2. Webhook — Atualize no painel Mercos:"
echo "     Clique no atalho 'URL do Webhook' na área de trabalho"
echo "     Ou execute: bash $INSTALL_DIR/mostrar_webhook.sh"
echo ""
echo "  3. Teste os atalhos na área de trabalho:"
echo "     Admin → janela própria de gerenciamento"
echo "     PDV   → janela fullscreen do caixa"
echo "     Consulta → janela de consulta de produtos"
echo ""
echo "  Se algo não funcionar, verifique os logs:"
echo "     tail -f $INSTALL_DIR/logs/python.log"
echo "     tail -f $INSTALL_DIR/logs/node.log"
echo ""
