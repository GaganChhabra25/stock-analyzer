"""
Options chain per-minute collector.

Triggered by cron every minute during market hours:
  */1 3-10 * * 1-5   (3:45 AM–10:00 AM UTC = 9:15 AM–3:30 PM IST)

What it captures per run:
  - ATM ± 6 strikes for NIFTY and BANKNIFTY (CE + PE)
  - LTP, Bid, Ask, OI, OI change, Volume
  - IV and Greeks (Black-Scholes)
  - Market snapshot: spot, VIX, PCR, ATM straddle, expected move, OI walls
"""

import logging
import os
import sys
from datetime import datetime, time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from options.kite_auth import get_kite
from options.instruments import (
    load_instruments, get_nearest_expiry,
    get_option_tokens, atm_strike,
)
from options.greeks import implied_volatility, calculate_greeks
from screener.db import _get_conn
from logging_config import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
SYMBOLS        = ["NIFTY", "BANKNIFTY"]
N_STRIKES      = 6        # ATM ± 6
RISK_FREE_RATE = 0.07     # 7% annualised
MARKET_OPEN    = time(9, 14)
MARKET_CLOSE   = time(15, 31)

# Kite instrument symbols for index spot prices
SPOT_SYMBOLS = {
    "NIFTY":     "NSE:NIFTY 50",
    "BANKNIFTY": "NSE:NIFTY BANK",
}
VIX_SYMBOL = "NSE:INDIA VIX"


def is_market_open() -> bool:
    """Return True if current IST time is within market hours."""
    from zoneinfo import ZoneInfo
    now_ist = datetime.now(ZoneInfo("Asia/Kolkata")).time()
    return MARKET_OPEN <= now_ist <= MARKET_CLOSE


def days_to_expiry(expiry) -> float:
    """Return fractional days to expiry (as year fraction for BS)."""
    from datetime import date
    today = date.today()
    delta = (expiry - today).days
    return max(delta / 365.0, 1 / 365.0)


def collect_once():
    """Run one full collection cycle. Called every minute by cron."""
    if not is_market_open():
        logger.debug("Market closed — skipping collection.")
        return

    kite = get_kite()
    if kite is None:
        logger.warning("Kite not authorized. Visit /kite/login to authorize.")
        return

    ts = datetime.now()
    logger.info("Collecting option chain at %s", ts.strftime("%H:%M:%S"))

    try:
        df_instruments = load_instruments(kite)
    except Exception as exc:
        logger.error("Failed to load instruments: %s", exc)
        return

    # Fetch spot + VIX in one call
    spot_quote_symbols = list(SPOT_SYMBOLS.values()) + [VIX_SYMBOL]
    try:
        spot_quotes = kite.quote(spot_quote_symbols)
    except Exception as exc:
        logger.error("Failed to fetch spot quotes: %s", exc)
        return

    vix = spot_quotes.get(VIX_SYMBOL, {}).get("last_price", None)

    rows_inserted = 0

    for symbol in SYMBOLS:
        spot_sym = SPOT_SYMBOLS[symbol]
        spot = spot_quotes.get(spot_sym, {}).get("last_price")
        if not spot:
            logger.warning("No spot price for %s", symbol)
            continue

        atm = atm_strike(spot, symbol)
        expiry = get_nearest_expiry(df_instruments, symbol)
        if not expiry:
            logger.warning("No upcoming expiry for %s", symbol)
            continue

        T = days_to_expiry(expiry)

        # Get instrument tokens for ATM ± N_STRIKES
        token_map = get_option_tokens(df_instruments, symbol, expiry, atm, N_STRIKES)
        if not token_map:
            logger.warning("No tokens found for %s %s", symbol, expiry)
            continue

        # Fetch quotes for all option instruments
        tokens = [f"NFO:{t}" for t in token_map.values()]
        try:
            option_quotes = kite.quote(tokens)
        except Exception as exc:
            logger.error("Failed to fetch option quotes for %s: %s", symbol, exc)
            continue

        # Build reverse map: token → (strike, option_type)
        token_to_key = {v: k for k, v in token_map.items()}

        option_rows = []
        call_oi_by_strike = {}
        put_oi_by_strike  = {}

        for token_str, quote in option_quotes.items():
            # token_str is like "NFO:12345678"
            try:
                raw_token = int(token_str.split(":")[1])
            except Exception:
                continue

            key = token_to_key.get(raw_token)
            if not key:
                continue

            strike, opt_type = key
            ltp    = quote.get("last_price", 0) or 0
            volume = quote.get("volume", 0) or 0
            oi     = quote.get("oi", 0) or 0
            oi_chg = (quote.get("oi", 0) or 0) - (quote.get("oi_day_low", 0) or 0)

            depth  = quote.get("depth", {})
            bid    = depth.get("buy",  [{}])[0].get("price") if depth else None
            ask    = depth.get("sell", [{}])[0].get("price") if depth else None

            # IV and Greeks
            iv = implied_volatility(ltp, spot, strike, T, RISK_FREE_RATE, opt_type)
            sigma = (iv / 100.0) if iv else 0.20
            greeks = calculate_greeks(spot, strike, T, RISK_FREE_RATE, sigma, opt_type)

            option_rows.append({
                "ts": ts, "instrument": symbol, "expiry": expiry,
                "strike": strike, "option_type": opt_type,
                "ltp": ltp, "bid": bid, "ask": ask,
                "oi": oi, "oi_change": oi_chg, "volume": volume,
                "iv": iv,
                "delta": greeks["delta"], "gamma": greeks["gamma"],
                "theta": greeks["theta"], "vega": greeks["vega"],
                "underlying_ltp": spot,
            })

            if opt_type == "CE":
                call_oi_by_strike[strike] = oi
            else:
                put_oi_by_strike[strike] = oi

        if not option_rows:
            continue

        # ── Insert option rows ─────────────────────────────────────────────────
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
                """, option_rows)
            conn.commit()
        rows_inserted += len(option_rows)

        # ── Market snapshot ────────────────────────────────────────────────────
        total_call_oi = sum(call_oi_by_strike.values())
        total_put_oi  = sum(put_oi_by_strike.values())
        pcr = round(total_put_oi / total_call_oi, 3) if total_call_oi else None

        # ATM straddle = ATM CE ltp + ATM PE ltp
        atm_call_ltp = next(
            (r["ltp"] for r in option_rows if r["strike"] == atm and r["option_type"] == "CE"), None)
        atm_put_ltp  = next(
            (r["ltp"] for r in option_rows if r["strike"] == atm and r["option_type"] == "PE"), None)
        atm_straddle = (
            round(atm_call_ltp + atm_put_ltp, 2)
            if atm_call_ltp and atm_put_ltp else None
        )
        expected_move = (
            round((atm_straddle / spot) * 100, 2)
            if atm_straddle and spot else None
        )

        # OI walls: strike with highest OI on each side
        call_oi_wall = max(call_oi_by_strike, key=call_oi_by_strike.get) if call_oi_by_strike else None
        put_oi_wall  = max(put_oi_by_strike,  key=put_oi_by_strike.get)  if put_oi_by_strike  else None

        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO market_snapshot (
                        ts, instrument, spot_price, vix, pcr_oi,
                        atm_strike, atm_straddle, expected_move,
                        call_oi_wall, put_oi_wall
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    ts, symbol, spot, vix, pcr,
                    atm, atm_straddle, expected_move,
                    call_oi_wall, put_oi_wall,
                ))
            conn.commit()

    logger.info("Inserted %d option rows across %d symbols.", rows_inserted, len(SYMBOLS))


if __name__ == "__main__":
    collect_once()
