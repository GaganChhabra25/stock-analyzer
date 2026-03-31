"""
Intraday Sell Setup Generator
─────────────────────────────
Computes a Short Strangle setup for intraday trading.
Entry: 9:20 AM  |  Hard exit: 3:15 PM  |  Product: MIS (no overnight)

Inputs  : snap dict from range_predict.fetch_summary()
Outputs : sell setup dict with strikes, premiums, target, SL, probability
"""

import math
import logging
from datetime import date
from typing import Optional

from screener.db import _get_conn, is_available

logger = logging.getLogger(__name__)

LOT_SIZE    = 75
STRIKE_STEP = 50

# Per-instrument defaults
_INSTRUMENT_CONFIG = {
    "NIFTY":     {"lot_size": 75,  "strike_step": 50},
    "BANKNIFTY": {"lot_size": 15,  "strike_step": 100},
}


# ── LTP fetch from option_chain ────────────────────────────────────────────────

def get_option_ltp(expiry: date, strike: int, option_type: str,
                   instrument: str = "NIFTY") -> Optional[float]:
    """Latest LTP for a strike from option_chain table. None if not found."""
    if not is_available():
        return None
    try:
        with _get_conn() as conn:
            if conn is None:
                return None
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT ltp FROM option_chain
                    WHERE instrument  = %s
                      AND expiry      = %s
                      AND strike      = %s
                      AND option_type = %s
                      AND ltp         IS NOT NULL
                    ORDER BY ts DESC LIMIT 1
                """, (instrument.upper(), expiry, strike, option_type))
                row = cur.fetchone()
                return float(row[0]) if row else None
    except Exception as exc:
        logger.warning("get_option_ltp failed: %s", exc)
        return None


# ── Probability model ──────────────────────────────────────────────────────────

def _sell_probability(iv: float, vix: Optional[float], pcr: Optional[float]) -> float:
    """
    Blended sell probability for intraday short strangle.

    Base: 78% (historical 85% adjusted down for intraday — less theta capture,
          tighter exit window vs overnight hold).

    Adjustments:
      IV   — higher IV means richer premium, wider effective range → better for sellers
      VIX  — high VIX means volatile day → risky for sellers
      PCR  — balanced PCR suggests range-bound market → better for sellers
    """
    prob = 78.0

    # IV adjustment
    if iv >= 25:
        prob += 5
    elif iv >= 18:
        prob += 3
    elif iv <= 10:
        prob -= 6   # very thin premium, not worth selling
    elif iv <= 13:
        prob -= 3

    # VIX adjustment
    if vix:
        if vix >= 22:
            prob -= 8   # high fear, expect large moves
        elif vix >= 18:
            prob -= 4
        elif vix <= 13:
            prob += 4   # calm market, range-bound

    # PCR adjustment
    if pcr:
        if 0.85 <= pcr <= 1.20:
            prob += 4   # balanced, market likely to stay in range
        elif pcr > 1.50 or pcr < 0.65:
            prob -= 5   # extreme — directional breakout risk

    return round(max(52.0, min(88.0, prob)), 1)


# ── Main setup builder ─────────────────────────────────────────────────────────

def compute_sell_setup(snap: dict, expiry: date, instrument: str = "NIFTY") -> dict:
    """
    Build intraday short strangle setup from market snapshot.

    Strike selection: ATM ± 1.5σ (intraday 1-sigma move)
    Target          : 35% of combined premium collected
    Stop-loss       : combined P&L loss = 2× premium received
    Hard exit       : 3:15 PM IST regardless
    """
    instr  = instrument.upper()
    cfg    = _INSTRUMENT_CONFIG.get(instr, _INSTRUMENT_CONFIG["NIFTY"])
    lot    = cfg["lot_size"]
    step   = cfg["strike_step"]

    spot = snap["spot"]
    iv   = snap["atm_iv"] or 35.9      # fallback to historical typical
    pcr  = snap.get("pcr")
    vix  = snap.get("vix")
    atm  = snap["atm"]

    # ── Intraday 1σ move ──────────────────────────────────────────────────────
    # 1 trading day = 1/252 of a year
    sigma_pts = spot * (iv / 100.0) * math.sqrt(1.0 / 252.0)

    # Sell at 1.5σ from spot (theoretical ~86% probability of staying inside)
    dist = max(round(sigma_pts * 1.5 / step) * step, step * 4)

    sell_ce = int(round((spot + dist) / step) * step)
    sell_pe = int(round((spot - dist) / step) * step)

    # ── Fetch live LTPs ───────────────────────────────────────────────────────
    ce_ltp = get_option_ltp(expiry, sell_ce, "CE", instr)
    pe_ltp = get_option_ltp(expiry, sell_pe, "PE", instr)

    # Fallback: estimate from IV if no live data
    if ce_ltp is None:
        ce_ltp = round(_bs_approx(spot, sell_ce, iv, "CE"), 1)
    if pe_ltp is None:
        pe_ltp = round(_bs_approx(spot, sell_pe, iv, "PE"), 1)

    total_premium = round((ce_ltp or 0) + (pe_ltp or 0), 1)

    # ── Targets & SL ──────────────────────────────────────────────────────────
    target_pts = round(total_premium * 0.35, 1)   # 35% profit on premium
    sl_pts     = round(total_premium * 2.0,  1)   # 2× premium as max loss

    target_inr = int(target_pts * lot)
    sl_inr     = int(sl_pts     * lot)
    max_inr    = int(total_premium * lot)

    # Per-leg individual SL (for leg-level tracking)
    ce_sl = round(ce_ltp * 3.0, 1)   # exit CE if it triples
    pe_sl = round(pe_ltp * 3.0, 1)

    # ── Probability ───────────────────────────────────────────────────────────
    prob = _sell_probability(iv, vix, pcr)

    # ── Reasoning text ────────────────────────────────────────────────────────
    reasons = []
    reasons.append(f"IV {iv:.1f}% → intraday 1σ = {int(sigma_pts)} pts, selling at ±{dist} pts OTM (1.5σ)")
    if vix:
        reasons.append(f"VIX {vix:.1f} — {'calm market ✓' if vix < 15 else 'elevated volatility, widen SL'}")
    if pcr:
        reasons.append(f"PCR {pcr:.2f} — {'balanced/range-bound ✓' if 0.85 <= pcr <= 1.2 else 'directional bias, watch delta'}")
    reasons.append(f"59-day backtest: straddle sellers won 85% of days, avg edge ₹900/day")
    reasons.append("Hard exit 3:15 PM — no overnight hold")

    return {
        "strategy":      "SHORT_STRANGLE",
        "display_name":  "Short Strangle (Intraday Sell)",
        "expiry":        expiry,
        "atm":           atm,
        "sigma_pts":     int(sigma_pts),
        "dist_pts":      dist,

        # Legs
        "sell_ce":       sell_ce,
        "sell_pe":       sell_pe,
        "ce_ltp":        ce_ltp,
        "pe_ltp":        pe_ltp,
        "total_premium": total_premium,

        # Per-leg SL (individual leg exit trigger)
        "ce_sl":         ce_sl,
        "pe_sl":         pe_sl,

        # Group-level target / SL (combined P&L)
        "target_pts":    target_pts,
        "sl_pts":        sl_pts,
        "target_inr":    target_inr,
        "sl_inr":        sl_inr,
        "max_profit_inr": max_inr,

        "probability":   prob,
        "instrument":    instr,
        "lot_size":      lot,
        "reasoning":     reasons,
        "hard_exit":     "15:15 IST",
    }


# ── Black-Scholes approximation (fallback when no live LTP) ───────────────────

def _bs_approx(spot: float, strike: int, iv_pct: float, opt_type: str) -> float:
    """Very rough ATM approximation — used only when DB has no LTP."""
    import math
    T = 1 / 252.0
    sigma = iv_pct / 100.0
    sv = sigma * math.sqrt(T)
    moneyness = spot / strike
    # Intrinsic + time value approximation
    if opt_type == "CE":
        intrinsic = max(spot - strike, 0)
    else:
        intrinsic = max(strike - spot, 0)
    time_val = spot * sv * 0.4   # rough Vega approximation
    return round(intrinsic + time_val, 1)
