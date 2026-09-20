"""
WTI / Brent / USDINR 1-minute intraday collector (Phase-2, additive).
See CRUDE_DATA_PHASE2_IMPLEMENTATION.md (trade-bot repo) for the full report.

Separate from global_prices.py (which stays daily-only, unmodified) and from
us_market.py (which already covers DXY/Gold/US VIX at 5-minute cadence for
V1 -- reused as-is, not duplicated here; see the Phase-2 report's DXY/GOLD/VIX
section for why).

Source: yfinance (existing dependency, unofficial, no API key). Feasibility
audit (CRUDE_DATA_COLLECTION_FEASIBILITY.md) already established:
  - Yahoo's own 1-minute intraday history is capped at roughly the last 7
    days regardless of when this collector starts -- a real vendor ceiling,
    not a bug. Live-forward collection accumulates history beyond that cap.
  - Commodity/FX quotes from yfinance are delayed, not real-time, and this
    has not been independently re-verified against Yahoo's current terms
    this session -- every row is conservatively tagged 'DELAYED' rather than
    claiming a freshness Yahoo does not document.

Does NOT forward-fill missing minutes. A minute yfinance does not return is
simply not inserted -- gap/coverage detection belongs in the health-monitor
job (crude_collection_health.py), not in fabricated rows here.

Run (intended cadence: every 1 minute during the MCX 09:00-23:30 IST
session -- see report for the exact recommended crontab line; NOT installed
by this change):
    python options/global_prices_intraday.py
"""

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from screener.db import _get_conn
from logging_config import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

# symbol_name -> Yahoo Finance ticker. Only the P1 external-context set
# (WTI/Brent/USDINR) -- NatGas/DXY/Gold/VIX are explicitly out of scope for
# this collector (see report: NatGas wasn't in the approved P1 list; DXY/
# Gold/VIX are reused from the existing us_market table instead).
SYMBOLS = {
    "WTI":    "CL=F",
    "BRENT":  "BZ=F",
    "USDINR": "USDINR=X",
}

# Conservative, documented (not tuned from trading outcomes): a row whose
# own bar-close time is already this old by the time we observed it is
# tagged STALE instead of the default DELAYED classification.
STALE_AGE_MS_THRESHOLD = 15 * 60 * 1000  # 15 minutes

QUALITY_DELAYED = "DELAYED"
QUALITY_STALE = "STALE"


def classify_price_quality(data_age_ms: Optional[int]) -> str:
    """Pure, deterministic. See module docstring for why 'DELAYED' (never
    'REALTIME') is the default rather than a claim this system cannot
    verify against Yahoo's actual, undocumented latency behavior.
    """
    if data_age_ms is not None and data_age_ms > STALE_AGE_MS_THRESHOLD:
        return QUALITY_STALE
    return QUALITY_DELAYED


_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS global_prices_intraday (
        minute_ts     TIMESTAMPTZ NOT NULL,
        instrument    VARCHAR(20) NOT NULL,
        open          NUMERIC(14,4),
        high          NUMERIC(14,4),
        low           NUMERIC(14,4),
        close         NUMERIC(14,4),
        volume        BIGINT,
        source        VARCHAR(20) NOT NULL DEFAULT 'yfinance',
        source_ts     TIMESTAMPTZ,
        received_at   TIMESTAMPTZ NOT NULL,
        data_age_ms   BIGINT,
        quality_flag  VARCHAR(20),
        available_at  TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (minute_ts, instrument)
    );
    CREATE INDEX IF NOT EXISTS idx_global_prices_intraday_instrument
        ON global_prices_intraday (instrument, minute_ts DESC);
"""


def _ensure_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(_TABLE_SQL)
    conn.commit()


def _upsert(conn, rows: list) -> int:
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO global_prices_intraday
                (minute_ts, instrument, open, high, low, close, volume,
                 source, source_ts, received_at, data_age_ms, quality_flag)
            VALUES
                (%(minute_ts)s, %(instrument)s, %(open)s, %(high)s, %(low)s,
                 %(close)s, %(volume)s, %(source)s, %(source_ts)s,
                 %(received_at)s, %(data_age_ms)s, %(quality_flag)s)
            ON CONFLICT (minute_ts, instrument) DO UPDATE SET
                open         = EXCLUDED.open,
                high         = EXCLUDED.high,
                low          = EXCLUDED.low,
                close        = EXCLUDED.close,
                volume       = EXCLUDED.volume,
                source_ts    = EXCLUDED.source_ts,
                received_at  = EXCLUDED.received_at,
                data_age_ms  = EXCLUDED.data_age_ms,
                quality_flag = EXCLUDED.quality_flag
        """, rows)
    conn.commit()
    return len(rows)


def _rows_for_symbol(df, instrument: str, observed_at: datetime) -> list:
    rows = []
    for ts, row in df.iterrows():
        if hasattr(ts, "to_pydatetime"):
            ts = ts.to_pydatetime()
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        close = row.get("Close")
        if close is None or (hasattr(close, "__float__") and str(close) == "nan"):
            continue

        minute_ts = ts.replace(second=0, microsecond=0)
        data_age_ms = max(0, round((observed_at - minute_ts).total_seconds() * 1000))
        volume = row.get("Volume")
        try:
            volume = int(volume) if volume is not None and str(volume) != "nan" else None
        except (TypeError, ValueError):
            volume = None
        rows.append({
            "minute_ts":   minute_ts,
            "instrument":  instrument,
            "open":        float(row.get("Open") or close),
            "high":        float(row.get("High") or close),
            "low":         float(row.get("Low") or close),
            "close":       float(close),
            "volume":      volume,
            "source":      "yfinance",
            "source_ts":   minute_ts,
            "received_at": observed_at,
            "data_age_ms": data_age_ms,
            "quality_flag": classify_price_quality(data_age_ms),
        })
    return rows


def _try_run_lock():
    """Return a held DB advisory-lock connection, or None when busy.

    Same non-blocking pg_try_advisory_lock(hashtext(...)) idiom already used
    by options/collector.py's `_try_symbol_lock()` for CRUDEOIL/NATURALGAS
    overlap protection -- reused here rather than inventing a new mechanism,
    so a slow/hung yfinance call in one scheduled run cannot overlap with
    the next minute's run. Held for the whole fetch_and_store() cycle, same
    as collector.py's usage, so a plain `with _get_conn()` (commits/closes
    on exit) cannot be used for the lock connection itself.
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
            ("global_prices_intraday",),
        )
        acquired = bool(cur.fetchone()[0])
    if not acquired:
        conn.close()
        return None
    return conn


def fetch_and_store() -> int:
    try:
        import yfinance as yf
    except ImportError:
        logger.error("[GLOBAL-INTRADAY] yfinance not installed.")
        return 0

    lock_conn = _try_run_lock()
    if lock_conn is None:
        logger.info("[GLOBAL-INTRADAY] Previous run still in progress; skipping overlap.")
        return 0

    try:
        return _fetch_and_store_locked(yf)
    finally:
        try:
            with lock_conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", ("global_prices_intraday",))
        finally:
            lock_conn.close()


def _fetch_and_store_locked(yf) -> int:
    observed_at = datetime.now(timezone.utc)
    total = 0

    for instrument, ticker in SYMBOLS.items():
        try:
            df = yf.download(
                ticker,
                period="1d",
                interval="1m",
                progress=False,
                auto_adjust=True,
            )
            if df.empty:
                logger.debug("[GLOBAL-INTRADAY] No 1m data for %s (%s)", instrument, ticker)
                continue
            if hasattr(df.columns, "levels"):
                df.columns = df.columns.get_level_values(0)
        except Exception as exc:
            logger.error("[GLOBAL-INTRADAY] yfinance failed for %s (%s): %s", instrument, ticker, exc)
            continue

        rows = _rows_for_symbol(df, instrument, observed_at)
        if not rows:
            continue

        try:
            with _get_conn() as conn:
                if conn is None:
                    logger.warning("[GLOBAL-INTRADAY] DB unavailable; skipping %s this cycle.", instrument)
                    continue
                _ensure_table(conn)
                n = _upsert(conn, rows)
                total += n
                logger.info("[GLOBAL-INTRADAY] %s (%s): upserted %d rows.", instrument, ticker, n)
        except Exception as exc:
            logger.error("[GLOBAL-INTRADAY] DB write failed for %s (other instruments unaffected): %s",
                         instrument, exc)

    return total


if __name__ == "__main__":
    n = fetch_and_store()
    logger.info("[GLOBAL-INTRADAY] Done — %d rows total.", n)
