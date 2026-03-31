"""
Technical signals for intraday sell setup.
Fetches data via Kite Connect (already authenticated).

Signals computed:
  EMA 20/50 (daily)   → trend direction
  PDH / PDL           → previous day high/low (key S/R)
  VWAP (15-min today) → intraday bias
  RSI 14 (15-min)     → momentum state
  Bollinger width     → squeeze / expansion detection

Degrades gracefully: returns empty dict if Kite not available.
"""

import math
import logging
from datetime import date, datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# NSE index instrument tokens (constant, won't change)
_INDEX_TOKENS = {
    "NIFTY":     256265,
    "BANKNIFTY": 260105,
}

# Simple in-process cache: {instrument: (timestamp, signals)}
_cache: dict = {}
_CACHE_TTL_SECS = 300   # 5 minutes


# ── helpers ────────────────────────────────────────────────────────────────────

def _ema(closes: list, period: int) -> float:
    k = 2.0 / (period + 1)
    val = closes[0]
    for c in closes[1:]:
        val = c * k + val * (1 - k)
    return val


def _rsi(closes: list, period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [max(d, 0) for d in deltas[-period:]]
    losses = [abs(min(d, 0)) for d in deltas[-period:]]
    avg_g = sum(gains)  / period
    avg_l = sum(losses) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return round(100 - 100 / (1 + rs), 1)


def _vwap(candles: list) -> Optional[float]:
    """VWAP from list of dicts with high/low/close/volume."""
    tv = pv = 0.0
    for c in candles:
        tp = (c["high"] + c["low"] + c["close"]) / 3.0
        v  = c.get("volume", 0) or 0
        pv += tp * v
        tv += v
    return round(pv / tv, 2) if tv > 0 else None


def _bb_squeeze(closes: list, period: int = 20) -> tuple:
    """Returns (bb_width_pct, is_squeeze). Squeeze = width < 1.5%."""
    if len(closes) < period:
        return None, False
    window = closes[-period:]
    mean = sum(window) / period
    std  = math.sqrt(sum((x - mean) ** 2 for x in window) / period)
    upper = mean + 2 * std
    lower = mean - 2 * std
    width = round((upper - lower) / mean * 100, 2) if mean else None
    return width, (width is not None and width < 1.5)


# ── main ───────────────────────────────────────────────────────────────────────

def get_tech_signals(instrument: str = "NIFTY") -> dict:
    """
    Returns tech signal dict. Empty dict if Kite unavailable.
    Cached for 5 minutes.
    """
    import time as _time
    instr = instrument.upper()

    # Cache check
    if instr in _cache:
        ts, sig = _cache[instr]
        if _time.time() - ts < _CACHE_TTL_SECS:
            return sig

    try:
        from options.kite_auth import get_kite
        kite = get_kite()
        if kite is None:
            return {}

        token = _INDEX_TOKENS.get(instr)
        if token is None:
            return {}

        today     = date.today()
        from_date = today - timedelta(days=70)   # enough for EMA50

        # ── Daily candles → EMA20/50, PDH/PDL, Bollinger ─────────────────────
        daily = kite.historical_data(token, from_date, today, "day")
        if len(daily) < 22:
            return {}

        d_closes = [c["close"] for c in daily]
        ema20    = _ema(d_closes[-21:],  20)
        ema50    = _ema(d_closes[-51:],  50) if len(d_closes) >= 51 else None
        bb_width, squeeze = _bb_squeeze(d_closes)

        prev_day  = daily[-2] if len(daily) >= 2 else daily[-1]
        pdh       = prev_day["high"]
        pdl       = prev_day["low"]
        prev_close = prev_day["close"]

        if ema50:
            if ema20 > ema50 * 1.003:
                trend = "BULLISH"
            elif ema20 < ema50 * 0.997:
                trend = "BEARISH"
            else:
                trend = "NEUTRAL"
        else:
            trend = "BULLISH" if d_closes[-1] > ema20 else "BEARISH"

        # ── 15-min candles today → VWAP, RSI ─────────────────────────────────
        intraday = kite.historical_data(token, today, today, "15minute")
        vwap     = _vwap(intraday) if intraday else None
        rsi15    = _rsi([c["close"] for c in intraday]) if len(intraday) >= 15 else None

        spot_now = intraday[-1]["close"] if intraday else d_closes[-1]
        vwap_bias = None
        if vwap:
            vwap_bias = "ABOVE" if spot_now > vwap else "BELOW"

        signals = {
            "trend":       trend,
            "ema20":       round(ema20, 1),
            "ema50":       round(ema50, 1) if ema50 else None,
            "pdh":         pdh,
            "pdl":         pdl,
            "prev_close":  prev_close,
            "vwap":        vwap,
            "vwap_bias":   vwap_bias,
            "rsi15":       rsi15,
            "bb_width":    bb_width,
            "bb_squeeze":  squeeze,
            "spot_now":    spot_now,
        }

        _cache[instr] = (_time.time(), signals)
        return signals

    except Exception as exc:
        logger.warning("get_tech_signals(%s) failed: %s", instr, exc)
        return {}
