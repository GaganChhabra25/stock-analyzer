#!/bin/bash
# =============================================================================
# Stock Analyzer — Universal Deployment Script
# =============================================================================
# Run this on any fresh Ubuntu server to deploy the app from scratch.
#
# Usage:
#   chmod +x deploy/deploy.sh
#   ./deploy/deploy.sh
#
# It will interactively ask for:
#   - Server public IP (or domain)
#   - Google OAuth credentials
#   - Allowed emails
#   - Git repo URL (or use local files)
# =============================================================================

set -e

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

info()    { echo -e "${CYAN}[INFO]${NC}  $1"; }
success() { echo -e "${GREEN}[OK]${NC}    $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}=================================================${NC}"
echo -e "${CYAN}   Stock Analyzer — Deployment Setup${NC}"
echo -e "${CYAN}=================================================${NC}"
echo ""

# ── Detect current user & home ────────────────────────────────────────────────
CURRENT_USER=$(whoami)
HOME_DIR=$(eval echo ~$CURRENT_USER)
APP_DIR="$HOME_DIR/stock-analyzer"

info "Deploying as user: $CURRENT_USER"
info "App directory will be: $APP_DIR"
echo ""

# ── Collect inputs ────────────────────────────────────────────────────────────
echo -e "${YELLOW}--- Configuration ---${NC}"
echo ""

# Public IP / domain
DETECTED_IP=$(curl -s --max-time 5 ifconfig.me 2>/dev/null || echo "")
if [ -n "$DETECTED_IP" ]; then
    read -p "Server public IP or domain [$DETECTED_IP]: " SERVER_HOST
    SERVER_HOST="${SERVER_HOST:-$DETECTED_IP}"
else
    read -p "Server public IP or domain: " SERVER_HOST
    [ -z "$SERVER_HOST" ] && error "Server IP/domain is required."
fi

echo ""

# Git repo or local
read -p "Git repo URL (leave blank if running from local files already): " GIT_REPO

echo ""

# Google OAuth
read -p "Google Client ID: " GOOGLE_CLIENT_ID
[ -z "$GOOGLE_CLIENT_ID" ] && error "Google Client ID is required."

read -p "Google Client Secret: " GOOGLE_CLIENT_SECRET
[ -z "$GOOGLE_CLIENT_SECRET" ] && error "Google Client Secret is required."

echo ""
read -p "Allowed emails (comma-separated, e.g. you@gmail.com): " ALLOWED_EMAILS
[ -z "$ALLOWED_EMAILS" ] && error "At least one allowed email is required."

echo ""
echo -e "${YELLOW}--- Summary ---${NC}"
echo "  Server:         $SERVER_HOST"
echo "  App directory:  $APP_DIR"
echo "  Git repo:       ${GIT_REPO:-'(using files already on disk)'}"
echo "  Allowed emails: $ALLOWED_EMAILS"
echo ""
read -p "Proceed with deployment? [Y/n]: " CONFIRM
CONFIRM="${CONFIRM:-Y}"
[[ "$CONFIRM" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }
echo ""

# ── 1. System packages ────────────────────────────────────────────────────────
info "[1/7] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y python3 python3-pip python3-venv nginx git curl 2>/dev/null
success "System packages installed."

# ── 2. Firewall ───────────────────────────────────────────────────────────────
info "[2/7] Configuring firewall (ports 80 & 443)..."
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80  -j ACCEPT 2>/dev/null || true
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT 2>/dev/null || true
# Persist rules
sudo apt-get install -y iptables-persistent 2>/dev/null || true
sudo netfilter-persistent save 2>/dev/null || true
success "Firewall configured."
warn "Also open ports 80/443 in your cloud provider's Security List / Firewall rules."

# ── 3. Clone or update repo ───────────────────────────────────────────────────
info "[3/7] Setting up application files..."
if [ -n "$GIT_REPO" ]; then
    if [ -d "$APP_DIR/.git" ]; then
        info "Repo already exists — pulling latest..."
        git -C "$APP_DIR" pull
    else
        git clone "$GIT_REPO" "$APP_DIR"
    fi
    success "Repo ready at $APP_DIR"
else
    if [ ! -d "$APP_DIR" ]; then
        error "No git repo provided and $APP_DIR does not exist. Cannot continue."
    fi
    info "Using existing files at $APP_DIR"
fi

# ── 4. Python venv + dependencies ────────────────────────────────────────────
info "[4/7] Setting up Python virtual environment..."
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip -q
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q
success "Python environment ready."

# ── 5. Write .env file ────────────────────────────────────────────────────────
info "[5/7] Writing .env file..."
FLASK_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# Preserve existing secret key if .env already exists
if [ -f "$APP_DIR/.env" ]; then
    EXISTING_SECRET=$(grep -E '^FLASK_SECRET_KEY=' "$APP_DIR/.env" | cut -d= -f2-)
    if [ -n "$EXISTING_SECRET" ] && [ "$EXISTING_SECRET" != "change-me-to-a-random-64-char-hex-string" ]; then
        FLASK_SECRET="$EXISTING_SECRET"
        info "Keeping existing FLASK_SECRET_KEY."
    fi
fi

# Back up existing .env so we can restore DB/Telegram vars after overwrite
[ -f "$APP_DIR/.env" ] && cp "$APP_DIR/.env" "$APP_DIR/.env.bak" || true

cat > "$APP_DIR/.env" <<EOF
FLASK_SECRET_KEY=$FLASK_SECRET
GOOGLE_CLIENT_ID=$GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET=$GOOGLE_CLIENT_SECRET
ALLOWED_EMAILS=$ALLOWED_EMAILS
EOF

# Preserve DATABASE_URL and Telegram vars across redeploys
for KEY in DATABASE_URL TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID; do
    EXISTING_VAL=$(grep -E "^${KEY}=" "$APP_DIR/.env.bak" 2>/dev/null | cut -d= -f2- || true)
    if [ -n "$EXISTING_VAL" ]; then
        echo "${KEY}=${EXISTING_VAL}" >> "$APP_DIR/.env"
        info "Preserved $KEY from previous .env"
    fi
done
rm -f "$APP_DIR/.env.bak"

chmod 600 "$APP_DIR/.env"
success ".env written."

# ── 6. Log directory + systemd service ───────────────────────────────────────
info "[6/7] Installing systemd service..."
mkdir -p "$APP_DIR/logs"

# Generate service file dynamically (uses actual user/paths, not hardcoded ubuntu)
sudo tee /etc/systemd/system/stock-analyzer.service > /dev/null <<EOF
[Unit]
Description=Stock Analyzer Web App
After=network.target

[Service]
User=$CURRENT_USER
Group=$CURRENT_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/venv/bin/gunicorn \\
    --workers 2 \\
    --bind 127.0.0.1:5000 \\
    --timeout 660 \\
    --access-logfile $APP_DIR/logs/access.log \\
    --error-logfile  $APP_DIR/logs/error.log \\
    app:app
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable stock-analyzer
sudo systemctl restart stock-analyzer
sleep 2

if sudo systemctl is-active --quiet stock-analyzer; then
    success "Service is running."
else
    error "Service failed to start. Check: sudo journalctl -u stock-analyzer -n 50"
fi

# ── 7. Nginx ──────────────────────────────────────────────────────────────────
info "[7/7] Configuring nginx..."

sudo tee /etc/nginx/sites-available/stock-analyzer > /dev/null <<EOF
server {
    listen 80;
    server_name $SERVER_HOST;

    add_header X-Frame-Options "SAMEORIGIN";
    add_header X-Content-Type-Options "nosniff";
    add_header Referrer-Policy "strict-origin-when-cross-origin";

    location / {
        proxy_pass         http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_read_timeout 600s;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/stock-analyzer /etc/nginx/sites-enabled/stock-analyzer
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
success "Nginx configured."

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}=================================================${NC}"
echo -e "${GREEN}   Deployment complete!${NC}"
echo -e "${GREEN}=================================================${NC}"
echo ""
echo -e "  App URL:  ${CYAN}http://$SERVER_HOST${NC}"
echo ""
echo "  Useful commands:"
echo "    sudo systemctl status stock-analyzer"
echo "    sudo journalctl -u stock-analyzer -f"
echo "    tail -f $APP_DIR/logs/error.log"
echo ""
echo -e "${YELLOW}  IMPORTANT: Add this redirect URI in Google Cloud Console:${NC}"
echo -e "  ${CYAN}http://$SERVER_HOST/auth/callback${NC}"
echo ""
echo "  Optional — add HTTPS (requires a domain name):"
echo "    sudo apt install certbot python3-certbot-nginx"
echo "    sudo certbot --nginx -d $SERVER_HOST"
echo ""
