"""
YFinanceProvider — fetches NSE/BSE stock data via yfinance.

Implements StockDataProvider so StockAnalyzer can be tested with a mock
and production code can later swap to a paid data source without touching
any scoring logic.
"""

import logging
from typing import Dict, Optional, Tuple

import pandas as pd
import yfinance as yf

from core.interfaces import StockDataProvider
from core.exceptions import DataFetchError

logger = logging.getLogger(__name__)


class YFinanceProvider(StockDataProvider):
    """
    Fetches stock info + 1-year price history from yfinance.

    Tries suffixes in order: .NS (NSE) → .BO (BSE) → bare symbol.
    Returns the first result that has non-empty price history.
    """

    _SUFFIXES = (".NS", ".BO", "")

    def fetch(self, symbol: str) -> Tuple[Dict, Optional[pd.DataFrame]]:
        """
        Returns (info_dict, history_df).
        info_dict is empty dict if no fundamentals available.
        history_df is None if no price history available.
        """
        sym_upper = symbol.upper()

        for suffix in self._SUFFIXES:
            # Avoid double-suffixing if symbol already has one
            if sym_upper.endswith((".NS", ".BO")):
                ticker_sym = sym_upper
            else:
                ticker_sym = sym_upper + suffix

            try:
                ticker = yf.Ticker(ticker_sym)
                info   = ticker.info or {}
                hist   = ticker.history(period="1y")

                if not hist.empty:
                    logger.debug("Fetched %s via %s (%d bars)", symbol, ticker_sym, len(hist))
                    return info, hist

            except Exception as exc:
                logger.debug("yfinance fetch failed for %s: %s", ticker_sym, exc)
                continue

        logger.warning("No data found for symbol %s (tried .NS, .BO, bare)", symbol)
        return {}, None
