#!/usr/bin/env python3
"""
Stock Analyzer — Entry Point
Usage:  python main.py [--stocks-only] [--mf-only] [--no-report]
"""

import sys
import argparse

# Fix Windows UTF-8 encoding (needed for ₹ symbol)
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, Exception):
        pass

import logging
import os

from colorama import Fore, Style, init as colorama_init
colorama_init(autoreset=True)

import pandas as pd

from logging_config import configure_logging
from config import GOALS, ZERODHA_HOLDINGS_FILE, MF_HOLDINGS_FILE, REPORT_OUTPUT_FILE, HTML_REPORT_FILE
from utils.data_loader import load_zerodha_holdings, load_mf_holdings
from analyzers.stock_analyzer import StockAnalyzer
from analyzers.mf_analyzer import MFAnalyzer
from analyzers.portfolio_analyzer import PortfolioAnalyzer
from utils.report_generator import generate_full_report
from utils.html_report import generate_html_report

configure_logging()
logger = logging.getLogger(__name__)


def _header():
    print(f"\n{Fore.CYAN}{'=' * 72}")
    print("  STOCK ANALYZER  |  NSE/BSE + Mutual Funds  |  Goal: Rs.6 Crore in 15y")
    print(f"{'=' * 72}{Style.RESET_ALL}\n")


def run(stocks_only=False, mf_only=False, save_report=True):
    _header()

    stock_results = []
    etf_results   = []
    mf_results    = []

    # ── Holdings (stocks + ETFs) ──────────────────────────────────────────────
    if not mf_only:
        try:
            holdings = load_zerodha_holdings(ZERODHA_HOLDINGS_FILE)
        except FileNotFoundError as e:
            print(f"{Fore.RED}{e}{Style.RESET_ALL}")
            logger.error("Holdings file not found: %s", e)
            holdings = None

        if holdings is not None and not holdings.empty:
            analyzer = StockAnalyzer()
            etf_df   = holdings[holdings["Is_ETF"] == True]
            stk_df   = holdings[holdings["Is_ETF"] == False]

            # -- ETFs (no yfinance needed)
            print(f"{Fore.WHITE}Processing {len(etf_df)} ETFs …{Style.RESET_ALL}")
            for _, row in etf_df.iterrows():
                res = analyzer.analyze_etf(
                    symbol        = row["Symbol"],
                    quantity      = float(row["Quantity"]),
                    avg_cost      = float(row["Avg_Cost"]),
                    ltp           = float(row["LTP"])           if row["LTP"] is not None           else 0,
                    current_value = float(row["Current_Value"]) if row["Current_Value"] is not None else 0,
                    pnl           = float(row["PnL"])           if row["PnL"] is not None           else 0,
                    etf_name      = row["ETF_Name"],
                    etf_category  = row["ETF_Category"],
                )
                etf_results.append(res)

            # -- Direct stocks (yfinance for fundamentals)
            print(f"{Fore.WHITE}Fetching fundamentals for {len(stk_df)} direct stocks …{Style.RESET_ALL}")
            total = len(stk_df)
            for i, (_, row) in enumerate(stk_df.iterrows()):
                sym = row["Symbol"]
                print(f"  [{i+1}/{total}] {sym:15s}", end=" ", flush=True)
                res = analyzer.analyze(
                    symbol            = sym,
                    quantity          = float(row["Quantity"]),
                    avg_cost          = float(row["Avg_Cost"]),
                    ltp               = float(row["LTP"])           if row["LTP"] is not None           else None,
                    current_value_csv = float(row["Current_Value"]) if row["Current_Value"] is not None else None,
                    pnl_csv           = float(row["PnL"])           if row["PnL"] is not None           else None,
                )
                stock_results.append(res)
                color = (Fore.GREEN if res.action == "ADD MORE"
                         else Fore.RED   if res.action == "EXIT"
                         else Fore.CYAN)
                print(f"{color}{res.action}{Style.RESET_ALL}  score={res.total_score or 'N/A'}")
        else:
            print(f"{Fore.YELLOW}No stock holdings loaded.{Style.RESET_ALL}")

    # ── Mutual Funds ──────────────────────────────────────────────────────────
    if not stocks_only:
        try:
            mf_holdings = load_mf_holdings(MF_HOLDINGS_FILE)
        except FileNotFoundError as e:
            print(f"{Fore.YELLOW}No MF file found ({e}). Skipping MF analysis.{Style.RESET_ALL}")
            logger.warning("MF holdings file not found: %s", e)
            mf_holdings = None

        if mf_holdings is not None and not mf_holdings.empty:
            mf_analyzer = MFAnalyzer()
            print(f"\n{Fore.WHITE}Fetching MF NAV data from mfapi.in …{Style.RESET_ALL}")
            total = len(mf_holdings)
            for i, row in mf_holdings.iterrows():
                name = row["Fund_Name"]
                code = int(row["Scheme_Code"])
                print(f"  [{i+1}/{total}] {name[:50]} …", end=" ", flush=True)
                res = mf_analyzer.analyze(
                    fund_name         = name,
                    scheme_code       = code,
                    monthly_sip       = float(row["Monthly_SIP"]),
                    total_units       = float(row["Total_Units"]),
                    avg_purchase_nav  = float(row["Avg_Purchase_NAV"]),
                    expense_ratio     = float(row.get("Expense_Ratio", 0) or 0),
                    current_nav_csv   = float(row["Current_NAV"])   if "Current_NAV"   in row and pd.notna(row.get("Current_NAV"))   else None,
                    current_value_csv = float(row["Current_Value"]) if "Current_Value" in row and pd.notna(row.get("Current_Value")) else None,
                    pnl_csv           = float(row["PnL"])           if "PnL"           in row and pd.notna(row.get("PnL"))           else None,
                    category_hint     = row.get("Category", None),
                )
                mf_results.append(res)
                color = (Fore.GREEN if res.action == "ADD MORE"
                         else Fore.RED   if res.action == "EXIT"
                         else Fore.CYAN)
                print(f"{color}{res.action}{Style.RESET_ALL}")

    # ── Portfolio aggregation ─────────────────────────────────────────────────
    all_equity = stock_results + etf_results
    pa         = PortfolioAnalyzer(all_equity, mf_results)
    summary    = pa.summary()
    proj       = pa.projection()
    warnings   = pa.allocation_warnings()

    # ── Reports ───────────────────────────────────────────────────────────────
    generate_full_report(
        stock_results = stock_results,
        etf_results   = etf_results,
        mf_results    = mf_results,
        summary       = summary,
        projection    = proj,
        output_file   = REPORT_OUTPUT_FILE if save_report else None,
    )

    if save_report:
        html_path = generate_html_report(
            stock_results = stock_results,
            etf_results   = etf_results,
            mf_results    = mf_results,
            summary       = summary,
            projection    = proj,
            warnings      = warnings,
            output_file   = HTML_REPORT_FILE,
        )
        print(f"{Fore.GREEN}HTML Report -> {os.path.abspath(html_path)}{Style.RESET_ALL}")

    if warnings:
        print(f"\n{Fore.YELLOW}PORTFOLIO WARNINGS{Style.RESET_ALL}")
        for w in warnings:
            print(f"  * {w}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stock & MF Analyzer")
    parser.add_argument("--stocks-only", action="store_true")
    parser.add_argument("--mf-only",     action="store_true")
    parser.add_argument("--no-report",   action="store_true")
    args = parser.parse_args()
    try:
        run(stocks_only=args.stocks_only, mf_only=args.mf_only, save_report=not args.no_report)
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Interrupted.{Style.RESET_ALL}")
        sys.exit(0)
