#!/usr/bin/env bash
# =============================================================================
# cutover.sh — Switch from system cron → Docker cron (one-time migration)
#
# Prerequisites:
#   1. validate.sh passed all checks
#   2. Run after market hours (recommended: after 11:35 PM IST)
#
# Usage:  bash deploy/contabo/cutover.sh
# =============================================================================

set -euo pipefail
cd /home/stockapp/stock-analyzer

echo ""
echo "══════════════════════════════════════════"
echo " Cron Cutover: System → Docker"
echo "══════════════════════════════════════════"

# ── Step 1: Dump current host DB ─────────────────────────────────────────────
echo ""
echo "[ 1/5 ] Dumping host PostgreSQL..."
BACKUP=/home/stockapp/stock_analyzer_cutover_$(date +%Y%m%d_%H%M%S).sql
sudo -u stockapp pg_dump -U stockanalyzer stock_analyzer > "$BACKUP"
echo "  Backup saved: $BACKUP"

# ── Step 2: Start Docker postgres ────────────────────────────────────────────
echo ""
echo "[ 2/5 ] Starting Docker postgres..."
docker compose up -d postgres
echo "  Waiting for postgres to be healthy..."
until docker compose exec postgres pg_isready -U stockanalyzer -d stock_analyzer -q; do
    sleep 2
done
echo "  Postgres healthy"

# ── Step 3: Restore data into Docker postgres ─────────────────────────────────
echo ""
echo "[ 3/5 ] Restoring data into Docker postgres..."
docker compose exec -T postgres psql -U stockanalyzer stock_analyzer < "$BACKUP"
echo "  Restore complete"

# ── Step 4: Start cron-worker ─────────────────────────────────────────────────
echo ""
echo "[ 4/5 ] Starting Docker cron-worker..."
docker compose up -d cron-worker
sleep 3
docker compose ps cron-worker
echo "  cron-worker running"

# ── Step 5: Remove system crontab ────────────────────────────────────────────
echo ""
echo "[ 5/5 ] Removing system crontab (Docker cron is now active)..."
crontab -u stockapp -r 2>/dev/null || true
echo "  System crontab removed"

echo ""
echo "══════════════════════════════════════════"
echo " Cutover complete!"
echo ""
echo " Monitor:  docker compose logs -f cron-worker"
echo " DB shell: docker compose exec postgres psql -U stockanalyzer stock_analyzer"
echo ""
echo " To shut down web app when ready:"
echo "   systemctl stop stock-analyzer"
echo "   systemctl disable stock-analyzer"
echo "══════════════════════════════════════════"
echo ""
