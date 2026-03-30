"""
Shared pytest fixtures for the stock-analyzer test suite.

Fixtures here are auto-discovered by pytest and available in all test files
without explicit imports.
"""

import pytest
import pandas as pd
import numpy as np
from unittest.mock import MagicMock
from datetime import datetime, timedelta


# ── Price series factories ────────────────────────────────────────────────────

def make_price_series(n: int = 252, start: float = 1000.0,
                      trend: float = 0.0005) -> pd.Series:
    """
    Build a realistic synthetic daily close price series.

    Args:
        n:      Number of trading days.
        start:  Starting price.
        trend:  Daily drift (0.0005 ≈ +13% per year).
    """
    rng  = np.random.default_rng(seed=42)
    returns = rng.normal(trend, 0.012, n)
    prices  = start * np.cumprod(1 + returns)
    idx = pd.date_range(end=datetime.today(), periods=n, freq="B")
    return pd.Series(prices, index=idx, name="Close")


def make_ohlcv(n: int = 252, start: float = 1000.0) -> pd.DataFrame:
    """Build a synthetic OHLCV DataFrame."""
    close = make_price_series(n, start)
    rng   = np.random.default_rng(seed=42)
    high  = close * (1 + rng.uniform(0.001, 0.02, n))
    low   = close * (1 - rng.uniform(0.001, 0.02, n))
    open_ = close.shift(1).fillna(close.iloc[0])
    vol   = rng.integers(500_000, 5_000_000, n).astype(float)
    return pd.DataFrame({
        "Open":   open_.values,
        "High":   high,
        "Low":    low,
        "Close":  close.values,
        "Volume": vol,
    }, index=close.index)


# ── Provider mocks ────────────────────────────────────────────────────────────

@pytest.fixture
def mock_yf_provider():
    """
    StockDataProvider mock returning a healthy stock profile.

    Fundamentals: PE 20, ROE 18%, D/E 0.5, Revenue growth 12%
    Price history: 252 bars trending upward
    """
    hist = make_ohlcv(252, start=1400.0)
    info = {
        "trailingPE":        20.0,
        "returnOnEquity":    0.18,   # 18%
        "debtToEquity":      50.0,   # yfinance returns D/E × 100 for many stocks
        "revenueGrowth":     0.12,
        "currentRatio":      2.1,
        "priceToBook":       2.5,
        "enterpriseToEbitda": 12.0,
        "currentPrice":      float(hist["Close"].iloc[-1]),
    }
    provider = MagicMock()
    provider.fetch.return_value = (info, hist)
    return provider


@pytest.fixture
def mock_yf_provider_no_data():
    """StockDataProvider mock that returns no data (simulates network failure)."""
    provider = MagicMock()
    provider.fetch.return_value = ({}, None)
    return provider


@pytest.fixture
def mock_mf_provider():
    """
    MFDataProvider mock returning 5 years of fake NAV history.
    NAV grows at ~14% per year: starts at 50, ends at ~97.
    """
    today    = datetime.today()
    nav_data = []
    nav      = 50.0
    for days_ago in range(365 * 5, -1, -1):
        date = today - timedelta(days=days_ago)
        if date.weekday() < 5:   # weekdays only
            nav  *= (1 + 0.00053)  # ≈14% per year daily compound
            nav_data.append({"date": date.strftime("%d-%m-%Y"), "nav": f"{nav:.4f}"})

    provider = MagicMock()
    provider.fetch_nav_history.return_value = {
        "status": "SUCCESS",
        "meta": {
            "scheme_name":     "Test Flexi Cap Fund - Direct Growth",
            "scheme_category": "Flexi Cap Fund",
            "scheme_code":     "999999",
        },
        "data": list(reversed(nav_data)),  # mfapi returns newest first
    }
    return provider


@pytest.fixture
def mock_mf_provider_no_data():
    """MFDataProvider mock that returns None (simulates API failure)."""
    provider = MagicMock()
    provider.fetch_nav_history.return_value = None
    return provider
