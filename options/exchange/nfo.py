"""
NFO exchange collector — NIFTY options.

Delegates all instrument helpers to the existing options/instruments.py
module so that backfill.py and app.py imports remain unaffected.
"""

import logging
from datetime import date, time
from typing import Optional

import pandas as pd

from options.exchange.base import ExchangeCollector
from options.instruments import (
    load_instruments,
    get_nearest_expiry,
    get_option_tokens,
    atm_strike,
)

logger = logging.getLogger(__name__)

_SPOT_SYMBOLS = {
    "NIFTY":     "NSE:NIFTY 50",
}


class NFOCollector(ExchangeCollector):
    exchange     = "NFO"
    symbols      = ["NIFTY"]
    n_strikes    = 20           # ATM ± 20 — covers 1.5σ sell setups
    market_open  = time(9, 14)
    market_close = time(15, 31)
    quote_prefix = "NFO:"
    vix_symbol   = "NSE:INDIA VIX"

    def load_instruments(self, kite) -> pd.DataFrame:
        return load_instruments(kite)

    def get_spot(self, kite, symbol: str, df: pd.DataFrame) -> Optional[float]:
        """Fetch NSE index quote (df not needed for NFO)."""
        try:
            key = _SPOT_SYMBOLS[symbol]
            q   = kite.quote([key])
            return q.get(key, {}).get("last_price")
        except Exception as exc:
            logger.error("[NFO] Spot fetch failed for %s: %s", symbol, exc)
            return None

    def get_expiries(self, df: pd.DataFrame, symbol: str) -> list:
        """Return only the nearest/current weekly expiry."""
        nearest = get_nearest_expiry(df, symbol)
        return [nearest] if nearest else []

    def get_option_tokens(
        self, df: pd.DataFrame, symbol: str, expiry: date, atm: int, n_strikes: int
    ) -> dict:
        return get_option_tokens(df, symbol, expiry, atm, n_strikes)

    def atm_strike(self, price: float, symbol: str) -> int:
        return atm_strike(price, symbol)
