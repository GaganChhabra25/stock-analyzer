#!/bin/bash
# =============================================================================
# Stock Analyzer — PostgreSQL + Cron Setup
# =============================================================================
# Run this ONCE on your GCP VM after deploying the app.
#
# What it does:
#   1. Installs PostgreSQL
#   2. Creates DB + user
#   3. Adds 2 GB swap (prevents OOM on e2-micro)
#   4. Appends DB_URL + Telegram vars to .env
#   5. Installs daily cron job (8:30 AM IST = 3:00 AM UTC, Mon–Sat)
#   6. Runs the screener once immediately to verify everything works
#
# Usage:
#   chmod +x deploy/setup_screener_cron.sh
#   ./deploy/setup_screener_cron.sh
# =============================================================================

set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $1"; }
success() { echo -e "${GREEN}[OK]${NC}    $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $1"; }
err()     { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

CURRENT_USER=$(whoami)
HOME_DIR=$(eval echo ~$CURRENT_USER)
APP_DIR="$HOME_DIR/stock-analyzer"
VENV="$APP_DIR/venv"
LOG_FILE="$APP_DIR/logs/screener.log"
DB_NAME="stock_analyzer"
DB_USER="stockanalyzer"

echo ""
echo -e "${CYAN}=================================================${NC}"
echo -e "${CYAN}   Stock Analyzer — Screener + DB Setup${NC}"
echo -e "${CYAN}=================================================${NC}"
echo ""
info "App directory : $APP_DIR"
info "Python venv   : $VENV"
info "Log file      : $LOG_FILE"
echo ""

[ -d "$APP_DIR" ] || err "App directory $APP_DIR not found. Deploy the app first."
[ -f "$VENV/bin/python" ] || err "Python venv not found at $VENV. Run deploy.sh first."

# ── Collect credentials ───────────────────────────────────────────────────────
echo -e "${YELLOW}--- Credentials ---${NC}"
echo ""

read -s -p "PostgreSQL password for '$DB_USER' user (choose any strong password): " DB_PASS
echo ""
[ -z "$DB_PASS" ] && err "Password cannot be empty."

read -p "Telegram bot token: " TG_TOKEN
[ -z "$TG_TOKEN" ] && err "Telegram bot token is required."

read -p "Telegram chat ID: " TG_CHAT
[ -z "$TG_CHAT" ] && err "Telegram chat ID is required."

echo ""

# ── 1. Swap file ──────────────────────────────────────────────────────────────
info "[1/6] Setting up 2 GB swap file (prevents OOM on 1 GB RAM)..."
if swapon --show | grep -q /swapfile; then
    info "Swap file already active — skipping."
else
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    success "2 GB swap enabled."
fi

# ── 2. PostgreSQL ─────────────────────────────────────────────────────────────
info "[2/6] Installing PostgreSQL..."
sudo apt-get update -qq
sudo apt-get install -y postgresql postgresql-contrib
sudo systemctl enable postgresql
sudo systemctl start  postgresql
success "PostgreSQL installed and running."

info "Creating database '$DB_NAME' and user '$DB_USER'..."
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
SQL
success "Database ready."

# ── 3. PostgreSQL tuning for 1 GB RAM ─────────────────────────────────────────
info "[3/6] Tuning PostgreSQL for 1 GB RAM..."
PG_CONF=$(sudo -u postgres psql -t -c "SHOW config_file;" | tr -d ' ')
sudo sed -i "s/^#*max_connections\s*=.*/max_connections = 20/"    "$PG_CONF"
sudo sed -i "s/^#*shared_buffers\s*=.*/shared_buffers = 128MB/"  "$PG_CONF"
sudo sed -i "s/^#*work_mem\s*=.*/work_mem = 4MB/"                "$PG_CONF"
sudo systemctl reload postgresql
success "PostgreSQL tuned."

# ── 4. Update .env ────────────────────────────────────────────────────────────
info "[4/6] Updating .env with DB and Telegram credentials..."
ENV_FILE="$APP_DIR/.env"
DB_URL="postgresql://$DB_USER:$DB_PASS@localhost:5432/$DB_NAME"

# Remove any existing entries for these keys before appending
sed -i '/^DATABASE_URL=/d'        "$ENV_FILE" 2>/dev/null || true
sed -i '/^TELEGRAM_BOT_TOKEN=/d'  "$ENV_FILE" 2>/dev/null || true
sed -i '/^TELEGRAM_CHAT_ID=/d'    "$ENV_FILE" 2>/dev/null || true

cat >> "$ENV_FILE" <<ENV

# PostgreSQL
DATABASE_URL=$DB_URL

# Telegram alerts
TELEGRAM_BOT_TOKEN=$TG_TOKEN
TELEGRAM_CHAT_ID=$TG_CHAT
ENV

chmod 600 "$ENV_FILE"
success ".env updated."

# ── 5. Cron job ───────────────────────────────────────────────────────────────
info "[5/6] Installing daily cron job (8:30 AM IST = 3:00 AM UTC, Mon–Sat)..."
mkdir -p "$APP_DIR/logs"

CRON_CMD="0 3 * * 1-6 cd $APP_DIR && $VENV/bin/python run_screener.py --no-open >> $LOG_FILE 2>&1"

# Remove old entry if exists, then add fresh
( crontab -l 2>/dev/null | grep -v "run_screener.py" ; echo "$CRON_CMD" ) | crontab -
success "Cron job installed."

echo ""
crontab -l | grep "run_screener"
echo ""

# ── 6. Verification run ───────────────────────────────────────────────────────
info "[6/6] Running screener now to verify end-to-end setup..."
echo ""
warn "This will take 2–3 minutes (fetching market data)..."
echo ""

cd "$APP_DIR"
"$VENV/bin/python" run_screener.py --no-open 2>&1 | tail -20

echo ""
echo -e "${GREEN}=================================================${NC}"
echo -e "${GREEN}   Setup complete!${NC}"
echo -e "${GREEN}=================================================${NC}"
echo ""
echo "  Cron schedule  : Every Mon–Sat at 8:30 AM IST"
echo "  Log file       : $LOG_FILE"
echo "  Database       : $DB_NAME @ localhost:5432"
echo ""
echo "  Useful commands:"
echo "    tail -f $LOG_FILE                          # watch live screener output"
echo "    crontab -l                                  # verify cron job"
echo "    sudo systemctl status postgresql            # DB status"
echo "    psql -U $DB_USER -d $DB_NAME -h localhost  # connect to DB"
echo ""
echo "  Analytics queries (after a few weeks of data):"
echo "    SELECT * FROM v_model_accuracy;"
echo "    SELECT * FROM v_signal_importance;"
echo "    SELECT * FROM v_prob_calibration;"
echo ""
