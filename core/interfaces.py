"""
Abstract base classes (interfaces) for external data providers.

Define the contract here; concrete implementations live in providers/.
This decoupling allows:
  - Unit testing with mock providers (no network calls)
  - Swapping data sources without changing analyzer logic
    (e.g., replace yfinance with a paid NSE API in the future)

Python 3.9 compatible.
"""

from abc import ABC, abstractmethod
from typing import Dict, Optional, Tuple

import pandas as pd


class StockDataProvider(ABC):
    """Provides price history and fundamental data for a stock symbol."""

    @abstractmethod
    def fetch(self, symbol: str) -> Tuple[Dict, Optional[pd.DataFrame]]:
        """
        Fetch stock data for *symbol*.

        Returns:
            (info_dict, price_history_df)

            info_dict       — yfinance-compatible dict with keys like
                              trailingPE, returnOnEquity, debtToEquity, etc.
                              Empty dict if fundamentals unavailable.

            price_history_df — DataFrame with columns [Open, High, Low, Close, Volume]
                               indexed by date (at least 1 year of daily bars).
                               None if history unavailable.
        """
        ...


class MFDataProvider(ABC):
    """Provides NAV history for a mutual fund scheme."""

    @abstractmethod
    def fetch_nav_history(self, scheme_code: int) -> Optional[Dict]:
        """
        Fetch NAV history for *scheme_code* from mfapi.in (or equivalent).

        Returns:
            Raw API response dict with structure:
              {
                "status": "SUCCESS",
                "meta": {"scheme_category": "...", ...},
                "data": [{"date": "DD-MM-YYYY", "nav": "123.45"}, ...]
              }
            None if fetch failed or no data found.
        """
        ...
