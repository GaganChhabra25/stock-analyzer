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

    # ── Technical signals (Kite) ─────────────────────────────────────────────
    try:
        from options.technicals import get_tech_signals
        tech = get_tech_signals(instr)
    except Exception:
        tech = {}

    # ── Intraday 1σ move ──────────────────────────────────────────────────────
    # 1 trading day = 1/252 of a year
    sigma_pts = spot * (iv / 100.0) * math.sqrt(1.0 / 252.0)

    # Base distance = 1.5σ; skew away from trend direction
    ce_mult = 1.5
    pe_mult = 1.5
    if tech.get("trend") == "BULLISH":
        ce_mult = 1.7   # widen CE — uptrend, CE breach less likely
        pe_mult = 1.4   # tighten PE — support stronger in uptrend
    elif tech.get("trend") == "BEARISH":
        ce_mult = 1.4
        pe_mult = 1.7   # widen PE — downtrend, PE breach less likely

    dist_ce = max(round(sigma_pts * ce_mult / step) * step, step * 4)
    dist_pe = max(round(sigma_pts * pe_mult / step) * step, step * 4)

    # PDH/PDL override: never sell CE below PDH or PE above PDL
    pdh = tech.get("pdh")
    pdl = tech.get("pdl")

    sell_ce = int(round((spot + dist_ce) / step) * step)
    sell_pe = int(round((spot - dist_pe) / step) * step)

    if pdh and sell_ce < pdh:
        sell_ce = int(round(pdh / step + 1) * step)   # push CE above PDH
    if pdl and sell_pe > pdl:
        sell_pe = int(round(pdl / step - 1) * step)   # push PE below PDL

    # ── Fetch live LTPs ───────────────────────────────────────────────────────
    ce_ltp = get_option_ltp(expiry, sell_ce, "CE", instr)
    pe_ltp = get_option_ltp(expiry, sell_pe, "PE", instr)

    # Fallback: estimate from IV if no live data
    if ce_ltp is None:
        ce_ltp = round(_bs_approx(spot, sell_ce, iv, "CE", expiry), 1)
    if pe_ltp is None:
        pe_ltp = round(_bs_approx(spot, sell_pe, iv, "PE", expiry), 1)

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

    # ── Probability (base + tech adjustments) ────────────────────────────────
    prob = _sell_probability(iv, vix, pcr)

    if tech:
        # BB squeeze: breakout imminent → risky for sellers
        if tech.get("bb_squeeze"):
            prob -= 6
        # RSI extremes: overbought/oversold → directional risk
        rsi = tech.get("rsi15")
        if rsi is not None:
            if rsi > 72 or rsi < 28:
                prob -= 4
            elif 40 <= rsi <= 60:
                prob += 3   # range-bound momentum
        # VWAP and trend aligned → market is well-behaved
        trend = tech.get("trend", "")
        vwap_bias = tech.get("vwap_bias", "")
        if (trend == "BULLISH" and vwap_bias == "ABOVE") or \
           (trend == "BEARISH" and vwap_bias == "BELOW"):
            prob += 3   # trending cleanly, range is well-defined
        prob = round(max(50.0, min(89.0, prob)), 1)

    # ── Reasoning text ────────────────────────────────────────────────────────
    reasons = []
    reasons.append(
        f"IV {iv:.1f}% → intraday 1σ = {int(sigma_pts)} pts │ "
        f"CE at +{dist_ce} pts ({ce_mult}σ), PE at −{dist_pe} pts ({pe_mult}σ)"
    )
    if vix:
        reasons.append(f"VIX {vix:.1f} — {'calm ✓' if vix < 15 else 'elevated, widen SL'}")
    if pcr:
        reasons.append(f"PCR {pcr:.2f} — {'range-bound ✓' if 0.85 <= pcr <= 1.2 else 'directional bias'}")

    # Tech reasons
    if tech:
        trend = tech.get("trend", "")
        if trend:
            reasons.append(
                f"Trend: {trend} (EMA20 {tech['ema20']:,.0f}"
                + (f" / EMA50 {tech['ema50']:,.0f}" if tech.get('ema50') else "")
                + f") — {'CE side safer ✓' if trend == 'BULLISH' else 'PE side safer ✓' if trend == 'BEARISH' else 'neutral'}"
            )
        if tech.get("vwap"):
            reasons.append(
                f"VWAP {tech['vwap']:,.0f} — spot is {tech.get('vwap_bias','?').lower()} VWAP"
                + (" ✓" if (trend == "BULLISH" and tech.get("vwap_bias") == "ABOVE")
                        or (trend == "BEARISH" and tech.get("vwap_bias") == "BELOW") else "")
            )
        if tech.get("rsi15") is not None:
            rsi = tech["rsi15"]
            reasons.append(
                f"RSI(14) 15-min: {rsi} — "
                + ("⚠ overbought, CE risky" if rsi > 72 else
                   "⚠ oversold, PE risky"   if rsi < 28 else
                   "range-bound zone ✓")
            )
        if pdh and pdl:
            reasons.append(f"PDH {pdh:,.0f} / PDL {pdl:,.0f} — strikes placed outside prev-day range")
        if tech.get("bb_squeeze"):
            reasons.append(f"⚠ Bollinger squeeze detected (width {tech['bb_width']:.1f}%) — breakout risk, reduce size")

    reasons.append("Hard exit 3:15 PM — no overnight hold")

    return {
        "strategy":      "SHORT_STRANGLE",
        "display_name":  "Short Strangle (Intraday Sell)",
        "expiry":        expiry,
        "atm":           atm,
        "sigma_pts":     int(sigma_pts),
        "dist_ce_pts":   dist_ce,
        "dist_pe_pts":   dist_pe,

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
        "tech": {
            "trend":      tech.get("trend"),
            "vwap":       tech.get("vwap"),
            "vwap_bias":  tech.get("vwap_bias"),
            "rsi15":      tech.get("rsi15"),
            "pdh":        tech.get("pdh"),
            "pdl":        tech.get("pdl"),
            "bb_squeeze": tech.get("bb_squeeze"),
            "bb_width":   tech.get("bb_width"),
        } if tech else {},
    }


# ── Black-Scholes approximation (fallback when no live LTP) ───────────────────

def _bs_approx(spot: float, strike: int, iv_pct: float, opt_type: str,
               expiry: Optional[date] = None) -> float:
    """Very rough ATM approximation — used only when DB has no LTP."""
    import math
    if expiry is not None:
        dte = max((expiry - date.today()).days, 1)
    else:
        dte = 1
    T = dte / 252.0
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
