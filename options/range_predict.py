"""
Weekly Nifty Range Predictor
────────────────────────────
Queries DB with aggregation SQL → 8 numbers → range prediction.
35L rows never touch Python memory or LLM context.

Usage:
    python options/range_predict.py
    python options/range_predict.py --json    # machine-readable output
"""

import math
import sys
import json
import argparse
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from screener.db import _get_conn, is_available


# ─── helpers ──────────────────────────────────────────────────────────────────

def _next_thursday(from_date: date = None) -> date:
    d = from_date or date.today()
    days = (3 - d.weekday()) % 7
    return d + timedelta(days=days if days else 7)


def _expected_move(spot: float, iv_pct: float, dte: int) -> float:
    """Black-Scholes 1-sigma move in index points."""
    return spot * (iv_pct / 100.0) * math.sqrt(max(dte, 1) / 365.0)


# ─── Step 1: ONE SQL call → 8 numbers ─────────────────────────────────────────

def fetch_summary(expiry: date) -> dict:
    """
    Single SQL query that aggregates 35L rows down to 8 numbers.
    Returns {} if DB unavailable.
    """
    if not is_available():
        return {}

    with _get_conn() as conn:
        if conn is None:
            return {}
        with conn.cursor() as cur:

            # ── A. Latest spot + ATM IV from market_snapshot ──────────────────
            cur.execute("""
                SELECT
                    spot_price,
                    vix,
                    pcr_oi,
                    atm_strike,
                    atm_straddle,
                    call_oi_wall,
                    put_oi_wall,
                    ts
                FROM market_snapshot
                WHERE instrument = 'NIFTY'
                ORDER BY ts DESC
                LIMIT 1
            """)
            snap = cur.fetchone()

            if snap:
                spot, vix, pcr, atm, straddle, ce_wall, pe_wall, ts = snap
            else:
                # Fallback: get spot from option_chain
                cur.execute("""
                    SELECT underlying_ltp, ts
                    FROM option_chain
                    WHERE instrument = 'NIFTY' AND underlying_ltp IS NOT NULL
                    ORDER BY ts DESC LIMIT 1
                """)
                row = cur.fetchone()
                if not row:
                    return {}
                spot, ts = row
                vix = pcr = atm = straddle = ce_wall = pe_wall = None

            spot = float(spot)
            atm = int(round(spot / 50) * 50) if not atm else int(atm)

            # ── B. ATM IV for this expiry (avg last 2 hours, CE+PE) ───────────
            cur.execute("""
                SELECT ROUND(AVG(iv)::NUMERIC, 2)
                FROM option_chain
                WHERE instrument = 'NIFTY'
                  AND expiry     = %s
                  AND strike     = %s
                  AND iv         IS NOT NULL
                  AND ts        >= NOW() - INTERVAL '2 hours'
            """, (expiry, atm))
            iv_row = cur.fetchone()
            atm_iv = float(iv_row[0]) if iv_row and iv_row[0] else None

            # ── C. Max Pain: strike that minimises total OI payout ────────────
            #    Aggregate OI per strike for this expiry (latest snapshot minute)
            cur.execute("""
                WITH latest_min AS (
                    SELECT MAX(ts) AS max_ts
                    FROM option_chain
                    WHERE instrument = 'NIFTY' AND expiry = %s
                ),
                oi_per_strike AS (
                    SELECT
                        strike,
                        SUM(CASE WHEN option_type = 'CE' THEN COALESCE(oi,0) ELSE 0 END) AS ce_oi,
                        SUM(CASE WHEN option_type = 'PE' THEN COALESCE(oi,0) ELSE 0 END) AS pe_oi
                    FROM option_chain, latest_min
                    WHERE instrument = 'NIFTY'
                      AND expiry     = %s
                      AND ts         = latest_min.max_ts
                    GROUP BY strike
                ),
                all_strikes AS (SELECT strike FROM oi_per_strike),
                pain AS (
                    SELECT
                        a.strike AS expiry_at,
                        SUM(
                            CASE WHEN o.strike > a.strike
                                 THEN (o.strike - a.strike) * o.ce_oi
                                 ELSE 0 END
                            +
                            CASE WHEN o.strike < a.strike
                                 THEN (a.strike - o.strike) * o.pe_oi
                                 ELSE 0 END
                        ) AS total_pain
                    FROM all_strikes a
                    CROSS JOIN oi_per_strike o
                    GROUP BY a.strike
                )
                SELECT expiry_at FROM pain ORDER BY total_pain ASC LIMIT 1
            """, (expiry, expiry))
            mp_row = cur.fetchone()
            max_pain = int(mp_row[0]) if mp_row else atm

            # ── D. Historical avg weekly range for similar IV band ─────────────
            #    weekly_summaries view doesn't exist yet → compute inline
            cur.execute("""
                WITH daily AS (
                    SELECT
                        ts::date                        AS d,
                        MAX(underlying_ltp)             AS day_high,
                        MIN(underlying_ltp)             AS day_low
                    FROM option_chain
                    WHERE instrument = 'NIFTY'
                      AND underlying_ltp IS NOT NULL
                      AND ts >= NOW() - INTERVAL '90 days'
                    GROUP BY ts::date
                ),
                weekly AS (
                    SELECT
                        DATE_TRUNC('week', d)           AS wk,
                        MAX(day_high) - MIN(day_low)    AS weekly_range
                    FROM daily
                    GROUP BY DATE_TRUNC('week', d)
                    HAVING COUNT(*) >= 3   -- at least 3 trading days
                )
                SELECT
                    ROUND(AVG(weekly_range)::NUMERIC, 0)  AS avg_range,
                    ROUND(PERCENTILE_CONT(0.75)
                          WITHIN GROUP (ORDER BY weekly_range)::NUMERIC, 0) AS p75_range,
                    COUNT(*) AS weeks
                FROM weekly
            """)
            hist = cur.fetchone()
            avg_range = int(hist[0]) if hist and hist[0] else None
            p75_range = int(hist[1]) if hist and hist[1] else None
            hist_weeks = int(hist[2]) if hist and hist[2] else 0

    return {
        "spot":       spot,
        "atm":        atm,
        "atm_iv":     atm_iv,
        "vix":        float(vix)   if vix   else None,
        "pcr":        float(pcr)   if pcr   else None,
        "straddle":   float(straddle) if straddle else None,
        "ce_wall":    int(ce_wall) if ce_wall else None,
        "pe_wall":    int(pe_wall) if pe_wall else None,
        "max_pain":   max_pain,
        "avg_range":  avg_range,
        "p75_range":  p75_range,
        "hist_weeks": hist_weeks,
        "as_of":      str(ts),
    }


# ─── Step 2: Compute range (pure math, no DB) ──────────────────────────────────

def compute_range(s: dict, expiry: date) -> dict:
    today = date.today()
    dte   = max((expiry - today).days, 1)
    spot  = s["spot"]

    # IV: use live ATM IV, else fallback to VIX proxy, else historical typical
    iv = s["atm_iv"] or (s["vix"] * 1.4 if s["vix"] else 35.9)

    expected_move = _expected_move(spot, iv, dte)

    iv_lo = int(spot - expected_move)
    iv_hi = int(spot + expected_move)

    # OI wall anchoring: nudge range to nearest OI walls
    ce_wall = s.get("ce_wall") or iv_hi
    pe_wall = s.get("pe_wall") or iv_lo

    # Final predicted range: between PE wall and CE wall, bounded by IV move
    pred_lo = max(pe_wall, iv_lo - 100)
    pred_hi = min(ce_wall, iv_hi + 100)

    # Bias from PCR
    pcr = s.get("pcr")
    if pcr:
        if pcr > 1.2:
            bias = "BULLISH (put writers dominating)"
        elif pcr < 0.8:
            bias = "BEARISH (call writers dominating)"
        else:
            bias = "NEUTRAL"
    else:
        bias = "UNKNOWN (no PCR data)"

    return {
        "expiry":         expiry.strftime("%d %b %Y (%A)"),
        "dte":            dte,
        "spot":           spot,
        "iv":             round(iv, 1),
        "expected_move":  int(expected_move),
        "iv_range":       {"lo": iv_lo, "hi": iv_hi},
        "pred_range":     {"lo": pred_lo, "hi": pred_hi},
        "max_pain":       s["max_pain"],
        "ce_wall":        ce_wall,
        "pe_wall":        pe_wall,
        "pcr":            pcr,
        "bias":           bias,
        "straddle":       s.get("straddle"),
        "avg_weekly_range": s.get("avg_range"),
        "p75_weekly_range": s.get("p75_range"),
        "hist_weeks":     s.get("hist_weeks", 0),
        "as_of":          s.get("as_of"),
    }


# ─── Step 3: Print compact report ─────────────────────────────────────────────

def print_report(r: dict):
    sep = "─" * 52
    print(f"\n{sep}")
    print(f"  NIFTY WEEKLY RANGE PREDICTION")
    print(f"  Expiry : {r['expiry']}  (DTE {r['dte']})")
    print(sep)
    print(f"  Spot       : {r['spot']:>10,.2f}")
    print(f"  ATM IV     : {r['iv']:>10.1f}%")
    if r['straddle']:
        print(f"  ATM Straddle: {r['straddle']:>9,.0f} pts")
    print()
    print(f"  ── Expected Move (1σ = ±{r['expected_move']:,} pts) ──")
    print(f"  IV Range   : {r['iv_range']['lo']:,}  –  {r['iv_range']['hi']:,}")
    print()
    print(f"  ── OI-Anchored Prediction ──")
    print(f"  RANGE      : {r['pred_range']['lo']:,}  –  {r['pred_range']['hi']:,}")
    print(f"  Max Pain   : {r['max_pain']:,}  (gravity centre)")
    print(f"  PE Wall    : {r['pe_wall']:,}  (support)")
    print(f"  CE Wall    : {r['ce_wall']:,}  (resistance)")
    print()
    if r['pcr']:
        print(f"  PCR        : {r['pcr']:.2f}  →  {r['bias']}")
    else:
        print(f"  Bias       : {r['bias']}")
    print()
    if r['avg_weekly_range']:
        print(f"  ── Historical Context ({r['hist_weeks']} weeks) ──")
        print(f"  Avg weekly range : {r['avg_weekly_range']:,} pts")
        print(f"  75th %ile range  : {r['p75_weekly_range']:,} pts")
        width = r['pred_range']['hi'] - r['pred_range']['lo']
        tag = "LOW VOL week" if width < r['avg_weekly_range'] else "HIGH VOL week"
        print(f"  This week width  : {width:,} pts  →  {tag}")
    print()
    print(f"  ── Trade Idea ──")
    sell_ce = int(round(r['ce_wall'] / 50) * 50)
    sell_pe = int(round(r['pe_wall'] / 50) * 50)
    buy_ce  = sell_ce + 500
    buy_pe  = sell_pe - 500
    print(f"  Iron Condor: BUY {buy_pe} PE / SELL {sell_pe} PE")
    print(f"               SELL {sell_ce} CE / BUY {buy_ce} CE")
    print()
    print(f"  As of: {r['as_of']}")
    print(sep)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Nifty weekly range prediction")
    parser.add_argument("--json", action="store_true", help="Output JSON only")
    parser.add_argument("--expiry", help="Override expiry date YYYY-MM-DD")
    args = parser.parse_args()

    # Determine expiry
    if args.expiry:
        from datetime import datetime
        expiry = datetime.strptime(args.expiry, "%Y-%m-%d").date()
    else:
        today  = date.today()
        expiry = _next_thursday(today)
        # If today IS Thursday and market still open, use today
        if today.weekday() == 3:
            expiry = today

    # Fetch summary (35L rows → 8 numbers via SQL)
    summary = fetch_summary(expiry)

    if not summary:
        print("ERROR: DB unavailable or no data. Check DATABASE_URL in .env", file=sys.stderr)
        sys.exit(1)

    result = compute_range(summary, expiry)

    if args.json:
        print(json.dumps(result, default=str, indent=2))
    else:
        print_report(result)


if __name__ == "__main__":
    main()
