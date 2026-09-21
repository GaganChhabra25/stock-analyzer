"""
US markets data collector — S&P500 / Nasdaq / Dow futures, DXY, US VIX, US 10Y yield.

Source: yfinance (free, no auth required)

Modes:
  --intraday : fetch latest 5-min candles for all symbols (runs every 5 min via cron)
  default    : backfill last 60 days of daily bars (runs at 08:35 IST Mon-Sat)

Cron:
  5   5 * * 1-6    options/us_market.py            (daily, Mon-Sat)
  */5 15-22 * * 1-5 options/us_market.py --intraday (US market hours CEST)

Table: us_market (ts, symbol, interval, open, high, low, close, volume)

Symbols:
  SP500F  → ES=F       (S&P 500 E-mini futures, 24h)
  NASDAQF → NQ=F       (Nasdaq 100 E-mini futures, 24h)
  DOWF    → YM=F       (Dow Jones E-mini futures, 24h)
  DXY     → DX-Y.NYB   (US Dollar Index)
  USVIX   → ^VIX       (CBOE Volatility Index)
  US10Y   → ^TNX       (US 10-Year Treasury Yield)
  GOLDF   → GC=F       (Gold futures, international benchmark)
"""

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from options.tg import once, db_size, table_rows, now_ist
from screener.db import _get_conn
from logging_config import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

SYMBOLS = {
    "SP500F":  "ES=F",
    "NASDAQF": "NQ=F",
    "DOWF":    "YM=F",
    "DXY":     "DX-Y.NYB",
    "USVIX":   "^VIX",
    "US10Y":   "^TNX",
    "GOLDF":   "GC=F",
}

# P0-3 (CRUDE_DATA_FORENSIC_CERTIFICATION.md, 2026-09-20): us_market had zero
# PIT/availability-clock mechanism -- only `ts` (the bar's own close time),
# never when this process actually observed/wrote the row. These 3 columns
# are additive/nullable (see _ensure_columns below); historical rows stay
# NULL forever (never backfilled/guessed). Only genuinely NEW rows inserted
# after this deploy get a real, honestly-knowable received_at -- the
# ON CONFLICT branch below deliberately excludes them so a later re-run of
# the 60-day daily backfill (or the rolling 1-day intraday window) can never
# bump an already-known row's received_at forward to "now" and quietly
# understate its true age. Quality tag mirrors global_prices_intraday.py's
# already-reviewed convention: yfinance is DELAYED, never claimed REALTIME.
QUALITY_DELAYED = "DELAYED"

_ALTER_SQL = """
    ALTER TABLE us_market ADD COLUMN IF NOT EXISTS received_at  TIMESTAMPTZ;
    ALTER TABLE us_market ADD COLUMN IF NOT EXISTS data_age_ms  BIGINT;
    ALTER TABLE us_market ADD COLUMN IF NOT EXISTS quality_flag VARCHAR(20);
    ALTER TABLE us_market ADD COLUMN IF NOT EXISTS last_revised_at TIMESTAMPTZ;
    ALTER TABLE us_market ADD COLUMN IF NOT EXISTS revision_count INTEGER NOT NULL DEFAULT 0;
"""
# PIT follow-up (CRUDE_DATA_REMEDIATION_20260921.md investigation): P0-3
# froze `received_at` to first-write time, but the ON CONFLICT branch still
# blindly overwrote `close`/OHLC on every re-fetch -- so a provider-side
# correction (or a yfinance re-download landing a different value for the
# same (ts, symbol, interval)) could silently change what `close` means for
# an already-"known" row without leaving any trace that a revision happened.
# A downstream feature computed as of the original `received_at` would then
# see the *revised* value as if it had always been knowable at that time --
# a lookahead/repaint risk. Smallest safe fix: never touch `received_at`
# (already true), and add `last_revised_at`/`revision_count` so a genuine
# value change is explicitly logged instead of silently applied. Consumers
# that need strict causal visibility (see mcx_feature_pipeline.py's
# `_fetch_intraday_markets()`) can then gate on
# GREATEST(received_at, last_revised_at) instead of `received_at` alone.


def _revision_fields(
    existing_close: Optional[float],
    incoming_close: float,
    existing_last_revised_at: Optional[datetime],
    existing_revision_count: int,
    incoming_received_at: datetime,
) -> tuple[Optional[datetime], int]:
    """Pure, unit-tested mirror of the `ON CONFLICT ... DO UPDATE` CASE
    clauses in `_upsert()`'s SQL below (see `last_revised_at`/
    `revision_count`). Not called at runtime -- the live upsert always goes
    through the single atomic SQL statement to avoid a SELECT/UPSERT race
    window -- this function exists only so that identical decision logic
    can be exercised and tested without a live Postgres connection. Keep
    this in sync with the SQL CASE by hand if either changes.
    """
    if existing_close is not None and incoming_close != existing_close:
        return incoming_received_at, existing_revision_count + 1
    return existing_last_revised_at, existing_revision_count


def _ensure_columns(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(_ALTER_SQL)
    conn.commit()


def _upsert(rows: list, interval: str) -> int:
    """Upsert rows into us_market. Returns count inserted."""
    if not rows:
        return 0
    with _get_conn() as conn:
        _ensure_columns(conn)
        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO us_market
                    (ts, symbol, interval, open, high, low, close, volume,
                     received_at, data_age_ms, quality_flag)
                VALUES
                    (%(ts)s, %(symbol)s, %(interval)s,
                     %(open)s, %(high)s, %(low)s, %(close)s, %(volume)s,
                     %(received_at)s, %(data_age_ms)s, %(quality_flag)s)
                ON CONFLICT (ts, symbol, interval) DO UPDATE SET
                    open   = EXCLUDED.open,
                    high   = EXCLUDED.high,
                    low    = EXCLUDED.low,
                    close  = EXCLUDED.close,
                    volume = EXCLUDED.volume,
                    -- Revision log (additive, PIT follow-up): only stamp a
                    -- revision when the value actually changed (IS DISTINCT
                    -- FROM is NULL-safe). received_at/data_age_ms/
                    -- quality_flag remain untouched below -- this only
                    -- records *that* and *when* a later re-fetch changed
                    -- the close, never rewrites the original known-time.
                    last_revised_at = CASE
                        WHEN us_market.close IS DISTINCT FROM EXCLUDED.close
                        THEN EXCLUDED.received_at
                        ELSE us_market.last_revised_at
                    END,
                    revision_count = CASE
                        WHEN us_market.close IS DISTINCT FROM EXCLUDED.close
                        THEN us_market.revision_count + 1
                        ELSE us_market.revision_count
                    END
                    -- received_at/data_age_ms/quality_flag intentionally NOT
                    -- updated here: preserves the row's true first-known
                    -- time across repeated backfill/intraday re-fetches of
                    -- the same (ts, symbol, interval) instead of refreshing
                    -- it to "now" on every re-run.
            """, rows)
        conn.commit()
    return len(rows)


def _df_to_rows(df, symbol: str, interval: str, observed_at: datetime) -> list:
    """Convert yfinance DataFrame to list of dicts for upsert."""
    rows = []
    for ts, row in df.iterrows():
        # yfinance returns tz-aware timestamps (UTC)
        if hasattr(ts, "to_pydatetime"):
            ts = ts.to_pydatetime()
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        close = row.get("Close") or row.get("close")
        if close is None or (hasattr(close, "__float__") and str(close) == "nan"):
            continue

        data_age_ms = max(0, round((observed_at - ts).total_seconds() * 1000))
        rows.append({
            "ts":       ts,
            "symbol":   symbol,
            "interval": interval,
            "open":     float(row.get("Open")   or close),
            "high":     float(row.get("High")   or close),
            "low":      float(row.get("Low")    or close),
            "close":    float(close),
            "volume":   int(row.get("Volume")   or 0),
            "received_at":  observed_at,
            "data_age_ms":  data_age_ms,
            "quality_flag": QUALITY_DELAYED,
        })
    return rows


def run_intraday() -> None:
    """Fetch latest 5-min candles for all symbols (last 1 day window)."""
    try:
        import yfinance as yf
    except ImportError:
        logger.error("[US-MARKET] yfinance not installed.")
        sys.exit(1)

    total = 0
    observed_at = datetime.now(timezone.utc)
    for symbol, ticker in SYMBOLS.items():
        try:
            df = yf.download(
                ticker,
                period="1d",
                interval="5m",
                progress=False,
                auto_adjust=True,
            )
            if df.empty:
                logger.debug("[US-MARKET] No intraday data for %s (%s)", symbol, ticker)
                continue

            # Flatten multi-level columns yfinance sometimes returns
            if hasattr(df.columns, "levels"):
                df.columns = df.columns.get_level_values(0)

            rows  = _df_to_rows(df, symbol, "5minute", observed_at)
            total += _upsert(rows, "5minute")
            logger.debug("[US-MARKET] %s: %d candles", symbol, len(rows))

        except Exception as exc:
            logger.error("[US-MARKET] Intraday fetch failed for %s: %s", symbol, exc)

    logger.info("[US-MARKET] Intraday run complete — %d candles.", total)

    once("us_start",
         f"\U0001f7e2 US Market Started\n"
         f"{now_ist()}\n"
         f"Collecting: SP500 / Nasdaq / Dow / DXY / VIX / 10Y")

    # After US close (~01:30 AM IST = 22:00 CEST) — send end message once
    now_utc = datetime.now(timezone.utc)
    if now_utc.hour >= 20:   # CEST 22:00+ = IST 01:30 AM+
        once("us_end",
             f"\U0001f534 US Market Done\n"
             f"{now_ist()}\n"
             f"Today: {total:,} rows\n"
             f"us_market total: {table_rows('us_market'):,} rows\n"
             f"DB: {db_size()}")


def run_daily() -> None:
    """Backfill last 60 days of daily bars for all symbols."""
    try:
        import yfinance as yf
    except ImportError:
        logger.error("[US-MARKET] yfinance not installed.")
        sys.exit(1)

    total = 0
    observed_at = datetime.now(timezone.utc)
    for symbol, ticker in SYMBOLS.items():
        try:
            df = yf.download(
                ticker,
                period="60d",
                interval="1d",
                progress=False,
                auto_adjust=True,
            )
            if df.empty:
                logger.warning("[US-MARKET] No daily data for %s (%s)", symbol, ticker)
                continue

            if hasattr(df.columns, "levels"):
                df.columns = df.columns.get_level_values(0)

            rows  = _df_to_rows(df, symbol, "day", observed_at)
            total += _upsert(rows, "day")
            logger.info("[US-MARKET] %s: %d daily candles upserted", symbol, len(rows))

        except Exception as exc:
            logger.error("[US-MARKET] Daily fetch failed for %s: %s", symbol, exc)

    logger.info("[US-MARKET] Daily run complete — %d candles.", total)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="US markets data collector")
    parser.add_argument("--intraday", action="store_true",
                        help="Fetch 5-min intraday candles (run every 5 min via cron)")
    args = parser.parse_args()

    if args.intraday:
        run_intraday()
    else:
        run_daily()
