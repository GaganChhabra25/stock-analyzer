"""
Analyzes NSE/BSE direct-equity stocks using yfinance.
Scores: Fundamentals (50%) + Valuation (30%) + Momentum (20%).
For ETFs: uses pre-known price from Zerodha CSV and gives category-based advice.

Uses YFinanceProvider by default; inject a custom StockDataProvider for testing:

    mock_provider = MockProvider(...)
    analyzer = StockAnalyzer(provider=mock_provider)
"""

import logging
from typing import Optional

import numpy as np

from config import STOCK_THRESHOLDS, SCORE_WEIGHTS, GOALS
from core.constants import SCORE_GREAT, SCORE_GOOD, SCORE_WARN, SCORE_BAD, SCORE_DEFAULT_NEUTRAL
from core.interfaces import StockDataProvider
from core.models import StockResult

logger = logging.getLogger(__name__)


# ── ETF advice map ────────────────────────────────────────────────────────────

ETF_ADVICE = {
    "Broad Market": (
        "ADD MORE",
        "Core broad-market ETF — ideal anchor for 15-year goal. Keep accumulating via SIP.",
    ),
    "Broad Market - Midcap": (
        "HOLD",
        "Midcap index ETF adds growth kicker. Hold; don't over-allocate (keep <10% of portfolio).",
    ),
    "Sector - Banking": (
        "HOLD",
        "Banking sector ETF. Banks are cyclical; good long-term but avoid over-concentration.",
    ),
    "Sector - IT": (
        "HOLD",
        "IT sector under pressure from global slowdown. Hold existing; avoid adding more now.",
    ),
    "Sector - Pharma": (
        "HOLD",
        "Pharma is defensive & growth-oriented. Hold for long term.",
    ),
    "Sector - Auto": (
        "HOLD",
        "Auto sector in EV transition. Hold existing; review in 1 year.",
    ),
    "Sector - Defence": (
        "HOLD",
        "Defence ETF is a thematic bet. Keep small allocation (<5%). Don't chase after the rally.",
    ),
    "Sector - Metal": (
        "HOLD",
        "Metal ETF is highly cyclical. Hold if bullish on global commodity cycle; else trim.",
    ),
    "Sector - Energy": (
        "HOLD",
        "Oil ETF is cyclical. Global energy transition is a long-term headwind. Avoid adding.",
    ),
    "Sector - Healthcare": (
        "HOLD",
        "Healthcare ETF — defensive and growing. Good for long-term.",
    ),
    "Sector - Infrastructure": (
        "HOLD",
        "Rail/Infra ETF is a thematic bet. India infra story is strong but valuations are stretched. Hold.",
    ),
    "Commodity - Gold": (
        "HOLD",
        "Gold ETF is a good inflation hedge. 5-10% allocation is appropriate. Avoid exceeding that.",
    ),
    "International - US Tech": (
        "ADD MORE",
        "Nasdaq 100 ETF provides USD exposure + US tech growth. Excellent diversifier. Hold.",
    ),
}


class StockAnalyzer:
    """
    Analyzes individual stock and ETF holdings.

    Args:
        provider: Optional StockDataProvider. Defaults to YFinanceProvider.
                  Pass a mock in tests to avoid real network calls.
    """

    def __init__(self, provider: Optional[StockDataProvider] = None):
        self.t = STOCK_THRESHOLDS
        self.w = SCORE_WEIGHTS

        if provider is None:
            from providers.yfinance_provider import YFinanceProvider
            provider = YFinanceProvider()
        self._provider = provider

    # ── ETF analysis (no data provider needed) ────────────────────────────────

    def analyze_etf(self, symbol: str, quantity: float, avg_cost: float,
                    ltp: float, current_value: float, pnl: float,
                    etf_name: str, etf_category: str) -> StockResult:
        gain_loss_pct = round((ltp / avg_cost - 1) * 100, 2) if avg_cost else None
        investment    = round(quantity * avg_cost, 2)
        action, reason = ETF_ADVICE.get(
            etf_category,
            ("HOLD", "ETF — hold as part of diversified portfolio."),
        )
        return StockResult(
            symbol          = symbol,
            name            = etf_name,
            quantity        = quantity,
            avg_cost        = avg_cost,
            current_price   = ltp,
            current_value   = round(current_value, 2),
            investment      = investment,
            gain_loss       = round(pnl, 2) if pnl is not None else round(current_value - investment, 2),
            gain_loss_pct   = gain_loss_pct,
            is_etf          = True,
            etf_category    = etf_category,
            action          = action,
            reason          = reason,
            key_metrics     = f"Category: {etf_category}",
        )

    # ── Scoring ───────────────────────────────────────────────────────────────

    def _score_fundamentals(self, info: dict) -> float:
        score, total = 0, 0

        roe = info.get("returnOnEquity")
        if roe is not None:
            roe_pct = roe * 100
            total += 30
            if roe_pct >= self.t["min_roe"] * 2:     score += 30
            elif roe_pct >= self.t["min_roe"] * 1.5: score += 22
            elif roe_pct >= self.t["min_roe"]:        score += 15
            elif roe_pct >= self.t["min_roe"] * 0.5: score += 5

        raw_de = info.get("debtToEquity")
        if raw_de is not None:
            de = raw_de / 100 if raw_de > 3 else raw_de
            total += 25
            if de <= self.t["max_debt_equity"] * 0.33:   score += 25
            elif de <= self.t["max_debt_equity"] * 0.67: score += 18
            elif de <= self.t["max_debt_equity"]:         score += 10

        rg = info.get("revenueGrowth")
        if rg is not None:
            rg_pct = rg * 100
            total += 25
            if rg_pct >= self.t["min_revenue_growth"] * 3:   score += 25
            elif rg_pct >= self.t["min_revenue_growth"] * 2: score += 18
            elif rg_pct >= self.t["min_revenue_growth"]:      score += 12
            elif rg_pct >= 0:                                  score += 4

        cr = info.get("currentRatio")
        if cr is not None:
            total += 20
            if cr >= self.t["min_current_ratio"] * 2:     score += 20
            elif cr >= self.t["min_current_ratio"] * 1.5: score += 15
            elif cr >= self.t["min_current_ratio"]:        score += 8

        return (score / total * 100) if total else SCORE_DEFAULT_NEUTRAL

    def _score_valuation(self, info: dict) -> float:
        score, total = 0, 0

        pe = info.get("trailingPE")
        if pe and pe > 0:
            total += 50
            if pe <= self.t["max_pe"] * 0.40:   score += 50
            elif pe <= self.t["max_pe"] * 0.60: score += 38
            elif pe <= self.t["max_pe"] * 0.80: score += 25
            elif pe <= self.t["max_pe"]:         score += 12

        pb = info.get("priceToBook")
        if pb and pb > 0:
            total += 25
            if pb <= 1.5:  score += 25
            elif pb <= 3:  score += 18
            elif pb <= 5:  score += 10
            elif pb <= 8:  score += 4

        ev = info.get("enterpriseToEbitda")
        if ev and ev > 0:
            total += 25
            if ev <= 8:    score += 25
            elif ev <= 15: score += 18
            elif ev <= 25: score += 10
            elif ev <= 35: score += 4

        return (score / total * 100) if total else SCORE_DEFAULT_NEUTRAL

    def _score_momentum(self, hist) -> float:
        if hist is None or hist.empty:
            return SCORE_DEFAULT_NEUTRAL
        close = hist["Close"]
        score = 0

        if len(close) >= 2:
            yr_ret = (close.iloc[-1] / close.iloc[0] - 1) * 100
            if yr_ret >= 30:   score += 35
            elif yr_ret >= 15: score += 28
            elif yr_ret >= 5:  score += 20
            elif yr_ret >= 0:  score += 10
            else:              score += 2
        else:
            score += 17

        if len(close) >= 200:
            ma50  = close.tail(50).mean()
            ma200 = close.tail(200).mean()
            cur   = close.iloc[-1]
            if cur > ma50 > ma200:  score += 35
            elif cur > ma200:       score += 22
            elif cur > ma50:        score += 12
        else:
            score += 17

        if len(close) >= 30:
            ann_vol = close.pct_change().dropna().std() * np.sqrt(252) * 100
            if ann_vol < 20:   score += 30
            elif ann_vol < 30: score += 22
            elif ann_vol < 45: score += 12
            elif ann_vol < 60: score += 5
        else:
            score += 15

        return min(float(score), 100.0)

    # ── Recommendation ────────────────────────────────────────────────────────

    @staticmethod
    def _recommend(total_score: float, gain_loss_pct: Optional[float]):
        if total_score >= SCORE_GREAT:
            action = "ADD MORE"
            reason = "Strong fundamentals + good valuation. Good for 15-year goal."
        elif total_score >= SCORE_GOOD:
            action = "HOLD"
            reason = "Solid stock. Continue holding; review after next earnings."
        elif total_score >= SCORE_WARN:
            action = "HOLD"
            reason = "Moderate signals. Watch quarterly results before adding more."
            if gain_loss_pct is not None and gain_loss_pct > 50:
                reason += " Consider partial profit booking."
        elif total_score >= SCORE_BAD:
            action = "EXIT"
            reason = "Weak fundamentals or expensive. Exit and redeploy."
        else:
            action = "EXIT"
            reason = "Poor score across all parameters. Exit and reinvest in stronger holdings."
        return action, reason

    # ── Public API ────────────────────────────────────────────────────────────

    def analyze(self, symbol: str, quantity: float, avg_cost: float,
                ltp: Optional[float] = None,
                current_value_csv: Optional[float] = None,
                pnl_csv: Optional[float] = None) -> StockResult:
        """Analyze a direct equity stock. Uses LTP from CSV if provided."""
        logger.debug("Analyzing %s", symbol)
        info, hist = self._provider.fetch(symbol)

        investment = round(quantity * avg_cost, 2)

        # Prefer CSV price; fall back to yfinance
        price = ltp
        if price is None:
            price = info.get("currentPrice") or info.get("regularMarketPrice")
            if price is None and hist is not None and not hist.empty:
                price = float(hist["Close"].iloc[-1])

        cur_val  = current_value_csv or (round(float(price) * quantity, 2) if price else None)
        gain     = pnl_csv if pnl_csv is not None else (round(cur_val - investment, 2) if cur_val else None)
        gain_pct = round((float(price) / avg_cost - 1) * 100, 2) if price and avg_cost else None

        # Default result when no data available
        if not info and (hist is None or hist.empty):
            logger.warning("No data for %s — defaulting to HOLD", symbol)
            return StockResult(
                symbol        = symbol,
                name          = symbol,
                quantity      = quantity,
                avg_cost      = avg_cost,
                current_price = round(float(price), 2) if price else None,
                current_value = cur_val,
                investment    = investment,
                gain_loss     = gain,
                gain_loss_pct = gain_pct,
                action        = "HOLD",
                reason        = "Could not fetch fundamentals from yfinance.",
            )

        f = self._score_fundamentals(info)
        v = self._score_valuation(info)
        m = self._score_momentum(hist)
        total = f * self.w["fundamentals"] + v * self.w["valuation"] + m * self.w["momentum"]

        roe_raw = info.get("returnOnEquity")
        de_raw  = info.get("debtToEquity")

        action, reason = self._recommend(total, gain_pct)

        return StockResult(
            symbol             = symbol,
            name               = symbol,
            quantity           = quantity,
            avg_cost           = avg_cost,
            current_price      = round(float(price), 2) if price else None,
            current_value      = cur_val,
            investment         = investment,
            gain_loss          = gain,
            gain_loss_pct      = gain_pct,
            is_etf             = False,
            pe                 = round(float(info.get("trailingPE") or 0), 1),
            roe                = round(float(roe_raw or 0) * 100, 1),
            debt_equity        = round(
                float(de_raw or 0) / 100 if (de_raw or 0) > 3 else float(de_raw or 0), 2
            ),
            fundamentals_score = round(f, 1),
            valuation_score    = round(v, 1),
            momentum_score     = round(m, 1),
            total_score        = round(total, 1),
            key_metrics        = (
                f"PE={round(float(info.get('trailingPE') or 0), 1)}  "
                f"ROE={round(float(roe_raw or 0) * 100, 1)}%  "
                f"D/E={round(float(de_raw or 0) / 100 if (de_raw or 0) > 3 else float(de_raw or 0), 2)}  "
                f"[F={f:.0f} V={v:.0f} M={m:.0f}]"
            ),
            action = action,
            reason = reason,
        )
