"""
Technical indicator functions — pure, stateless, fully testable.

Extracted from screener/analyzer.py so they can be:
  - Unit tested with synthetic price data (no network calls)
  - Reused by future strategy modules without importing StockScreener

All functions are module-level (no class) because they are pure transforms:
output depends only on input data, not on any mutable state.
"""

import numpy as np
import pandas as pd

from core.constants import (
    DIRECTION_SCORE_SCALE,
    PROB_RANGE_MIN,
    PROB_RANGE_MAX,
)


# ── Low-level indicators ──────────────────────────────────────────────────────

def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Compute RSI-{period} for a price series."""
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(com=period - 1, adjust=True, min_periods=period).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=period - 1, adjust=True, min_periods=period).mean()
    rs    = gain / loss
    return 100 - (100 / (1 + rs))


def macd(close: pd.Series):
    """
    Compute MACD line, signal line, and histogram.

    Returns: (macd_series, signal_series, histogram_series)
    """
    ema12  = close.ewm(span=12, adjust=False).mean()
    ema26  = close.ewm(span=26, adjust=False).mean()
    macd_  = ema12 - ema26
    signal = macd_.ewm(span=9, adjust=False).mean()
    hist   = macd_ - signal
    return macd_, signal, hist


def bollinger(close: pd.Series, period: int = 20):
    """
    Compute Bollinger Bands.

    Returns: (middle_band, upper_band, lower_band)
    """
    sma = close.rolling(period).mean()
    std = close.rolling(period).std()
    return sma, sma + 2 * std, sma - 2 * std


def safe_last(series: pd.Series) -> float:
    """Return the last non-NaN value as float, or 0.0 if series is empty."""
    v = series.dropna()
    return float(v.iloc[-1]) if not v.empty else 0.0


# ── Composite direction scoring ───────────────────────────────────────────────

def score_direction(close: pd.Series, volume: pd.Series):
    """
    Score the directional bias of a stock using 5 technical signals.

    Returns:
        (raw_score, norm_score, direction, breakdown, rsi_val, vol_ratio)

        raw_score   — signed integer in approx [-110, +110]
        norm_score  — normalised float in [0, 100] (50 = neutral)
        direction   — "UP" or "DOWN"
        breakdown   — dict of per-signal values and scores (for display)
        rsi_val     — latest RSI value (exposed for downstream use)
        vol_ratio   — 5-day / 20-day volume ratio (exposed for probability calc)
    """
    latest = float(close.iloc[-1])
    breakdown = {}
    score = 0

    # ── RSI (max contribution ±30) ────────────────────────────────────────
    rsi_ser = rsi(close)
    rsi_val = safe_last(rsi_ser)

    if   rsi_val < 30:  rs = 30
    elif rsi_val < 40:  rs = 20
    elif rsi_val < 50:  rs = 10
    elif rsi_val < 60:  rs = -5
    elif rsi_val < 70:  rs = -18
    else:               rs = -30
    score += rs
    breakdown["rsi"] = {"value": round(rsi_val, 1), "score": rs}

    # ── MACD (max contribution ±25) ───────────────────────────────────────
    macd_, sig, _ = macd(close)
    mv, sv = safe_last(macd_), safe_last(sig)

    if   mv > sv and mv > 0:  ms = 25
    elif mv > sv:              ms = 14
    elif mv < sv and mv < 0:  ms = -25
    else:                      ms = -14
    score += ms
    breakdown["macd"] = {
        "value":  round(mv, 3),
        "signal": round(sv, 3),
        "score":  ms,
        "cross":  "Bullish" if mv > sv else "Bearish",
    }

    # ── Price vs SMAs (max contribution ±25) ─────────────────────────────
    sma20  = close.rolling(20).mean()
    sma50  = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    s20, s50, s200 = safe_last(sma20), safe_last(sma50), safe_last(sma200)
    ps = 0
    if s20  > 0: ps += 10 if latest > s20  else -10
    if s50  > 0: ps += 10 if latest > s50  else -10
    if s200 > 0: ps +=  5 if latest > s200 else -5
    score += ps
    breakdown["sma"] = {
        "sma20":  round(s20, 2),
        "sma50":  round(s50, 2),
        "sma200": round(s200, 2),
        "score":  ps,
        "vs20":   round((latest / s20  - 1) * 100, 1) if s20  else 0,
        "vs50":   round((latest / s50  - 1) * 100, 1) if s50  else 0,
        "vs200":  round((latest / s200 - 1) * 100, 1) if s200 else 0,
    }

    # ── Bollinger Band position (max contribution ±20) ────────────────────
    _, bb_up, bb_low = bollinger(close)
    bu, bl = safe_last(bb_up), safe_last(bb_low)
    bbs = 0
    if bu > bl:
        bb_pos = (latest - bl) / (bu - bl)   # 0 = lower band, 1 = upper band
        breakdown["bb_pos_pct"] = round(bb_pos * 100, 1)
        if   bb_pos < 0.15: bbs = 20
        elif bb_pos < 0.30: bbs = 12
        elif bb_pos > 0.85: bbs = -20
        elif bb_pos > 0.70: bbs = -12
    score += bbs
    breakdown["bollinger"] = {"upper": round(bu, 2), "lower": round(bl, 2), "score": bbs}

    # ── Volume trend amplifier (max contribution ±10) ─────────────────────
    v5  = float(volume.tail(5).mean())
    v20 = float(volume.tail(20).mean())
    vol_ratio = round(v5 / v20, 2) if v20 > 0 else 1.0
    vs = 0
    if   vol_ratio > 2.0: vs = 10
    elif vol_ratio > 1.3: vs = 6
    elif vol_ratio < 0.6: vs = -8
    elif vol_ratio < 0.8: vs = -4
    score += vs
    breakdown["volume"] = {"ratio": vol_ratio, "score": vs}

    # ── Normalise to 0-100 ────────────────────────────────────────────────
    norm = min(100.0, max(0.0, 50.0 + score * DIRECTION_SCORE_SCALE))
    direction = "UP" if score >= 0 else "DOWN"

    return score, norm, direction, breakdown, rsi_val, vol_ratio


# ── Win rate ──────────────────────────────────────────────────────────────────

def calc_win_rate(monthly_hist: list, direction: str):
    """
    Compute the fraction of past months where the actual movement matched
    the predicted direction.

    Returns:
        (win_rate_pct, backtest_list)
    """
    from datetime import datetime
    past = [m for m in monthly_hist if m["label"] != datetime.today().strftime("%b %Y")]
    if not past:
        return 50.0, []

    wins = 0
    backtest = []
    for m in past:
        actual_up    = m["ret"] > 0
        predicted_up = direction == "UP"
        win          = actual_up == predicted_up
        wins += int(win)
        backtest.append({
            "label":     m["label"],
            "ret":       m["ret"],
            "direction": m["direction"],
            "predicted": direction,
            "win":       win,
        })

    win_rate = round(wins / len(past) * 100, 1)
    return win_rate, backtest


def weekly_win_rate(close: pd.Series) -> float:
    """Percent of Mon→Fri weeks that were positive over the last ~12 months."""
    wkly = close.resample("W-FRI").last().pct_change().dropna() * 100
    if len(wkly) < 8:
        return 50.0
    return round(float((wkly > 0).sum()) / len(wkly) * 100, 1)


# ── Composite probability ─────────────────────────────────────────────────────

def calc_probability(norm_score: float, win_rate: float, vol_ratio: float) -> float:
    """
    Compute composite trade probability combining technical strength,
    historical accuracy, and volume confirmation.

    Output is clamped to [PROB_RANGE_MIN, PROB_RANGE_MAX] (72–92 by default)
    so only high-conviction setups are surfaced.
    """
    tech_contrib = norm_score / 100           # 0.0 – 1.0
    hist_contrib = win_rate / 100             # 0.0 – 1.0
    vol_contrib  = min(vol_ratio / 5, 0.08)  # max 0.08 bonus

    raw = tech_contrib * 0.45 + hist_contrib * 0.45 + vol_contrib

    # Stretch to configured range
    span = PROB_RANGE_MAX - PROB_RANGE_MIN    # 20 points
    prob = PROB_RANGE_MIN + (raw - 0.5) * span * 2
    return round(min(PROB_RANGE_MAX, max(PROB_RANGE_MIN, prob)), 1)
