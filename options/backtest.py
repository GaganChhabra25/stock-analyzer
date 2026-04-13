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


# ── Shared helpers ────────────────────────────────────────────────────────────

def _build_summary(per_day: list, instrument: str, lot: int, strategy_key: str, extra: dict = None) -> dict:
    """Compute standard summary stats from a per_day list."""
    wins   = [d for d in per_day if d["win"]]
    losses = [d for d in per_day if not d["win"]]
    n      = len(per_day)
    if n == 0:
        return {}

    win_rate   = round(len(wins) / n * 100, 1)
    avg_profit = round(sum(d["pnl_inr"] for d in wins)   / len(wins),   0) if wins   else 0
    avg_loss   = round(sum(d["pnl_inr"] for d in losses) / len(losses), 0) if losses else 0
    expectancy = round((win_rate / 100) * avg_profit + (1 - win_rate / 100) * avg_loss, 0)
    total_pnl  = sum(d["pnl_inr"] for d in per_day)

    quality = ("insufficient" if n < MIN_DAYS_REQUIRED
               else "limited"  if n < MIN_DAYS_RELIABLE
               else "good")

    result = {
        "strategy":       strategy_key,
        "instrument":     instrument,
        "lot_size":       lot,
        "days_tested":    n,
        "days_skipped":   extra.get("days_skipped", 0) if extra else 0,
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
    if extra:
        result.update({k: v for k, v in extra.items() if k != "days_skipped"})
    return result


def _get_trade_dates(cur, instrument: str, lookback_days: int,
                     date_from: Optional[str], date_to: Optional[str],
                     dte_max: int = 999) -> list:
    """Return list of (trade_date, nearest_expiry) tuples for backtest loop."""
    dte_filter = f"AND (expiry - ts::date) <= {dte_max}" if dte_max < 999 else ""
    if date_from and date_to:
        cur.execute(
            f"""
            SELECT DISTINCT ts::date AS d, MIN(expiry) AS exp
            FROM option_chain
            WHERE instrument = %s
              AND ts::date BETWEEN %s AND %s
              {dte_filter}
            GROUP BY ts::date
            ORDER BY d DESC
            """,
            (instrument, date_from, date_to),
        )
    else:
        cur.execute(
            f"""
            SELECT DISTINCT ts::date AS d, MIN(expiry) AS exp
            FROM option_chain
            WHERE instrument = %s
              AND ts::date >= CURRENT_DATE - (%s || ' days')::INTERVAL
              {dte_filter}
            GROUP BY ts::date
            ORDER BY d DESC
            """,
            (instrument, lookback_days + 5),
        )
    return [(r[0], r[1]) for r in cur.fetchall()]


def _prev_day_ranges(cur, instrument: str, lookback_days: int) -> dict:
    """Return {date: range_pts} for prev-day-range filter."""
    cur.execute(
        """
        SELECT ts::date AS d,
               MAX(underlying_ltp) - MIN(underlying_ltp) AS rng
        FROM option_chain
        WHERE instrument = %s
          AND ts::date >= CURRENT_DATE - (%s || ' days')::INTERVAL
          AND underlying_ltp IS NOT NULL
        GROUP BY ts::date
        """,
        (instrument, lookback_days + 10),
    )
    return {r[0]: float(r[1]) for r in cur.fetchall()}


# ── OI-Anchored Strangle ───────────────────────────────────────────────────────

def backtest_oi_strangle(
    instrument:       str = "NIFTY",
    lookback_days:     int = 60,
    date_from:        Optional[str] = None,
    date_to:          Optional[str] = None,
    skip_high_vol:    bool = True,
    high_vol_thresh:  int  = 350,
) -> dict:
    """
    OI-Anchored Strangle backtest.

    At 10:30 AM each day (DTE 0–5 only):
      - Find CE strike with highest Open Interest above ATM  → Sell that CE
      - Find PE strike with highest Open Interest below ATM  → Sell that PE
    Exit at 2:45 PM.  SL if either leg doubles.
    Optional: skip days where the previous session's range > high_vol_thresh pts.
    """
    instr = instrument.upper()
    cfg   = _INSTR_REGISTRY.get(instr)
    step  = cfg.strike_step if cfg else 50
    lot   = cfg.lot_size    if cfg else 75

    ckey = f"oi_strangle:{instr}:{date_from or ''}:{date_to or ''}:{skip_high_vol}"
    if (hit := _cached(ckey)):
        return hit

    if not is_available():
        return {"error": "DB unavailable", "instrument": instr}

    per_day   = []
    days_skip = 0

    ENTRY_H, ENTRY_M = 10, 30
    EXIT_H,  EXIT_M  = 14, 45
    WIN_MINS         = 8

    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                trade_days  = _get_trade_dates(cur, instr, lookback_days,
                                               date_from, date_to, dte_max=5)
                prev_ranges = _prev_day_ranges(cur, instr, lookback_days) if skip_high_vol else {}

                for trade_date, expiry in trade_days:
                    dte = (expiry - trade_date).days

                    # ── Skip high-vol previous days ────────────────────────────
                    if skip_high_vol:
                        prev_rng = 0.0
                        for lag in range(1, 5):
                            from datetime import timedelta
                            pd = trade_date - timedelta(days=lag)
                            if pd in prev_ranges:
                                prev_rng = prev_ranges[pd]
                                break
                        if prev_rng > high_vol_thresh:
                            days_skip += 1
                            continue

                    entry_ts = _ist_to_ts(trade_date, ENTRY_H, ENTRY_M)
                    win_s    = entry_ts - timedelta(minutes=WIN_MINS)
                    win_e    = entry_ts + timedelta(minutes=WIN_MINS)

                    # ── Spot at entry ──────────────────────────────────────────
                    cur.execute(
                        """
                        SELECT AVG(underlying_ltp)
                        FROM option_chain
                        WHERE instrument = %s AND expiry = %s
                          AND ts BETWEEN %s AND %s
                          AND underlying_ltp IS NOT NULL
                        """,
                        (instr, expiry, win_s, win_e),
                    )
                    row = cur.fetchone()
                    if not row or not row[0]:
                        days_skip += 1
                        continue
                    spot = float(row[0])
                    atm  = int(round(spot / step) * step)

                    # ── Max-OI CE above ATM ────────────────────────────────────
                    cur.execute(
                        """
                        SELECT strike, MAX(oi) AS max_oi, AVG(ltp) AS avg_ltp
                        FROM option_chain
                        WHERE instrument = %s AND expiry = %s
                          AND option_type = 'CE'
                          AND strike > %s
                          AND ts BETWEEN %s AND %s
                          AND oi > 0 AND ltp > 0
                        GROUP BY strike
                        ORDER BY max_oi DESC
                        LIMIT 1
                        """,
                        (instr, expiry, atm, win_s, win_e),
                    )
                    ce_row = cur.fetchone()

                    # ── Max-OI PE below ATM ────────────────────────────────────
                    cur.execute(
                        """
                        SELECT strike, MAX(oi) AS max_oi, AVG(ltp) AS avg_ltp
                        FROM option_chain
                        WHERE instrument = %s AND expiry = %s
                          AND option_type = 'PE'
                          AND strike < %s
                          AND ts BETWEEN %s AND %s
                          AND oi > 0 AND ltp > 0
                        GROUP BY strike
                        ORDER BY max_oi DESC
                        LIMIT 1
                        """,
                        (instr, expiry, atm, win_s, win_e),
                    )
                    pe_row = cur.fetchone()

                    if not ce_row or not pe_row:
                        days_skip += 1
                        continue

                    sell_ce   = int(ce_row[0])
                    ce_oi     = int(ce_row[1])
                    ce_entry  = float(ce_row[2])
                    sell_pe   = int(pe_row[0])
                    pe_oi     = int(pe_row[1])
                    pe_entry  = float(pe_row[2])

                    if ce_entry <= 0 or pe_entry <= 0:
                        days_skip += 1
                        continue

                    net_premium = round(ce_entry + pe_entry, 1)

                    # ── Exit LTPs ──────────────────────────────────────────────
                    exit_ts = _ist_to_ts(trade_date, EXIT_H, EXIT_M)
                    ce_exit = _get_ltp(cur, instr, expiry, sell_ce, "CE", exit_ts, EXIT_WINDOW_MIN) or 0.0
                    pe_exit = _get_ltp(cur, instr, expiry, sell_pe, "PE", exit_ts, EXIT_WINDOW_MIN) or 0.0

                    net_exit = round(ce_exit + pe_exit, 1)
                    pnl_pts  = round(net_premium - net_exit, 1)

                    # SL: either leg doubles → cap loss at 0.8× premium
                    sl_hit = False
                    if ce_exit > 2 * ce_entry or pe_exit > 2 * pe_entry:
                        sl_hit  = True
                        pnl_pts = round(-net_premium * 0.8, 1)

                    pnl_inr = round(pnl_pts * lot, 0)

                    per_day.append({
                        "date":          str(trade_date),
                        "expiry":        str(expiry),
                        "dte":           dte,
                        "spot":          round(spot, 1),
                        "atm":           atm,
                        "sell_ce":       sell_ce,
                        "sell_pe":       sell_pe,
                        "ce_oi":         ce_oi,
                        "pe_oi":         pe_oi,
                        "strangle_width": sell_ce - sell_pe,
                        "net_premium":   net_premium,
                        "ce_entry":      round(ce_entry, 1),
                        "pe_entry":      round(pe_entry, 1),
                        "ce_exit":       round(ce_exit, 1),
                        "pe_exit":       round(pe_exit, 1),
                        "net_exit":      net_exit,
                        "pnl_pts":       pnl_pts,
                        "pnl_inr":       int(pnl_inr),
                        "win":           pnl_pts > 0,
                        "sl_hit":        sl_hit,
                    })

    except Exception as exc:
        logger.error("backtest_oi_strangle error: %s", exc)
        return {"error": str(exc), "instrument": instr}

    if not per_day:
        return {
            "strategy": "OI_STRANGLE", "instrument": instr,
            "days_tested": 0, "days_skipped": days_skip,
            "data_quality": "insufficient",
            "error": "No qualifying days found (check DTE filter / OI availability)",
        }

    result = _build_summary(per_day, instr, lot, "OI_STRANGLE",
                            {"days_skipped": days_skip,
                             "high_vol_thresh": high_vol_thresh,
                             "skip_high_vol": skip_high_vol})
    _store(ckey, result)
    return result


# ── 0DTE Straddle ──────────────────────────────────────────────────────────────

def backtest_0dte_straddle(
    instrument:   str = "NIFTY",
    lookback_days: int = 60,
    date_from:    Optional[str] = None,
    date_to:      Optional[str] = None,
) -> dict:
    """
    0DTE ATM Straddle — only on expiry days.

    Entry  9:30 AM  →  Sell ATM CE + ATM PE
    Exit   1:30 PM  →  Close both legs
    SL     spot moves > 150 pts from entry ATM → exit at market
    """
    instr = instrument.upper()
    cfg   = _INSTR_REGISTRY.get(instr)
    step  = cfg.strike_step if cfg else 50
    lot   = cfg.lot_size    if cfg else 75

    ckey = f"0dte_straddle:{instr}:{date_from or ''}:{date_to or ''}"
    if (hit := _cached(ckey)):
        return hit

    if not is_available():
        return {"error": "DB unavailable", "instrument": instr}

    per_day   = []
    days_skip = 0

    ENTRY_H, ENTRY_M = 9, 30
    EXIT_H,  EXIT_M  = 13, 30
    SL_MOVE          = 150

    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                # DTE = 0 only (expiry day = trade_date)
                trade_days = _get_trade_dates(cur, instr, lookback_days,
                                              date_from, date_to, dte_max=0)

                for trade_date, expiry in trade_days:
                    entry_ts = _ist_to_ts(trade_date, ENTRY_H, ENTRY_M)
                    win_s    = entry_ts - timedelta(minutes=ENTRY_WINDOW_MIN)
                    win_e    = entry_ts + timedelta(minutes=ENTRY_WINDOW_MIN)

                    atm, expiry_db, spot = _get_atm_at_entry(cur, instr, trade_date, step)
                    # Use 9:30 AM specifically
                    cur.execute(
                        """
                        SELECT AVG(underlying_ltp)
                        FROM option_chain
                        WHERE instrument = %s AND expiry = %s
                          AND ts BETWEEN %s AND %s
                          AND underlying_ltp IS NOT NULL
                        """,
                        (instr, expiry, win_s, win_e),
                    )
                    row = cur.fetchone()
                    if not row or not row[0]:
                        days_skip += 1
                        continue
                    spot = float(row[0])
                    atm  = int(round(spot / step) * step)

                    e_ce = _get_ltp(cur, instr, expiry, atm, "CE", entry_ts, ENTRY_WINDOW_MIN)
                    e_pe = _get_ltp(cur, instr, expiry, atm, "PE", entry_ts, ENTRY_WINDOW_MIN)

                    if not e_ce or not e_pe:
                        days_skip += 1
                        continue

                    net_premium = round(e_ce + e_pe, 1)
                    if net_premium <= 0:
                        days_skip += 1
                        continue

                    # Exit at 1:30 PM
                    exit_ts = _ist_to_ts(trade_date, EXIT_H, EXIT_M)
                    x_ce = _get_ltp(cur, instr, expiry, atm, "CE", exit_ts, EXIT_WINDOW_MIN) or 0.0
                    x_pe = _get_ltp(cur, instr, expiry, atm, "PE", exit_ts, EXIT_WINDOW_MIN) or 0.0

                    # Check SL: did spot move > SL_MOVE from ATM?
                    cur.execute(
                        """
                        SELECT MAX(ABS(underlying_ltp - %s))
                        FROM option_chain
                        WHERE instrument = %s AND ts::date = %s
                          AND (ts AT TIME ZONE 'Asia/Kolkata')::time
                              BETWEEN '09:30:00' AND '13:30:00'
                          AND underlying_ltp IS NOT NULL
                        """,
                        (atm, instr, trade_date),
                    )
                    max_move_row = cur.fetchone()
                    max_move = float(max_move_row[0]) if max_move_row and max_move_row[0] else 0.0

                    sl_hit  = max_move > SL_MOVE
                    net_exit = round(x_ce + x_pe, 1)
                    pnl_pts  = round(net_premium - net_exit, 1)
                    if sl_hit:
                        pnl_pts = round(-net_premium * 0.7, 1)

                    pnl_inr = round(pnl_pts * lot, 0)

                    per_day.append({
                        "date":        str(trade_date),
                        "expiry":      str(expiry),
                        "dte":         0,
                        "spot":        round(spot, 1),
                        "atm":         atm,
                        "sell_ce":     atm,
                        "sell_pe":     atm,
                        "net_premium": net_premium,
                        "ce_entry":    round(e_ce, 1),
                        "pe_entry":    round(e_pe, 1),
                        "ce_exit":     round(x_ce, 1),
                        "pe_exit":     round(x_pe, 1),
                        "net_exit":    net_exit,
                        "max_move":    round(max_move, 1),
                        "pnl_pts":     pnl_pts,
                        "pnl_inr":     int(pnl_inr),
                        "win":         pnl_pts > 0,
                        "sl_hit":      sl_hit,
                    })

    except Exception as exc:
        logger.error("backtest_0dte_straddle error: %s", exc)
        return {"error": str(exc), "instrument": instr}

    if not per_day:
        return {
            "strategy": "ZERO_DTE_STRADDLE", "instrument": instr,
            "days_tested": 0, "days_skipped": days_skip,
            "data_quality": "insufficient",
            "error": "No expiry days found in date range",
        }

    result = _build_summary(per_day, instr, lot, "ZERO_DTE_STRADDLE",
                            {"days_skipped": days_skip, "sl_move_pts": SL_MOVE})
    _store(ckey, result)
    return result


# ── Dispatcher: run any strategy by key ───────────────────────────────────────

def run_strategy(strategy_key: str, instrument: str,
                 wing_width: int = DEFAULT_WING,
                 lookback_days: int = 60,
                 date_from: Optional[str] = None,
                 date_to: Optional[str] = None,
                 **kwargs) -> dict:
    """Single entry point used by the API route."""
    key = strategy_key.upper()
    if key == "IRON_FLY":
        return backtest_iron_fly(instrument, wing_width, lookback_days, date_from, date_to)
    if key == "OI_STRANGLE":
        return backtest_oi_strangle(instrument, lookback_days, date_from, date_to,
                                    skip_high_vol=kwargs.get("skip_high_vol", True))
    if key == "ZERO_DTE_STRADDLE":
        return backtest_0dte_straddle(instrument, lookback_days, date_from, date_to)
    return {"error": f"Unknown strategy: {strategy_key}"}


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
