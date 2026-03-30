"""
providers — Concrete implementations of core data provider interfaces.

Each module wraps one external data source and can be swapped or mocked
independently of the analyzer logic.
"""

from providers.yfinance_provider import YFinanceProvider
from providers.mfapi_provider import MFApiProvider

__all__ = ["YFinanceProvider", "MFApiProvider"]
