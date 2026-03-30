"""
Custom exception hierarchy for stock-analyzer.

Raise these instead of returning None or bare except-pass so callers can
distinguish error types and handle them explicitly.
"""


class StockAnalyzerError(Exception):
    """Base exception for all stock-analyzer errors."""


# ── Data providers ────────────────────────────────────────────────────────────

class ProviderError(StockAnalyzerError):
    """An external data provider failed (network, API, parse error)."""


class DataFetchError(ProviderError):
    """Network or API call to an external provider failed."""


class DataNotFoundError(ProviderError):
    """Symbol or scheme code returned no usable data from the provider."""


# ── Holdings loading ──────────────────────────────────────────────────────────

class InvalidHoldingsError(StockAnalyzerError):
    """A holdings CSV is malformed or contains invalid rows."""


class InvalidSchemeError(StockAnalyzerError):
    """A mutual fund scheme code is invalid or not found on mfapi.in."""
