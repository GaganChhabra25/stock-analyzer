"""
MCX exchange collector — NATURALGAS and CRUDEOIL options.

MCX-specific differences from NFO:
  - Underlying = near-month futures LTP (no cash segment for commodities).
  - Monthly expiry; next-month added automatically within 5 days of expiry (rollover).
  - Market hours: 9:00 AM – 11:30 PM IST (much longer than NSE).
  - On certain Indian holidays MCX runs an evening-only session (5:00 PM – 11:30 PM).
  - A small set of days MCX is fully closed (Republic Day, Independence Day, etc.).
  - No VIX equivalent — vix stored as NULL in market_snapshot.
  - 10 strikes above and below ATM (vs 20 for NFO).
"""

import logging
from datetime import date, time, datetime
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

_EVENING_OPEN  = time(17, 0)   # 5:00 PM IST — evening session start
_MARKET_OPEN   = time(9, 0)    # 9:00 AM IST — normal session start
_MARKET_CLOSE  = time(23, 30)  # 11:30 PM IST


class MCXCollector(ExchangeCollector):
    exchange     = "MCX"
    symbols      = ["NATURALGAS", "CRUDEOIL"]
    n_strikes    = 10           # ATM ± 10 as requested
    market_open  = _MARKET_OPEN
    market_close = _MARKET_CLOSE
    quote_prefix = "MCX:"
    vix_symbol   = None         # no commodity VIX

    def __init__(self, symbols: Optional[list[str]] = None) -> None:
        """Optionally isolate collection to one or more MCX symbols."""
        self.symbols = list(symbols) if symbols is not None else list(type(self).symbols)

    def is_open(self) -> bool:
        """
        MCX-aware market hours check.

        Three cases:
        1. MCX_HOLIDAYS       → fully closed, return False.
        2. MCX_EVENING_ONLY_DAYS → evening session only (5 PM – 11:30 PM IST).
        3. Normal weekday      → full session (9 AM – 11:30 PM IST).
        """
        from zoneinfo import ZoneInfo
        from config import MCX_HOLIDAYS, MCX_EVENING_ONLY_DAYS

        now     = datetime.now(ZoneInfo("Asia/Kolkata"))
        today   = now.date().strftime("%Y-%m-%d")
        now_t   = now.time()

        if today in MCX_HOLIDAYS:
            logger.info("[MCX] Holiday (fully closed) — skipping.")
            return False

        if today in MCX_EVENING_ONLY_DAYS:
            open_result = _EVENING_OPEN <= now_t <= _MARKET_CLOSE
            if not open_result:
                logger.info("[MCX] Evening-only day — market opens at 17:00 IST.")
            return open_result

        # Normal trading day
        return _MARKET_OPEN <= now_t <= _MARKET_CLOSE

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
