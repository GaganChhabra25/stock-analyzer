"""
NSE High-Probability Market Screener
─────────────────────────────────────
Scans Nifty 500 + liquid mid/small caps for stocks with the
highest statistical edge of closing significantly UP or DOWN by EOM.

Usage:
    python run_screener.py                              # full scan, top 15
    python run_screener.py --top 6                      # change number of picks
    python run_screener.py --fast                       # quick scan (~60 stocks)
    python run_screener.py --no-open                    # don't auto-open HTML
    python run_screener.py --track INFY                 # add INFY to active tracking
    python run_screener.py --track INFY --price 1450 --direction UP
    python run_screener.py --untrack INFY               # stop tracking INFY
"""

import argparse
import json
import sys
import os
import webbrowser
from datetime import datetime
from pathlib import Path

# Load .env before anything else so DATABASE_URL is available
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

# ── Stdout encoding fix for ₹ symbol on Windows ───────────────────────────────
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from screener.analyzer           import StockScreener
from screener.report             import generate_screener_report
from screener.market_overview    import fetch_market_overview
from screener.db                 import save_market_overview, get_accuracy_summary, is_available as db_available
from screener.telegram_notifier  import send_predictions, send_error, is_configured as tg_configured


REPORT_FILE         = "reports/screener_report.html"
ACTIVE_TRADES_FILE  = "data/active_trades.json"

BANNER = """
╔══════════════════════════════════════════════════════════════╗
║          NSE HIGH-PROBABILITY MARKET SCREENER                ║
║    Finds top EOM trade setups with statistical edge          ║
╚══════════════════════════════════════════════════════════════╝
"""


def load_active_trades() -> dict:
    if os.path.exists(ACTIVE_TRADES_FILE):
        try:
            with open(ACTIVE_TRADES_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_active_trades(trades: dict):
    os.makedirs(os.path.dirname(ACTIVE_TRADES_FILE), exist_ok=True)
    with open(ACTIVE_TRADES_FILE, "w", encoding="utf-8") as f:
        json.dump(trades, f, indent=2, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(description="NSE Market Screener")
    parser.add_argument("--top",       type=int,   default=15,  help="Number of top picks (default: 15)")
    parser.add_argument("--fast",      action="store_true",     help="Quick scan with reduced universe")
    parser.add_argument("--no-open",   action="store_true",     help="Don't open HTML in browser")
    parser.add_argument("--track",     type=str,   default=None, help="Add NSE symbol to active trade tracking")
    parser.add_argument("--price",     type=float, default=0,   help="Entry price for tracked stock")
    parser.add_argument("--direction", type=str,   default="UP", choices=["UP", "DOWN"],
                        help="Trade direction for manually tracked stock")
    parser.add_argument("--untrack",   type=str,   default=None, help="Remove symbol from active tracking")
    args = parser.parse_args()

    print(BANNER)
    print(f"  Date      : {datetime.now().strftime('%d %b %Y, %I:%M %p')}")
    print(f"  Top picks : {args.top}")
    print(f"  Mode      : {'Fast (~60 stocks)' if args.fast else 'Full universe (~120 stocks)'}")
    print(f"  Filter    : Avg daily vol > Rs.5 Cr  |  Min 4 months data")
    print()

    # ── Manage active trades file ──────────────────────────────────────────────
    active_trades = load_active_trades()

    if args.untrack:
        sym = args.untrack.upper().strip()
        if sym in active_trades:
            del active_trades[sym]
            save_active_trades(active_trades)
            print(f"  Removed {sym} from active tracking.\n")
        else:
            print(f"  {sym} was not in active tracking.\n")

    if args.track:
        sym = args.track.upper().strip()
        active_trades[sym] = {
            "symbol":            sym,
            "entry_price":       args.price,
            "entry_direction":   args.direction,
            "entry_probability": 0,    # filled after analysis below
            "entry_cmp":         args.price,
            "added_at":          datetime.now().strftime("%d %b %Y, %I:%M %p"),
            "name":              sym,
        }
        save_active_trades(active_trades)
        print(f"  Added {sym} to active tracking (direction: {args.direction}, price: {args.price or 'auto'}).\n")

    # ── Run screener ───────────────────────────────────────────────────────────
    screener              = StockScreener()
    candidates, all_stocks = screener.run(top_n=args.top, fast=args.fast)

    if not candidates:
        print("  No candidates found. Try running again or use --fast mode.")
        sys.exit(1)

    # ── Analyze tracked symbols not already in any analyzed stock ─────────────
    candidate_syms = {c["symbol"] for c in all_stocks}
    extra_syms     = [sym for sym in active_trades if sym not in candidate_syms]
    extra_data     = screener.analyze_extra_symbols(extra_syms)

    # Update entry_probability / entry_cmp when first tracked (entry_probability == 0)
    trades_changed = False
    for sym, result in extra_data.items():
        if sym in active_trades and active_trades[sym].get("entry_probability", 0) == 0:
            active_trades[sym]["entry_probability"] = result["probability"]
            active_trades[sym]["entry_cmp"]         = result["cmp"]
            active_trades[sym]["name"]              = result["name"]
            trades_changed = True
    # Also fill in for symbols that appeared in top picks
    for c in candidates:
        if c["symbol"] in active_trades and active_trades[c["symbol"]].get("entry_probability", 0) == 0:
            active_trades[c["symbol"]]["entry_probability"] = c["probability"]
            active_trades[c["symbol"]]["entry_cmp"]         = c["cmp"]
            active_trades[c["symbol"]]["name"]              = c["name"]
            trades_changed = True
    if trades_changed:
        save_active_trades(active_trades)

    # ── Print summary to terminal ──────────────────────────────────────────────
    try:
        from colorama import Fore, Style, init
        init()
        use_color = True
    except ImportError:
        use_color = False

    print(f"\n{'='*70}")
    print(f"  TOP {len(candidates)} CANDIDATES")
    print(f"{'='*70}")
    for i, c in enumerate(candidates, 1):
        direction = c["direction"]
        prob      = c["probability"]
        wr        = c["win_rate"]
        if use_color:
            dir_str = (Fore.GREEN + f"▲ {direction}" if direction == "UP"
                       else Fore.RED + f"▼ {direction}")
            reset = Style.RESET_ALL
        else:
            dir_str = f"{'▲' if direction == 'UP' else '▼'} {direction}"
            reset = ""
        print(
            f"  #{i:1d}  {c['symbol']:12s}  CMP Rs.{c['cmp']:>9,.2f}"
            f"  {dir_str}{reset:20s}"
            f"  Prob: {prob:.0f}%  |  Win Rate: {wr:.0f}%"
            f"  |  RSI: {c['rsi']:.0f}  |  {c['sector']}"
        )

    if extra_data:
        print(f"\n  {'='*68}")
        print(f"  TRACKED ACTIVE TRADES ({len(extra_data)} symbol(s))")
        print(f"  {'='*68}")
        for sym, c in extra_data.items():
            meta = active_trades.get(sym, {})
            entry_p = meta.get("entry_price", 0)
            entry_d = meta.get("entry_direction", "—")
            pnl_str = ""
            if entry_p > 0:
                pnl = (c["cmp"] - entry_p) / entry_p * 100
                pnl_str = f"  P&L: {'+' if pnl>=0 else ''}{pnl:.1f}%"
            dir_str = f"{'▲' if c['direction']=='UP' else '▼'} {c['direction']}"
            flip = ""
            if entry_d and entry_d != c["direction"]:
                flip = "  ⚠ DIRECTION FLIPPED!"
            print(
                f"  {sym:12s}  CMP Rs.{c['cmp']:>9,.2f}"
                f"  {dir_str:12s}  Prob: {c['probability']:.0f}%{pnl_str}{flip}"
            )

    # ── Fetch market overview (Nifty / Crude / NatGas) ────────────────────────
    print(f"  Fetching market overview (Nifty 50, MCX Crude, MCX NatGas)…")
    try:
        mkt_overview = fetch_market_overview()
        n50_cmp = mkt_overview["nifty"].get("cmp")
        cr_cmp  = mkt_overview["crude_oil"].get("cmp")
        ng_cmp  = mkt_overview["nat_gas"].get("cmp")
        print(f"  Nifty 50: ₹{n50_cmp:,.2f}" if n50_cmp else "  Nifty 50: —")
        print(f"  Crude Oil: ₹{cr_cmp:,.2f}/bbl" if cr_cmp else "  Crude Oil: —")
        print(f"  Nat Gas:   ₹{ng_cmp:,.2f}/mmBtu" if ng_cmp else "  Nat Gas: —")
    except Exception as e:
        print(f"  Market overview fetch failed: {e}")
        mkt_overview = {}

    # ── Persist predictions + outcomes to PostgreSQL ───────────────────────────
    accuracy_rows: list = []
    if db_available() and mkt_overview:
        try:
            n_saved = save_market_overview(mkt_overview)
            print(f"  DB: saved {n_saved} prediction(s) to PostgreSQL.")
            accuracy_rows = get_accuracy_summary()
        except Exception as e:
            print(f"  DB: save failed — {e}")

    # ── Send Telegram alert (disabled) ────────────────────────────────────────
    # Uncomment to re-enable market prediction alerts on Telegram
    # if tg_configured() and mkt_overview:
    #     try:
    #         ok = send_predictions(mkt_overview, accuracy_rows)
    #         print(f"  Telegram: alert {'sent ✓' if ok else 'failed ✗'}")
    #     except Exception as e:
    #         print(f"  Telegram: send failed — {e}")
    #         try:
    #             send_error("run_screener.py", str(e))
    #         except Exception:
    #             pass

    # ── Generate HTML report ───────────────────────────────────────────────────
    print(f"\n  Generating HTML report…")
    out = generate_screener_report(
        candidates,
        REPORT_FILE,
        active_trades_meta=active_trades,
        active_trades_data=extra_data,
        all_stocks=all_stocks,
        market_overview=mkt_overview,
    )
    abs_path = os.path.abspath(out)
    print(f"  Report saved: {abs_path}")

    if not args.no_open:
        webbrowser.open(f"file:///{abs_path.replace(os.sep, '/')}")
        print("  Opened in browser.")

    print()


if __name__ == "__main__":
    main()
