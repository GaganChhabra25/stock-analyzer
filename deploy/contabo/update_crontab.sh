#!/bin/bash
# =============================================================================
# Stock Analyzer — Apply crontab from deploy/contabo/crontab to Contabo VPS
# Run from repo root: bash deploy/contabo/update_crontab.sh
# Or via: python scripts/server.py cron-apply
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CRONTAB_FILE="$SCRIPT_DIR/crontab"
APP_USER="stockapp"

if [ ! -f "$CRONTAB_FILE" ]; then
    echo "[ERROR] Crontab file not found: $CRONTAB_FILE"
    exit 1
fi

echo "[INFO] Applying crontab for $APP_USER from $CRONTAB_FILE ..."
crontab -u "$APP_USER" "$CRONTAB_FILE"

echo "[OK] Crontab applied:"
crontab -u "$APP_USER" -l
