"""
Dataclass models replacing raw dict returns from analyzers.

Using dataclasses (not TypedDict) to get:
  - Runtime validation via __post_init__
  - Attribute access (result.action) instead of key access (result["action"])
  - Clear, self-documenting contracts between layers

Python 3.9 compatible: uses Optional[X] instead of X | None.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


_VALID_ACTIONS = frozenset({"ADD MORE", "HOLD", "EXIT"})


# ── Stock / ETF result ────────────────────────────────────────────────────────

@dataclass
class StockResult:
    """Result of analyzing a single direct equity or ETF holding."""

    symbol:             str
    name:               str
    quantity:           float
    avg_cost:           float
    investment:         float
    action:             str
    reason:             str

    current_price:      Optional[float] = None
    current_value:      Optional[float] = None
    gain_loss:          Optional[float] = None
    gain_loss_pct:      Optional[float] = None

    is_etf:             bool            = False
    etf_category:       Optional[str]   = None

    # Fundamental / valuation / momentum scores (None for ETFs)
    pe:                 Optional[float] = None
    roe:                Optional[float] = None
    debt_equity:        Optional[float] = None
    fundamentals_score: Optional[float] = None
    valuation_score:    Optional[float] = None
    momentum_score:     Optional[float] = None
    total_score:        Optional[float] = None
    key_metrics:        str             = ""

    def __post_init__(self):
        if self.action not in _VALID_ACTIONS:
            raise ValueError(
                f"StockResult.action must be one of {_VALID_ACTIONS}, got {self.action!r}"
            )


# ── Mutual fund result ────────────────────────────────────────────────────────

@dataclass
class MFResult:
    """Result of analyzing a single mutual fund holding."""

    fund_name:          str
    scheme_code:        int
    monthly_sip:        float
    total_units:        float
    avg_purchase_nav:   float
    invested:           float
    category:           str
    action:             str
    reason:             str
    sip_suggestion:     str

    current_nav:        Optional[float] = None
    current_value:      Optional[float] = None
    gain_loss:          Optional[float] = None
    gain_loss_pct:      Optional[float] = None

    return_1yr:         Optional[float] = None
    return_3yr:         Optional[float] = None
    return_5yr:         Optional[float] = None
    score:              Optional[float] = None

    def __post_init__(self):
        if self.action not in _VALID_ACTIONS:
            raise ValueError(
                f"MFResult.action must be one of {_VALID_ACTIONS}, got {self.action!r}"
            )


# ── Portfolio summary ─────────────────────────────────────────────────────────

@dataclass
class PortfolioSummary:
    """Aggregated portfolio totals."""

    stocks_value:    float
    stocks_invested: float
    stocks_pl:       float
    mf_value:        float
    mf_invested:     float
    mf_pl:           float
    total_value:     float
    total_invested:  float
    stocks_pct:      float
    mf_pct:          float
    monthly_sip:     float
    stocks_pl_pct:   float
    mf_pl_pct:       float
