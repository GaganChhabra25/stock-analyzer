"""Backfill causal NIFTY features from retained one-minute snapshots.

The job reads only NIFTY rows, selects the nearest non-expired weekly expiry
and ATM +/-10 strikes for each retained minute, and writes idempotently to the
two NIFTY feature tables. Historical samples are timestamped at minute close
and marked ``source_interval_seconds=60``. Future target/label columns are
deliberately left untouched.
"""

from __future__ import annotations

import argparse
import itertools
import logging
import os
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values

load_dotenv(Path(__file__).parent.parent / ".env")

from logging_config import configure_logging
from options.nifty_retention import ADVISORY_LOCK_ID, RETENTION_DAYS
from options.nifty_ws import (
    FEATURE_SCHEMA_SQL,
    IST,
    N_STRIKES,
    STRIKE_STEP,
    _feature_history,
    _previous_feature_totals,
    build_feature_payloads,
)

configure_logging()
logger = logging.getLogger(__name__)

SYMBOL = "NIFTY"
SOURCE_INTERVAL_SECONDS = 60

HISTORICAL_SNAPSHOTS_SQL = """
WITH snapshots AS (
    SELECT DISTINCT ON (date_trunc('minute', ms.ts))
           ms.ts, ms.spot_price, ms.vix,
           COALESCE(ms.atm_strike, ROUND(ms.spot_price / %s) * %s)::integer AS atm_strike
    FROM market_snapshot ms
    WHERE ms.instrument = 'NIFTY'
      AND ms.ts >= %s
      AND ms.ts < %s
    ORDER BY date_trunc('minute', ms.ts), ms.ts DESC
), nearest_expiry AS (
    SELECT snapshots.ts, MIN(oc.expiry) AS expiry
    FROM snapshots
    JOIN option_chain oc
      ON oc.ts = snapshots.ts
     AND oc.instrument = 'NIFTY'
     AND oc.expiry >= %s
    GROUP BY snapshots.ts
)
SELECT snapshots.ts, snapshots.spot_price, snapshots.vix, snapshots.atm_strike,
       oc.expiry, oc.strike, oc.option_type, oc.ltp, oc.bid, oc.ask,
       oc.oi, oc.oi_change, oc.volume, oc.iv, oc.delta, oc.gamma,
       oc.theta, oc.vega, oc.underlying_ltp
FROM snapshots
JOIN nearest_expiry
  ON nearest_expiry.ts = snapshots.ts
JOIN option_chain oc
  ON oc.ts = snapshots.ts
 AND oc.instrument = 'NIFTY'
 AND oc.expiry = nearest_expiry.expiry
 AND oc.strike BETWEEN snapshots.atm_strike - %s AND snapshots.atm_strike + %s
ORDER BY snapshots.ts, oc.strike, oc.option_type
"""


def _connect():
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    return psycopg2.connect(database_url, application_name="nifty_minute_feature_backfill")


def _minute_close(source_ts: datetime) -> datetime:
    """Return a causal, regular minute-close timestamp for a source snapshot."""
    local_ts = source_ts.astimezone(IST)
    return local_ts.replace(second=0, microsecond=0) + timedelta(minutes=1)


def _market_from_option_rows(spot: float, vix, atm: int, option_rows: list[tuple]) -> dict:
    calls = {int(row[3]): float(row[8] or 0) for row in option_rows if row[4] == "CE"}
    puts = {int(row[3]): float(row[8] or 0) for row in option_rows if row[4] == "PE"}
    total_call = sum(calls.values())
    total_put = sum(puts.values())
    option_by_key = {(int(row[3]), row[4]): row for row in option_rows}
    atm_ce = option_by_key.get((atm, "CE"))
    atm_pe = option_by_key.get((atm, "PE"))
    straddle = (
        float(atm_ce[5]) + float(atm_pe[5])
        if atm_ce and atm_pe and atm_ce[5] is not None and atm_pe[5] is not None
        else None
    )
    return {
        "spot": spot,
        "vix": float(vix) if vix is not None else None,
        "atm": atm,
        "pcr": total_put / total_call if total_call else None,
        "straddle": straddle,
        "expected_move": straddle / spot * 100 if straddle and spot else None,
        "call_wall": max(calls, key=calls.get) if calls else None,
        "put_wall": max(puts, key=puts.get) if puts else None,
    }


def _option_tuple(feature_ts: datetime, row: tuple) -> tuple:
    return (
        feature_ts,
        SYMBOL,
        row[4],
        int(row[5]),
        row[6],
        row[7],
        row[8],
        row[9],
        row[10],
        row[11],
        row[12],
        row[13],
        row[14],
        row[15],
        row[16],
        row[17],
        row[18] if row[18] is not None else row[1],
    )


def _bulk_upsert(cur, table: str, payloads: list[dict], conflict_columns: tuple[str, ...]) -> None:
    if not payloads:
        return
    columns = tuple(payloads[0])
    if any(tuple(payload) != columns for payload in payloads):
        raise RuntimeError(f"inconsistent {table} payload columns")
    updates = ", ".join(
        f"{column} = EXCLUDED.{column}" for column in columns if column not in conflict_columns
    )
    execute_values(
        cur,
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES %s "
        f"ON CONFLICT ({', '.join(conflict_columns)}) DO UPDATE SET {updates}",
        [tuple(payload[column] for column in columns) for payload in payloads],
        page_size=500,
    )


def _fetch_day_rows(
    conn,
    trade_date: date,
    cutoff: datetime,
    end: datetime,
) -> list[tuple]:
    day_start = max(datetime.combine(trade_date, time.min, tzinfo=IST), cutoff)
    day_end = min(datetime.combine(trade_date, time.min, tzinfo=IST) + timedelta(days=1), end)
    distance = N_STRIKES * STRIKE_STEP
    with conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '10min'")
        cur.execute(
            HISTORICAL_SNAPSHOTS_SQL,
            (STRIKE_STEP, STRIKE_STEP, day_start, day_end, trade_date, distance, distance),
        )
        return cur.fetchall()


def _available_dates(conn, cutoff: datetime, end: datetime) -> list[date]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT (ts AT TIME ZONE 'Asia/Kolkata')::date
            FROM market_snapshot
            WHERE instrument = 'NIFTY' AND ts >= %s AND ts < %s
            ORDER BY 1
            """,
            (cutoff, end),
        )
        return [row[0] for row in cur.fetchall()]


def _build_day_payloads(rows: list[tuple]) -> tuple[list[dict], list[dict]]:
    _feature_history.clear()
    _previous_feature_totals.clear()
    nifty_payloads: list[dict] = []
    expiry_payloads: list[dict] = []

    for _source_ts, grouped in itertools.groupby(rows, key=lambda row: row[0]):
        snapshot_rows = list(grouped)
        feature_ts = _minute_close(snapshot_rows[0][0])
        spot = float(snapshot_rows[0][1] or snapshot_rows[0][18] or 0)
        if spot <= 0:
            continue
        atm = int(snapshot_rows[0][3])
        option_rows = [_option_tuple(feature_ts, row) for row in snapshot_rows]
        market = _market_from_option_rows(spot, snapshot_rows[0][2], atm, option_rows)
        nifty, expiry = build_feature_payloads(feature_ts, option_rows, market)
        if not nifty or not expiry:
            continue
        nifty["source_interval_seconds"] = SOURCE_INTERVAL_SECONDS
        expiry["source_interval_seconds"] = SOURCE_INTERVAL_SECONDS
        nifty_payloads.append(nifty)
        expiry_payloads.append(expiry)

    return nifty_payloads, expiry_payloads


def run(now: datetime | None = None, dry_run: bool = False) -> dict[str, int]:
    now = (now or datetime.now(IST)).astimezone(IST)
    if now.weekday() < 5 and time(9, 14) <= now.time() <= time(15, 31):
        raise RuntimeError("refusing to backfill during NSE market hours")

    cutoff = now - timedelta(days=RETENTION_DAYS)
    end = now
    conn = _connect()
    conn.autocommit = False
    totals = {"dates": 0, "source_minutes": 0, "nifty_features": 0, "nifty_expiry_features": 0}
    locked = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK_ID,))
            locked = bool(cur.fetchone()[0])
        conn.commit()
        if not locked:
            raise RuntimeError("NIFTY retention/backfill job is already active")

        if not dry_run:
            with conn.cursor() as cur:
                cur.execute(FEATURE_SCHEMA_SQL)
            conn.commit()

        dates = _available_dates(conn, cutoff, end)
        conn.rollback()
        totals["dates"] = len(dates)

        for trade_date in dates:
            rows = _fetch_day_rows(conn, trade_date, cutoff, end)
            nifty_payloads, expiry_payloads = _build_day_payloads(rows)
            totals["source_minutes"] += len(nifty_payloads)
            if dry_run:
                conn.rollback()
            else:
                with conn.cursor() as cur:
                    cur.execute("SET LOCAL lock_timeout = '5s'")
                    _bulk_upsert(cur, "nifty_features", nifty_payloads, ("ts",))
                    _bulk_upsert(
                        cur,
                        "nifty_expiry_features",
                        expiry_payloads,
                        ("ts", "expiry_date"),
                    )
                conn.commit()
            totals["nifty_features"] += len(nifty_payloads)
            totals["nifty_expiry_features"] += len(expiry_payloads)
            logger.info(
                "[NIFTY-BACKFILL] date=%s minutes=%d dry_run=%s",
                trade_date,
                len(nifty_payloads),
                dry_run,
            )

        logger.info("[NIFTY-BACKFILL] complete cutoff=%s totals=%s", cutoff.isoformat(), totals)
        return totals
    finally:
        _feature_history.clear()
        _previous_feature_totals.clear()
        try:
            conn.rollback()
            if locked:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_ID,))
                conn.commit()
        finally:
            conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill retained NIFTY features at one minute")
    parser.add_argument("--dry-run", action="store_true", help="Compute coverage without writing rows")
    args = parser.parse_args()
    print(run(dry_run=args.dry_run))
