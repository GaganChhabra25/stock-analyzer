#!/usr/bin/env bash
# =============================================================================
# validate.sh — Test Docker cron-worker WITHOUT affecting system cron
#
# Run AFTER DB is migrated into Docker postgres (see MIGRATE.md steps 1-4).
# System cron keeps running throughout — safe during market hours.
#
# Usage:  bash deploy/contabo/validate.sh
# =============================================================================

set -euo pipefail
cd /home/stockapp/stock-analyzer

echo ""
echo "══════════════════════════════════════════"
echo " Docker Cron-Worker Validation"
echo "══════════════════════════════════════════"

# ── 1. DB connection ──────────────────────────────────────────────────────────
echo ""
echo "[ 1/4 ] Testing DB connection..."
docker compose run --rm cron-worker python -c "
import os
from dotenv import load_dotenv
load_dotenv()
# Override to docker DB
os.environ['DATABASE_URL'] = os.environ['DATABASE_URL'].replace('localhost', 'postgres').replace(':5432', ':5432')
import psycopg2
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM kite_tokens')
count = cur.fetchone()[0]
conn.close()
print(f'  DB OK — kite_tokens rows: {count}')
"

# ── 2. Telegram message ───────────────────────────────────────────────────────
echo ""
echo "[ 2/4 ] Sending Telegram test message..."
docker compose run --rm cron-worker python -c "
import os, requests
from dotenv import load_dotenv
load_dotenv()
token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
chat  = os.environ.get('TELEGRAM_CHAT_ID', '')
if not token or not chat:
    print('  SKIP — TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set')
else:
    r = requests.post(
        f'https://api.telegram.org/bot{token}/sendMessage',
        json={'chat_id': chat, 'text': '✅ Docker cron-worker validation test — ignore this message'},
        timeout=10
    )
    ok = r.json().get('ok', False)
    print(f'  Telegram: {\"OK\" if ok else r.text}')
"

# ── 3. Kite auto-login (dry run) ──────────────────────────────────────────────
echo ""
echo "[ 3/4 ] Running Kite auto-login..."
echo "  (This will generate a real token and save to Docker DB)"
docker compose run --rm cron-worker python options/kite_auto_login.py
echo "  Kite login: done"

# ── 4. Verify token saved in Docker DB ───────────────────────────────────────
echo ""
echo "[ 4/4 ] Verifying token saved in Docker DB..."
docker compose run --rm cron-worker python -c "
import os
from datetime import date
from dotenv import load_dotenv
load_dotenv()
import psycopg2
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute(\"SELECT zerodha_user_id, token_date FROM kite_tokens WHERE token_date = %s\", (date.today(),))
rows = cur.fetchall()
conn.close()
if rows:
    print(f'  Token saved: {rows}')
else:
    print('  WARNING: No token found for today — check kite_auto_login logs')
"

echo ""
echo "══════════════════════════════════════════"
echo " Validation complete."
echo " If all 4 checks passed → run cutover.sh"
echo "══════════════════════════════════════════"
echo ""
