"""
Aggregates stock + MF results and runs portfolio-level analysis.
"""

import logging
from typing import List

from config import GOALS
from core.models import MFResult, PortfolioSummary, StockResult
from utils.corpus_calculator import project_corpus

logger = logging.getLogger(__name__)


class PortfolioAnalyzer:
    """
    Aggregates a list of StockResult and MFResult into portfolio-level metrics.

    Args:
        stock_results: List of StockResult (both direct stocks and ETFs).
        mf_results:    List of MFResult.
    """

    def __init__(self, stock_results: List[StockResult], mf_results: List[MFResult]):
        self.stocks = stock_results
        self.mfs    = mf_results

    def summary(self) -> PortfolioSummary:
        stocks_value = sum(
            r.current_value for r in self.stocks
            if r.current_value is not None
        )
        mf_value = sum(
            r.current_value for r in self.mfs
            if r.current_value is not None
        )
        total = stocks_value + mf_value

        total_invested_stocks = sum(r.investment for r in self.stocks)
        total_invested_mf     = sum(
            r.invested for r in self.mfs if r.invested is not None
        )

        total_monthly_sip = sum(r.monthly_sip for r in self.mfs)

        stocks_pl = round(stocks_value - total_invested_stocks, 2)
        mf_pl     = round(mf_value     - total_invested_mf,     2)

        return PortfolioSummary(
            stocks_value    = round(stocks_value, 2),
            stocks_invested = round(total_invested_stocks, 2),
            stocks_pl       = stocks_pl,
            mf_value        = round(mf_value, 2),
            mf_invested     = round(total_invested_mf, 2),
            mf_pl           = mf_pl,
            total_value     = round(total, 2),
            total_invested  = round(total_invested_stocks + total_invested_mf, 2),
            stocks_pct      = round(stocks_value / total * 100, 1) if total else 0,
            mf_pct          = round(mf_value / total * 100, 1)     if total else 0,
            monthly_sip     = round(total_monthly_sip, 2),
            stocks_pl_pct   = self._pl_pct(self.stocks, "gain_loss", "investment"),
            mf_pl_pct       = self._pl_pct(self.mfs,    "gain_loss", "invested"),
        )

    @staticmethod
    def _pl_pct(items, gain_attr: str, cost_attr: str) -> float:
        gain = sum(getattr(r, gain_attr) or 0 for r in items)
        cost = sum(getattr(r, cost_attr) or 0 for r in items)
        return round(gain / cost * 100, 1) if cost else 0

    def projection(self) -> dict:
        s = self.summary()
        return project_corpus(
            current_portfolio_value = s.total_value,
            monthly_sip             = s.monthly_sip,
            annual_rate             = GOALS["expected_cagr"],
            years                   = GOALS["time_horizon_years"],
        )

    def allocation_warnings(self) -> List[str]:
        """Return a list of portfolio-level warning strings."""
        s        = self.summary()
        warnings = []

        if s.stocks_pct > 80:
            warnings.append(
                "High equity concentration (>80% in direct stocks). "
                "Consider adding more MF/debt for stability."
            )

        proj = self.projection()
        if not proj["on_track"] and proj["required_sip"] > s.monthly_sip * 1.5:
            warnings.append(
                f"Current SIP (₹{s.monthly_sip:,.0f}/mo) is too low. "
                f"Need ₹{proj['required_sip']:,.0f}/mo to reach ₹6 Cr target."
            )

        if s.monthly_sip == 0:
            warnings.append(
                "No active SIP detected. Starting a monthly SIP is essential for "
                "compounding towards your 15-year corpus goal."
            )

        if len(self.mfs) > 6:
            warnings.append(
                f"You hold {len(self.mfs)} MFs — over-diversification reduces focus. "
                "Consolidate to 4-6 high-conviction funds."
            )

        if len(self.mfs) == 0:
            warnings.append(
                "No Mutual Funds detected. Adding 2-3 diversified equity MFs via SIP "
                "provides automatic rebalancing and is ideal for your 15-year goal."
            )

        return warnings
