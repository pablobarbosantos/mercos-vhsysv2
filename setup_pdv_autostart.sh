#!/bin/bash
# =============================================================================
# Configura o PDV para abrir automaticamente ao ligar o servidor
# Execute uma vez no servidor:  bash setup_pdv_autostart.sh
# =============================================================================

set -e

VERDE='\033[0;32m'
AMARELO='\033[1;33m'
VERMELHO='\033[0;31m'
NC='\033[0m'
ok()  { echo -e "${VERDE}✔ $1${NC}"; }
msg() { echo -e "${AMARELO}→ $1${NC}"; }
err() { echo -e "${VERMELHO}✘ $1${NC}"; }

INSTALL_DIR="/root/mercos_vhsys"
USUARIO=$(logname 2>/dev/null || echo "${SUDO_USER:-pablo}")
HOME_USUARIO=$(eval echo "~$USUARIO")

msg "Usuário detectado: $USUARIO (home: $HOME_USUARIO)"

# =============================================================================
# 1. Verificar que o diretório de instalação existe
# =============================================================================
if [ ! -d "$INSTALL_DIR" ]; then
    err "Diretório $INSTALL_DIR não encontrado. Execute setup_ubuntu.sh primeiro."
    exit 1
fi
ok "Instalação encontrada em $INSTALL_DIR"

# =============================================================================
# 2. Garantir que pablo tem acesso ao diretório de instalação (em /root)
# =============================================================================
if [ "$INSTALL_DIR" = "/root/mercos_vhsys" ]; then
    chmod o+rx /root
    ok "Permissão de leitura em /root concedida para outros usuários"
fi

# =============================================================================
# 3. Configurar auto-login no GDM3
# =============================================================================
msg "Configurando auto-login para $USUARIO no GDM3..."

GDM_CONF="/etc/gdm3/custom.conf"

if [ ! -f "$GDM_CONF" ]; then
    err "Arquivo $GDM_CONF não encontrado. Verifique se o GDM3 está instalado."
    exit 1
fi

# Verifica se auto-login já está configurado
if grep -q "AutomaticLoginEnable=true" "$GDM_CONF"; then
    ok "Auto-login já configurado em $GDM_CONF"
else
    # Adiciona dentro do bloco [daemon]
    sudo sed -i '/^\[daemon\]/a AutomaticLoginEnable=true\nAutomaticLogin='"$USUARIO" "$GDM_CONF"
    ok "Auto-login configurado para $USUARIO"
fi

# =============================================================================
# 4. Criar arquivo de autostart do GNOME
# =============================================================================
msg "Criando autostart do PDV para $USUARIO..."

AUTOSTART_DIR="$HOME_USUARIO/.config/autostart"
mkdir -p "$AUTOSTART_DIR"

cat > "$AUTOSTART_DIR/PDV.desktop" << EOF
[Desktop Entry]
Type=Application
Name=PDV — Pablo Agro
Comment=Ponto de Venda — abre automaticamente ao iniciar a sessão
Exec=bash -c "sleep 8 && cd ${INSTALL_DIR} && source venv/bin/activate && python pdv/main.py"
Terminal=false
X-GNOME-Autostart-enabled=true
EOF

chown "$USUARIO:$USUARIO" "$AUTOSTART_DIR/PDV.desktop"
ok "Arquivo de autostart criado: $AUTOSTART_DIR/PDV.desktop"

# =============================================================================
# 5. Resumo
# =============================================================================
echo ""
echo "=========================================="
echo "      PDV AUTOSTART CONFIGURADO!          "
echo "=========================================="
echo ""
ok "Auto-login: $USUARIO entra automaticamente no boot"
ok "PDV: abre 8s após o login (aguarda mercos-main subir)"
echo ""
echo -e "${AMARELO}Próximos passos:${NC}"
echo "  sudo reboot"
echo "  → PDV deve abrir automaticamente na tela"
echo ""
echo "  Se não abrir, verifique:"
echo "    journalctl -u mercos-main --since '5 min ago'"
echo "    tail -30 ${INSTALL_DIR}/logs/python.log"
echo ""
