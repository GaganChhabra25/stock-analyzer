"""
Global commodity reference prices collector.

Fetches daily OHLC + day-over-day % change for:
  WTI    — CL=F  (Crude Oil front-month, USD/barrel)
  BRENT  — BZ=F  (Brent Crude, USD/barrel)
  NATGAS — NG=F  (Henry Hub Natural Gas, USD/MMBtu)
  USDINR — USDINR=X (USD/INR spot rate)

Source: Yahoo Finance via yfinance (no API key needed).
Backfills last 90 days on first run; subsequent runs just update.

Why these matter for MCX option selling:
  MCX Crude  ≈ WTI × USDINR  (with ~1 day lag + premium)
  MCX NatGas ≈ Henry Hub × USDINR × conversion factor
  Overnight WTI move → predicts MCX open gap next morning.

Run: python options/global_prices.py
Crontab: 0 5 * * 1-6  (8:30 AM IST = CEST 05:00)
"""

import logging
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import yfinance as yf
from screener.db import _get_conn
from logging_config import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

BACKFILL_DAYS = 90

# symbol_name → Yahoo Finance ticker
TICKERS = {
    "WTI":    "CL=F",
    "BRENT":  "BZ=F",
    "NATGAS": "NG=F",
    "USDINR": "USDINR=X",
}


def _fetch_latest_stored_date(symbol: str):
    """Return the most recent date already in DB for this symbol, or None."""
    with _get_conn() as conn:
        if conn is None:
            return None
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MAX(date) FROM global_prices WHERE symbol = %s", (symbol,)
            )
            row = cur.fetchone()
            return row[0] if row else None


def fetch_and_store(days: int = BACKFILL_DAYS) -> int:
    """
    Fetch global prices from Yahoo Finance and upsert into global_prices.
    Only fetches data newer than what's already stored (incremental).
    """
    today = date.today()
    total = 0

    for symbol, ticker in TICKERS.items():
        latest = _fetch_latest_stored_date(symbol)
        if latest and (today - latest).days <= 1:
            logger.debug("[GlobalPrices] %s already up to date (%s).", symbol, latest)
            continue

        # Start from day after last stored, or full backfill
        start = (latest + timedelta(days=1)) if latest else (today - timedelta(days=days))
        end   = today + timedelta(days=1)   # yfinance end is exclusive

        try:
            t    = yf.Ticker(ticker)
            hist = t.history(start=start, end=end, auto_adjust=True)
            if hist.empty:
                logger.warning("[GlobalPrices] No data returned for %s (%s).", symbol, ticker)
                continue
        except Exception as exc:
            logger.error("[GlobalPrices] yfinance failed for %s (%s): %s", symbol, ticker, exc)
            continue

        hist = hist.reset_index()
        rows = []
        prev_close = None

        for _, row in hist.iterrows():
            row_date = row["Date"].date() if hasattr(row["Date"], "date") else row["Date"]
            close    = float(row["Close"])
            chg      = round(((close - prev_close) / prev_close) * 100, 4) if prev_close else None
            rows.append({
                "date":       row_date,
                "symbol":     symbol,
                "open":       float(row["Open"]),
                "high":       float(row["High"]),
                "low":        float(row["Low"]),
                "close":      close,
                "change_pct": chg,
            })
            prev_close = close

        if not rows:
            continue

        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO global_prices
                        (date, symbol, open, high, low, close, change_pct)
                    VALUES
                        (%(date)s, %(symbol)s, %(open)s, %(high)s,
                         %(low)s, %(close)s, %(change_pct)s)
                    ON CONFLICT (date, symbol) DO UPDATE SET
                        open       = EXCLUDED.open,
                        high       = EXCLUDED.high,
                        low        = EXCLUDED.low,
                        close      = EXCLUDED.close,
                        change_pct = EXCLUDED.change_pct
                """, rows)
            conn.commit()

        logger.info("[GlobalPrices] %s (%s): upserted %d rows.", symbol, ticker, len(rows))
        total += len(rows)

    return total


if __name__ == "__main__":
    n = fetch_and_store()
    logger.info("[GlobalPrices] Done — %d rows total.", n)
