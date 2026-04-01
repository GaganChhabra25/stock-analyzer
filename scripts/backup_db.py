"""
Database Backup Script — Direct Connection (psycopg2, version-agnostic)
────────────────────────────────────────────────────────────────────────
Connects directly to Contabo PostgreSQL and dumps tables to CSV
in D:\\Gagan\\database_backups

Usage:
    python scripts/backup_db.py                  # all tables
    python scripts/backup_db.py --tables OPTION_CHAIN MARKET_SNAPSHOT
"""

import os, sys, io, logging, argparse
from datetime import datetime, timedelta
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

# ── Contabo DB config ──────────────────────────────────────────────────────────

DB_HOST     = "185.211.6.5"
DB_PORT     = 5432
DB_NAME     = "stock_analyzer"
DB_USER     = "stockanalyzer"
DB_PASSWORD = os.environ["CONTABO_DB_PASSWORD"]

BACKUP_ROOT = Path(r"D:\Gagan\database_backups")
KEEP_DAYS   = 30

TABLES = ["option_chain", "market_snapshot", "intraday_trades", "kite_tokens"]

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def human_size(path: Path) -> str:
    n = path.stat().st_size
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024: return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} GB"


def get_conn():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD,
        connect_timeout=15,
    )


def dump_table(conn, table: str, out: Path) -> bool:
    """Dump a single table to CSV using COPY TO STDOUT."""
    out.parent.mkdir(parents=True, exist_ok=True)
    log.info("Dumping %-30s → %s", table, out.name)

    try:
        with conn.cursor() as cur:
            with open(out, "w", encoding="utf-8", newline="") as f:
                # Write header
                cur.execute(f"SELECT * FROM {table} LIMIT 0")
                cols = [d[0] for d in cur.description]
                f.write(",".join(cols) + "\n")

            # Use COPY for fast bulk export
            with open(out, "ab") as f:
                cur.copy_expert(
                    f"COPY {table} TO STDOUT WITH (FORMAT CSV, HEADER FALSE, ENCODING 'UTF8')",
                    f,
                )

        log.info("  OK  %-30s  %s", table, human_size(out))
        return True

    except Exception as e:
        log.error("  FAILED  %s: %s", table, str(e)[:300])
        if out.exists():
            out.unlink()
        return False


def cleanup_old():
    cutoff, n = datetime.now() - timedelta(days=KEEP_DAYS), 0
    for f in BACKUP_ROOT.rglob("*.csv"):
        if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
            f.unlink(); n += 1
    if n:
        log.info("Removed %d backup(s) older than %d days", n, KEEP_DAYS)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tables", nargs="+", metavar="TABLE",
                        help="Specific tables to back up (default: all)")
    args = parser.parse_args()

    tables = [t.lower() for t in args.tables] if args.tables else TABLES

    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)

    now      = datetime.now()
    date_str = now.strftime("%Y%m%d_%H%M%S")

    log.info("=" * 55)
    log.info("Stock Analyzer DB Backup")
    log.info("Server      : %s:%s", DB_HOST, DB_PORT)
    log.info("Database    : %s  (user: %s)", DB_NAME, DB_USER)
    log.info("Tables      : %s", ", ".join(tables))
    log.info("Destination : %s", BACKUP_ROOT)
    log.info("=" * 55)

    log.info("Connecting to %s:%s ...", DB_HOST, DB_PORT)
    try:
        conn = get_conn()
        log.info("Connected.")
    except Exception as e:
        log.error("Connection failed: %s", e)
        sys.exit(1)

    ok_count = fail_count = 0

    try:
        for table in tables:
            out = BACKUP_ROOT / f"{table}_{date_str}.csv"
            success = dump_table(conn, table, out)
            if success:
                ok_count += 1
            else:
                fail_count += 1
    finally:
        conn.close()

    cleanup_old()

    total_mb = sum(f.stat().st_size for f in BACKUP_ROOT.rglob("*.csv")) / 1024 / 1024

    log.info("=" * 55)
    log.info("Backups : %d OK  /  %d failed", ok_count, fail_count)
    log.info("Total   : %.1f MB in %s", total_mb, BACKUP_ROOT)
    log.info("=" * 55)

    if fail_count:
        sys.exit(1)


if __name__ == "__main__":
    main()
