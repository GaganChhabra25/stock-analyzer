"""
NSE indices + sector indices + USDINR per-minute OHLC collector.

Symbols collected:
  Broad indices : NIFTY50, BANKNIFTY, FINNIFTY, MIDCPNIFTY, INDIAVIX
  Sector indices: NIFTYIT, NIFTYAUTO, NIFTYPHARMA, NIFTYMETAL,
                  NIFTYFMCG, NIFTYENERGY, NIFTYPSUBANK, NIFTYPRIVBANK,
                  NIFTYMEDIA, NIFTYREALTY
  Currency      : USDINR (near-month futures, CDS exchange)

Modes:
  --minute  : fetch last 5 min of data for all symbols (runs every minute via cron)
  --daily   : backfill last 60 days of daily bars (runs at 09:05 IST)
  --backfill: full 60-day minute backfill for all symbols (run manually once)

Cron:
  */1 5-12 * * 1-5    options/nse_ohlc.py --minute   (market hours)
  35  5    * * 1-5    options/nse_ohlc.py --daily    (pre-market)

Table: nse_ohlc (ts, symbol, interval, open, high, low, close, volume)
"""

import argparse
import logging
import sys
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from options.kite_auth import get_kite
from screener.db import _get_conn
from logging_config import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

IST          = ZoneInfo("Asia/Kolkata")
BACKFILL_DAYS = 60

# ── Fixed instrument tokens (stable across time) ───────────────────────────────
BROAD_TOKENS = {
    "NIFTY50":    256265,
    "BANKNIFTY":  260105,
    "FINNIFTY":   257801,
    "INDIAVIX":   264969,
    "MIDCPNIFTY": 288009,
}

# Kite tradingsymbol → our internal symbol name for sector indices
SECTOR_INDEX_MAP = {
    "NIFTY IT":           "NIFTYIT",
    "NIFTY AUTO":         "NIFTYAUTO",
    "NIFTY PHARMA":       "NIFTYPHARMA",
    "NIFTY METAL":        "NIFTYMETAL",
    "NIFTY FMCG":         "NIFTYFMCG",
    "NIFTY ENERGY":       "NIFTYENERGY",
    "NIFTY PSU BANK":     "NIFTYPSUBANK",
    "NIFTY PRIVATE BANK": "NIFTYPRIVBANK",
    "NIFTY MEDIA":        "NIFTYMEDIA",
    "NIFTY REALTY":       "NIFTYREALTY",
}


def _to_ist(dt) -> datetime:
    if isinstance(dt, datetime):
        return dt.replace(tzinfo=IST) if dt.tzinfo is None else dt.astimezone(IST)
    return datetime(dt.year, dt.month, dt.day, tzinfo=IST)


def _is_market_open() -> bool:
    """True during NSE trading hours (9:15–15:30 IST, Mon–Fri)."""
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    t = now.time()
    from datetime import time
    return time(9, 14) <= t <= time(15, 31)


def resolve_sector_tokens(kite) -> dict:
    """Look up sector index instrument tokens from Kite NSE instruments dump."""
    try:
        instruments = kite.instruments("NSE")
    except Exception as exc:
        logger.warning("[NSE-OHLC] Could not load NSE instruments: %s", exc)
        return {}

    result = {}
    for instr in instruments:
        ts  = instr.get("tradingsymbol", "")
        seg = str(instr.get("segment", ""))
        if ts in SECTOR_INDEX_MAP and "INDEX" in seg.upper():
            sym = SECTOR_INDEX_MAP[ts]
            result[sym] = int(instr["instrument_token"])

    missing = set(SECTOR_INDEX_MAP.values()) - set(result.keys())
    for m in missing:
        logger.warning("[NSE-OHLC] Token not found for sector index: %s", m)

    logger.info("[NSE-OHLC] Resolved %d/%d sector tokens", len(result), len(SECTOR_INDEX_MAP))
    return result


def resolve_usdinr_token(kite) -> Optional[int]:
    """Return near-month USDINR futures token from CDS instruments."""
    try:
        instruments = kite.instruments("CDS")
    except Exception as exc:
        logger.warning("[NSE-OHLC] Could not load CDS instruments: %s", exc)
        return None

    today = date.today()
    candidates = [
        i for i in instruments
        if i.get("name") == "USDINR"
        and i.get("instrument_type") == "FUT"
        and i.get("expiry") and i["expiry"] >= today
    ]
    if not candidates:
        logger.warning("[NSE-OHLC] No USDINR futures found in CDS instruments")
        return None

    candidates.sort(key=lambda x: x["expiry"])
    token = int(candidates[0]["instrument_token"])
    logger.info("[NSE-OHLC] USDINR near-month token: %d  expiry: %s",
                token, candidates[0]["expiry"])
    return token


def fetch_and_store(kite, token_map: dict, from_dt, to_dt, interval: str) -> int:
    """
    Fetch OHLC candles for all tokens and upsert into nse_ohlc.
    token_map: {symbol_name: instrument_token}
    Returns total rows upserted.
    """
    total = 0

    with _get_conn() as conn:
        with conn.cursor() as cur:
            for symbol, token in token_map.items():
                try:
                    candles = kite.historical_data(
                        instrument_token=token,
                        from_date=from_dt,
                        to_date=to_dt,
                        interval=interval,
                        continuous=False,
                    )
                except Exception as exc:
                    logger.error("[NSE-OHLC] %s (token %d): %s", symbol, token, exc)
                    _time.sleep(0.5)
                    continue

                if not candles:
                    continue

                rows = [{
                    "ts":       _to_ist(c["date"]),
                    "symbol":   symbol,
                    "interval": interval,
                    "open":     c["open"],
                    "high":     c["high"],
                    "low":      c["low"],
                    "close":    c["close"],
                    "volume":   c.get("volume") or 0,
                } for c in candles]

                cur.executemany("""
                    INSERT INTO nse_ohlc
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
                total += len(rows)
                _time.sleep(0.2)   # stay within Kite rate limits

        conn.commit()

    logger.info("[NSE-OHLC] Upserted %d candles (%s, %s → %s)",
                total, interval, from_dt, to_dt)
    return total


def build_token_map(kite) -> dict:
    """Combine broad indices + sector indices + USDINR into one token map."""
    tokens = dict(BROAD_TOKENS)
    tokens.update(resolve_sector_tokens(kite))
    usdinr = resolve_usdinr_token(kite)
    if usdinr:
        tokens["USDINR"] = usdinr
    return tokens


def run_minute(kite) -> None:
    """Fetch last 5 min of 1-minute candles for all symbols."""
    if not _is_market_open():
        logger.debug("[NSE-OHLC] Market closed — skipping minute run.")
        return

    now     = datetime.now(IST)
    from_dt = now - timedelta(minutes=5)
    to_dt   = now

    token_map = build_token_map(kite)
    total = fetch_and_store(kite, token_map, from_dt, to_dt, "minute")
    logger.info("[NSE-OHLC] Minute run complete — %d candles.", total)


def _minute_data_exists() -> bool:
    """True if nse_ohlc already has any minute-interval rows."""
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM nse_ohlc WHERE interval = 'minute' LIMIT 1")
            return cur.fetchone() is not None


def run_daily(kite) -> None:
    """
    Backfill last 60 days of daily candles for all symbols.
    On first ever run (no minute data in DB), automatically triggers the
    60-day minute backfill so no manual step is needed.
    """
    to_date   = date.today()
    from_date = to_date - timedelta(days=BACKFILL_DAYS)

    token_map = build_token_map(kite)
    total = fetch_and_store(kite, token_map, from_date, to_date, "day")
    logger.info("[NSE-OHLC] Daily run complete — %d candles.", total)

    if not _minute_data_exists():
        logger.info("[NSE-OHLC] No minute data found — auto-triggering 60-day backfill.")
        run_backfill(kite)


def run_backfill(kite) -> None:
    """Full 60-day minute backfill — run manually once to seed historical data."""
    logger.info("[NSE-OHLC] Starting full 60-day minute backfill …")
    token_map = build_token_map(kite)
    today     = date.today()

    # Kite allows 60 days of minute data; fetch in 5-day chunks to avoid timeouts
    total = 0
    chunk = 5
    for offset in range(0, BACKFILL_DAYS, chunk):
        to_date   = today - timedelta(days=offset)
        from_date = to_date - timedelta(days=chunk)
        total    += fetch_and_store(kite, token_map, from_date, to_date, "minute")
        _time.sleep(1)

    logger.info("[NSE-OHLC] Backfill complete — %d total candles.", total)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NSE indices OHLC collector")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--minute",   action="store_true",
                      help="Fetch last 5-min slice of 1-min candles (cron every minute)")
    mode.add_argument("--daily",    action="store_true",
                      help="Backfill last 60 days of daily candles (cron pre-market)")
    mode.add_argument("--backfill", action="store_true",
                      help="Full 60-day minute backfill (run manually once)")
    args = parser.parse_args()

    kite = get_kite()
    if not kite:
        logger.error("[NSE-OHLC] Kite not authorised. Visit /kite/login.")
        sys.exit(1)

    if args.backfill:
        run_backfill(kite)
    elif args.daily:
        run_daily(kite)
    else:
        run_minute(kite)
