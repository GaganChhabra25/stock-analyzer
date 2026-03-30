# Contabo VPS Deployment

**Server:** 185.211.6.5 | Ubuntu 24.04 | 4 vCPU | 8 GB RAM | 150 GB SSD

## First-Time Setup

SSH into the server as root, then:

```bash
# 1. Clone the repo
git clone https://github.com/GaganChhabra25/stock-analyzer.git
cd stock-analyzer

# 2. Run setup (takes ~5 mins)
chmod +x deploy/contabo/setup.sh
bash deploy/contabo/setup.sh
```

The script will ask for:
- Google OAuth Client ID + Secret
- Allowed email addresses
- PostgreSQL password
- Telegram bot token + chat ID

## Updating the App

After pushing new code to GitHub:

```bash
bash deploy/contabo/update.sh
```

## Cron Jobs

| Job | Schedule | Log |
|-----|----------|-----|
| Daily screener | Mon–Sat 8:30 AM IST | `logs/screener.log` |
| Options collector | Every 1 min, 9:15–3:30 IST (when enabled) | `logs/options.log` |

Enable options collector once Kite Connect is configured:
```bash
# Edit crontab and uncomment the options collector line
crontab -u stockapp -e
```

## Key Paths

| Path | Purpose |
|------|---------|
| `/home/stockapp/stock-analyzer/` | App root |
| `/home/stockapp/stock-analyzer/.env` | Credentials |
| `/home/stockapp/stock-analyzer/logs/` | All logs |
| `/etc/systemd/system/stock-analyzer.service` | Systemd service |
| `/etc/nginx/sites-available/stock-analyzer` | Nginx config |

## Useful Commands

```bash
# App status
systemctl status stock-analyzer

# Live app logs
journalctl -u stock-analyzer -f

# Live screener log
tail -f /home/stockapp/stock-analyzer/logs/screener.log

# Connect to DB
psql -U stockanalyzer -d stock_analyzer -h localhost

# Restart everything
systemctl restart stock-analyzer nginx postgresql
```

## After Setup — Google OAuth

Add this redirect URI in Google Cloud Console:
```
http://185.211.6.5/auth/callback
```

## After Setup — Kite Connect

1. Sign up at kite.trade → Create app → get API key + secret
2. Add to `.env`:
   ```
   KITE_API_KEY=your_key
   KITE_API_SECRET=your_secret
   ```
3. `systemctl restart stock-analyzer`
4. Uncomment options collector in crontab
