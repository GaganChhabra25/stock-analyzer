# Deployment Guide — Any Server

Two services deploy hote hain:
- **postgres** — Docker container, named volume (data persistent)
- **cron-worker** — Docker container, supercronic cron scheduler

## Prerequisites

- Ubuntu/Debian server (fresh or existing)
- Git access to repo
- `.env` file with credentials (see `.env.example`)

---

## Step 1 — Server Setup (first time only)

```bash
# Install Docker
curl -fsSL https://get.docker.com | sh
systemctl enable docker && systemctl start docker

# Create app user
useradd -m -s /bin/bash stockapp

# Clone repo
git clone https://github.com/GaganChhabra25/stock-analyzer.git /home/stockapp/stock-analyzer
chown -R stockapp:stockapp /home/stockapp/stock-analyzer
```

---

## Step 2 — Configure .env

```bash
cp /home/stockapp/stock-analyzer/.env.example /home/stockapp/stock-analyzer/.env
nano /home/stockapp/stock-analyzer/.env
```

Required vars:
```
POSTGRES_USER=stockanalyzer
POSTGRES_PASSWORD=<strong-password>
DATABASE_URL=postgresql://stockanalyzer:<password>@localhost:5432/stock_analyzer  # overridden by compose

KITE_API_KEY=...
KITE_API_SECRET=...
KITE_ADMIN_USER_ID=...
ZERODHA_USER_ID=...
ZERODHA_PASSWORD=...
ZERODHA_TOTP_SECRET=...

TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

---

## Step 3 — Start Containers

```bash
cd /home/stockapp/stock-analyzer
docker compose up -d
```

Verify:
```bash
docker compose ps
# Both containers should show: Up / healthy
```

---

## Step 4 — Restore Data (migration from old server)

```bash
# On OLD server — dump
PGPASSWORD=<pass> pg_dump -h 127.0.0.1 -U stockanalyzer stock_analyzer > backup.sql

# Copy to new server
scp backup.sql root@<new-server-ip>:/home/stockapp/

# On NEW server — restore into Docker postgres
docker compose exec -T postgres psql -U stockanalyzer stock_analyzer < /home/stockapp/backup.sql
```

---

## Step 5 — Validate

```bash
# DB check
docker compose exec postgres psql -U stockanalyzer stock_analyzer -c \
  "SELECT instrument, MAX(ts) AT TIME ZONE 'Asia/Kolkata' AS last FROM option_chain GROUP BY instrument;"

# Kite login (generates today's token)
docker compose run --rm cron-worker python options/kite_auto_login.py

# Cron logs
docker compose logs -f cron-worker
```

---

## Day-to-Day Operations

```bash
# Status
docker compose ps

# Live cron logs
docker compose logs -f cron-worker

# Restart cron-worker (after code change)
docker compose restart cron-worker

# DB shell
docker compose exec postgres psql -U stockanalyzer stock_analyzer

# Stop everything
docker compose down

# Stop + wipe DB (careful!)
docker compose down -v
```

---

## Code Deploy (automatic via GitHub Actions)

Every `git push master`:
1. SSH to server → git pull
2. `docker compose build cron-worker` (new image)
3. App restart (if still running)

After build, restart cron-worker manually to pick up new code:
```bash
docker compose restart cron-worker
```

---

## DBeaver Connection

Connect via SSH tunnel:

| Field | Value |
|---|---|
| SSH Host | `<server-ip>` |
| SSH User | `root` |
| DB Host | `localhost` |
| DB Port | `5433` |
| Database | `stock_analyzer` |
| User | `stockanalyzer` |
| Password | `<POSTGRES_PASSWORD>` |

> Port 5433 = Docker postgres exposed port on host.

---

## Server Migration Checklist

- [ ] New server provisioned
- [ ] Docker installed (`curl -fsSL https://get.docker.com | sh`)
- [ ] Repo cloned
- [ ] `.env` configured
- [ ] `docker compose up -d` — both containers healthy
- [ ] DB backup restored from old server
- [ ] Kite login validated
- [ ] Telegram test message received
- [ ] Cron logs clean (no errors)
- [ ] DNS/IP updated if needed
- [ ] Old server stopped
