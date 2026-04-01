#!/bin/bash
# =============================================================================
# Stock Analyzer — Update Crontab on Contabo VPS
# Run as: root
# All times in UTC (server is UTC). IST = UTC+5:30
# =============================================================================

APP_USER="stockapp"
APP_DIR="/home/$APP_USER/stock-analyzer"
VENV="$APP_DIR/venv"
LOG="$APP_DIR/logs"

echo "[INFO] Updating crontab for $APP_USER..."

crontab -u "$APP_USER" - <<EOF
# ── Screener: 9:00 AM IST Mon-Sat = 3:30 AM UTC ──────────────────────────────
30 3 * * 1-6 cd $APP_DIR && $VENV/bin/python run_screener.py --no-open >> $LOG/screener.log 2>&1

# ── Kite reminder: 9:00 AM IST = 3:30 AM UTC ─────────────────────────────────
30 3 * * 1-5 cd $APP_DIR && $VENV/bin/python -c "from options.kite_auth import send_daily_reminder; send_daily_reminder()" >> $LOG/kite.log 2>&1

# ── Options collector: every minute 9:14 AM–3:31 PM IST = 3:44–10:01 AM UTC ──
*/1 3-10 * * 1-5 cd $APP_DIR && $VENV/bin/python options/collector.py >> $LOG/options.log 2>&1

# ── EOD summary Telegram: 3:35 PM IST = 10:05 AM UTC ─────────────────────────
5 10 * * 1-5 cd $APP_DIR && $VENV/bin/python options/collector.py --eod >> $LOG/options.log 2>&1
EOF

echo "[OK] Crontab updated:"
crontab -u "$APP_USER" -l
