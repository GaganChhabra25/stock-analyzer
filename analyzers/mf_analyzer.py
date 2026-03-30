"""
Analyzes Indian Mutual Funds using mfapi.in (free, no auth required).
API: https://api.mfapi.in/mf/{scheme_code}

Uses MFApiProvider by default; inject a custom MFDataProvider for testing:

    mock_provider = MockMFProvider(...)
    analyzer = MFAnalyzer(provider=mock_provider)
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from config import MF_THRESHOLDS, PREFERRED_MF_CATEGORIES, GOALS
from core.interfaces import MFDataProvider
from core.models import MFResult

logger = logging.getLogger(__name__)


class MFAnalyzer:
    """
    Analyzes individual mutual fund holdings.

    Args:
        provider: Optional MFDataProvider. Defaults to MFApiProvider.
                  Pass a mock in tests to avoid real network calls.
    """

    def __init__(self, provider: Optional[MFDataProvider] = None):
        self.t = MF_THRESHOLDS

        if provider is None:
            from providers.mfapi_provider import MFApiProvider
            provider = MFApiProvider()
        self._provider = provider

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _nav_on_date(self, nav_data: list, target_date: datetime) -> Optional[float]:
        """Find NAV closest to target_date (searches backward up to 10 days)."""
        for delta in range(10):
            d = (target_date - timedelta(days=delta)).strftime("%d-%m-%Y")
            for entry in nav_data:
                if entry["date"] == d:
                    return float(entry["nav"])
        return None

    def _calc_returns(self, nav_data: list) -> dict:
        if not nav_data:
            return {}
        current_nav = float(nav_data[0]["nav"])
        now         = datetime.today()
        returns     = {}
        for label, years in [("return_1yr", 1), ("return_3yr", 3), ("return_5yr", 5)]:
            past_nav = self._nav_on_date(nav_data, now - timedelta(days=365 * years))
            if past_nav and past_nav > 0:
                total_return = (current_nav / past_nav) - 1
                ann_return   = ((1 + total_return) ** (1 / years) - 1) * 100
                returns[label] = round(ann_return, 2)
        returns["current_nav"] = current_nav
        return returns

    # ── Scoring ───────────────────────────────────────────────────────────────

    def _score(self, returns: dict, category: str, expense_ratio: float) -> float:
        score, total = 0, 0

        r1 = returns.get("return_1yr")
        if r1 is not None:
            total += 25
            if r1 >= self.t["min_1yr_return"] * 1.5:  score += 25
            elif r1 >= self.t["min_1yr_return"]:        score += 18
            elif r1 >= self.t["min_1yr_return"] * 0.7: score += 8

        r3 = returns.get("return_3yr")
        if r3 is not None:
            total += 35
            if r3 >= self.t["min_3yr_return"] * 1.3:  score += 35
            elif r3 >= self.t["min_3yr_return"]:        score += 26
            elif r3 >= self.t["min_3yr_return"] * 0.7: score += 12

        r5 = returns.get("return_5yr")
        if r5 is not None:
            total += 25
            if r5 >= self.t["min_5yr_return"] * 1.2:  score += 25
            elif r5 >= self.t["min_5yr_return"]:        score += 18
            elif r5 >= self.t["min_5yr_return"] * 0.7: score += 8

        if expense_ratio and expense_ratio > 0:
            total += 15
            if expense_ratio <= 0.3:    score += 15
            elif expense_ratio <= 0.8:  score += 12
            elif expense_ratio <= 1.2:  score += 8
            elif expense_ratio <= self.t["max_expense_ratio"]: score += 4

        if any(cat.lower() in category.lower() for cat in PREFERRED_MF_CATEGORIES):
            score += 5
            total += 5

        return (score / total * 100) if total else 50.0

    # ── Recommendation ────────────────────────────────────────────────────────

    def _recommend(self, score: float, category: str,
                   monthly_sip: float, returns: dict):
        r5     = returns.get("return_5yr")
        cat_ok = any(c.lower() in category.lower() for c in PREFERRED_MF_CATEGORIES)

        if score >= 70 and cat_ok:
            action   = "ADD MORE"
            reason   = "Top performer in a preferred category. Increase SIP if possible."
            sip_note = (
                f"Consider increasing SIP to ₹{monthly_sip * 1.25:,.0f} (+25%)"
                if monthly_sip > 0 else "Consider starting SIP."
            )
        elif score >= 55 and cat_ok:
            action   = "HOLD"
            reason   = "Good consistent returns. Suitable for 15-year goal."
            sip_note = "Maintain current SIP."
        elif score >= 40:
            action   = "HOLD"
            reason   = "Moderate performance. Review after 6 months; compare with category peers."
            sip_note = "Pause new SIP if better alternatives exist."
        elif not cat_ok:
            action   = "HOLD"
            reason   = (
                f"'{category}' is outside preferred categories for your moderate long-term goal. "
                "Consider switching to Flexi Cap / Large & Mid Cap."
            )
            sip_note = "Redirect SIP to preferred categories."
        else:
            action   = "EXIT"
            reason   = "Underperforming. Redeploy into a better fund in same category."
            sip_note = "Stop SIP and switch to higher-rated fund."

        if r5 is not None and r5 < GOALS["expected_cagr"] * 100 * 0.7:
            reason += f" 5yr CAGR only {r5:.1f}% — below 15% target."

        return action, reason, sip_note

    # ── Category fallback advice ──────────────────────────────────────────────

    def _category_advice(self, category: str, monthly_sip: float,
                         gain_pct: Optional[float] = None):
        """Fallback advice based purely on fund category when no return history available."""
        cat = category.lower()
        if "small cap" in cat:
            action   = "HOLD"
            reason   = "Small Cap Fund — high risk/reward. Good for 15yr horizon but volatile. Don't over-allocate (max 15% of portfolio)."
            sip_note = "Maintain SIP; don't increase beyond 15% portfolio share."
        elif "flexi cap" in cat or "multi cap" in cat:
            action   = "ADD MORE"
            reason   = "Flexi/Multi Cap Fund — excellent for long-term. Fund manager has freedom to shift across market caps. Ideal for your 15yr goal."
            sip_note = "Core holding. Continue or increase SIP."
        elif "large & mid" in cat:
            action   = "ADD MORE"
            reason   = "Large & Mid Cap Fund — balanced growth + stability. Good fit for moderate investor with 15yr view."
            sip_note = "Maintain or increase SIP."
        elif "large cap" in cat:
            action   = "HOLD"
            reason   = "Large Cap Fund — stable, lower volatility. Good anchor holding. Consider pairing with a mid/flexi cap for higher growth."
            sip_note = "Maintain SIP as core allocation."
        elif "focused" in cat:
            action   = "HOLD"
            reason   = "Focused Fund — concentrated 25-30 stock portfolio. High conviction but higher individual stock risk."
            sip_note = "Keep allocation small (<15% of MF portfolio)."
        elif "contra" in cat or "value" in cat:
            action   = "HOLD"
            reason   = "Value/Contra Fund — long-term oriented strategy. Works well for 15yr holding. Patience required during underperformance phases."
            sip_note = "Hold. Contra funds need 5+ years to deliver; stay the course."
        elif "sectoral" in cat or "thematic" in cat:
            action   = "HOLD"
            reason   = "Sectoral/Thematic Fund — concentrated sector bet. Higher risk. Review sector outlook before adding more."
            sip_note = "Keep allocation small (<10%). Consider stopping SIP if sector outlook is weak."
        elif "elss" in cat:
            action   = "HOLD"
            reason   = "ELSS — tax-saving + equity growth. 3yr lock-in aligns well with long-term investing. Continue for Section 80C benefit."
            sip_note = "Continue for tax benefit (up to ₹1.5L/yr in 80C)."
        else:
            action   = "HOLD"
            reason   = "Diversified equity fund. Hold and review returns after 1 year."
            sip_note = "Maintain current SIP."

        if gain_pct is not None and gain_pct < -15:
            reason += f" Currently down {gain_pct:.1f}% — don't panic; continue SIP to average down."
        elif gain_pct is not None and gain_pct > 30:
            reason += f" Up {gain_pct:.1f}% — good performance. Stay invested."

        return action, reason, sip_note

    # ── Public API ────────────────────────────────────────────────────────────

    def analyze(self, fund_name: str, scheme_code: int, monthly_sip: float,
                total_units: float, avg_purchase_nav: float,
                expense_ratio: float = 0.0,
                current_nav_csv: Optional[float] = None,
                current_value_csv: Optional[float] = None,
                pnl_csv: Optional[float] = None,
                category_hint: Optional[str] = None) -> MFResult:

        logger.debug("Analyzing MF: %s (scheme %s)", fund_name, scheme_code)

        invested = round(total_units * avg_purchase_nav, 2)
        cur_val  = current_value_csv or None
        gain     = pnl_csv if pnl_csv is not None else (round(cur_val - invested, 2) if cur_val else None)
        gain_pct = round(gain / invested * 100, 2) if gain is not None and invested else None

        category = category_hint or "Unknown"
        returns  = {}

        raw = self._provider.fetch_nav_history(scheme_code) if scheme_code else None

        if raw:
            meta     = raw.get("meta", {})
            nav_data = raw.get("data", [])
            api_cat  = meta.get("scheme_category", "")
            if api_cat:
                category = api_cat

            returns     = self._calc_returns(nav_data)
            current_nav = returns.pop("current_nav", None)

            if current_nav and not current_nav_csv:
                cur_val  = round(current_nav * total_units, 2)
                gain     = round(cur_val - invested, 2)
                gain_pct = round((cur_val / invested - 1) * 100, 2) if invested > 0 else None
        else:
            current_nav = current_nav_csv

        score              = self._score(returns, category, expense_ratio)
        action, reason, sip_note = self._recommend(score, category, monthly_sip, returns)

        if not returns:
            action, reason, sip_note = self._category_advice(category, monthly_sip, gain_pct)

        return MFResult(
            fund_name        = fund_name,
            scheme_code      = scheme_code,
            monthly_sip      = monthly_sip,
            total_units      = total_units,
            avg_purchase_nav = avg_purchase_nav,
            invested         = invested,
            current_nav      = round(current_nav, 4) if current_nav else current_nav_csv,
            current_value    = cur_val,
            gain_loss        = gain,
            gain_loss_pct    = gain_pct,
            return_1yr       = returns.get("return_1yr"),
            return_3yr       = returns.get("return_3yr"),
            return_5yr       = returns.get("return_5yr"),
            category         = category,
            score            = round(score, 1),
            action           = action,
            reason           = reason,
            sip_suggestion   = sip_note,
        )
