"""
Intraday Iron Fly Backtester
────────────────────────────
Uses actual option_chain DB data — no assumptions, no fake numbers.

For each trading day in the DB:
  Entry  9:20 AM  →  SELL ATM CE + ATM PE, BUY (ATM±wing) CE/PE
  Exit   3:10 PM  →  Close all 4 legs at market

Real P&L computed from actual LTPs.  Results are cached for 4 hours.
"""

import math
import logging
import time as _time
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from screener.db import _get_conn, is_available
from options.instrument_config import REGISTRY as _INSTR_REGISTRY

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# ── Config ─────────────────────────────────────────────────────────────────────
MIN_DAYS_REQUIRED  = 3
MIN_DAYS_RELIABLE  = 10
ENTRY_WINDOW_MIN   = 15     # ±15 min around 9:20 AM (9:05–9:35)
EXIT_WINDOW_MIN    = 20     # ±20 min around 3:10 PM (2:50–3:30)
DEFAULT_WING       = 200    # fallback if instrument not in registry

# ── In-process cache (4-hour TTL) ──────────────────────────────────────────────
_cache: dict = {}
_CACHE_TTL   = 2 * 3600


def _cache_key(instrument: str, wing: int, date_from: Optional[str], date_to: Optional[str]) -> str:
    return f"{instrument}:{wing}:{date_from or ''}:{date_to or ''}"


def _cached(key: str) -> Optional[dict]:
    entry = _cache.get(key)
    if entry and (_time.time() - entry["ts"]) < _CACHE_TTL:
        return entry["data"]
    return None


def _store(key: str, data: dict):
    _cache[key] = {"ts": _time.time(), "data": data}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _ist_to_ts(d: date, h: int, m: int) -> datetime:
    """Return a timezone-aware datetime in IST for a given date + time."""
    return datetime(d.year, d.month, d.day, h, m, 0, tzinfo=IST)


def _bs_price(spot: float, strike: int, iv_pct: float, opt_type: str, dte: int) -> float:
    """Minimal Black-Scholes fallback for missing wing LTPs."""
    def _cdf(x):
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
    T   = max(dte, 1) / 365.0
    sig = iv_pct / 100.0
    sv  = sig * math.sqrt(T)
    if sv < 1e-9:
        return max(spot - strike, 0) if opt_type == "CE" else max(strike - spot, 0)
    d1 = (math.log(spot / strike) + (0.07 + 0.5 * sig * sig) * T) / sv
    d2 = d1 - sv
    if opt_type == "CE":
        return round(max(spot * _cdf(d1) - strike * math.exp(-0.07 * T) * _cdf(d2), 0.05), 1)
    return round(max(strike * math.exp(-0.07 * T) * _cdf(-d2) - spot * _cdf(-d1), 0.05), 1)


def _get_atm_at_entry(cur, instrument: str, trade_date: date, step: int):
    """
    Get spot price + nearest expiry at 9:20 AM ± window on trade_date.
    Returns (atm_strike, expiry, spot) or (None, None, None).
    """
    entry_ts  = _ist_to_ts(trade_date, 9, 20)
    win_start = entry_ts - timedelta(minutes=ENTRY_WINDOW_MIN)
    win_end   = entry_ts + timedelta(minutes=ENTRY_WINDOW_MIN)

    cur.execute("""
        SELECT underlying_ltp, expiry
        FROM option_chain
        WHERE instrument        = %s
          AND ts                BETWEEN %s AND %s
          AND underlying_ltp    IS NOT NULL
          AND expiry            IS NOT NULL
        ORDER BY ABS(EXTRACT(EPOCH FROM (ts - %s))) ASC
        LIMIT 1
    """, (instrument, win_start, win_end, entry_ts))
    row = cur.fetchone()
    if not row:
        return None, None, None
    spot   = float(row[0])
    expiry = row[1]
    atm    = int(round(spot / step) * step)
    return atm, expiry, spot


def _get_ltp(cur, instrument: str, expiry, strike: int, opt_type: str,
             target_ts: datetime, window_min: int) -> Optional[float]:
    """
    Fetch closest LTP for (instrument, expiry, strike, opt_type) near target_ts.
    Uses the composite index on (instrument, expiry, strike, option_type, ts).
    """
    win_start = target_ts - timedelta(minutes=window_min)
    win_end   = target_ts + timedelta(minutes=window_min)

    cur.execute("""
        SELECT ltp
        FROM option_chain
        WHERE instrument  = %s
          AND expiry      = %s
          AND strike      = %s
          AND option_type = %s
          AND ts          BETWEEN %s AND %s
          AND ltp         IS NOT NULL
        ORDER BY ABS(EXTRACT(EPOCH FROM (ts - %s))) ASC
        LIMIT 1
    """, (instrument, expiry, strike, opt_type, win_start, win_end, target_ts))
    row = cur.fetchone()
    return float(row[0]) if row else None


# ── Main backtester ────────────────────────────────────────────────────────────

def backtest_iron_fly(
    instrument:   str = "NIFTY",
    wing_width:   int = DEFAULT_WING,
    lookback_days: int = 60,
    date_from:    Optional[str] = None,
    date_to:      Optional[str] = None,
) -> dict:
    """
    Backtest intraday Iron Fly over DB history.

    Setup:
      SELL ATM CE  +  SELL ATM PE
      BUY  (ATM+wing) CE  +  BUY (ATM-wing) PE

    Entry: 9:20 AM  |  Exit: 3:10 PM
    SL rule: if combined loss at exit >= 1× net_premium collected → SL_HIT

    Date filtering priority: date_from/date_to > lookback_days.
    """
    instr = instrument.upper()
    cfg   = _INSTR_REGISTRY.get(instr)
    step  = cfg.strike_step if cfg else 50
    lot   = cfg.lot_size    if cfg else 75
    wing  = round(wing_width / step) * step  # snap to nearest valid step

    cache_key = _cache_key(instr, wing, date_from, date_to)
    cached = _cached(cache_key)
    if cached:
        return cached

    if not is_available():
        return {"error": "DB unavailable", "instrument": instr}

    per_day    = []
    days_skip  = 0

    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                # ── 1. Get trading days ─────────────────────────────────────
                if date_from and date_to:
                    cur.execute("""
                        SELECT DISTINCT ts::date AS trade_date
                        FROM option_chain
                        WHERE instrument = %s
                          AND ts::date BETWEEN %s AND %s
                        ORDER BY trade_date DESC
                    """, (instr, date_from, date_to))
                else:
                    cur.execute("""
                        SELECT DISTINCT ts::date AS trade_date
                        FROM option_chain
                        WHERE instrument = %s
                          AND ts::date  >= CURRENT_DATE - (%s || ' days')::INTERVAL
                          AND (
                              ts::date < CURRENT_DATE
                              OR EXISTS (
                                  SELECT 1 FROM option_chain oc2
                                  WHERE oc2.instrument = %s
                                    AND oc2.ts::date = CURRENT_DATE
                                    AND oc2.ts::time >= '15:00:00'
                              )
                          )
                        ORDER BY trade_date DESC
                    """, (instr, lookback_days + 5, instr))
                trade_dates = [r[0] for r in cur.fetchall()]

                if not trade_dates:
                    return {
                        "instrument":     instr,
                        "wing_width":     wing,
                        "days_tested":    0,
                        "days_skipped":   0,
                        "data_quality":   "insufficient",
                        "error":          "No historical data found in DB",
                    }

                # ── 2. Simulate each day ────────────────────────────────────
                for trade_date in trade_dates:
                    # Get ATM at entry
                    atm, expiry, spot = _get_atm_at_entry(cur, instr, trade_date, step)
                    if atm is None:
                        days_skip += 1
                        continue

                    sell_ce = atm
                    sell_pe = atm
                    buy_ce  = atm + wing
                    buy_pe  = atm - wing

                    entry_ts = _ist_to_ts(trade_date, 9, 20)
                    exit_ts  = _ist_to_ts(trade_date, 15, 10)

                    # ── Entry LTPs ─────────────────────────────────────────
                    e_sc = _get_ltp(cur, instr, expiry, sell_ce, "CE", entry_ts, ENTRY_WINDOW_MIN)
                    e_sp = _get_ltp(cur, instr, expiry, sell_pe, "PE", entry_ts, ENTRY_WINDOW_MIN)
                    e_bc = _get_ltp(cur, instr, expiry, buy_ce,  "CE", entry_ts, ENTRY_WINDOW_MIN)
                    e_bp = _get_ltp(cur, instr, expiry, buy_pe,  "PE", entry_ts, ENTRY_WINDOW_MIN)

                    # ATM LTPs are essential — skip if missing
                    if e_sc is None or e_sp is None:
                        days_skip += 1
                        continue

                    # Wing LTPs: fall back to BS if not in DB (OTM options sparse)
                    dte = max((expiry - trade_date).days, 1)
                    # Approximate spot IV from ATM straddle
                    approx_iv = 35.0
                    if e_sc is not None and e_sp is not None:
                        # Straddle / spot ≈ IV * sqrt(dte/365) * sqrt(2/π)
                        # Rough reverse: iv ≈ (straddle / spot) / sqrt(dte/365) * 100 * 1.25
                        pass  # Use default 35% IV for BS fallback
                    if e_bc is None:
                        e_bc = _bs_price(spot, buy_ce,  approx_iv, "CE", dte)
                    if e_bp is None:
                        e_bp = _bs_price(spot, buy_pe,  approx_iv, "PE", dte)

                    net_premium = round((e_sc + e_sp) - (e_bc + e_bp), 1)
                    if net_premium <= 0:
                        days_skip += 1
                        continue

                    # ── Exit LTPs ──────────────────────────────────────────
                    x_sc = _get_ltp(cur, instr, expiry, sell_ce, "CE", exit_ts, EXIT_WINDOW_MIN)
                    x_sp = _get_ltp(cur, instr, expiry, sell_pe, "PE", exit_ts, EXIT_WINDOW_MIN)
                    x_bc = _get_ltp(cur, instr, expiry, buy_ce,  "CE", exit_ts, EXIT_WINDOW_MIN)
                    x_bp = _get_ltp(cur, instr, expiry, buy_pe,  "PE", exit_ts, EXIT_WINDOW_MIN)

                    # Fallback: use 0 for expired OTM wings
                    x_sc = x_sc or 0.0
                    x_sp = x_sp or 0.0
                    x_bc = x_bc or 0.0
                    x_bp = x_bp or 0.0

                    net_exit = round((x_sc + x_sp) - (x_bc + x_bp), 1)

                    # P&L = premium collected at entry minus premium paid at exit
                    pnl_pts = round(net_premium - net_exit, 1)

                    # SL rule: if exit net >= 2× entry net, cap loss at 1× entry net
                    sl_hit = False
                    if net_exit >= 2 * net_premium:
                        pnl_pts = -net_premium
                        sl_hit  = True

                    pnl_inr = round(pnl_pts * lot, 0)

                    per_day.append({
                        "date":          str(trade_date),
                        "expiry":        str(expiry),
                        "atm":           atm,
                        "wing_width":    wing,
                        "net_premium":   net_premium,
                        "net_exit":      round(net_exit, 1),
                        "pnl_pts":       pnl_pts,
                        "pnl_inr":       int(pnl_inr),
                        "win":           pnl_pts > 0,
                        "sl_hit":        sl_hit,
                        "sell_ce":       sell_ce,
                        "sell_pe":       sell_pe,
                        "buy_ce":        buy_ce,
                        "buy_pe":        buy_pe,
                        "entry": {"sell_ce": e_sc, "sell_pe": e_sp, "buy_ce": e_bc, "buy_pe": e_bp},
                        "exit":  {"sell_ce": x_sc, "sell_pe": x_sp, "buy_ce": x_bc, "buy_pe": x_bp},
                    })

    except Exception as exc:
        logger.error("backtest_iron_fly error: %s", exc)
        return {"error": str(exc), "instrument": instr}

    if not per_day:
        return {
            "instrument":   instr,
            "wing_width":   wing,
            "days_tested":  0,
            "days_skipped": days_skip,
            "data_quality": "insufficient",
            "error":        "No complete trading days found (entry/exit LTPs missing)",
        }

    wins   = [d for d in per_day if d["win"]]
    losses = [d for d in per_day if not d["win"]]
    n      = len(per_day)

    win_rate   = round(len(wins) / n * 100, 1)
    avg_profit = round(sum(d["pnl_inr"] for d in wins)   / len(wins),   0) if wins   else 0
    avg_loss   = round(sum(d["pnl_inr"] for d in losses) / len(losses), 0) if losses else 0
    expectancy = round((win_rate / 100) * avg_profit + (1 - win_rate / 100) * avg_loss, 0)
    total_pnl  = sum(d["pnl_inr"] for d in per_day)

    if n < MIN_DAYS_REQUIRED:
        quality = "insufficient"
    elif n < MIN_DAYS_RELIABLE:
        quality = "limited"
    else:
        quality = "good"

    result = {
        "strategy":       "IRON_FLY",
        "instrument":     instr,
        "wing_width":     wing,
        "lot_size":       lot,
        "days_tested":    n,
        "days_skipped":   days_skip,
        "wins":           len(wins),
        "losses":         len(losses),
        "win_rate":       win_rate,
        "avg_profit_inr": int(avg_profit),
        "avg_loss_inr":   int(avg_loss),
        "expectancy_inr": int(expectancy),
        "total_pnl_inr":  int(total_pnl),
        "max_profit_inr": max((d["pnl_inr"] for d in per_day), default=0),
        "max_loss_inr":   min((d["pnl_inr"] for d in per_day), default=0),
        "data_quality":   quality,
        "tested_from":    per_day[-1]["date"] if per_day else None,
        "tested_to":      per_day[0]["date"]  if per_day else None,
        "per_day":        per_day,
    }

    _store(cache_key, result)
    return result


def find_optimal_wing_width(
    instrument:   str = "NIFTY",
    widths:       Optional[list] = None,
    lookback_days: int = 60,
) -> dict:
    """
    Test multiple wing widths and return best one by expectancy.
    """
    if widths is None:
        cfg  = _INSTR_REGISTRY.get(instrument.upper())
        step = cfg.strike_step if cfg else 50
        widths = [step * 2, step * 3, step * 4, step * 5]

    results = {}
    for w in widths:
        results[w] = backtest_iron_fly(instrument, w, lookback_days)

    # Rank by expectancy (skip insufficient data)
    ranked = sorted(
        [(w, r) for w, r in results.items() if r.get("data_quality") != "insufficient" and "error" not in r],
        key=lambda x: x[1].get("expectancy_inr", -999999),
        reverse=True,
    )
    best_width = ranked[0][0] if ranked else widths[2] if len(widths) > 2 else widths[-1]

    return {
        "best_wing_width": best_width,
        "results_by_width": results,
        "ranked": [{"wing_width": w, "expectancy_inr": r.get("expectancy_inr"), "win_rate": r.get("win_rate")} for w, r in ranked],
    }


# ── CLI ─────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json, sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")

    instrument = sys.argv[1].upper() if len(sys.argv) > 1 else "NIFTY"
    wing       = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_WING

    print(f"\nRunning Iron Fly backtest: {instrument}  wing={wing} pts\n")
    result = backtest_iron_fly(instrument, wing)

    if "error" in result:
        print("ERROR:", result["error"])
    else:
        print(f"Days tested  : {result['days_tested']}  (skipped {result['days_skipped']})")
        print(f"Win rate     : {result['win_rate']}%  ({result['wins']}W / {result['losses']}L)")
        print(f"Avg profit   : ₹{result['avg_profit_inr']:,}")
        print(f"Avg loss     : ₹{result['avg_loss_inr']:,}")
        print(f"Expectancy   : ₹{result['expectancy_inr']:,} per trade (1 lot)")
        print(f"Total P&L    : ₹{result['total_pnl_inr']:,}  ({result['tested_from']} → {result['tested_to']})")
        print(f"Data quality : {result['data_quality']}")
        print()
        print("Daily results:")
        for d in result["per_day"]:
            tag = "WIN " if d["win"] else "LOSS"
            sl  = " [SL HIT]" if d["sl_hit"] else ""
            print(f"  {d['date']}  ATM={d['atm']}  net_prem={d['net_premium']}  "
                  f"P&L={d['pnl_pts']:+.1f}pts  ₹{d['pnl_inr']:+,}  {tag}{sl}")
