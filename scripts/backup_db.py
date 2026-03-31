"""
Database Backup Script
──────────────────────
Reads DATABASE_URL from .env, runs pg_dump, saves to D:\\Gagan\\database_backups

Backups created:
  1. Full database dump        → full/stock_analyzer_YYYYMMDD_HHMMSS.backup
  2. option_chain table        → tables/option_chain_YYYYMMDD.backup
  3. market_snapshot table     → tables/market_snapshot_YYYYMMDD.backup
  4. intraday_trades table     → tables/intraday_trades_YYYYMMDD.backup

Retention: keeps last 30 days, older files auto-deleted.

Run:
    python scripts/backup_db.py
    python scripts/backup_db.py --tables-only   # skip full dump (faster)
    python scripts/backup_db.py --full-only     # only full dump
"""

import os
import re
import sys
import shutil
import logging
import argparse
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

# ── Config ─────────────────────────────────────────────────────────────────────

BACKUP_ROOT  = Path(r"D:\Gagan\database_backups")
PG_DUMP      = Path(r"C:\Program Files\PostgreSQL\13\bin\pg_dump.exe")
ENV_FILE     = Path(__file__).parent.parent / ".env"
KEEP_DAYS    = 30

IMPORTANT_TABLES = [
    "option_chain",
    "market_snapshot",
    "intraday_trades",
    "kite_tokens",
]

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_database_url() -> str:
    """Parse DATABASE_URL from .env file."""
    if not ENV_FILE.exists():
        log.error(".env file not found at %s", ENV_FILE)
        sys.exit(1)

    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("DATABASE_URL="):
            url = line.split("=", 1)[1].strip().strip('"').strip("'")
            return url

    log.error("DATABASE_URL not found in .env")
    sys.exit(1)


def _parse_url(url: str) -> dict:
    """Parse postgresql://user:pass@host:port/dbname into parts."""
    p = urlparse(url)
    return {
        "host":     p.hostname or "localhost",
        "port":     str(p.port or 5432),
        "user":     p.username or "postgres",
        "password": p.password or "",
        "dbname":   p.path.lstrip("/"),
    }


def _human_size(path: Path) -> str:
    size = path.stat().st_size
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _run_dump(args: list, env: dict, label: str, out_path: Path) -> bool:
    """Run pg_dump subprocess. Returns True on success."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    log.info("Dumping %-35s → %s", label, out_path.name)

    result = subprocess.run(
        args,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        log.error("FAILED: %s\n%s", label, result.stderr[:500])
        if out_path.exists():
            out_path.unlink()
        return False

    log.info("  Done  %-35s  %s", label, _human_size(out_path))
    return True


def _cleanup_old(folder: Path, keep_days: int):
    """Delete .backup files older than keep_days."""
    cutoff = datetime.now() - timedelta(days=keep_days)
    deleted = 0
    for f in folder.rglob("*.backup"):
        if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
            f.unlink()
            deleted += 1
    if deleted:
        log.info("Cleaned up %d old backup(s) (>%d days)", deleted, keep_days)


# ── Core backup functions ─────────────────────────────────────────────────────

def backup_full(creds: dict, stamp: str) -> bool:
    """Full database dump in custom format (compressed, restorable)."""
    out = BACKUP_ROOT / "full" / f"stock_analyzer_{stamp}.backup"
    env = {"PGPASSWORD": creds["password"]}
    args = [
        str(PG_DUMP),
        "-h", creds["host"],
        "-p", creds["port"],
        "-U", creds["user"],
        "-F", "c",          # custom format (compressed)
        "-f", str(out),
        creds["dbname"],
    ]
    return _run_dump(args, env, "FULL DATABASE", out)


def backup_table(creds: dict, table: str, date_str: str) -> bool:
    """Single table dump — schema + data, custom format."""
    out = BACKUP_ROOT / "tables" / f"{table}_{date_str}.backup"
    env = {"PGPASSWORD": creds["password"]}
    args = [
        str(PG_DUMP),
        "-h", creds["host"],
        "-p", creds["port"],
        "-U", creds["user"],
        "-F", "c",
        "-t", table,
        "-f", str(out),
        creds["dbname"],
    ]
    return _run_dump(args, env, f"table:{table}", out)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="PostgreSQL backup script")
    parser.add_argument("--tables-only", action="store_true",
                        help="Skip full dump, only backup individual tables")
    parser.add_argument("--full-only",   action="store_true",
                        help="Only full dump, skip individual tables")
    parser.add_argument("--port", type=str, default=None,
                        help="Override DB port (e.g. 5433 when using SSH tunnel)")
    args = parser.parse_args()

    # Sanity checks
    if not PG_DUMP.exists():
        log.error("pg_dump not found at %s", PG_DUMP)
        log.error("Install PostgreSQL client tools from https://www.postgresql.org/download/windows/")
        sys.exit(1)

    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)

    url   = _read_database_url()
    creds = _parse_url(url)

    # Port override — used when SSH tunnel runs on a different port
    if args.port:
        creds["port"] = args.port
        log.info("Port overridden to %s (SSH tunnel)", args.port)

    now       = datetime.now()
    stamp     = now.strftime("%Y%m%d_%H%M%S")   # e.g. 20260331_143022
    date_str  = now.strftime("%Y%m%d")           # e.g. 20260331

    log.info("=" * 55)
    log.info("DB Backup  →  %s", BACKUP_ROOT)
    log.info("Database   :  %s @ %s:%s", creds["dbname"], creds["host"], creds["port"])
    log.info("=" * 55)

    # SSH tunnel reminder if host is localhost
    if creds["host"] in ("localhost", "127.0.0.1"):
        log.info("NOTE: DATABASE_URL points to localhost.")
        log.info("      If DB is on Contabo, make sure SSH tunnel is active:")
        log.info("      ssh -L 5433:localhost:5432 user@CONTABO_IP -N")
        log.info("")

    full_ok = False
    table_results = []

    # Full dump
    if not args.tables_only:
        full_ok = backup_full(creds, stamp)

    # Table dumps
    if not args.full_only:
        for table in IMPORTANT_TABLES:
            table_results.append((table, backup_table(creds, table, date_str)))

    # Cleanup old backups
    _cleanup_old(BACKUP_ROOT, KEEP_DAYS)

    # Summary
    log.info("=" * 55)
    table_ok   = sum(1 for _, s in table_results if s)
    table_fail = len(table_results) - table_ok

    if not args.tables_only:
        log.info("Full backup   : %s", "OK" if full_ok else "FAILED")
    if not args.full_only:
        log.info("Table backups : %d OK  /  %d not found (may not exist yet)", table_ok, table_fail)

    log.info("Saved to      : %s", BACKUP_ROOT)
    total = sum(f.stat().st_size for f in BACKUP_ROOT.rglob("*.backup"))
    log.info("Total size    : %.1f MB", total / 1024 / 1024)

    # Exit with error only if full backup failed
    if not args.tables_only and not full_ok:
        log.error("Full backup FAILED — check connection / SSH tunnel")
        sys.exit(1)


if __name__ == "__main__":
    main()
