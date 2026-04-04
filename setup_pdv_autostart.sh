#!/bin/bash
# =============================================================================
# Configura o PDV para abrir automaticamente ao ligar o servidor
# Compatível com Xubuntu (LightDM + XFCE)
# Execute uma vez no servidor:  sudo bash setup_pdv_autostart.sh
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
# 2. Garantir acesso ao diretório de instalação (em /root)
# =============================================================================
if [[ "$INSTALL_DIR" == /root/* ]]; then
    chmod o+rx /root
    ok "Permissão de leitura em /root concedida"
fi

# =============================================================================
# 3. Configurar auto-login no LightDM (Xubuntu)
# =============================================================================
msg "Configurando auto-login para $USUARIO no LightDM..."

LIGHTDM_CONF="/etc/lightdm/lightdm.conf"

# Cria o arquivo se não existir
if [ ! -f "$LIGHTDM_CONF" ]; then
    echo "[Seat:*]" | sudo tee "$LIGHTDM_CONF" > /dev/null
fi

if grep -q "^autologin-user=" "$LIGHTDM_CONF"; then
    sudo sed -i "s/^autologin-user=.*/autologin-user=${USUARIO}/" "$LIGHTDM_CONF"
    ok "Auto-login atualizado para $USUARIO"
else
    # Garante que a seção [Seat:*] existe e adiciona as linhas
    if grep -q "^\[Seat:\*\]" "$LIGHTDM_CONF"; then
        sudo sed -i "/^\[Seat:\*\]/a autologin-user=${USUARIO}\nautologin-user-timeout=0" "$LIGHTDM_CONF"
    else
        printf "\n[Seat:*]\nautologin-user=%s\nautologin-user-timeout=0\n" "$USUARIO" | sudo tee -a "$LIGHTDM_CONF" > /dev/null
    fi
    ok "Auto-login configurado para $USUARIO"
fi

# =============================================================================
# 4. Criar arquivo de autostart XDG (funciona no XFCE)
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
EOF

chown -R "$USUARIO:$USUARIO" "$AUTOSTART_DIR"
ok "Arquivo de autostart criado: $AUTOSTART_DIR/PDV.desktop"

# =============================================================================
# 5. Resumo
# =============================================================================
echo ""
echo "=========================================="
echo "      PDV AUTOSTART CONFIGURADO!          "
echo "=========================================="
echo ""
ok "Auto-login: $USUARIO entra automaticamente no boot (LightDM)"
ok "PDV: abre 8s após o login (aguarda mercos-main subir)"
echo ""
echo -e "${AMARELO}Próximos passos:${NC}"
echo "  sudo reboot"
echo "  → PDV deve abrir automaticamente na tela HDMI"
echo ""
echo "  Se não abrir, verifique:"
echo "    journalctl -u mercos-main --since '5 min ago'"
echo "    tail -30 ${INSTALL_DIR}/logs/python.log"
echo ""
