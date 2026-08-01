"""Daily post-market rolling retention for NIFTY market and feature data.

Only NIFTY/NIFTY50 predicates are eligible. MCX tables and rows are deliberately
absent. Deletes run in bounded batches so normal PostgreSQL vacuum can reclaim
space without one oversized transaction.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from logging_config import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

RETENTION_DAYS = 30
BATCH_SIZE = 50_000
ADVISORY_LOCK_ID = 684_930_030


@dataclass(frozen=True)
class RetentionTarget:
    table: str
    predicate: str
    uses_date_cutoff: bool = False


TARGETS = (
    RetentionTarget("option_chain", "instrument = 'NIFTY' AND ts < %s"),
    RetentionTarget("market_snapshot", "instrument = 'NIFTY' AND ts < %s"),
    RetentionTarget("nifty_futures", "ts < %s"),
    RetentionTarget("nse_ohlc", "symbol = 'NIFTY50' AND ts < %s"),
    RetentionTarget("derived_daily", "instrument = 'NIFTY' AND date < %s", True),
    RetentionTarget("nifty_features", "ts < %s"),
    RetentionTarget("nifty_expiry_features", "ts < %s"),
    RetentionTarget("predictions", "instrument = 'NIFTY' AND ts < %s"),
    RetentionTarget("pipeline_runs", "instrument = 'NIFTY' AND started_at < %s"),
    RetentionTarget("market_predictions", "instrument = 'NIFTY' AND run_at < %s"),
    RetentionTarget("market_outcomes", "instrument = 'NIFTY' AND recorded_at < %s"),
    RetentionTarget("intraday_trades", "instrument = 'NIFTY' AND trade_date < %s", True),
    RetentionTarget(
        "deployments",
        "instrument = 'NIFTY' AND status IN ('CANCELLED', 'CLOSED', 'FAILED') AND created_at < %s",
    ),
)


def _connect():
    import psycopg2

    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    return psycopg2.connect(database_url, application_name="nifty_30_day_retention")


def _table_exists(cur, table: str) -> bool:
    cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
    return cur.fetchone()[0] is not None


def _delete_batches(conn, target: RetentionTarget, cutoff) -> int:
    total = 0
    while True:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout = '5s'")
            cur.execute("SET LOCAL statement_timeout = '10min'")
            if not _table_exists(cur, target.table):
                conn.rollback()
                logger.info("[NIFTY-RETENTION] %s absent; skipping", target.table)
                return total
            cur.execute(
                f"""
                WITH victims AS (
                    SELECT ctid FROM {target.table}
                    WHERE {target.predicate}
                    LIMIT %s
                )
                DELETE FROM {target.table} t
                USING victims v
                WHERE t.ctid = v.ctid
                """,
                (cutoff, BATCH_SIZE),
            )
            deleted = cur.rowcount
        conn.commit()
        total += deleted
        if deleted < BATCH_SIZE:
            return total


def run(now: datetime | None = None, dry_run: bool = False) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    cutoff_ts = now - timedelta(days=RETENTION_DAYS)
    cutoff_date = cutoff_ts.date()
    deleted: dict[str, int] = {}

    conn = _connect()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK_ID,))
            if not cur.fetchone()[0]:
                raise RuntimeError("another NIFTY retention run is active")
        conn.commit()

        for target in TARGETS:
            cutoff = cutoff_date if target.uses_date_cutoff else cutoff_ts
            if dry_run:
                with conn.cursor() as cur:
                    if not _table_exists(cur, target.table):
                        count = 0
                    else:
                        cur.execute(
                            f"SELECT COUNT(*) FROM {target.table} WHERE {target.predicate}",
                            (cutoff,),
                        )
                        count = cur.fetchone()[0]
                conn.rollback()
            else:
                count = _delete_batches(conn, target, cutoff)
            deleted[target.table] = count
            logger.info(
                "[NIFTY-RETENTION] %s %s=%d",
                target.table,
                "candidates" if dry_run else "deleted",
                count,
            )

        logger.info(
            "[NIFTY-RETENTION] Complete dry_run=%s cutoff=%s total=%d",
            dry_run,
            cutoff_ts.isoformat(),
            sum(deleted.values()),
        )
        return deleted
    finally:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_ID,))
            conn.commit()
        except Exception:
            conn.rollback()
        conn.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Retain only the latest 30 days of NIFTY data")
    parser.add_argument("--dry-run", action="store_true", help="Count candidates without deleting")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
