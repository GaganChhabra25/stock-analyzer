"""
Backfill historical options data using Kite Connect historical API.

Pulls per-minute OHLCV + OI for NIFTY and BANKNIFTY options
(ATM ± 6 strikes) for a given date range and inserts into option_chain.

Usage:
    python options/backfill.py                  # last 5 trading days
    python options/backfill.py --days 10        # last 10 trading days
    python options/backfill.py --from 2026-03-24 --to 2026-03-28
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

SYMBOLS = ["NIFTY", "BANKNIFTY"]
N_STRIKES = 6
RISK_FREE_RATE = 0.07

# Fixed Kite instrument tokens for index spot prices
INDEX_TOKENS = {
    "NIFTY":     256265,   # NSE:NIFTY 50
    "BANKNIFTY": 260105,   # NSE:NIFTY BANK
}


def trading_days_back(n: int):
    """Return list of last n trading days (Mon-Fri), most recent first."""
    days = []
    d = date.today() - timedelta(days=1)
    while len(days) < n:
        if d.weekday() < 5:  # Mon=0 … Fri=4
            days.append(d)
        d -= timedelta(days=1)
    return days


def fetch_spot_history(kite, symbol: str, from_dt: datetime, to_dt: datetime) -> dict:
    """
    Fetch per-minute spot price history for index.
    Returns {datetime: spot_price}.
    """
    token = INDEX_TOKENS[symbol]
    try:
        records = kite.historical_data(
            instrument_token=token,
            from_date=from_dt,
            to_date=to_dt,
            interval="minute",
        )
        return {r["date"].replace(tzinfo=None): r["close"] for r in records}
    except Exception as exc:
        logger.error("Failed to fetch spot history for %s: %s", symbol, exc)
        return {}


def get_expiries_in_range(df, symbol: str, from_date: date, to_date: date):
    """Return all expiries that overlap with the date range."""
    sym_df = df[df["tradingsymbol"].str.startswith(symbol)]
    expiries = sorted(sym_df["expiry"].unique())
    result = []
    for e in expiries:
        # Handle both date objects and strings (from JSON cache)
        if isinstance(e, str):
            e = date.fromisoformat(e)
        if e >= from_date:
            result.append(e)
    return result


def get_strikes_for_range(spot_history: dict, symbol: str, n: int) -> set:
    """Compute the union of all ATM ± n strikes seen across the spot history."""
    step = STRIKE_STEP.get(symbol, 50)
    strikes = set()
    for spot in spot_history.values():
        atm = int(round(spot / step) * step)
        for i in range(-n, n + 1):
            strikes.add(atm + i * step)
    return strikes


def backfill_symbol(kite, df, symbol: str, from_date: date, to_date: date):
    """Backfill one symbol (NIFTY or BANKNIFTY) for the given date range."""
    from_dt = datetime.combine(from_date, datetime.min.time().replace(hour=9, minute=15))
    to_dt   = datetime.combine(to_date,   datetime.min.time().replace(hour=15, minute=30))

    logger.info("Fetching %s spot history %s → %s", symbol, from_date, to_date)
    spot_history = fetch_spot_history(kite, symbol, from_dt, to_dt)
    if not spot_history:
        logger.error("No spot data for %s — aborting.", symbol)
        return

    # Determine all relevant strikes
    strikes = get_strikes_for_range(spot_history, symbol, N_STRIKES)
    logger.info("%s: %d unique strikes to backfill", symbol, len(strikes))

    # Get expiries active during the range
    expiries = get_expiries_in_range(df, symbol, from_date, to_date)
    if not expiries:
        logger.error("No expiries found for %s in range.", symbol)
        return

    # Use the earliest expiry that covers the range (weekly)
    # For multi-week ranges, use the expiry closest to each day
    expiry = expiries[0]
    logger.info("%s: using expiry %s", symbol, expiry)

    # Get instrument tokens for all relevant strikes
    # Normalise expiry column to string for comparison
    expiry_str = expiry.isoformat() if isinstance(expiry, date) else expiry
    df_expiry  = df["expiry"].apply(lambda x: x.isoformat() if isinstance(x, date) else str(x))

    sym_df = df[
        df["tradingsymbol"].str.startswith(symbol) &
        (df_expiry == expiry_str) &
        (df["strike"].isin(strikes)) &
        (df["instrument_type"].isin(["CE", "PE"]))
    ]

    if sym_df.empty:
        logger.error("No instruments found for %s expiry %s", symbol, expiry)
        return

    total_rows = 0
    processed  = 0

    for _, row in sym_df.iterrows():
        token      = int(row["instrument_token"])
        strike     = int(row["strike"])
        opt_type   = row["instrument_type"]
        processed += 1

        try:
            records = kite.historical_data(
                instrument_token=token,
                from_date=from_dt,
                to_date=to_dt,
                interval="minute",
            )
        except Exception as exc:
            logger.warning("Failed %s %s %s: %s", symbol, strike, opt_type, exc)
            time.sleep(0.5)
            continue

        if not records:
            continue

        # Rate limit: ~3 req/sec
        time.sleep(0.35)

        T = max((expiry - date.today()).days, 1) / 365.0

        option_rows = []
        for rec in records:
            ts  = rec["date"].replace(tzinfo=None)
            ltp = rec["close"] or 0
            oi  = rec.get("oi", 0) or 0

            spot = spot_history.get(ts)
            if not spot:
                # Find nearest spot (within 1 min)
                closest = min(spot_history.keys(), key=lambda t: abs((t - ts).total_seconds()))
                spot = spot_history[closest] if abs((closest - ts).total_seconds()) <= 60 else None

            if not spot:
                continue

            iv     = implied_volatility(ltp, spot, strike, T, RISK_FREE_RATE, opt_type)
            sigma  = (iv / 100.0) if iv else 0.20
            greeks = calculate_greeks(spot, strike, T, RISK_FREE_RATE, sigma, opt_type)

            option_rows.append({
                "ts": ts, "instrument": symbol, "expiry": expiry,
                "strike": strike, "option_type": opt_type,
                "ltp": ltp, "bid": None, "ask": None,
                "oi": oi, "oi_change": 0,
                "volume": rec.get("volume", 0) or 0,
                "iv": iv,
                "delta": greeks["delta"], "gamma": greeks["gamma"],
                "theta": greeks["theta"], "vega": greeks["vega"],
                "underlying_ltp": spot,
            })

        if not option_rows:
            continue

        # Insert — skip duplicates
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
        logger.info("[%d/%d] %s %s %s: %d rows inserted",
                    processed, len(sym_df), symbol, strike, opt_type, len(option_rows))

    logger.info("%s backfill done. Total rows inserted: %d", symbol, total_rows)
    return total_rows


def main():
    parser = argparse.ArgumentParser(description="Backfill historical options data")
    parser.add_argument("--days", type=int, default=5,
                        help="Number of trading days to backfill (default: 5)")
    parser.add_argument("--from", dest="from_date", type=str,
                        help="Start date YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", type=str,
                        help="End date YYYY-MM-DD")
    args = parser.parse_args()

    if args.from_date and args.to_date:
        from_date = date.fromisoformat(args.from_date)
        to_date   = date.fromisoformat(args.to_date)
    else:
        days = trading_days_back(args.days)
        from_date = min(days)
        to_date   = max(days)

    logger.info("Backfilling %s → %s", from_date, to_date)

    kite = get_kite()
    if not kite:
        logger.error("Kite not authorized. Run kite_auto_login.py first.")
        sys.exit(1)

    df = load_instruments(kite)

    grand_total = 0
    for symbol in SYMBOLS:
        rows = backfill_symbol(kite, df, symbol, from_date, to_date)
        grand_total += (rows or 0)

    logger.info("Backfill complete. Grand total rows: %d", grand_total)

    # Send Telegram summary
    token   = __import__("os").environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = __import__("os").environ.get("TELEGRAM_CHAT_ID", "")
    if token and chat_id:
        import requests
        msg = (
            f"Backfill complete!\n"
            f"Range: {from_date} to {to_date}\n"
            f"Total rows inserted: {grand_total:,}\n"
            f"Symbols: NIFTY + BANKNIFTY"
        )
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg},
            timeout=10,
        )


if __name__ == "__main__":
    main()
