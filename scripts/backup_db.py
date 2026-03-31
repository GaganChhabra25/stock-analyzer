"""
Database Backup Script
──────────────────────
Auto SSH tunnel (password-based) → pg_dump → D:\\Gagan\\database_backups

Usage:
    python scripts/backup_db.py                  # full + all tables
    python scripts/backup_db.py --full-only
    python scripts/backup_db.py --tables-only
"""

import os, sys, logging, argparse, subprocess, getpass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

try:
    from sshtunnel import SSHTunnelForwarder
except ImportError:
    print("Run once: pip install sshtunnel")
    sys.exit(1)

# ── Config ─────────────────────────────────────────────────────────────────────

BACKUP_ROOT = Path(r"D:\Gagan\database_backups")
PG_DUMP     = Path(r"C:\Program Files\PostgreSQL\13\bin\pg_dump.exe")
ENV_FILE    = Path(__file__).parent.parent / ".env"

SSH_HOST    = "185.211.6.5"
SSH_USER    = "root"
SSH_PORT    = 22
DB_PORT     = 5432        # PostgreSQL port on Contabo

KEEP_DAYS   = 30

TABLES = ["option_chain", "market_snapshot", "intraday_trades", "kite_tokens"]

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)
# Suppress noisy sshtunnel logs
logging.getLogger("paramiko").setLevel(logging.WARNING)
logging.getLogger("sshtunnel").setLevel(logging.WARNING)


# ── Helpers ───────────────────────────────────────────────────────────────────

def read_db_creds() -> dict:
    if not ENV_FILE.exists():
        log.error(".env not found: %s", ENV_FILE); sys.exit(1)
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("DATABASE_URL="):
            url = line.split("=", 1)[1].strip().strip('"\'')
            p = urlparse(url)
            return {
                "user":     p.username or "postgres",
                "password": p.password or "",
                "dbname":   p.path.lstrip("/"),
            }
    log.error("DATABASE_URL not found in .env"); sys.exit(1)


def human_size(path: Path) -> str:
    n = path.stat().st_size
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024: return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} GB"


def pg_dump(label: str, out: Path, extra: list, creds: dict, port: int) -> bool:
    out.parent.mkdir(parents=True, exist_ok=True)
    log.info("Dumping %-34s → %s", label, out.name)
    env = {**os.environ, "PGPASSWORD": creds["password"]}
    cmd = [
        str(PG_DUMP),
        "-h", "127.0.0.1", "-p", str(port),
        "-U", creds["user"],
        "-F", "c", "-f", str(out),
    ] + extra + [creds["dbname"]]

    r = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if r.returncode != 0:
        log.error("  FAILED: %s", r.stderr.strip()[:300])
        if out.exists(): out.unlink()
        return False
    log.info("  OK     %-34s  %s", label, human_size(out))
    return True


def cleanup_old():
    cutoff, n = datetime.now() - timedelta(days=KEEP_DAYS), 0
    for f in BACKUP_ROOT.rglob("*.backup"):
        if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
            f.unlink(); n += 1
    if n: log.info("Removed %d old backup(s)", n)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--full-only",   action="store_true")
    parser.add_argument("--tables-only", action="store_true")
    args = parser.parse_args()

    if not PG_DUMP.exists():
        log.error("pg_dump.exe not found: %s", PG_DUMP); sys.exit(1)

    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    creds    = read_db_creds()
    now      = datetime.now()
    stamp    = now.strftime("%Y%m%d_%H%M%S")
    date_str = now.strftime("%Y%m%d")

    log.info("=" * 55)
    log.info("Stock Analyzer DB Backup")
    log.info("Server      : %s@%s", SSH_USER, SSH_HOST)
    log.info("Database    : %s", creds["dbname"])
    log.info("Destination : %s", BACKUP_ROOT)
    log.info("=" * 55)

    # Ask SSH password
    ssh_pass = getpass.getpass(f"\nSSH password for {SSH_USER}@{SSH_HOST}: ")

    # Ask DB password (Contabo PostgreSQL)
    db_pass_input = getpass.getpass(f"DB  password for {creds['user']}@stock_analyzer : ")
    if db_pass_input:
        creds["password"] = db_pass_input
    print()

    # Open tunnel
    log.info("Opening SSH tunnel...")
    try:
        tunnel = SSHTunnelForwarder(
            (SSH_HOST, SSH_PORT),
            ssh_username=SSH_USER,
            ssh_password=ssh_pass,
            remote_bind_address=("127.0.0.1", DB_PORT),
        )
        tunnel.start()
    except Exception as e:
        log.error("Tunnel failed: %s", e)
        log.error("Check SSH credentials or server availability.")
        sys.exit(1)

    local_port = tunnel.local_bind_port
    log.info("Tunnel ready on localhost:%d", local_port)

    try:
        full_ok    = True
        tbl_ok     = 0
        tbl_fail   = 0

        if not args.tables_only:
            out = BACKUP_ROOT / "full" / f"stock_analyzer_{stamp}.backup"
            full_ok = pg_dump("FULL DATABASE", out, [], creds, local_port)

        if not args.full_only:
            for t in TABLES:
                out = BACKUP_ROOT / "tables" / f"{t}_{date_str}.backup"
                ok  = pg_dump(f"table: {t}", out, ["-t", t], creds, local_port)
                tbl_ok += ok; tbl_fail += not ok

    finally:
        tunnel.stop()
        log.info("SSH tunnel closed.")

    cleanup_old()

    total_mb = sum(f.stat().st_size for f in BACKUP_ROOT.rglob("*.backup")) / 1024 / 1024
    log.info("=" * 55)
    if not args.tables_only:
        log.info("Full backup   : %s", "OK ✓" if full_ok else "FAILED ✗")
    if not args.full_only:
        log.info("Table backups : %d OK  /  %d failed", tbl_ok, tbl_fail)
    log.info("Total size    : %.1f MB", total_mb)
    log.info("Saved to      : %s", BACKUP_ROOT)
    log.info("=" * 55)

    if not args.tables_only and not full_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
