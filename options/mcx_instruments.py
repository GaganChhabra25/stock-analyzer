"""
MCX instrument cache and helpers for Natural Gas and Crude Oil options.

Mirrors the structure of options/instruments.py (which handles NFO).
All MCX instruments are loaded from kite.instruments("MCX") and cached
once per day.

Key MCX specifics:
  - No cash segment — futures LTP is used as the "spot" / underlying price.
  - Options are on futures (options-on-futures model).
  - Monthly expiry only (no weekly expiry for commodities).
  - Instrument name field (not tradingsymbol prefix) is used for filtering
    to avoid picking up mini contracts (NATURALGASM, CRUDEOILM).
"""

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).parent.parent / "data" / "kite_cache"
MCX_CACHE  = CACHE_DIR / "mcx_instruments.json"

# Commodities we collect
MCX_SYMBOLS = ["NATURALGAS", "CRUDEOIL"]

# Nearest valid strike step per symbol (in ₹)
MCX_STRIKE_STEP = {
    "NATURALGAS": 10,    # price ~₹200–400, 10-pt step = ₹100 range for 10 strikes
    "CRUDEOIL":   100,   # price ~₹5000–7000, 100-pt step = ₹1000 range for 10 strikes
}


def load_mcx_instruments(kite) -> pd.DataFrame:
    """
    Return DataFrame of MCX FUT + CE/PE instruments for NATURALGAS and CRUDEOIL.
    Uses a same-day cache file to avoid repeated API calls.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if MCX_CACHE.exists():
        mtime = datetime.fromtimestamp(MCX_CACHE.stat().st_mtime).date()
        if mtime == date.today():
            logger.debug("Using cached MCX instruments.")
            with open(MCX_CACHE) as f:
                df = pd.DataFrame(json.load(f))
            df["expiry"] = pd.to_datetime(df["expiry"]).dt.date
            return df

    logger.info("Fetching MCX instruments from Kite Connect...")
    instruments = kite.instruments("MCX")
    df = pd.DataFrame(instruments)

    # Filter by name (not tradingsymbol) to exclude mini contracts
    df = df[
        df["name"].isin(MCX_SYMBOLS) &
        df["instrument_type"].isin(["CE", "PE", "FUT"])
    ].copy()

    df["expiry"] = pd.to_datetime(df["expiry"]).dt.date

    records = df.copy()
    records["expiry"] = records["expiry"].astype(str)
    with open(MCX_CACHE, "w") as f:
        json.dump(records.to_dict("records"), f)

    logger.info("Cached %d MCX instruments.", len(df))
    return df


def get_mcx_nearest_expiry(df: pd.DataFrame, symbol: str) -> Optional[date]:
    """Return the nearest upcoming expiry for the given MCX futures symbol."""
    today = date.today()
    fut_df = df[(df["name"] == symbol) & (df["instrument_type"] == "FUT")]
    expiries = sorted(fut_df["expiry"].unique())
    future = [e for e in expiries if e >= today]
    return future[0] if future else None


def get_mcx_futures_ltp(kite, symbol: str, df: pd.DataFrame) -> Optional[float]:
    """
    Fetch the LTP of the near-month futures contract.
    This is used as the underlying price for ATM calculation and Greeks.
    """
    expiry = get_mcx_nearest_expiry(df, symbol)
    if not expiry:
        logger.warning("[MCX] No futures expiry found for %s", symbol)
        return None

    fut_row = df[
        (df["name"] == symbol) &
        (df["instrument_type"] == "FUT") &
        (df["expiry"] == expiry)
    ]
    if fut_row.empty:
        logger.warning("[MCX] No futures row for %s expiry %s", symbol, expiry)
        return None

    tradingsymbol = fut_row.iloc[0]["tradingsymbol"]
    try:
        q = kite.quote([f"MCX:{tradingsymbol}"])
        return q.get(f"MCX:{tradingsymbol}", {}).get("last_price")
    except Exception as exc:
        logger.error("[MCX] Futures LTP fetch failed for %s: %s", symbol, exc)
        return None


def get_mcx_option_tokens(
    df: pd.DataFrame,
    symbol: str,
    expiry: date,
    atm: int,
    n_strikes: int,
) -> dict:
    """
    Return {(strike, option_type): tradingsymbol} for ATM ± n_strikes.
    Only CE and PE instrument types are returned (FUT excluded).
    """
    step    = MCX_STRIKE_STEP.get(symbol, 50)
    strikes = [atm + i * step for i in range(-n_strikes, n_strikes + 1)]

    subset = df[
        (df["name"] == symbol) &
        (df["expiry"] == expiry) &
        (df["strike"].isin(strikes)) &
        (df["instrument_type"].isin(["CE", "PE"]))
    ]

    return {
        (int(row["strike"]), row["instrument_type"]): row["tradingsymbol"]
        for _, row in subset.iterrows()
    }


def atm_strike_mcx(price: float, symbol: str) -> int:
    """Round the futures price to the nearest valid MCX strike."""
    step = MCX_STRIKE_STEP.get(symbol, 50)
    return int(round(price / step) * step)
