"""
Aggregates stock + MF results and runs portfolio-level analysis.
"""

from config import GOALS
from utils.corpus_calculator import project_corpus


class PortfolioAnalyzer:
    def __init__(self, stock_results: list, mf_results: list):
        self.stocks = stock_results
        self.mfs    = mf_results

    def summary(self) -> dict:
        stocks_value = sum(
            r["current_value"] for r in self.stocks
            if r.get("current_value") is not None
        )
        mf_value = sum(
            r["current_value"] for r in self.mfs
            if r.get("current_value") is not None
        )
        total = stocks_value + mf_value

        total_invested_stocks = sum(r["investment"] for r in self.stocks)
        total_invested_mf     = sum(
            r["invested"] for r in self.mfs if r.get("invested") is not None
        )

        total_monthly_sip = sum(r["monthly_sip"] for r in self.mfs)

        stocks_pl = round(stocks_value - total_invested_stocks, 2)
        mf_pl     = round(mf_value - total_invested_mf, 2)

        return {
            "stocks_value":        round(stocks_value, 2),
            "stocks_invested":     round(total_invested_stocks, 2),
            "stocks_pl":           stocks_pl,
            "mf_value":            round(mf_value, 2),
            "mf_invested":         round(total_invested_mf, 2),
            "mf_pl":               mf_pl,
            "total_value":         round(total, 2),
            "total_invested":      round(total_invested_stocks + total_invested_mf, 2),
            "stocks_pct":          round(stocks_value / total * 100, 1) if total else 0,
            "mf_pct":              round(mf_value / total * 100, 1) if total else 0,
            "monthly_sip":         round(total_monthly_sip, 2),
            "stocks_pl_pct":       self._portfolio_pl_pct(self.stocks, "gain_loss", "investment"),
            "mf_pl_pct":           self._portfolio_pl_pct(self.mfs, "gain_loss", "invested"),
        }

    @staticmethod
    def _portfolio_pl_pct(items, gain_key, cost_key):
        gain = sum(r.get(gain_key) or 0 for r in items)
        cost = sum(r.get(cost_key) or 0 for r in items)
        return round(gain / cost * 100, 1) if cost else 0

    def projection(self) -> dict:
        s = self.summary()
        return project_corpus(
            current_portfolio_value=s["total_value"],
            monthly_sip=s["monthly_sip"],
            annual_rate=GOALS["expected_cagr"],
            years=GOALS["time_horizon_years"],
        )

    def allocation_warnings(self):
        """Return any portfolio-level warnings."""
        s   = self.summary()
        warnings = []

        # Over-concentration in stocks (>80%)
        if s["stocks_pct"] > 80:
            warnings.append(
                "High equity concentration (>80% in direct stocks). "
                "Consider adding more MF/debt for stability."
            )

        # SIP adequacy
        proj = self.projection()
        if not proj["on_track"] and proj["required_sip"] > s["monthly_sip"] * 1.5:
            warnings.append(
                f"Current SIP (₹{s['monthly_sip']:,.0f}/mo) is too low. "
                f"Need ₹{proj['required_sip']:,.0f}/mo to reach ₹6 Cr target."
            )

        # No SIP running
        if s["monthly_sip"] == 0:
            warnings.append(
                "No active SIP detected. Starting a monthly SIP is essential for "
                "compounding towards your 15-year corpus goal."
            )

        # Too many MFs
        if len(self.mfs) > 6:
            warnings.append(
                f"You hold {len(self.mfs)} MFs — over-diversification reduces focus. "
                "Consolidate to 4-6 high-conviction funds."
            )

        # Too few MFs
        if len(self.mfs) == 0:
            warnings.append(
                "No Mutual Funds detected. Adding 2-3 diversified equity MFs via SIP "
                "provides automatic rebalancing and is ideal for your 15-year goal."
            )

        return warnings
