"""
CRUDE option-chain freshness observability report (Phase-1, see
CRUDE_DATA_PHASE1_IMPLEMENTATION.md in the trade-bot repo).

Read-only. Does NOT widen ATM+/-10, does NOT change any option strategy
logic, does NOT touch the collector. This exists purely to make the existing
staleness problem (documented in the 2026-09-19 forensic audits and the
Phase-0 feasibility study: ~43% of mcx_crude_option_pressure_second rows are
>3s stale over any recent 14-day window) measurable on demand, and to test
the root-cause hypothesis that staleness concentrates in far-from-ATM
strikes (thin liquidity), which is the reason strike-widening was
deliberately NOT pursued in this phase.

No schema change was needed: mcx_crude_option_pressure_second already
stores each contract's own age_ms inside its `chain` JSONB (see
_build_option_pressure() in crudeoil_ws.py) -- this script only reads it.

Usage:
    python options/crude_option_freshness_report.py --date 2026-09-19
    python options/crude_option_freshness_report.py            # defaults to today
"""

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from screener.db import _get_conn

SYMBOL = "CRUDEOIL"
FRESH_MS = 3000
STALE_MID_MS = 15000


def _bucket(age_ms: float) -> str:
    if age_ms <= FRESH_MS:
        return "<=3s"
    if age_ms <= STALE_MID_MS:
        return "3-15s"
    return ">15s"


def report_pressure_second(target_date: date) -> None:
    """Per-second option-pressure freshness, overall and by strike distance
    from ATM (the actual root-cause axis -- far strikes tick less often).
    """
    start = datetime.combine(target_date, datetime.min.time())
    end = start + timedelta(days=1)

    with _get_conn() as conn:
        if conn is None:
            print("DB unavailable.")
            return
        with conn.cursor() as cur:
            cur.execute("""
                SELECT atm_strike, chain
                FROM mcx_crude_option_pressure_second
                WHERE ts >= %s AND ts < %s
            """, (start, end))
            rows = cur.fetchall()

    if not rows:
        print(f"No mcx_crude_option_pressure_second rows for {target_date}.")
        return

    overall = {"<=3s": 0, "3-15s": 0, ">15s": 0}
    by_distance: dict = {}   # abs(strike - atm) in steps -> bucket counts
    total_contracts = 0

    for atm_strike, chain in rows:
        contracts = chain if isinstance(chain, list) else json.loads(chain)
        for c in contracts:
            age_ms = c.get("age_ms")
            if age_ms is None:
                continue
            total_contracts += 1
            b = _bucket(age_ms)
            overall[b] += 1

            strike = c.get("strike")
            if strike is None:
                continue
            distance_steps = abs(int(strike) - int(atm_strike)) // 50
            slot = by_distance.setdefault(distance_steps, {"<=3s": 0, "3-15s": 0, ">15s": 0})
            slot[b] += 1

    print(f"\n=== mcx_crude_option_pressure_second freshness — {target_date} ===")
    print(f"Snapshot rows: {len(rows)}  Contract-observations: {total_contracts}")
    for b in ["<=3s", "3-15s", ">15s"]:
        pct = overall[b] / total_contracts * 100 if total_contracts else 0.0
        print(f"  {b:>6}: {overall[b]:>8}  ({pct:5.1f}%)")

    print("\nFreshness by distance-from-ATM (in 50pt strike steps):")
    print(f"  {'dist':>5}  {'<=3s':>8}  {'3-15s':>8}  {'>15s':>8}  {'total':>8}")
    for dist in sorted(by_distance):
        slot = by_distance[dist]
        tot = sum(slot.values())
        print(f"  {dist:>5}  {slot['<=3s']:>8}  {slot['3-15s']:>8}  {slot['>15s']:>8}  {tot:>8}")


def report_option_chain(target_date: date) -> None:
    """Per-minute REST option_chain freshness (available_at - ts) and
    per-contract quote staleness (now-relative quote_ts age is not
    meaningful retrospectively, so this reports collection latency only,
    matching the metric the 2026-09-19 backfill-feasibility audit used).
    """
    start = datetime.combine(target_date, datetime.min.time())
    end = start + timedelta(days=1)

    with _get_conn() as conn:
        if conn is None:
            print("DB unavailable.")
            return
        with conn.cursor() as cur:
            cur.execute("""
                SELECT EXTRACT(EPOCH FROM (available_at - ts)) * 1000
                FROM option_chain
                WHERE instrument = %s AND ts >= %s AND ts < %s AND available_at IS NOT NULL
            """, (SYMBOL, start, end))
            latencies = sorted(r[0] for r in cur.fetchall() if r[0] is not None)

    print(f"\n=== option_chain (REST) collection latency — {target_date} ===")
    if not latencies:
        print("No rows with a populated available_at for this date.")
        return
    n = len(latencies)

    def pct(p):
        idx = min(n - 1, int(p * n))
        return latencies[idx]

    print(f"  rows={n}  p50={pct(0.50):.0f}ms  p90={pct(0.90):.0f}ms  "
          f"p99={pct(0.99):.0f}ms  max={latencies[-1]:.0f}ms")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CRUDE option freshness report (read-only)")
    parser.add_argument("--date", type=str, default=None,
                         help="YYYY-MM-DD, defaults to today")
    args = parser.parse_args()
    target = date.fromisoformat(args.date) if args.date else date.today()
    report_pressure_second(target)
    report_option_chain(target)
