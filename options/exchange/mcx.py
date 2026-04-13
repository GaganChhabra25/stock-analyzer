"""
MCX exchange collector — NATURALGAS and CRUDEOIL options.

MCX-specific differences from NFO:
  - Underlying = near-month futures LTP (no cash segment for commodities).
  - Monthly expiry; next-month added automatically within 5 days of expiry (rollover).
  - Market hours: 9:00 AM – 11:30 PM IST (much longer than NSE).
  - No VIX equivalent — vix stored as NULL in market_snapshot.
  - 10 strikes above and below ATM (vs 20 for NFO).
"""

import logging
from datetime import date, time
from typing import Optional

import pandas as pd

from options.exchange.base import ExchangeCollector
from options.mcx_instruments import (
    load_mcx_instruments,
    get_mcx_options_expiries,
    get_mcx_futures_ltp,
    get_mcx_option_tokens,
    atm_strike_mcx,
)

logger = logging.getLogger(__name__)


class MCXCollector(ExchangeCollector):
    exchange     = "MCX"
    symbols      = ["NATURALGAS", "CRUDEOIL"]
    n_strikes    = 10           # ATM ± 10 as requested
    market_open  = time(9, 0)
    market_close = time(23, 30)
    quote_prefix = "MCX:"
    vix_symbol   = None         # no commodity VIX

    def load_instruments(self, kite) -> pd.DataFrame:
        return load_mcx_instruments(kite)

    def get_spot(self, kite, symbol: str, df: pd.DataFrame) -> Optional[float]:
        """Fetch near-month futures LTP as the underlying price."""
        return get_mcx_futures_ltp(kite, symbol, df)

    def get_expiries(self, df: pd.DataFrame, symbol: str) -> list:
        """
        Returns [current_expiry] normally.
        Within 5 days of expiry returns [current_expiry, next_expiry]
        so rollover liquidity is captured.
        """
        return get_mcx_options_expiries(df, symbol)

    def get_option_tokens(
        self, df: pd.DataFrame, symbol: str, expiry: date, atm: int, n_strikes: int
    ) -> dict:
        return get_mcx_option_tokens(df, symbol, expiry, atm, n_strikes)

    def atm_strike(self, price: float, symbol: str) -> int:
        return atm_strike_mcx(price, symbol)
