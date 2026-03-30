#!/bin/bash
# =============================================================================
# Stock Analyzer — Contabo Quick Update Script
# =============================================================================
# Pulls latest code from GitHub and restarts the app.
# Run as: root or stockapp
#
# Usage:
#   bash deploy/contabo/update.sh
# =============================================================================

set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $1"; }
success() { echo -e "${GREEN}[OK]${NC}    $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

APP_USER="stockapp"
APP_DIR="/home/$APP_USER/stock-analyzer"
VENV="$APP_DIR/venv"

info "Pulling latest code..."
sudo -u "$APP_USER" git -C "$APP_DIR" pull

info "Installing any new dependencies..."
sudo -u "$APP_USER" "$VENV/bin/pip" install -r "$APP_DIR/requirements.txt" -q

info "Restarting app..."
systemctl restart stock-analyzer
sleep 2

if systemctl is-active --quiet stock-analyzer; then
    success "App updated and running."
else
    error "App failed to start. Check: journalctl -u stock-analyzer -n 50"
fi
