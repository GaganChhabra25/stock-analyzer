"""
Console + text-file report generator.
Sections: Direct Stocks | ETFs | Mutual Funds | Portfolio Summary
"""

import os
import sys
from datetime import datetime
from colorama import Fore, Style, init as colorama_init
from tabulate import tabulate

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, Exception):
        pass

colorama_init(autoreset=True)

# ── Helpers ──────────────────────────────────────────────────────────────────

def _cr(val) -> str:
    """Format number as Rs. string."""
    try:
        val = float(val)
    except (TypeError, ValueError):
        return "N/A"
    if val >= 1e7:   return f"Rs.{val/1e7:.2f} Cr"
    if val >= 1e5:   return f"Rs.{val/1e5:.2f} L"
    return f"Rs.{val:,.0f}"


def _ac(action: str) -> str:
    """Color an action string."""
    a = action.upper().strip()
    if a == "EXIT":
        return Fore.RED + action + Style.RESET_ALL
    if a == "ADD MORE":
        return Fore.GREEN + action + Style.RESET_ALL
    return Fore.CYAN + action + Style.RESET_ALL


def _pc(val) -> str:
    """Color a P&L percentage."""
    if val is None: return "N/A"
    s = f"{val:+.1f}%"
    return (Fore.GREEN + s + Style.RESET_ALL) if val >= 0 else (Fore.RED + s + Style.RESET_ALL)


def _section(title: str):
    line = "=" * 72
    print(f"\n{Fore.CYAN}{line}\n  {title}\n{line}{Style.RESET_ALL}")


def _subsection(title: str):
    pad = max(0, 66 - len(title))
    print(f"\n{Fore.YELLOW}-- {title} {'-' * pad}{Style.RESET_ALL}")


# ── Direct stocks ────────────────────────────────────────────────────────────

def print_stock_results(results: list):
    if not results:
        return
    _section("DIRECT EQUITY HOLDINGS — FUNDAMENTAL ANALYSIS")

    rows = []
    for r in results:
        rows.append([
            r["symbol"],
            r["quantity"],
            f"Rs.{r['avg_cost']:,.0f}",
            f"Rs.{r['current_price']:,.0f}" if r["current_price"] else "N/A",
            _cr(r["current_value"]),
            _pc(r["gain_loss_pct"]),
            f"{r['total_score']:.0f}/100" if r["total_score"] is not None else "N/A",
            _ac(r["action"]),
        ])
    headers = ["Symbol", "Qty", "Avg Cost", "CMP", "Cur Value", "P&L%", "Score", "Action"]
    print(tabulate(rows, headers=headers, tablefmt="rounded_outline"))

    _subsection("Notes & Metrics")
    for r in results:
        score_str = f"[{r['total_score']:.0f}/100]" if r["total_score"] is not None else ""
        print(f"  {Fore.WHITE}{r['symbol']:12s}{Style.RESET_ALL} {score_str:10s} -> {_ac(r['action'])}")
        print(f"    {r['reason']}")
        if r.get("key_metrics"):
            print(f"    {r['key_metrics']}")
        print()


# ── ETFs ─────────────────────────────────────────────────────────────────────

def print_etf_results(results: list):
    if not results:
        return
    _section("ETF / INDEX FUND HOLDINGS")

    rows = []
    for r in results:
        rows.append([
            r["symbol"],
            r["name"][:30],
            r["quantity"],
            f"Rs.{r['avg_cost']:,.2f}",
            f"Rs.{r['current_price']:,.2f}" if r["current_price"] else "N/A",
            _cr(r["current_value"]),
            _pc(r["gain_loss_pct"]),
            _ac(r["action"]),
        ])
    headers = ["Symbol", "ETF Name", "Qty", "Avg Cost", "CMP", "Cur Value", "P&L%", "Action"]
    print(tabulate(rows, headers=headers, tablefmt="rounded_outline"))

    _subsection("ETF Advice")
    for r in results:
        print(f"  {Fore.WHITE}{r['symbol']:12s}{Style.RESET_ALL} {r['etf_category']:35s} -> {_ac(r['action'])}")
        print(f"    {r['reason']}")
        print()


# ── MFs ──────────────────────────────────────────────────────────────────────

def print_mf_results(results: list):
    if not results:
        return
    _section("MUTUAL FUND HOLDINGS")

    rows = []
    for r in results:
        rows.append([
            r["fund_name"][:38],
            _cr(r["invested"]),
            _cr(r["current_value"]),
            _pc(r["gain_loss_pct"]),
            f"{r['return_1yr']:+.1f}%" if r.get("return_1yr") is not None else "N/A",
            f"{r['return_3yr']:+.1f}%" if r.get("return_3yr") is not None else "N/A",
            f"{r['return_5yr']:+.1f}%" if r.get("return_5yr") is not None else "N/A",
            f"Rs.{r['monthly_sip']:,.0f}",
            _ac(r["action"]),
        ])
    headers = ["Fund", "Invested", "Cur Value", "P&L%", "1Y", "3Y", "5Y", "SIP/mo", "Action"]
    print(tabulate(rows, headers=headers, tablefmt="rounded_outline"))

    _subsection("SIP Recommendations")
    for r in results:
        print(f"  {Fore.WHITE}{r['fund_name'][:55]}{Style.RESET_ALL}")
        print(f"    Category : {r.get('category','Unknown')}")
        print(f"    Action   : {_ac(r['action'])}")
        print(f"    Note     : {r['reason']}")
        if r.get("sip_suggestion"):
            print(f"    SIP      : {Fore.CYAN}{r['sip_suggestion']}{Style.RESET_ALL}")
        print()


# ── Portfolio summary ────────────────────────────────────────────────────────

def print_portfolio_summary(summary: dict, projection: dict):
    _section("PORTFOLIO SUMMARY & CORPUS PROJECTION")

    rows = [
        ["Direct Stocks",  _cr(summary["stocks_value"]),   f"{summary['stocks_pct']:.1f}%"],
        ["Mutual Funds",   _cr(summary["mf_value"]),        f"{summary['mf_pct']:.1f}%"],
        ["TOTAL",          _cr(summary["total_value"]),     "100.0%"],
    ]
    print(tabulate(rows, headers=["Category", "Current Value", "Allocation"],
                   tablefmt="rounded_outline"))

    _subsection("Corpus Projection at 15% CAGR — 15 Years")
    proj_rows = [
        ["Current Portfolio Value",    _cr(projection["current_portfolio"])],
        ["FV of Current Portfolio",    _cr(projection["fv_portfolio"])],
        ["Monthly SIP",                _cr(projection["monthly_sip"])],
        ["FV of Future SIPs",          _cr(projection["fv_sip"])],
        ["PROJECTED CORPUS",           _cr(projection["total_projected"])],
        ["TARGET CORPUS",              _cr(projection["target_corpus"])],
        ["Gap / Surplus",              _cr(projection["gap"])],
    ]
    print(tabulate(proj_rows, headers=["Metric", "Value"], tablefmt="rounded_outline"))

    if projection["on_track"]:
        print(f"\n  {Fore.GREEN}YOU ARE ON TRACK to reach Rs.6 Crore!{Style.RESET_ALL}")
    else:
        print(f"\n  {Fore.RED}NOT ON TRACK. Gap: {_cr(abs(projection['gap']))}{Style.RESET_ALL}")
        print(f"  {Fore.YELLOW}  Required monthly SIP to close gap: {_cr(projection['required_sip'])}{Style.RESET_ALL}")

    _subsection("Scenario Analysis")
    sc_rows = [[lbl, _cr(val)] for lbl, val in projection["scenarios"].items()]
    print(tabulate(sc_rows, headers=["Scenario", "Projected Corpus"], tablefmt="rounded_outline"))


# ── Master report ────────────────────────────────────────────────────────────

def generate_full_report(stock_results: list, etf_results: list, mf_results: list,
                         summary: dict, projection: dict, output_file: str = None):
    print_stock_results(stock_results)
    print_etf_results(etf_results)
    print_mf_results(mf_results)
    print_portfolio_summary(summary, projection)

    if not output_file:
        return

    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        f.write(f"STOCK ANALYZER REPORT — {ts}\n{'=' * 72}\n\n")

        f.write("DIRECT STOCKS\n" + "-" * 40 + "\n")
        for r in stock_results:
            f.write(f"{r['symbol']:12s}  Score:{str(r.get('total_score','N/A')):>6}  Action: {r['action']}\n")
            f.write(f"  {r['reason']}\n")
            if r.get("key_metrics"):
                f.write(f"  {r['key_metrics']}\n")
            f.write("\n")

        f.write("\nETFs / INDEX FUNDS\n" + "-" * 40 + "\n")
        for r in etf_results:
            f.write(f"{r['symbol']:12s}  {r['etf_category']:35s}  Action: {r['action']}\n")
            f.write(f"  {r['reason']}\n\n")

        f.write("\nMUTUAL FUNDS\n" + "-" * 40 + "\n")
        for r in mf_results:
            f.write(f"{r['fund_name'][:55]:55s}  Action: {r['action']}\n")
            f.write(f"  {r['reason']}\n")
            if r.get("sip_suggestion"):
                f.write(f"  SIP: {r['sip_suggestion']}\n")
            f.write("\n")

        f.write("\nPORTFOLIO SUMMARY\n" + "-" * 40 + "\n")
        f.write(f"Total Portfolio   : {_cr(summary['total_value'])}\n")
        f.write(f"Projected Corpus  : {_cr(projection['total_projected'])}\n")
        f.write(f"Target Corpus     : {_cr(projection['target_corpus'])}\n")
        gap_lbl = "Surplus" if projection["gap"] < 0 else "Gap"
        f.write(f"{gap_lbl:18s}: {_cr(abs(projection['gap']))}\n")
        if not projection["on_track"]:
            f.write(f"Required SIP/mo   : {_cr(projection['required_sip'])}\n")

    print(f"\n{Fore.CYAN}Report saved -> {output_file}{Style.RESET_ALL}\n")
