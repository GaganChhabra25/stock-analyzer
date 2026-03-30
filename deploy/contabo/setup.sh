#!/bin/bash
# =============================================================================
# Stock Analyzer — Contabo VPS Setup Script
# =============================================================================
# Server: Contabo VPS, Ubuntu 24.04, 4 vCPU, 8 GB RAM, 150 GB SSD
# Run as: root
#
# Usage:
#   chmod +x deploy/contabo/setup.sh
#   ./deploy/contabo/setup.sh
#
# What it does:
#   1. Creates a dedicated app user (stockapp)
#   2. Installs system packages (Python 3.12, PostgreSQL 16, Nginx, Git)
#   3. Clones repo from GitHub
#   4. Sets up Python venv + dependencies
#   5. Configures PostgreSQL (tuned for 8 GB RAM)
#   6. Writes .env file
#   7. Installs systemd service
#   8. Configures Nginx
#   9. Sets up cron jobs (screener + options collector placeholder)
#   10. Runs schema and verifies everything
# =============================================================================

set -e

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $1"; }
success() { echo -e "${GREEN}[OK]${NC}    $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# ── Must run as root ──────────────────────────────────────────────────────────
[ "$(id -u)" -eq 0 ] || error "Run this script as root: sudo bash setup.sh"

# ── Constants ─────────────────────────────────────────────────────────────────
APP_USER="stockapp"
APP_DIR="/home/$APP_USER/stock-analyzer"
VENV="$APP_DIR/venv"
GIT_REPO="https://github.com/GaganChhabra25/stock-analyzer.git"
DB_NAME="stock_analyzer"
DB_USER="stockanalyzer"
SERVER_IP="185.211.6.5"

echo ""
echo -e "${CYAN}=================================================${NC}"
echo -e "${CYAN}   Stock Analyzer — Contabo Setup${NC}"
echo -e "${CYAN}   Server: $SERVER_IP${NC}"
echo -e "${CYAN}=================================================${NC}"
echo ""

# ── Collect credentials ───────────────────────────────────────────────────────
echo -e "${YELLOW}--- Credentials ---${NC}"
echo ""

read -p    "Google Client ID: "                          GOOGLE_CLIENT_ID
[ -z "$GOOGLE_CLIENT_ID" ]     && error "Google Client ID is required."

read -p    "Google Client Secret: "                      GOOGLE_CLIENT_SECRET
[ -z "$GOOGLE_CLIENT_SECRET" ] && error "Google Client Secret is required."

read -p    "Allowed emails (comma-separated): "          ALLOWED_EMAILS
[ -z "$ALLOWED_EMAILS" ]       && error "At least one allowed email is required."

read -s -p "PostgreSQL password for '$DB_USER': "        DB_PASS
echo ""
[ -z "$DB_PASS" ]              && error "DB password cannot be empty."

read -p    "Telegram bot token: "                        TG_TOKEN
[ -z "$TG_TOKEN" ]             && error "Telegram bot token is required."

read -p    "Telegram chat ID: "                          TG_CHAT
[ -z "$TG_CHAT" ]              && error "Telegram chat ID is required."

echo ""
echo -e "${YELLOW}--- Summary ---${NC}"
echo "  Server IP   : $SERVER_IP"
echo "  App user    : $APP_USER"
echo "  App dir     : $APP_DIR"
echo "  Git repo    : $GIT_REPO"
echo "  Database    : $DB_NAME @ localhost:5432"
echo ""
read -p "Proceed? [Y/n]: " CONFIRM
CONFIRM="${CONFIRM:-Y}"
[[ "$CONFIRM" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }
echo ""

# ── 1. System packages ────────────────────────────────────────────────────────
info "[1/9] Installing system packages..."
apt-get update -qq
apt-get install -y \
    python3 python3-pip python3-venv python3-dev \
    postgresql postgresql-contrib \
    nginx git curl build-essential \
    libpq-dev 2>/dev/null
systemctl enable postgresql nginx
systemctl start  postgresql nginx
success "System packages installed (Python $(python3 --version), PostgreSQL $(psql --version | awk '{print $3}'))."

# ── 2. App user ───────────────────────────────────────────────────────────────
info "[2/9] Creating app user '$APP_USER'..."
if id "$APP_USER" &>/dev/null; then
    info "User '$APP_USER' already exists — skipping."
else
    adduser --disabled-password --gecos "" "$APP_USER"
    success "User '$APP_USER' created."
fi

# ── 3. Clone repo ─────────────────────────────────────────────────────────────
info "[3/9] Cloning repo..."
if [ -d "$APP_DIR/.git" ]; then
    info "Repo already exists — pulling latest..."
    sudo -u "$APP_USER" git -C "$APP_DIR" pull
else
    sudo -u "$APP_USER" git clone "$GIT_REPO" "$APP_DIR"
fi
success "Repo ready at $APP_DIR"

# ── 4. Python venv + dependencies ────────────────────────────────────────────
info "[4/9] Setting up Python venv..."
sudo -u "$APP_USER" python3 -m venv "$VENV"
sudo -u "$APP_USER" "$VENV/bin/pip" install --upgrade pip -q
sudo -u "$APP_USER" "$VENV/bin/pip" install -r "$APP_DIR/requirements.txt" -q
# Kite Connect for options data collection
sudo -u "$APP_USER" "$VENV/bin/pip" install kiteconnect -q
success "Python environment ready."

# ── 5. PostgreSQL setup + tuning ──────────────────────────────────────────────
info "[5/9] Configuring PostgreSQL (tuned for 8 GB RAM)..."

# Create user + database
sudo -u postgres psql <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$DB_USER') THEN
    CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';
  ELSE
    ALTER USER $DB_USER WITH PASSWORD '$DB_PASS';
  END IF;
END
\$\$;
SELECT 'CREATE DATABASE' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$DB_NAME') \gexec
GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;
\c $DB_NAME
GRANT ALL ON SCHEMA public TO $DB_USER;
SQL

# Apply tuned config
PG_CONF=$(sudo -u postgres psql -t -c "SHOW config_file;" | tr -d ' ')
cat >> "$PG_CONF" <<PGCONF

# ── Contabo 8 GB RAM tuning ───────────────────────────────────────────────────
max_connections           = 50
shared_buffers            = 2GB
effective_cache_size      = 6GB
maintenance_work_mem      = 512MB
checkpoint_completion_target = 0.9
wal_buffers               = 16MB
default_statistics_target = 100
random_page_cost          = 1.1
effective_io_concurrency  = 200
work_mem                  = 32MB
min_wal_size              = 1GB
max_wal_size              = 4GB
PGCONF

systemctl restart postgresql
success "PostgreSQL configured and running."

# Run schema
info "Applying database schema..."
sudo -u "$APP_USER" PGPASSWORD="$DB_PASS" psql \
    -U "$DB_USER" -d "$DB_NAME" -h localhost \
    -f "$APP_DIR/schema.sql" -q
success "Schema applied."

# ── 6. Write .env ─────────────────────────────────────────────────────────────
info "[6/9] Writing .env..."
FLASK_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
DB_URL="postgresql://$DB_USER:$DB_PASS@localhost:5432/$DB_NAME"

cat > "$APP_DIR/.env" <<ENV
FLASK_SECRET_KEY=$FLASK_SECRET
GOOGLE_CLIENT_ID=$GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET=$GOOGLE_CLIENT_SECRET
ALLOWED_EMAILS=$ALLOWED_EMAILS

# PostgreSQL
DATABASE_URL=$DB_URL

# Telegram alerts
TELEGRAM_BOT_TOKEN=$TG_TOKEN
TELEGRAM_CHAT_ID=$TG_CHAT

# Kite Connect (fill in after creating app at kite.trade)
KITE_API_KEY=
KITE_API_SECRET=
KITE_ACCESS_TOKEN=
ENV

chmod 600 "$APP_DIR/.env"
chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
success ".env written."

# ── 7. Log directory + systemd service ───────────────────────────────────────
info "[7/9] Installing systemd service..."
mkdir -p "$APP_DIR/logs"
chown -R "$APP_USER:$APP_USER" "$APP_DIR/logs"

tee /etc/systemd/system/stock-analyzer.service > /dev/null <<SERVICE
[Unit]
Description=Stock Analyzer Web App
After=network.target postgresql.service

[Service]
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$VENV/bin/gunicorn \\
    --workers 3 \\
    --bind 127.0.0.1:5000 \\
    --timeout 660 \\
    --access-logfile $APP_DIR/logs/access.log \\
    --error-logfile  $APP_DIR/logs/error.log \\
    app:app
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable stock-analyzer
systemctl restart stock-analyzer
sleep 3

if systemctl is-active --quiet stock-analyzer; then
    success "stock-analyzer service is running."
else
    error "Service failed to start. Check: journalctl -u stock-analyzer -n 50"
fi

# ── 8. Nginx ──────────────────────────────────────────────────────────────────
info "[8/9] Configuring Nginx..."

tee /etc/nginx/sites-available/stock-analyzer > /dev/null <<NGINX
server {
    listen 80;
    server_name $SERVER_IP;

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
NGINX

ln -sf /etc/nginx/sites-available/stock-analyzer /etc/nginx/sites-enabled/stock-analyzer
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
success "Nginx configured."

# ── 9. Cron jobs ──────────────────────────────────────────────────────────────
info "[9/9] Installing cron jobs..."

SCREENER_CRON="0 3 * * 1-6 cd $APP_DIR && $VENV/bin/python run_screener.py --no-open >> $APP_DIR/logs/screener.log 2>&1"

# Options collector placeholder — activated once Kite Connect is configured
OPTIONS_CRON="# */1 9-15 * * 1-5 cd $APP_DIR && $VENV/bin/python options/collector.py >> $APP_DIR/logs/options.log 2>&1"

(
    crontab -u "$APP_USER" -l 2>/dev/null | grep -v "run_screener.py" | grep -v "options/collector.py"
    echo "$SCREENER_CRON"
    echo "$OPTIONS_CRON"
) | crontab -u "$APP_USER" -

success "Cron jobs installed."
echo ""
crontab -u "$APP_USER" -l
echo ""

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}=================================================${NC}"
echo -e "${GREEN}   Setup complete!${NC}"
echo -e "${GREEN}=================================================${NC}"
echo ""
echo -e "  App URL      : ${CYAN}http://$SERVER_IP${NC}"
echo "  App dir      : $APP_DIR"
echo "  Logs         : $APP_DIR/logs/"
echo "  DB           : $DB_NAME @ localhost:5432"
echo ""
echo "  Useful commands:"
echo "    systemctl status stock-analyzer"
echo "    journalctl -u stock-analyzer -f"
echo "    tail -f $APP_DIR/logs/screener.log"
echo "    tail -f $APP_DIR/logs/options.log"
echo ""
echo -e "${YELLOW}  Next steps:${NC}"
echo "  1. Add Google OAuth redirect URI in Google Cloud Console:"
echo -e "     ${CYAN}http://$SERVER_IP/auth/callback${NC}"
echo "  2. Sign up at kite.trade, fill KITE_API_KEY + KITE_API_SECRET in .env"
echo "  3. Run: systemctl restart stock-analyzer"
echo ""
