"""
CRUDE collection-health check (Phase-1, see CRUDE_DATA_PHASE1_IMPLEMENTATION.md
in the trade-bot repo for the full design/report).

Read-only against existing tables (mcx_ohlc, mcx_futures_depth,
mcx_crude_option_pressure_second, option_chain). Writes exactly one summary
row per collector per run into crude_collection_health. Does not touch, and
cannot destabilize, the live WebSocket/collector processes -- it is a
separate, short-lived script meant to be triggered by cron, same pattern as
options/collector.py and options/global_prices.py.

Run (intended cadence: every 1 minute):
    python options/crude_collection_health.py

Health state is operational metadata only -- never a strategy signal (Part 7
of the Phase-1 spec). Thresholds below are fixed and documented, not tuned
from trading outcomes.
"""

import logging
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from screener.db import _get_conn
from options.crude_tick_aggregator import classify_session_phase
from logging_config import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
SYMBOL = "CRUDEOIL"

# Health-check window: look back this far for coverage/gap/staleness stats.
# Kept short (5 min) so this script stays cheap enough to run every minute
# without scanning large ranges of high-frequency tables.
WINDOW_MINUTES = 5

# CRUDE_DATA_REMEDIATION_20260921.md Issue 1/6: us_market (DXY/Gold/VIX/
# SP500F/NASDAQF/DOWF/US10Y, options/us_market.py) had ZERO coverage in this
# health monitor before this change -- confirmed by reading this file: no
# check block referenced the table at all, and a direct query of
# crude_collection_health during the 2026-09-21 first-live-session audit
# found no us_market rows in it at any checked_at. This is a real, provable
# monitoring gap (not a threshold-tuning change): the collector's own
# staleness was already honestly disclosed via its P0-3 received_at/
# data_age_ms/quality_flag columns, but nothing surfaced that into this
# table's operational view.
#
# us_market has its own schedule, deliberately different from the MCX
# session calendar above -- reusing classify_session_phase() here would be
# wrong (it would falsely flag DISCONNECTED for the ~19 hours/day this
# collector is correctly idle). The two active windows below are copied
# verbatim from the deployed crontab.docker entries (server-local CEST,
# converted to UTC bounds so this check does not depend on the server's own
# timezone setting):
#   5   5 * * 1-6     -> options/us_market.py            (daily backfill)
#   */5 15-22 * * 1-5 -> options/us_market.py --intraday (US market hours)
# 15-22 CEST = 13:00-20:00 UTC; daily run is a single 03:05 UTC tick, given
# a short grace window below since it is not a continuous-coverage source.
US_MARKET_INTRADAY_SYMBOLS = ("DXY", "GOLDF", "USVIX", "SP500F", "NASDAQF", "DOWF", "US10Y")
US_MARKET_INTRADAY_START_UTC = time(13, 0)   # 15:00 CEST
US_MARKET_INTRADAY_END_UTC = time(20, 0)     # 22:00 CEST
US_MARKET_DAILY_RUN_UTC = time(3, 5)         # 05:05 CEST, Mon-Sat
US_MARKET_DAILY_GRACE_MINUTES = 15           # allow the daily cron itself to finish
US_MARKET_STALE_MINUTES = 10.0               # > 2x the 5-min intraday cadence

# Conservative, documented thresholds -- not optimized from trading outcomes.
STALE_ROW_AGE_SECONDS = {
    "mcx_ohlc": 5.0,
    "mcx_futures_depth": 5.0,
    "mcx_crude_option_pressure_second": 3.0,   # matches OPTION_FRESHNESS_SECONDS-adjacent gate
    "option_chain": 90.0,                       # matches V2's own snapshot-freshness tolerance
    # Phase-2 (research-only, see CRUDE_DATA_PHASE2_IMPLEMENTATION.md):
    "mcx_ohlc_next_contract": 5.0,
    "mcx_futures_depth_next_contract": 5.0,
}
# Phase-2 1-minute external context: expected cadence is 1 row/minute per
# instrument, not 1/second -- staleness threshold is minutes, not seconds.
GLOBAL_PRICES_INTRADAY_STALE_MINUTES = 5.0
DEGRADED_COVERAGE_PCT = 70.0     # below this during LIVE/SPECIAL -> DEGRADED, not HEALTHY
STALE_STATUS_PCT = 50.0          # more than half the window's rows stale -> STALE
GAP_ALERT_SECONDS = 10.0         # a single gap this long or longer flags DEGRADED


def _ensure_health_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS crude_collection_health (
                id                      BIGSERIAL       PRIMARY KEY,
                checked_at              TIMESTAMPTZ     NOT NULL DEFAULT clock_timestamp(),
                collector_name          VARCHAR(60)     NOT NULL,
                instrument              VARCHAR(20)     NOT NULL DEFAULT 'CRUDEOIL',
                session_phase           VARCHAR(20),
                last_source_ts          TIMESTAMPTZ,
                last_db_write_at        TIMESTAMPTZ,
                source_age_ms           BIGINT,
                window_start            TIMESTAMPTZ,
                window_end              TIMESTAMPTZ,
                expected_seconds        INTEGER,
                received_seconds        INTEGER,
                coverage_pct            DOUBLE PRECISION,
                gap_count               INTEGER,
                largest_gap_seconds     DOUBLE PRECISION,
                stale_rows              INTEGER,
                stale_pct               DOUBLE PRECISION,
                websocket_connected     BOOLEAN,
                reconnect_count         INTEGER,
                status                  VARCHAR(20)     NOT NULL,
                status_reason           TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_crude_collection_health_lookup
                ON crude_collection_health (collector_name, checked_at DESC);
        """)
    conn.commit()


def _second_level_health(conn, table: str, ts_col: str, avail_col: str,
                          window_start: datetime, window_end: datetime,
                          session_phase: str, filter_instrument: bool = True) -> dict:
    """Coverage/gap/staleness for a per-second table over the window.

    Purely derived from already-stored timestamps -- no assumption about
    cadence beyond "one row per second is possible", which matches how
    mcx_ohlc/mcx_futures_depth/mcx_crude_option_pressure_second are written.

    filter_instrument=False for tables with no `instrument` column (found
    during Phase-2 validation: mcx_crude_option_pressure_second has none --
    it is inherently CRUDEOIL-only by construction, so no filter is needed
    or possible). Pre-existing bug, not introduced by Phase-2 -- see
    CRUDE_DATA_PHASE2_IMPLEMENTATION.md validation section.
    """
    with conn.cursor() as cur:
        if filter_instrument:
            cur.execute(f"""
                SELECT {ts_col}, {avail_col}
                FROM {table}
                WHERE instrument = %s AND {ts_col} >= %s AND {ts_col} < %s
                ORDER BY {ts_col}
            """, (SYMBOL, window_start, window_end))
        else:
            cur.execute(f"""
                SELECT {ts_col}, {avail_col}
                FROM {table}
                WHERE {ts_col} >= %s AND {ts_col} < %s
                ORDER BY {ts_col}
            """, (window_start, window_end))
        rows = cur.fetchall()

    received = len(rows)
    expected = int((window_end - window_start).total_seconds())

    last_source_ts = rows[-1][0] if rows else None
    last_db_write_at = rows[-1][1] if rows and rows[-1][1] is not None else None

    gap_count = 0
    largest_gap = 0.0
    prev_ts = None
    stale_rows = 0
    for ts, avail_at in rows:
        if prev_ts is not None:
            gap = (ts - prev_ts).total_seconds()
            if gap > 1.0:
                gap_count += 1
                largest_gap = max(largest_gap, gap)
        prev_ts = ts
        threshold = STALE_ROW_AGE_SECONDS.get(table, 5.0)
        if avail_at is not None and (avail_at - ts).total_seconds() > threshold:
            stale_rows += 1

    coverage_pct = (received / expected * 100.0) if expected > 0 else None
    stale_pct = (stale_rows / received * 100.0) if received > 0 else None
    source_age_ms = (
        max(0, round((datetime.now(IST) - last_source_ts).total_seconds() * 1000))
        if last_source_ts is not None else None
    )

    status, reason = _classify_status(
        session_phase=session_phase, coverage_pct=coverage_pct,
        stale_pct=stale_pct, largest_gap=largest_gap, received=received,
    )

    return {
        "last_source_ts": last_source_ts,
        "last_db_write_at": last_db_write_at,
        "source_age_ms": source_age_ms,
        "expected_seconds": expected,
        "received_seconds": received,
        "coverage_pct": coverage_pct,
        "gap_count": gap_count,
        "largest_gap_seconds": largest_gap if received else None,
        "stale_rows": stale_rows,
        "stale_pct": stale_pct,
        "status": status,
        "status_reason": reason,
    }


def _classify_status(*, session_phase: str, coverage_pct: Optional[float],
                      stale_pct: Optional[float], largest_gap: float,
                      received: int) -> tuple:
    """Deterministic status classification. Conservative, documented
    thresholds -- never tuned from trading outcomes (Part 7 requirement).
    """
    if session_phase in ("CLOSED",):
        return "NON_TRADING", "Outside MCX session per calendar."
    if session_phase == "WARMUP":
        return "PARTIAL_SESSION", "Pre-market warmup window; sparse/frozen data is expected."
    if received == 0:
        return "DISCONNECTED", "No rows received in the health-check window during an active session."
    if stale_pct is not None and stale_pct > STALE_STATUS_PCT:
        return "STALE", f"{stale_pct:.1f}% of rows exceeded the staleness threshold."
    if coverage_pct is not None and coverage_pct < DEGRADED_COVERAGE_PCT:
        return "DEGRADED", f"Coverage {coverage_pct:.1f}% below the {DEGRADED_COVERAGE_PCT}% floor."
    if largest_gap >= GAP_ALERT_SECONDS:
        return "DEGRADED", f"Largest gap {largest_gap:.0f}s >= {GAP_ALERT_SECONDS:.0f}s threshold."
    return "HEALTHY", "Coverage/staleness/gaps within documented thresholds."


def _us_market_active_window(now_utc: datetime) -> tuple:
    """Is options/us_market.py's own cron expected to be producing fresh
    rows right now? Pure function of wall-clock time; mirrors the deployed
    crontab.docker entries verbatim (see module-level comment above the
    US_MARKET_* constants) -- does not invent a new schedule.

    Returns (active: bool, reason: str). `active=False` means "no cron tick
    is due imminently" -- the caller must treat this like session_phase
    CLOSED (NON_TRADING) rather than DISCONNECTED, since zero rows during
    this window is the correct, expected state, not a failure.
    """
    weekday = now_utc.weekday()  # Monday=0 .. Sunday=6
    t = now_utc.time()

    if weekday <= 4 and US_MARKET_INTRADAY_START_UTC <= t <= US_MARKET_INTRADAY_END_UTC:
        return True, "Within intraday cron window (13:00-20:00 UTC Mon-Fri)."

    if weekday <= 5:
        daily_end = (datetime.combine(now_utc.date(), US_MARKET_DAILY_RUN_UTC)
                     + timedelta(minutes=US_MARKET_DAILY_GRACE_MINUTES)).time()
        if US_MARKET_DAILY_RUN_UTC <= t <= daily_end:
            return True, "Within daily-backfill cron grace window (03:05 UTC Mon-Sat)."

    return False, "Outside us_market's own cron windows (US market hours / daily backfill) -- idle by design."


def _insert_health_row(conn, collector_name: str, session_phase: str, metrics: dict,
                        window_start: datetime, window_end: datetime,
                        instrument: str = SYMBOL) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO crude_collection_health (
                collector_name, instrument, session_phase,
                last_source_ts, last_db_write_at, source_age_ms,
                window_start, window_end, expected_seconds, received_seconds,
                coverage_pct, gap_count, largest_gap_seconds,
                stale_rows, stale_pct, status, status_reason
            ) VALUES (
                %(collector_name)s, %(instrument)s, %(session_phase)s,
                %(last_source_ts)s, %(last_db_write_at)s, %(source_age_ms)s,
                %(window_start)s, %(window_end)s, %(expected_seconds)s, %(received_seconds)s,
                %(coverage_pct)s, %(gap_count)s, %(largest_gap_seconds)s,
                %(stale_rows)s, %(stale_pct)s, %(status)s, %(status_reason)s
            )
        """, {
            "collector_name": collector_name,
            "instrument": instrument,
            "session_phase": session_phase,
            "window_start": window_start,
            "window_end": window_end,
            **metrics,
        })
    conn.commit()


def _try_run_lock():
    """Return a held DB advisory-lock connection, or None when busy.

    Same non-blocking pg_try_advisory_lock(hashtext(...)) idiom already used
    by options/collector.py's `_try_symbol_lock()` (CRUDEOIL/NATURALGAS
    overlap protection) and options/global_prices_intraday.py's equivalent
    guard -- reused here, not invented, so a slow health-check pass (several
    sequential queries against high-row-count tables) cannot overlap with
    the next scheduled run.
    """
    import psycopg2
    from screener.db import is_available, _db_url

    if not is_available():
        return None
    conn = psycopg2.connect(_db_url())
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_try_advisory_lock(hashtext(%s))",
            ("crude_collection_health",),
        )
        acquired = bool(cur.fetchone()[0])
    if not acquired:
        conn.close()
        return None
    return conn


def run_once() -> None:
    lock_conn = _try_run_lock()
    if lock_conn is None:
        logger.info("[CRUDE-HEALTH] Previous run still in progress; skipping overlap.")
        return
    try:
        _run_once_locked()
    finally:
        try:
            with lock_conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", ("crude_collection_health",))
        finally:
            lock_conn.close()


def _run_once_locked() -> None:
    with _get_conn() as conn:
        if conn is None:
            logger.warning("[CRUDE-HEALTH] DB unavailable; skipping this cycle.")
            return
        _ensure_health_table(conn)

        from config import MCX_HOLIDAYS, MCX_EVENING_ONLY_DAYS
        now = datetime.now(IST)
        session_phase = classify_session_phase(now, MCX_HOLIDAYS, MCX_EVENING_ONLY_DAYS)
        window_end = now
        window_start = now - timedelta(minutes=WINDOW_MINUTES)

        # mcx_ohlc / mcx_futures_depth: one row per second, per collector.
        for table, ts_col, avail_col in [
            ("mcx_ohlc", "ts", "available_at"),
            ("mcx_futures_depth", "ts", "available_at"),
            ("mcx_crude_option_pressure_second", "ts", "available_at"),
        ]:
            metrics = _second_level_health(
                conn, table, ts_col, avail_col, window_start, window_end, session_phase,
                filter_instrument=(table != "mcx_crude_option_pressure_second"),
            )
            _insert_health_row(conn, table, session_phase, metrics, window_start, window_end)
            logger.info(
                "[CRUDE-HEALTH] %-40s status=%-12s coverage=%s stale=%s gap=%s",
                table, metrics["status"],
                f"{metrics['coverage_pct']:.1f}%" if metrics["coverage_pct"] is not None else "n/a",
                f"{metrics['stale_pct']:.1f}%" if metrics["stale_pct"] is not None else "n/a",
                f"{metrics['largest_gap_seconds']:.0f}s" if metrics["largest_gap_seconds"] else "0s",
            )

        # option_chain: 1-minute cadence table, not per-second -- coverage
        # is measured against expected 1-minute rows, not expected seconds.
        with conn.cursor() as cur:
            cur.execute("""
                SELECT ts, available_at FROM option_chain
                WHERE instrument = %s AND ts >= %s AND ts < %s
                ORDER BY ts
            """, (SYMBOL, window_start, window_end))
            rows = cur.fetchall()
        distinct_minutes = len({r[0].replace(second=0, microsecond=0) for r in rows}) if rows else 0
        expected_minutes = max(1, WINDOW_MINUTES)
        coverage_pct = distinct_minutes / expected_minutes * 100.0
        stale_rows = sum(
            1 for ts, avail_at in rows
            if avail_at is not None and (avail_at - ts).total_seconds() > STALE_ROW_AGE_SECONDS["option_chain"]
        )
        stale_pct = (stale_rows / len(rows) * 100.0) if rows else None
        last_source_ts = rows[-1][0] if rows else None
        status, reason = _classify_status(
            session_phase=session_phase, coverage_pct=coverage_pct,
            stale_pct=stale_pct, largest_gap=0.0, received=len(rows),
        )
        oc_metrics = {
            "last_source_ts": last_source_ts,
            "last_db_write_at": rows[-1][1] if rows else None,
            "source_age_ms": (
                max(0, round((now - last_source_ts).total_seconds() * 1000))
                if last_source_ts is not None else None
            ),
            "expected_seconds": None,
            "received_seconds": len(rows),
            "coverage_pct": coverage_pct,
            "gap_count": None,
            "largest_gap_seconds": None,
            "stale_rows": stale_rows,
            "stale_pct": stale_pct,
            "status": status,
            "status_reason": reason,
        }
        _insert_health_row(conn, "option_chain", session_phase, oc_metrics, window_start, window_end)
        logger.info("[CRUDE-HEALTH] option_chain status=%s rows=%d stale=%s",
                    status, len(rows), f"{stale_pct:.1f}%" if stale_pct is not None else "n/a")

        # Phase-2 (see CRUDE_DATA_PHASE2_IMPLEMENTATION.md): next-contract
        # per-second tables, checked with the same reusable per-second logic.
        # Each wrapped independently -- a missing/not-yet-created next-
        # contract table (e.g. before the first Phase-2 deploy has run) must
        # never prevent the existing Phase-1 checks above from having already
        # written their rows.
        for table in ("mcx_ohlc_next_contract", "mcx_futures_depth_next_contract"):
            try:
                metrics = _second_level_health(
                    conn, table, "ts", "available_at", window_start, window_end, session_phase
                )
                _insert_health_row(conn, table, session_phase, metrics, window_start, window_end)
                logger.info("[CRUDE-HEALTH] %-40s status=%-12s coverage=%s",
                            table, metrics["status"],
                            f"{metrics['coverage_pct']:.1f}%" if metrics["coverage_pct"] is not None else "n/a")
            except Exception as exc:
                logger.warning("[CRUDE-HEALTH] %s check failed (other checks unaffected): %s", table, exc)
                conn.rollback()

        # Phase-2: WTI/Brent/USDINR 1-minute external context. Minute-level
        # cadence, not per-second -- coverage measured against expected
        # 1-minute rows over the window, same pattern as option_chain above.
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT instrument, minute_ts, available_at
                    FROM global_prices_intraday
                    WHERE minute_ts >= %s AND minute_ts < %s
                    ORDER BY instrument, minute_ts
                """, (window_start, window_end))
                gp_rows = cur.fetchall()
            by_instrument: dict = {}
            for instrument, minute_ts, avail_at in gp_rows:
                by_instrument.setdefault(instrument, []).append((minute_ts, avail_at))

            expected_minutes = max(1, WINDOW_MINUTES)
            for instrument in ("WTI", "BRENT", "USDINR"):
                rows_i = by_instrument.get(instrument, [])
                received = len(rows_i)
                coverage_pct = received / expected_minutes * 100.0
                stale_rows = sum(
                    1 for ts, avail_at in rows_i
                    if avail_at is not None
                    and (avail_at - ts).total_seconds() > GLOBAL_PRICES_INTRADAY_STALE_MINUTES * 60
                )
                stale_pct = (stale_rows / received * 100.0) if received else None
                last_source_ts = rows_i[-1][0] if rows_i else None
                status, reason = _classify_status(
                    session_phase=session_phase, coverage_pct=coverage_pct,
                    stale_pct=stale_pct, largest_gap=0.0, received=received,
                )
                gp_metrics = {
                    "last_source_ts": last_source_ts,
                    "last_db_write_at": rows_i[-1][1] if rows_i else None,
                    "source_age_ms": (
                        max(0, round((now - last_source_ts).total_seconds() * 1000))
                        if last_source_ts is not None else None
                    ),
                    "expected_seconds": None,
                    "received_seconds": received,
                    "coverage_pct": coverage_pct,
                    "gap_count": None,
                    "largest_gap_seconds": None,
                    "stale_rows": stale_rows,
                    "stale_pct": stale_pct,
                    "status": status,
                    "status_reason": reason,
                }
                _insert_health_row(conn, "global_prices_intraday", session_phase, gp_metrics,
                                    window_start, window_end, instrument=instrument)
            logger.info("[CRUDE-HEALTH] global_prices_intraday checked for %d instrument(s).",
                        len(by_instrument))
        except Exception as exc:
            logger.warning("[CRUDE-HEALTH] global_prices_intraday check failed (other checks unaffected): %s", exc)
            conn.rollback()

        # CRUDE_DATA_REMEDIATION_20260921.md Issue 1/6: us_market -- see
        # US_MARKET_* constants above for why this is not simply reusing the
        # MCX session_phase. Wrapped independently, same isolation pattern
        # as the two blocks above -- a failure here can never affect any
        # check that already ran and committed its own row.
        try:
            now_utc = window_end.astimezone(timezone.utc)
            active, window_reason = _us_market_active_window(now_utc)
            window_start_utc = window_start.astimezone(timezone.utc)

            with conn.cursor() as cur:
                cur.execute("""
                    SELECT symbol, ts, received_at, data_age_ms, quality_flag
                    FROM us_market
                    WHERE interval = '5minute'
                      AND ts >= %s AND ts < %s
                    ORDER BY symbol, ts
                """, (window_start_utc, now_utc))
                um_rows = cur.fetchall()
            by_symbol: dict = {}
            for symbol, ts, received_at, data_age_ms, quality_flag in um_rows:
                by_symbol.setdefault(symbol, []).append((ts, received_at, data_age_ms, quality_flag))

            # Cadence is 1 row / 5 min per symbol -- over a WINDOW_MINUTES=5
            # lookback, 1 row is "full coverage" for this source.
            expected_per_symbol = max(1, WINDOW_MINUTES // 5)
            for symbol in US_MARKET_INTRADAY_SYMBOLS:
                rows_i = by_symbol.get(symbol, [])
                received = len(rows_i)
                coverage_pct = min(100.0, received / expected_per_symbol * 100.0)
                stale_rows = sum(
                    1 for ts, received_at, data_age_ms, quality_flag in rows_i
                    if data_age_ms is not None and data_age_ms > US_MARKET_STALE_MINUTES * 60 * 1000
                )
                stale_pct = (stale_rows / received * 100.0) if received else None
                last_source_ts = rows_i[-1][0] if rows_i else None

                if not active:
                    status, reason = "NON_TRADING", window_reason
                else:
                    status, reason = _classify_status(
                        session_phase="LIVE", coverage_pct=coverage_pct,
                        stale_pct=stale_pct, largest_gap=0.0, received=received,
                    )
                um_metrics = {
                    "last_source_ts": last_source_ts,
                    "last_db_write_at": rows_i[-1][1] if rows_i else None,
                    "source_age_ms": (
                        max(0, round((now_utc - last_source_ts).total_seconds() * 1000))
                        if last_source_ts is not None else None
                    ),
                    "expected_seconds": None,
                    "received_seconds": received,
                    "coverage_pct": coverage_pct,
                    "gap_count": None,
                    "largest_gap_seconds": None,
                    "stale_rows": stale_rows,
                    "stale_pct": stale_pct,
                    "status": status,
                    "status_reason": reason,
                }
                _insert_health_row(conn, "us_market", session_phase, um_metrics,
                                    window_start, window_end, instrument=symbol)
            logger.info("[CRUDE-HEALTH] us_market checked for %d symbol(s), active_window=%s.",
                        len(US_MARKET_INTRADAY_SYMBOLS), active)
        except Exception as exc:
            logger.warning("[CRUDE-HEALTH] us_market check failed (other checks unaffected): %s", exc)
            conn.rollback()


if __name__ == "__main__":
    run_once()
