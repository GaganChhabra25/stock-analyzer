"""
Reset market predictions — wipe stale/random rows and regenerate from DB.

Usage (on Contabo server):
    cd /home/stockapp/stock-analyzer
    venv/bin/python scripts/reset_predictions.py

What it does:
    1. Deletes ALL rows from market_predictions and market_outcomes
    2. Calls fetch_market_overview() — uses DB signals (PCR, OI, VIX, MCX OHLC)
       plus yfinance price data for technicals
    3. Saves fresh predictions via save_market_overview()
"""

import sys
import os
from pathlib import Path

# Ensure app root is on sys.path (scripts/ dir is not the root)
_APP_ROOT = Path(__file__).parent.parent
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))
os.chdir(_APP_ROOT)

# Load .env so DATABASE_URL is available
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

# UTF-8 stdout for ₹ symbol
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from screener.db import _get_conn, is_available, init_schema, save_market_overview
from screener.market_overview import fetch_market_overview


def clear_predictions() -> int:
    """Delete all rows from market_predictions and market_outcomes. Returns total deleted."""
    if not is_available():
        print("ERROR: Database not available. Check DATABASE_URL in .env")
        sys.exit(1)

    with _get_conn() as conn:
        if conn is None:
            print("ERROR: Could not connect to database.")
            sys.exit(1)
        cur = conn.cursor()
        cur.execute("DELETE FROM market_predictions")
        deleted_p = cur.rowcount
        cur.execute("DELETE FROM market_outcomes")
        deleted_o = cur.rowcount
        cur.close()

    print(f"  Cleared {deleted_p} prediction row(s) and {deleted_o} outcome row(s).")
    return deleted_p + deleted_o


def main():
    print()
    print("=" * 60)
    print("  Stock Analyzer — Reset & Regenerate Predictions")
    print("=" * 60)
    print()

    # Step 1: wipe (init_schema before clear — may fail on duplicate index, that's ok)
    init_schema()
    print("[1/3] Clearing existing predictions...")
    clear_predictions()

    # Re-run init_schema after clearing so unique indexes are built clean
    init_schema()

    # Step 2: fetch fresh market overview (DB-native signals + yfinance prices)
    print()
    print("[2/3] Fetching market overview (Nifty / Crude / NatGas)...")
    print("      Uses DB signals: PCR trend, OI change, max pain, VIX,")
    print("      MCX OI confluence, seasonal patterns + yfinance technicals.")
    print()

    try:
        overview = fetch_market_overview()
    except Exception as exc:
        print(f"ERROR: fetch_market_overview() failed: {exc}")
        sys.exit(1)

    nifty = overview.get("nifty", {})
    crude = overview.get("crude_oil", {})
    natgas = overview.get("nat_gas", {})

    if nifty.get("cmp"):
        print(f"  Nifty 50  : ₹{nifty['cmp']:,.2f}")
    if crude.get("cmp"):
        print(f"  Crude Oil : ₹{crude['cmp']:,.2f}/bbl")
    if natgas.get("cmp"):
        print(f"  Nat Gas   : ₹{natgas['cmp']:,.2f}/mmBtu")

    print()
    _print_signals("NIFTY",  nifty.get("signals",  {}))
    _print_signals("CRUDE",  crude.get("signals",  {}))
    _print_signals("NATGAS", natgas.get("signals", {}))

    # Step 3: save
    print("[3/3] Saving new predictions to PostgreSQL...")
    try:
        n_saved = save_market_overview(overview)
    except Exception as exc:
        print(f"ERROR: save_market_overview() failed: {exc}")
        sys.exit(1)

    print(f"  Saved {n_saved} prediction row(s).")
    print()
    print("Done. Refresh the DB Insights page to see updated predictions.")
    print()


def _print_signals(label: str, signals: dict):
    if not signals:
        return
    parts = []
    for k, v in signals.items():
        if v is not None:
            try:
                parts.append(f"{k}={float(v):+.3f}")
            except (TypeError, ValueError):
                parts.append(f"{k}={v}")
    if parts:
        print(f"  {label:6s} signals: {', '.join(parts)}")


if __name__ == "__main__":
    main()
