"""
Intraday Iron Fly Setup Builder
────────────────────────────────
Replaces the old Short Strangle — Iron Fly has defined max loss.

Strategy:
  SELL ATM CE  +  SELL ATM PE         (collect premium)
  BUY  (ATM+wing) CE  +  BUY (ATM-wing) PE  (cap your loss)

Entry: 9:20 AM  |  Hard exit: 3:10 PM  |  Product: MIS

Win condition: market stays near ATM, both sold options decay.
Loss condition: large directional move breaches one of the wings.
Max loss is DEFINED = wing_width - net_premium.  No unlimited loss.
"""

import math
import logging
from datetime import date, datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

from screener.db import _get_conn, is_available

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# ── Instrument config ──────────────────────────────────────────────────────────
_INSTRUMENT_CONFIG = {
    "NIFTY":     {"lot_size": 75,  "strike_step": 50,  "default_wing": 200},
    "BANKNIFTY": {"lot_size": 15,  "strike_step": 100, "default_wing": 400},
}

# ── Market condition thresholds ────────────────────────────────────────────────
VIX_DO_NOT_TRADE  = 22.0   # Too volatile — avoid selling premium
VIX_ELEVATED      = 17.0   # Elevated risk — widen wings or reduce size
VIX_CALM          = 13.0   # Calm market — good for Iron Fly

HARD_EXIT = time(15, 10)


# ── LTP fetch (single query for all 4 legs) ────────────────────────────────────

def get_iron_fly_ltps(expiry: date, atm: int, wing: int,
                      instrument: str = "NIFTY") -> dict:
    """
    Fetch latest LTPs for all 4 Iron Fly legs in one DB query.
    Returns {(strike, opt_type): ltp}.
    Falls back to None per leg if no recent data.
    """
    instr = instrument.upper()
    strikes = [
        (atm,        "CE"),
        (atm,        "PE"),
        (atm + wing, "CE"),
        (atm - wing, "PE"),
    ]
    result = {}

    if not is_available():
        return result

    try:
        with _get_conn() as conn:
            if conn is None:
                return result
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT ON (strike, option_type)
                        strike, option_type, ltp
                    FROM option_chain
                    WHERE instrument = %s
                      AND expiry     = %s
                      AND (strike, option_type) IN %s
                      AND ltp IS NOT NULL
                    ORDER BY strike, option_type, ts DESC
                """, (instr, expiry, tuple(strikes)))
                for row in cur.fetchall():
                    result[(int(row[0]), row[1])] = float(row[2])
    except Exception as exc:
        logger.warning("get_iron_fly_ltps failed: %s", exc)

    return result


# ── Market condition checks ────────────────────────────────────────────────────

def _vix_assessment(vix: Optional[float]) -> dict:
    if vix is None:
        return {"level": "UNKNOWN", "warn": False, "do_not_trade": False,
                "message": "VIX unavailable — proceed with caution"}
    if vix >= VIX_DO_NOT_TRADE:
        return {"level": "DANGER", "warn": True, "do_not_trade": True,
                "message": f"VIX {vix:.1f} ≥ {VIX_DO_NOT_TRADE} — market too volatile, avoid selling premium today"}
    if vix >= VIX_ELEVATED:
        return {"level": "ELEVATED", "warn": True, "do_not_trade": False,
                "message": f"VIX {vix:.1f} elevated — consider reducing size or widening wings"}
    if vix <= VIX_CALM:
        return {"level": "CALM", "warn": False, "do_not_trade": False,
                "message": f"VIX {vix:.1f} — calm market, good conditions for Iron Fly"}
    return {"level": "NORMAL", "warn": False, "do_not_trade": False,
            "message": f"VIX {vix:.1f} — normal conditions"}


def _trading_window() -> dict:
    now = datetime.now(IST).time()
    if now < time(9, 15):
        return {"phase": "PRE_MARKET",    "tradeable": False, "message": "Market not open yet (opens 9:15)"}
    if now <= time(9, 30):
        return {"phase": "ENTRY_WINDOW",  "tradeable": True,  "message": "Entry window open (9:15–9:30)"}
    if now <= time(15, 0):
        return {"phase": "HOLD",          "tradeable": False, "message": "Past entry window — hold or skip today"}
    if now <= time(15, 10):
        return {"phase": "EXIT_IMMINENT", "tradeable": False, "message": "Approach 3:10 PM hard exit — close now"}
    return     {"phase": "CLOSED",        "tradeable": False, "message": "Market closed — showing last prices"}


# ── Black-Scholes fallback ─────────────────────────────────────────────────────

def _bs_approx(spot: float, strike: int, iv_pct: float, opt_type: str,
               expiry: Optional[date] = None) -> float:
    def _norm_cdf(x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    dte = max((expiry - date.today()).days, 1) if expiry else 1
    T   = dte / 365.0
    S   = float(spot)
    K   = float(strike)
    r   = 0.07
    sig = iv_pct / 100.0
    sv  = sig * math.sqrt(T)
    if sv < 1e-9:
        return round(max(S - K, 0) if opt_type == "CE" else max(K - S, 0), 1)

    d1 = (math.log(S / K) + (r + 0.5 * sig * sig) * T) / sv
    d2 = d1 - sv

    if opt_type == "CE":
        price = S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    else:
        price = K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)

    return round(max(price, 0.05), 1)


# ── Main setup builder ─────────────────────────────────────────────────────────

def compute_iron_fly_setup(
    snap:            dict,
    expiry:          date,
    instrument:      str  = "NIFTY",
    wing_width:      int  = None,
    cached_backtest: dict = None,
) -> dict:
    """
    Build intraday Iron Fly setup from market snapshot.

    Returns a setup dict with 4 legs, real backtested stats, and
    explicit DO NOT TRADE flag when conditions are bad.
    """
    instr = instrument.upper()
    cfg   = _INSTRUMENT_CONFIG.get(instr, _INSTRUMENT_CONFIG["NIFTY"])
    lot   = cfg["lot_size"]
    step  = cfg["strike_step"]
    wing  = wing_width or cfg["default_wing"]
    wing  = round(wing / step) * step   # snap to step

    spot = snap["spot"]
    iv   = snap["atm_iv"] or (snap["vix"] * 1.4 if snap["vix"] else 35.9)
    vix  = snap.get("vix")
    pcr  = snap.get("pcr")
    atm  = snap["atm"]

    # Sell ATM, buy wings
    sell_ce = atm
    sell_pe = atm
    buy_ce  = atm + wing
    buy_pe  = atm - wing

    # ── Fetch LTPs from DB ────────────────────────────────────────────────────
    ltp_map  = get_iron_fly_ltps(expiry, atm, wing, instr)
    ltp_src  = "db_latest"

    ce_ltp    = ltp_map.get((sell_ce, "CE"))
    pe_ltp    = ltp_map.get((sell_pe, "PE"))
    buy_ce_ltp = ltp_map.get((buy_ce, "CE"))
    buy_pe_ltp = ltp_map.get((buy_pe, "PE"))

    # Fallback to Black-Scholes if any leg missing
    if ce_ltp    is None: ce_ltp    = _bs_approx(spot, sell_ce, iv, "CE", expiry); ltp_src = "bs_approx"
    if pe_ltp    is None: pe_ltp    = _bs_approx(spot, sell_pe, iv, "PE", expiry); ltp_src = "bs_approx"
    if buy_ce_ltp is None: buy_ce_ltp = _bs_approx(spot, buy_ce, iv, "CE", expiry); ltp_src = "bs_approx"
    if buy_pe_ltp is None: buy_pe_ltp = _bs_approx(spot, buy_pe, iv, "PE", expiry); ltp_src = "bs_approx"

    ce_ltp     = round(ce_ltp,    1)
    pe_ltp     = round(pe_ltp,    1)
    buy_ce_ltp = round(buy_ce_ltp, 1)
    buy_pe_ltp = round(buy_pe_ltp, 1)

    # ── P&L math ──────────────────────────────────────────────────────────────
    net_premium  = round((ce_ltp + pe_ltp) - (buy_ce_ltp + buy_pe_ltp), 1)
    max_loss_pts = round(wing - net_premium, 1)   # worst case: one side goes full intrinsic

    # Realistic targets based on backtest; fallback to 50% of premium
    bt = cached_backtest or {}
    bt_quality = bt.get("data_quality", "insufficient")
    if bt_quality in ("good", "limited") and bt.get("avg_profit_inr"):
        target_inr = int(bt["avg_profit_inr"])
        target_pts = round(target_inr / lot, 1)
        sl_pts     = round(net_premium, 1)   # exit when you've lost what you collected
    else:
        target_pts = round(net_premium * 0.50, 1)
        sl_pts     = round(net_premium, 1)
        target_inr = int(target_pts * lot)

    sl_inr      = int(sl_pts     * lot)
    max_loss_inr = int(max_loss_pts * lot)
    max_profit_inr = int(net_premium * lot)

    # Per-leg SL: exit a sold leg if it 2.5× from entry (tighter than old 3×)
    ce_sl = round(ce_ltp * 2.5, 1)
    pe_sl = round(pe_ltp * 2.5, 1)

    # ── Market condition checks ───────────────────────────────────────────────
    vix_check  = _vix_assessment(vix)
    time_check = _trading_window()

    do_not_trade        = vix_check["do_not_trade"]
    do_not_trade_reason = vix_check["message"] if do_not_trade else None

    # ── Tech signals ──────────────────────────────────────────────────────────
    try:
        from options.technicals import get_tech_signals
        tech = get_tech_signals(instr)
    except Exception:
        tech = {}

    # ── Backtest stats ────────────────────────────────────────────────────────
    win_rate        = bt.get("win_rate")
    avg_profit_inr  = bt.get("avg_profit_inr")
    avg_loss_inr    = bt.get("avg_loss_inr")
    expectancy_inr  = bt.get("expectancy_inr")
    total_pnl_inr   = bt.get("total_pnl_inr")
    backtest_days   = bt.get("days_tested")

    # ── Reasoning ─────────────────────────────────────────────────────────────
    reasons = [
        f"Iron Fly: SELL {sell_ce} CE + SELL {sell_pe} PE | "
        f"BUY {buy_ce} CE + BUY {buy_pe} PE",
        f"Net premium collected: {net_premium} pts | Max loss defined: {max_loss_pts} pts ({wing} - {net_premium})",
        f"IV {iv:.1f}% | VIX {vix:.1f}" if vix else f"IV {iv:.1f}%",
    ]
    if pcr:
        reasons.append(
            f"PCR {pcr:.2f} — {'range-bound' if 0.85 <= pcr <= 1.20 else 'directional bias'}"
        )
    if tech.get("trend"):
        reasons.append(f"Trend: {tech['trend']} — "
                       + ("market is directional, monitor closely" if tech["trend"] != "NEUTRAL"
                          else "neutral, good for Iron Fly"))
    if vix_check["warn"]:
        reasons.append(f"⚠ {vix_check['message']}")
    reasons.append("Hard exit 3:10 PM — no overnight hold (MIS)")

    # ── Legs list ─────────────────────────────────────────────────────────────
    legs = [
        {"action": "SELL", "strike": sell_ce, "type": "CE", "ltp": ce_ltp,     "sl": ce_sl},
        {"action": "SELL", "strike": sell_pe, "type": "PE", "ltp": pe_ltp,     "sl": pe_sl},
        {"action": "BUY",  "strike": buy_ce,  "type": "CE", "ltp": buy_ce_ltp, "sl": None},
        {"action": "BUY",  "strike": buy_pe,  "type": "PE", "ltp": buy_pe_ltp, "sl": None},
    ]

    return {
        "strategy":         "IRON_FLY",
        "display_name":     "Iron Fly (Intraday)",
        "expiry":           expiry,
        "atm":              atm,
        "wing_width":       wing,

        # Legs
        "legs":             legs,
        "sell_ce":          sell_ce,
        "sell_pe":          sell_pe,
        "buy_ce":           buy_ce,
        "buy_pe":           buy_pe,
        "ce_ltp":           ce_ltp,
        "pe_ltp":           pe_ltp,
        "buy_ce_ltp":       buy_ce_ltp,
        "buy_pe_ltp":       buy_pe_ltp,

        # Per-leg SL triggers
        "ce_sl":            ce_sl,
        "pe_sl":            pe_sl,

        # P&L
        "net_premium":      net_premium,
        "max_profit_pts":   net_premium,
        "max_loss_pts":     max_loss_pts,
        "target_pts":       target_pts,
        "sl_pts":           sl_pts,
        "target_inr":       target_inr,
        "sl_inr":           sl_inr,
        "max_profit_inr":   max_profit_inr,
        "max_loss_inr":     max_loss_inr,

        # Backtested stats (real numbers from DB history)
        "win_rate":         win_rate,
        "avg_profit_inr":   avg_profit_inr,
        "avg_loss_inr":     avg_loss_inr,
        "expectancy_inr":   expectancy_inr,
        "total_pnl_inr":    total_pnl_inr,
        "backtest_days":    backtest_days,
        "backtest_quality": bt_quality,
        "daily_results":    bt.get("per_day", []),

        # Market condition gates
        "vix_warning":          vix_check,
        "do_not_trade":         do_not_trade,
        "do_not_trade_reason":  do_not_trade_reason,
        "trading_window":       time_check,

        "instrument":       instr,
        "lot_size":         lot,
        "reasoning":        reasons,
        "hard_exit":        "15:10 IST",
        "ltp_source":       ltp_src,

        "tech": {
            "trend":      tech.get("trend"),
            "vwap":       tech.get("vwap"),
            "vwap_bias":  tech.get("vwap_bias"),
            "rsi15":      tech.get("rsi15"),
            "pdh":        tech.get("pdh"),
            "pdl":        tech.get("pdl"),
            "bb_squeeze": tech.get("bb_squeeze"),
        } if tech else {},
    }
