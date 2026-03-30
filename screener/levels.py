"""
Key S/R Level Calculator for Options Selling.

Computes weekly & monthly pivot levels, CPR (Central Pivot Range),
and swing-based support/resistance clusters from pure OHLCV data.

All functions are pure/stateless — input data only, no side effects.

Pivot formulas (classical):
  PP = (H + L + C) / 3
  R1 = 2×PP - L       S1 = 2×PP - H
  R2 = PP + (H - L)   S2 = PP - (H - L)
  R3 = H + 2×(PP-L)   S3 = L - 2×(H-PP)

CPR (Central Pivot Range):
  BC = (H + L) / 2
  TC = (PP - BC) + PP
  Width % = (TC - BC) / PP × 100
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional


# ── Pivot computation ─────────────────────────────────────────────────────────

def _pivots(h: float, l: float, c: float) -> dict:
    """Compute full classical pivot set from a single candle's H, L, C."""
    pp = (h + l + c) / 3
    bc = (h + l) / 2
    tc = (pp - bc) + pp
    r1 = 2 * pp - l
    r2 = pp + (h - l)
    r3 = h + 2 * (pp - l)
    s1 = 2 * pp - h
    s2 = pp - (h - l)
    s3 = l - 2 * (h - pp)
    cpr_width = round((tc - bc) / pp * 100, 2) if pp > 0 else 0.0
    return {
        "pp":        round(pp, 2),
        "bc":        round(bc, 2),
        "tc":        round(tc, 2),
        "cpr_width": cpr_width,
        "r1":        round(r1, 2),
        "r2":        round(r2, 2),
        "r3":        round(r3, 2),
        "s1":        round(s1, 2),
        "s2":        round(s2, 2),
        "s3":        round(s3, 2),
    }


# ── Weekly pivots ─────────────────────────────────────────────────────────────

def weekly_pivots(hist: pd.DataFrame) -> Optional[dict]:
    """
    Compute pivot levels for the current week based on last completed week.

    Returns None if insufficient data.
    """
    df = hist.copy()
    df["iso_year"] = df.index.isocalendar().year.values
    df["iso_week"] = df.index.isocalendar().week.values

    grouped = df.groupby(["iso_year", "iso_week"])
    keys    = sorted(grouped.groups.keys())

    # Need at least 2 weeks: prev week + partial current
    if len(keys) < 2:
        return None

    prev_key = keys[-2]
    grp      = grouped.get_group(prev_key)

    h = float(grp["High"].max())
    l = float(grp["Low"].min())
    c = float(grp["Close"].iloc[-1])

    result = _pivots(h, l, c)
    result["label"] = f"Wk {prev_key[1]}/{str(prev_key[0])[-2:]}"
    result["prev_high"] = round(h, 2)
    result["prev_low"]  = round(l, 2)
    result["prev_close"]= round(c, 2)
    return result


# ── Monthly pivots ────────────────────────────────────────────────────────────

def monthly_pivots(close: pd.Series, high: pd.Series, low: pd.Series) -> Optional[dict]:
    """
    Compute pivot levels for the current month based on last completed month.

    Returns None if insufficient data.
    """
    monthly_h = high.resample("ME").max().dropna()
    monthly_l = low.resample("ME").min().dropna()
    monthly_c = close.resample("ME").last().dropna()

    # Need at least 2 completed months
    if len(monthly_c) < 2:
        return None

    h = float(monthly_h.iloc[-2])
    l = float(monthly_l.iloc[-2])
    c = float(monthly_c.iloc[-2])

    result = _pivots(h, l, c)
    result["label"]      = monthly_c.index[-2].strftime("%b %Y")
    result["prev_high"]  = round(h, 2)
    result["prev_low"]   = round(l, 2)
    result["prev_close"] = round(c, 2)
    return result


# ── Swing S/R clusters ────────────────────────────────────────────────────────

def swing_levels(high: pd.Series, low: pd.Series, lookback: int = 120,
                 cluster_pct: float = 1.5) -> dict:
    """
    Identify key swing high/low clusters over the last `lookback` trading days.

    Two swing points are merged into one zone if they are within `cluster_pct`%
    of each other. Returns top 3 resistance and top 3 support zones, each
    with a touch count (more touches = stronger zone).

    Returns:
        {
          "resistance": [{"level": float, "touches": int}, ...],
          "support":    [{"level": float, "touches": int}, ...],
        }
    """
    h = high.tail(lookback)
    l = low.tail(lookback)

    # Local swing highs: candle higher than its 5 neighbours on each side
    window = 5
    swing_highs = []
    for i in range(window, len(h) - window):
        segment = h.iloc[i - window: i + window + 1]
        if float(h.iloc[i]) == float(segment.max()):
            swing_highs.append(float(h.iloc[i]))

    swing_lows = []
    for i in range(window, len(l) - window):
        segment = l.iloc[i - window: i + window + 1]
        if float(l.iloc[i]) == float(segment.min()):
            swing_lows.append(float(l.iloc[i]))

    def cluster(points: list) -> list:
        if not points:
            return []
        points.sort(reverse=True)
        clusters = []
        for p in points:
            merged = False
            for c in clusters:
                if abs(p - c["level"]) / c["level"] * 100 <= cluster_pct:
                    # Weighted average keeps the cluster representative
                    c["level"] = round(
                        (c["level"] * c["touches"] + p) / (c["touches"] + 1), 2
                    )
                    c["touches"] += 1
                    merged = True
                    break
            if not merged:
                clusters.append({"level": round(p, 2), "touches": 1})
        clusters.sort(key=lambda x: x["touches"], reverse=True)
        return clusters[:3]

    return {
        "resistance": cluster(swing_highs),
        "support":    cluster(swing_lows),
    }


# ── Options selling recommendation ────────────────────────────────────────────

def options_selling_levels(
    cmp:        float,
    direction:  str,
    probability: float,
    weekly:     Optional[dict],
    monthly:    Optional[dict],
    swings:     dict,
    atr_pct:    float,
) -> dict:
    """
    Derive actionable options-selling levels based on pivot analysis.

    Strategy logic:
      - Bullish (UP, prob ≥ 75%): Sell PUT near best support (monthly S1 or swing support)
      - Bearish (DOWN, prob ≥ 75%): Sell CALL near best resistance (monthly R1 or swing resistance)
      - Neutral / conflicted: Iron Condor between monthly S2 and R2
      - Strangle: sell PUT at S2, sell CALL at R2 (wider, safer)

    Returns a dict with recommended action, levels, and confidence note.
    """
    m = monthly or {}
    w = weekly  or {}

    ms1 = m.get("s1", 0)
    ms2 = m.get("s2", 0)
    mr1 = m.get("r1", 0)
    mr2 = m.get("r2", 0)
    mpp = m.get("pp", cmp)

    ws1 = w.get("s1", 0)
    ws2 = w.get("s2", 0)
    wr1 = w.get("r1", 0)
    wr2 = w.get("r2", 0)

    # Best swing support/resistance near CMP
    sw_sup = next(
        (z["level"] for z in swings.get("support", []) if z["level"] < cmp * 0.99),
        ms1,
    )
    sw_res = next(
        (z["level"] for z in swings.get("resistance", []) if z["level"] > cmp * 1.01),
        mr1,
    )

    # Choose the stronger (closer to CMP) of pivot vs swing
    put_sell = max(ms1, sw_sup) if ms1 > 0 and sw_sup > 0 else (ms1 or sw_sup or cmp * 0.95)
    call_sell = min(mr1, sw_res) if mr1 > 0 and sw_res > 0 else (mr1 or sw_res or cmp * 1.05)

    # Put sell SL: monthly S2 breach
    put_sl   = ms2 if ms2 > 0 else round(put_sell * 0.97, 2)
    call_sl  = mr2 if mr2 > 0 else round(call_sell * 1.03, 2)

    # Distance checks — don't recommend if level is too close (<1 ATR) or too far (>4 ATR)
    def _dist_ok(level: float) -> bool:
        if level <= 0:
            return False
        dist_pct = abs(cmp - level) / cmp * 100
        return atr_pct <= dist_pct <= atr_pct * 4

    if direction == "UP" and probability >= 75 and _dist_ok(put_sell):
        strategy    = "Sell PUT"
        sell_level  = round(put_sell, 2)
        sl_level    = round(put_sl, 2)
        logic       = f"Bullish bias ({probability:.0f}%). Sell PUT at monthly S1 / swing support. SL if monthly S2 breaks."
        confidence  = "HIGH" if probability >= 82 else "MEDIUM"
        color       = "#56d364"

    elif direction == "DOWN" and probability >= 75 and _dist_ok(call_sell):
        strategy    = "Sell CALL"
        sell_level  = round(call_sell, 2)
        sl_level    = round(call_sl, 2)
        logic       = f"Bearish bias ({probability:.0f}%). Sell CALL at monthly R1 / swing resistance. SL if monthly R2 breaks."
        confidence  = "HIGH" if probability >= 82 else "MEDIUM"
        color       = "#f85149"

    else:
        # Iron Condor / Strangle — sell both sides in the wider monthly range
        strategy    = "Iron Condor"
        sell_level  = 0.0   # both sides
        sl_level    = 0.0
        logic       = (
            f"No strong directional edge ({probability:.0f}%). "
            f"Sell CALL ≥ R2 (₹{mr2:,.2f}) + Sell PUT ≤ S2 (₹{ms2:,.2f}) if vol is decent."
        )
        confidence  = "LOW"
        color       = "#d29922"

    # Weekly levels for near-term option sellers
    weekly_sell_put  = round(ws1, 2) if ws1 > 0 else 0.0
    weekly_sell_call = round(wr1, 2) if wr1 > 0 else 0.0

    return {
        "strategy":          strategy,
        "sell_level":        sell_level,
        "sl_level":          sl_level,
        "logic":             logic,
        "confidence":        confidence,
        "color":             color,
        "monthly_put_sell":  round(put_sell, 2),
        "monthly_call_sell": round(call_sell, 2),
        "monthly_s2":        round(ms2, 2),
        "monthly_r2":        round(mr2, 2),
        "weekly_put_sell":   weekly_sell_put,
        "weekly_call_sell":  weekly_sell_call,
    }


# ── Master entry point ────────────────────────────────────────────────────────

def calc_key_levels(
    hist:        pd.DataFrame,
    direction:   str,
    probability: float,
    atr_pct:     float,
) -> dict:
    """
    Compute all key S/R levels and options selling recommendation.

    Args:
        hist:        Full OHLCV DataFrame (DatetimeIndex, columns: Open/High/Low/Close/Volume)
        direction:   "UP" or "DOWN" (from score_direction)
        probability: composite probability score (from calc_probability)
        atr_pct:     ATR as % of price (for distance validation)

    Returns dict with keys:
        weekly, monthly, swings, options_rec
    """
    close = hist["Close"]
    high  = hist["High"]
    low   = hist["Low"]
    cmp   = float(close.iloc[-1])

    w_pivots = weekly_pivots(hist)
    m_pivots = monthly_pivots(close, high, low)
    swings   = swing_levels(high, low)
    opts_rec = options_selling_levels(
        cmp, direction, probability, w_pivots, m_pivots, swings, atr_pct
    )

    return {
        "weekly":      w_pivots,
        "monthly":     m_pivots,
        "swings":      swings,
        "options_rec": opts_rec,
    }
