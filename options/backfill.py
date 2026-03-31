"""
Backfill historical options data using Kite Connect historical API.

Pulls per-minute OHLCV + OI for NIFTY and BANKNIFTY options across
all near-term expiries (up to 90 days out), covering the full spot
price range over the last 59 days.

Usage:
    python options/backfill.py                      # all near-term expiries, 59 days
    python options/backfill.py --from 2026-02-01    # from a specific date
    python options/backfill.py --expiry 2026-04-07  # single expiry only
"""

import argparse
import logging
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from options.kite_auth import get_kite
from options.instruments import load_instruments, STRIKE_STEP
from options.greeks import implied_volatility, calculate_greeks
from screener.db import _get_conn
from logging_config import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

SYMBOLS        = ["NIFTY", "BANKNIFTY"]
RISK_FREE_RATE = 0.07
MAX_DAYS       = 59        # Kite API hard limit for minute data
MAX_EXPIRY_DAYS = 90       # Only pull expiries within 90 days

# Fixed Kite instrument tokens for index spot prices
INDEX_TOKENS = {
    "NIFTY":     256265,
    "BANKNIFTY": 260105,
}

# How many strikes either side of ATM range to cover
STRIKE_BUFFER = 15   # wider than live collector to cover full historical range


def fetch_spot_history(kite, symbol: str, from_dt: datetime, to_dt: datetime) -> dict:
    """Fetch per-minute spot price. Returns {datetime: close_price}."""
    token = INDEX_TOKENS[symbol]
    try:
        records = kite.historical_data(token, from_dt, to_dt, "minute")
        return {r["date"].replace(tzinfo=None): r["close"] for r in records}
    except Exception as exc:
        logger.error("Spot history failed for %s: %s", symbol, exc)
        return {}


def get_strike_range(spot_history: dict, symbol: str, buffer: int) -> set:
    """
    Return all strikes covering the full spot range + buffer strikes on each side.
    This ensures we capture all contracts that were ATM during the period.
    """
    step = STRIKE_STEP.get(symbol, 50)
    if not spot_history:
        return set()
    lo = min(spot_history.values())
    hi = max(spot_history.values())
    # ATM at lowest spot minus buffer, to ATM at highest spot plus buffer
    atm_lo = int(round(lo / step) * step) - buffer * step
    atm_hi = int(round(hi / step) * step) + buffer * step
    return set(range(atm_lo, atm_hi + step, step))


def get_near_term_expiries(df, symbol: str, max_days: int) -> list:
    """Return all expiries from today up to max_days out, sorted ascending."""
    today = date.today()
    cutoff = today + timedelta(days=max_days)
    expiries = sorted(df[df["tradingsymbol"].str.startswith(symbol)]["expiry"].unique())
    result = []
    for e in expiries:
        e_date = date.fromisoformat(str(e))
        if today <= e_date <= cutoff:
            result.append(e_date)
    return result


def already_have_data(symbol: str, expiry: date, from_date: date, to_date: date) -> bool:
    """Check if we already have data for this expiry/date range to avoid re-fetching."""
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) FROM option_chain
                    WHERE instrument = %s
                      AND expiry = %s
                      AND ts::date BETWEEN %s AND %s
                """, (symbol, expiry, from_date, to_date))
                return cur.fetchone()[0] > 0
    except Exception:
        return False


def backfill_expiry(kite, df, symbol: str, expiry: date,
                    from_date: date, to_date: date, strikes: set):
    """Backfill one expiry for one symbol."""
    expiry_str = expiry.isoformat()

    # Filter instruments for this expiry and strike range
    df_expiry = df["expiry"].apply(lambda x: str(x)[:10])
    sym_df = df[
        df["tradingsymbol"].str.startswith(symbol) &
        (df_expiry == expiry_str) &
        (df["strike"].isin(strikes)) &
        (df["instrument_type"].isin(["CE", "PE"]))
    ]

    if sym_df.empty:
        logger.warning("No instruments for %s expiry %s", symbol, expiry)
        return 0

    from_dt = datetime.combine(from_date, datetime.min.time().replace(hour=9, minute=15))
    to_dt   = datetime.combine(to_date,   datetime.min.time().replace(hour=15, minute=30))
    T = max((expiry - date.today()).days, 1) / 365.0

    # Need spot history for Greeks calculation
    spot_history = fetch_spot_history(kite, symbol, from_dt, to_dt)
    if not spot_history:
        logger.error("No spot data for %s — skipping expiry %s", symbol, expiry)
        return 0

    total_rows = 0
    n = len(sym_df)

    for i, (_, row) in enumerate(sym_df.iterrows(), 1):
        token    = int(row["instrument_token"])
        strike   = int(row["strike"])
        opt_type = row["instrument_type"]

        try:
            records = kite.historical_data(token, from_dt, to_dt, "minute")
        except Exception as exc:
            logger.warning("[%d/%d] %s %s %s %s: %s", i, n, symbol, expiry, strike, opt_type, exc)
            time.sleep(1)
            continue

        time.sleep(0.35)

        if not records:
            continue

        option_rows = []
        for rec in records:
            ts  = rec["date"].replace(tzinfo=None)
            ltp = rec["close"] or 0
            oi  = rec.get("oi", 0) or 0
            vol = rec.get("volume", 0) or 0

            spot = spot_history.get(ts)
            if not spot:
                closest = min(spot_history.keys(), key=lambda t: abs((t - ts).total_seconds()))
                if abs((closest - ts).total_seconds()) <= 60:
                    spot = spot_history[closest]
            if not spot or ltp <= 0:
                continue

            iv     = implied_volatility(ltp, spot, strike, T, RISK_FREE_RATE, opt_type)
            sigma  = (iv / 100.0) if iv else 0.20
            greeks = calculate_greeks(spot, strike, T, RISK_FREE_RATE, sigma, opt_type)

            option_rows.append({
                "ts": ts, "instrument": symbol, "expiry": expiry,
                "strike": strike, "option_type": opt_type,
                "ltp": ltp, "bid": None, "ask": None,
                "oi": oi, "oi_change": 0, "volume": vol,
                "iv": iv,
                "delta": greeks["delta"], "gamma": greeks["gamma"],
                "theta": greeks["theta"], "vega":  greeks["vega"],
                "underlying_ltp": spot,
            })

        if not option_rows:
            continue

        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO option_chain (
                        ts, instrument, expiry, strike, option_type,
                        ltp, bid, ask, oi, oi_change, volume, iv,
                        delta, gamma, theta, vega, underlying_ltp
                    ) VALUES (
                        %(ts)s, %(instrument)s, %(expiry)s, %(strike)s, %(option_type)s,
                        %(ltp)s, %(bid)s, %(ask)s, %(oi)s, %(oi_change)s, %(volume)s, %(iv)s,
                        %(delta)s, %(gamma)s, %(theta)s, %(vega)s, %(underlying_ltp)s
                    )
                    ON CONFLICT DO NOTHING
                """, option_rows)
            conn.commit()

        total_rows += len(option_rows)
        logger.info("[%d/%d] %s %s %s %s: %d rows", i, n, symbol, expiry, strike, opt_type, len(option_rows))

    return total_rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="from_date", type=str,
                        help="Start date YYYY-MM-DD (default: 59 days ago)")
    parser.add_argument("--expiry", type=str,
                        help="Single expiry date YYYY-MM-DD")
    args = parser.parse_args()

    today     = date.today()
    from_date = date.fromisoformat(args.from_date) if args.from_date else today - timedelta(days=MAX_DAYS)
    to_date   = today - timedelta(days=1)  # up to yesterday

    # Clamp to Kite's 60-day limit
    if (today - from_date).days > MAX_DAYS:
        from_date = today - timedelta(days=MAX_DAYS)
        logger.warning("Clamped from_date to %s (Kite 60-day limit)", from_date)

    logger.info("Backfilling %s to %s", from_date, to_date)

    kite = get_kite()
    if not kite:
        logger.error("Kite not authorized.")
        sys.exit(1)

    # Force refresh instruments (ignore cache — we need all expiries)
    from options.instruments import CACHE_FILE
    if CACHE_FILE.exists():
        CACHE_FILE.unlink()
    df = load_instruments(kite)
    logger.info("Loaded %d NFO instruments", len(df))

    grand_total = 0

    for symbol in SYMBOLS:
        logger.info("=" * 60)
        logger.info("Symbol: %s", symbol)

        # Fetch spot history to determine strike range
        from_dt = datetime.combine(from_date, datetime.min.time().replace(hour=9, minute=15))
        to_dt   = datetime.combine(to_date,   datetime.min.time().replace(hour=15, minute=30))
        spot_history = fetch_spot_history(kite, symbol, from_dt, to_dt)
        if not spot_history:
            logger.error("No spot data for %s — skipping", symbol)
            continue

        strikes = get_strike_range(spot_history, symbol, STRIKE_BUFFER)
        logger.info("%s spot range: %.0f - %.0f | %d strikes to cover",
                    symbol, min(spot_history.values()), max(spot_history.values()), len(strikes))

        # Get expiries
        if args.expiry:
            expiries = [date.fromisoformat(args.expiry)]
        else:
            expiries = get_near_term_expiries(df, symbol, MAX_EXPIRY_DAYS)

        logger.info("%s: %d expiries to process: %s", symbol, len(expiries),
                    [str(e) for e in expiries])

        for expiry in expiries:
            if already_have_data(symbol, expiry, from_date, to_date):
                logger.info("Skipping %s %s — data already exists", symbol, expiry)
                continue
            logger.info("Processing %s expiry %s...", symbol, expiry)
            rows = backfill_expiry(kite, df, symbol, expiry, from_date, to_date, strikes)
            grand_total += rows
            logger.info("%s %s done: %d rows", symbol, expiry, rows)

    logger.info("=" * 60)
    logger.info("Backfill complete. Grand total: %d rows", grand_total)

    # Telegram summary
    import os, requests as req
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id  = os.environ.get("TELEGRAM_CHAT_ID", "")
    if tg_token and chat_id:
        msg = (
            "Backfill complete!\n"
            "Range: " + str(from_date) + " to " + str(to_date) + "\n"
            "Total rows inserted: " + str(grand_total) + "\n"
            "Symbols: NIFTY + BANKNIFTY\n"
            "Expiries covered: near-term up to 90 days"
        )
        req.post("https://api.telegram.org/bot" + tg_token + "/sendMessage",
                 json={"chat_id": chat_id, "text": msg}, timeout=10)


if __name__ == "__main__":
    main()
