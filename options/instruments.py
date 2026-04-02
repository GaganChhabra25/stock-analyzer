"""
NFO instrument cache for Kite Connect.

Loads all NIFTY / BANKNIFTY option instruments once per day and
caches to a local JSON file to avoid repeated API calls.
"""

import json
import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

CACHE_DIR  = Path(__file__).parent.parent / "data" / "kite_cache"
CACHE_FILE = CACHE_DIR / "nfo_instruments.json"

# Strike step sizes
STRIKE_STEP = {"NIFTY": 50, "BANKNIFTY": 100}


def load_instruments(kite) -> pd.DataFrame:
    """
    Return DataFrame of NFO instruments for NIFTY and BANKNIFTY.
    Uses cached file if it exists and was created today.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Use cache if fresh (same calendar day)
    if CACHE_FILE.exists():
        mtime = datetime.fromtimestamp(CACHE_FILE.stat().st_mtime).date()
        if mtime == date.today():
            logger.debug("Using cached NFO instruments.")
            with open(CACHE_FILE) as f:
                df_cached = pd.DataFrame(json.load(f))
            # Restore expiry as date objects (JSON stores them as strings)
            df_cached["expiry"] = pd.to_datetime(df_cached["expiry"]).dt.date
            return df_cached

    logger.info("Fetching NFO instruments from Kite Connect...")
    instruments = kite.instruments("NFO")
    df = pd.DataFrame(instruments)

    # Keep only NIFTY and BANKNIFTY options
    df = df[
        df["tradingsymbol"].str.startswith(("NIFTY", "BANKNIFTY")) &
        (df["instrument_type"].isin(["CE", "PE"]))
    ].copy()

    df["expiry"] = pd.to_datetime(df["expiry"]).dt.date

    # Cache to disk
    records = df.copy()
    records["expiry"] = records["expiry"].astype(str)
    with open(CACHE_FILE, "w") as f:
        json.dump(records.to_dict("records"), f)

    logger.info("Cached %d NFO option instruments.", len(df))
    return df


def get_nearest_expiry(df: pd.DataFrame, symbol: str) -> Optional[date]:
    """Return the nearest upcoming expiry for the given symbol."""
    today = date.today()
    expiries = sorted(
        df[df["tradingsymbol"].str.startswith(symbol)]["expiry"].unique()
    )
    future = [e for e in expiries if e >= today]
    return future[0] if future else None


def get_monthly_expiry(df: pd.DataFrame, symbol: str) -> Optional[date]:
    """Return the current month's monthly expiry (last Thursday/Wednesday of the month)."""
    import calendar
    today = date.today()
    expiries = sorted(
        df[df["tradingsymbol"].str.startswith(symbol)]["expiry"].unique()
    )
    # Monthly expiry = last expiry of the current month; if already past, next month's
    for month_offset in (0, 1):
        yr  = today.year + ((today.month + month_offset - 1) // 12)
        mon = (today.month + month_offset - 1) % 12 + 1
        month_expiries = [e for e in expiries if e.year == yr and e.month == mon and e >= today]
        if month_expiries:
            return month_expiries[-1]   # last expiry in that month = monthly
    return None


def get_option_tokens(
    df: pd.DataFrame,
    symbol: str,
    expiry: date,
    atm_strike: int,
    n_strikes: int = 6,
) -> dict:
    """
    Return {(strike, option_type): tradingsymbol} for ATM ± n_strikes.
    Kite quote() API requires 'NFO:tradingsymbol' format, not token IDs.
    """
    step    = STRIKE_STEP.get(symbol, 50)
    strikes = [atm_strike + i * step for i in range(-n_strikes, n_strikes + 1)]

    subset = df[
        df["tradingsymbol"].str.startswith(symbol) &
        (df["expiry"] == expiry) &
        (df["strike"].isin(strikes)) &
        (df["instrument_type"].isin(["CE", "PE"]))
    ]

    return {
        (int(row["strike"]), row["instrument_type"]): row["tradingsymbol"]
        for _, row in subset.iterrows()
    }


def atm_strike(spot: float, symbol: str) -> int:
    """Round spot price to nearest ATM strike."""
    step = STRIKE_STEP.get(symbol, 50)
    return int(round(spot / step) * step)
