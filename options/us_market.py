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


def _upsert(rows: list, interval: str) -> int:
    """Upsert rows into us_market. Returns count inserted."""
    if not rows:
        return 0
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO us_market
                    (ts, symbol, interval, open, high, low, close, volume)
                VALUES
                    (%(ts)s, %(symbol)s, %(interval)s,
                     %(open)s, %(high)s, %(low)s, %(close)s, %(volume)s)
                ON CONFLICT (ts, symbol, interval) DO UPDATE SET
                    open   = EXCLUDED.open,
                    high   = EXCLUDED.high,
                    low    = EXCLUDED.low,
                    close  = EXCLUDED.close,
                    volume = EXCLUDED.volume
            """, rows)
        conn.commit()
    return len(rows)


def _df_to_rows(df, symbol: str, interval: str) -> list:
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

        rows.append({
            "ts":       ts,
            "symbol":   symbol,
            "interval": interval,
            "open":     float(row.get("Open")   or close),
            "high":     float(row.get("High")   or close),
            "low":      float(row.get("Low")    or close),
            "close":    float(close),
            "volume":   int(row.get("Volume")   or 0),
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

            rows  = _df_to_rows(df, symbol, "5minute")
            total += _upsert(rows, "5minute")
            logger.debug("[US-MARKET] %s: %d candles", symbol, len(rows))

        except Exception as exc:
            logger.error("[US-MARKET] Intraday fetch failed for %s: %s", symbol, exc)

    logger.info("[US-MARKET] Intraday run complete — %d candles.", total)


def run_daily() -> None:
    """Backfill last 60 days of daily bars for all symbols."""
    try:
        import yfinance as yf
    except ImportError:
        logger.error("[US-MARKET] yfinance not installed.")
        sys.exit(1)

    total = 0
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

            rows  = _df_to_rows(df, symbol, "day")
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
