#!/bin/bash
# =============================================================================
# Oracle Cloud Ubuntu VM — Stock Analyzer One-Shot Setup
# =============================================================================
# Run as ubuntu user AFTER:
#   1. SSH into your Oracle Cloud VM
#   2. Upload your project (git clone or scp)
#
# Usage:
#   chmod +x deploy/setup.sh
#   ./deploy/setup.sh
# =============================================================================

set -e   # exit on any error

APP_DIR="/home/ubuntu/stock-analyzer"
VENV="$APP_DIR/venv"
SERVICE_NAME="stock-analyzer"

echo ""
echo "=== Stock Analyzer — Oracle Cloud Setup ==="
echo ""

# ── 1. System packages ────────────────────────────────────────────────────────
echo "[1/7] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y python3 python3-pip python3-venv nginx certbot python3-certbot-nginx git

# ── 2. Firewall — open ports 80 and 443 ──────────────────────────────────────
echo "[2/7] Configuring firewall..."
# iptables rules (Oracle Cloud Ubuntu blocks ports by default)
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80  -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
# Persist iptables rules across reboots
sudo apt-get install -y iptables-persistent
sudo netfilter-persistent save

echo "      NOTE: Also open ports 80 & 443 in Oracle Cloud Console:"
echo "      Networking → VCN → Security List → Add Ingress Rules"

# ── 3. Python virtual environment + dependencies ──────────────────────────────
echo "[3/7] Setting up Python virtual environment..."
cd "$APP_DIR"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip -q
"$VENV/bin/pip" install -r requirements.txt -q
echo "      Done."

# ── 4. .env file ─────────────────────────────────────────────────────────────
echo "[4/7] Setting up environment variables..."
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    sed -i "s|change-me-to-a-random-64-char-hex-string|$SECRET|g" "$APP_DIR/.env"
    echo ""
    echo "  !! Created .env with a generated FLASK_SECRET_KEY."
    echo "  !! Edit $APP_DIR/.env and fill in:"
    echo "       GOOGLE_CLIENT_ID"
    echo "       GOOGLE_CLIENT_SECRET"
    echo "       ALLOWED_EMAILS"
    echo ""
else
    echo "      .env already exists — skipping."
fi
chmod 600 "$APP_DIR/.env"

# ── 5. Log directory ──────────────────────────────────────────────────────────
echo "[5/7] Creating log directory..."
mkdir -p "$APP_DIR/logs"

# ── 6. Systemd service ────────────────────────────────────────────────────────
echo "[6/7] Installing systemd service..."
sudo cp "$APP_DIR/deploy/stock-analyzer.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl start  "$SERVICE_NAME"
echo "      Service status:"
sudo systemctl is-active "$SERVICE_NAME" && echo "      Running OK" || echo "      FAILED — check: sudo journalctl -u $SERVICE_NAME"

# ── 7. Nginx ──────────────────────────────────────────────────────────────────
echo "[7/7] Configuring nginx..."
PUBLIC_IP=$(curl -s ifconfig.me 2>/dev/null || echo "YOUR_IP")
NGINX_CONF="/etc/nginx/sites-available/$SERVICE_NAME"
sudo cp "$APP_DIR/deploy/nginx.conf" "$NGINX_CONF"
sudo sed -i "s|YOUR_DOMAIN_OR_IP|$PUBLIC_IP|g" "$NGINX_CONF"
sudo ln -sf "$NGINX_CONF" "/etc/nginx/sites-enabled/$SERVICE_NAME"
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

echo ""
echo "=== Setup complete! ==="
echo ""
echo "  App running at: http://$PUBLIC_IP"
echo ""
echo "  Next steps:"
echo "  1. Edit .env with your Google OAuth credentials"
echo "  2. Restart service: sudo systemctl restart $SERVICE_NAME"
echo "  3. (Optional) Add a domain + HTTPS:"
echo "     sudo certbot --nginx -d yourdomain.com"
echo ""
echo "  Useful commands:"
echo "    sudo systemctl status $SERVICE_NAME"
echo "    sudo journalctl -u $SERVICE_NAME -f"
echo "    tail -f $APP_DIR/logs/error.log"
echo ""
