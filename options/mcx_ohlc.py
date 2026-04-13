"""
MCX futures OHLC collector.

Fetches NATURALGAS and CRUDEOIL near-month futures OHLC from Kite
historical API and upserts into mcx_ohlc table.

  Daily mode  (default): backfills/updates last 60 days of daily candles.
              Run at 9:00 AM IST to capture previous day's close.

  Intraday mode (--15min): fetches today + yesterday's 15-min candles.
              Run every 15 min during MCX hours for live S/R levels.

Usage:
  python options/mcx_ohlc.py           # daily candles
  python options/mcx_ohlc.py --15min   # 15-min intraday candles
"""

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from options.kite_auth import get_kite
from options.mcx_instruments import load_mcx_instruments
from screener.db import _get_conn
from logging_config import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

IST       = ZoneInfo("Asia/Kolkata")
SYMBOLS   = ["NATURALGAS", "CRUDEOIL"]
BACKFILL_DAYS = 60


def _get_futures_token(df, symbol: str):
    """Return (instrument_token, tradingsymbol) for near-month MCX futures."""
    from datetime import date as _date
    today = _date.today()
    fut = df[(df["name"] == symbol) & (df["instrument_type"] == "FUT")]
    upcoming = fut[fut["expiry"] >= today].sort_values("expiry")
    if upcoming.empty:
        return None, None
    row = upcoming.iloc[0]
    return int(row["instrument_token"]), row["tradingsymbol"]


def _to_ist(dt) -> datetime:
    """Ensure datetime is IST-aware. Date objects get midnight IST."""
    if isinstance(dt, datetime):
        return dt.replace(tzinfo=IST) if dt.tzinfo is None else dt.astimezone(IST)
    # date object → midnight IST
    return datetime(dt.year, dt.month, dt.day, tzinfo=IST)


def fetch_and_store(kite, symbol: str, interval: str,
                    from_date: date, to_date: date) -> int:
    """Fetch OHLC from Kite and upsert into mcx_ohlc. Returns rows upserted."""
    df_instr = load_mcx_instruments(kite)
    token, tradingsymbol = _get_futures_token(df_instr, symbol)
    if not token:
        logger.warning("[OHLC] No futures contract found for %s", symbol)
        return 0

    logger.info("[OHLC] %s %s %s → %s  (%s)", symbol, interval, from_date, to_date, tradingsymbol)
    try:
        candles = kite.historical_data(
            instrument_token=token,
            from_date=from_date,
            to_date=to_date,
            interval=interval,
            continuous=False,
            oi=True,
        )
    except Exception as exc:
        logger.error("[OHLC] Kite historical_data failed for %s %s: %s", symbol, interval, exc)
        return 0

    if not candles:
        logger.warning("[OHLC] No candles returned for %s %s", symbol, interval)
        return 0

    rows = [{
        "ts":         _to_ist(c["date"]),
        "instrument": symbol,
        "interval":   interval,
        "open":       c["open"],
        "high":       c["high"],
        "low":        c["low"],
        "close":      c["close"],
        "volume":     c.get("volume") or 0,
        "oi":         c.get("oi") or 0,
    } for c in candles]

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO mcx_ohlc
                    (ts, instrument, interval, open, high, low, close, volume, oi)
                VALUES
                    (%(ts)s, %(instrument)s, %(interval)s,
                     %(open)s, %(high)s, %(low)s, %(close)s, %(volume)s, %(oi)s)
                ON CONFLICT (ts, instrument, interval) DO UPDATE SET
                    open   = EXCLUDED.open,
                    high   = EXCLUDED.high,
                    low    = EXCLUDED.low,
                    close  = EXCLUDED.close,
                    volume = EXCLUDED.volume,
                    oi     = EXCLUDED.oi
            """, rows)
        conn.commit()

    logger.info("[OHLC] Upserted %d %s candles for %s", len(rows), interval, symbol)
    return len(rows)


def run_daily(kite) -> None:
    """Backfill / daily update: last 60 days of daily candles."""
    to_date   = date.today()
    from_date = to_date - timedelta(days=BACKFILL_DAYS)
    total = sum(fetch_and_store(kite, sym, "day", from_date, to_date) for sym in SYMBOLS)
    logger.info("[OHLC] Daily run complete — %d candles.", total)


def run_15min(kite) -> None:
    """Fetch today + yesterday 15-min candles (for intraday S/R)."""
    to_date   = date.today()
    from_date = to_date - timedelta(days=1)
    total = sum(fetch_and_store(kite, sym, "15minute", from_date, to_date) for sym in SYMBOLS)
    logger.info("[OHLC] 15-min run complete — %d candles.", total)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MCX OHLC collector")
    parser.add_argument("--15min", dest="intraday", action="store_true",
                        help="Fetch 15-min intraday candles instead of daily")
    args = parser.parse_args()

    kite = get_kite()
    if not kite:
        logger.error("[OHLC] Kite not authorised. Visit /kite/login.")
        sys.exit(1)

    if args.intraday:
        run_15min(kite)
    else:
        run_daily(kite)
