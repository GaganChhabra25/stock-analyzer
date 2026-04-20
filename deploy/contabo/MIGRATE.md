# Migration: Host PostgreSQL → Docker PostgreSQL

Run these steps **after market hours** (after 11:30 PM IST / MCX close).

## Step 1 — Dump existing data (on Contabo server)
```bash
sudo -u stockapp pg_dump -U stockanalyzer stock_analyzer > /home/stockapp/stock_analyzer_backup.sql
```

## Step 2 — Add Docker credentials to .env (on server)
```bash
# Add these two lines to /home/stockapp/stock-analyzer/.env
echo "POSTGRES_USER=stockanalyzer" >> /home/stockapp/stock-analyzer/.env
echo "POSTGRES_PASSWORD=<same password as current DB>" >> /home/stockapp/stock-analyzer/.env
```

## Step 3 — Start Docker containers
```bash
cd /home/stockapp/stock-analyzer
docker compose up -d postgres
# Wait for postgres to be healthy
docker compose ps
```

## Step 4 — Restore data into Docker postgres (port 5433 on host)
```bash
psql -h 127.0.0.1 -p 5433 -U stockanalyzer stock_analyzer < /home/stockapp/stock_analyzer_backup.sql
```

## Step 5 — Start cron-worker
```bash
docker compose up -d cron-worker
docker compose logs -f cron-worker   # verify no errors
```

## Step 6 — Stop host cron (system crontab)
```bash
crontab -u stockapp -r
```

## Step 7 — Verify (next morning after Kite auto-login at 08:30 IST)
```bash
docker compose logs cron-worker | grep -i "kite\|login\|token"
```

## Rollback (if anything goes wrong)
```bash
docker compose down
# System crontab is already removed — restore manually:
crontab -u stockapp /home/stockapp/stock-analyzer/deploy/contabo/crontab
```

## When app is shut down later
```bash
systemctl stop stock-analyzer
systemctl disable stock-analyzer
# Remove web app block from deploy.yml
```
