"""
NSE High-Probability Market Screener
─────────────────────────────────────
Scans Nifty 500 + liquid mid/small caps for stocks with the
highest statistical edge of closing significantly UP or DOWN by EOM.

Usage:
    python run_screener.py              # full scan, top 15
    python run_screener.py --top 6      # change number of picks
    python run_screener.py --fast       # quick scan (~60 stocks, faster)
    python run_screener.py --no-open    # don't auto-open HTML in browser
"""

import argparse
import sys
import os
import webbrowser
from datetime import datetime

# ── Stdout encoding fix for ₹ symbol on Windows ───────────────────────────────
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from screener.analyzer import StockScreener
from screener.report   import generate_screener_report


REPORT_FILE = "reports/screener_report.html"

BANNER = """
╔══════════════════════════════════════════════════════════════╗
║          NSE HIGH-PROBABILITY MARKET SCREENER                ║
║    Finds top EOM trade setups with statistical edge          ║
╚══════════════════════════════════════════════════════════════╝
"""


def main():
    parser = argparse.ArgumentParser(description="NSE Market Screener")
    parser.add_argument("--top",     type=int,  default=15,    help="Number of top picks (default: 15)")
    parser.add_argument("--fast",    action="store_true",      help="Quick scan with reduced universe")
    parser.add_argument("--no-open", action="store_true",      help="Don't open HTML in browser")
    args = parser.parse_args()

    print(BANNER)
    print(f"  Date      : {datetime.now().strftime('%d %b %Y, %I:%M %p')}")
    print(f"  Top picks : {args.top}")
    print(f"  Mode      : {'Fast (~60 stocks)' if args.fast else 'Full universe (~120 stocks)'}")
    print(f"  Filter    : Avg daily vol > Rs.5 Cr  |  Min 4 months data")
    print()

    screener   = StockScreener()
    candidates = screener.run(top_n=args.top, fast=args.fast)

    if not candidates:
        print("  No candidates found. Try running again or use --fast mode.")
        sys.exit(1)

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

    # ── Generate HTML report ───────────────────────────────────────────────────
    print(f"\n  Generating HTML report…")
    out = generate_screener_report(candidates, REPORT_FILE)
    abs_path = os.path.abspath(out)
    print(f"  Report saved: {abs_path}")

    if not args.no_open:
        webbrowser.open(f"file:///{abs_path.replace(os.sep, '/')}")
        print("  Opened in browser.")

    print()


if __name__ == "__main__":
    main()
