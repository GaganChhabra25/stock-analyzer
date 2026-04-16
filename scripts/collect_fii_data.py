"""
FII Derivatives Statistics Collector
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Downloads NSE's daily FII F&O participant data and stores in fii_derivative_stats.

Source: NSE archives (public, no auth needed)
  https://archives.nseindia.com/content/fo/fiiStats_DDMMMYYYY.csv

Schedule: Run at 5:00 PM IST (after NSE publishes end-of-day stats).
Usage:    venv/bin/python scripts/collect_fii_data.py [--date YYYY-MM-DD]

What it stores:
  - FII long/short contracts in Index Futures
  - FII long/short contracts in Index Options
  - Derived net positions (long - short)
  - Used by _db_fii_derivative_signal() in market_overview.py

Signal interpretation:
  FII net futures long  → institutions expect Nifty to rise → bullish
  FII net futures short → institutions expect Nifty to fall → bearish
  5-day trend of net position more meaningful than single-day value.
"""

import sys
import os
import argparse
import logging
from pathlib import Path
from datetime import date, timedelta
from io import StringIO

_APP_ROOT = Path(__file__).parent.parent
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))
os.chdir(_APP_ROOT)

from dotenv import load_dotenv
load_dotenv(_APP_ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

try:
    import requests
    _REQ_OK = True
except ImportError:
    _REQ_OK = False
    logger.error("requests not installed — pip install requests")
    sys.exit(1)

from screener.db import _get_conn, is_available, init_schema

# ── NSE Archive URL ────────────────────────────────────────────────────────────

_NSE_BASE = "https://archives.nseindia.com/content/fo"
_NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.nseindia.com/",
}


def _date_str(d: date) -> str:
    """'16Apr2026' format used in NSE archive filenames."""
    return d.strftime("%d%b%Y")


def _fetch_nse_fii_csv(d: date) -> dict:
    """
    Download NSE daily FII F&O stats CSV for a given date.
    Returns parsed dict or {} on failure.

    CSV columns we care about (row contains 'Index Futures'):
      Category, Buy Contracts, Buy Amount(Cr), Sell Contracts, Sell Amount(Cr),
      Open Interest Contracts, Open Interest Amount(Cr)

    We look for:
      - Row with 'FII' category AND 'Index Futures' type
      - Row with 'FII' category AND 'Index Options' type
    """
    url = f"{_NSE_BASE}/fiiStats_{_date_str(d)}.csv"
    try:
        resp = requests.get(url, headers=_NSE_HEADERS, timeout=20)
        if resp.status_code == 404:
            logger.debug("NSE FII CSV not found for %s (possibly non-trading day)", d)
            return {}
        if resp.status_code != 200:
            logger.warning("NSE FII CSV: HTTP %d for %s", resp.status_code, d)
            return {}
    except Exception as exc:
        logger.warning("NSE FII CSV fetch failed for %s: %s", d, exc)
        return {}

    try:
        import csv
        reader = csv.DictReader(StringIO(resp.text))
        rows = [r for r in reader]
    except Exception as exc:
        logger.warning("NSE FII CSV parse failed: %s", exc)
        return {}

    result: dict = {}

    for row in rows:
        # Normalise keys (strip spaces, lower)
        norm = {k.strip().lower(): v.strip() for k, v in row.items() if k}

        cat = norm.get("category", "").upper()
        typ = norm.get("type", "").lower()

        if "fii" not in cat:
            continue

        def _safe(k: str) -> float:
            try:
                return float(norm.get(k, "0").replace(",", "") or "0")
            except ValueError:
                return 0.0

        # Buy = long contracts, Sell = short contracts
        buy  = _safe("buy contracts") or _safe("buy value (cr)")
        sell = _safe("sell contracts") or _safe("sell value (cr)")
        oi   = _safe("open interest contracts") or _safe("oi amount (cr)")

        if "index future" in typ:
            result["fii_idx_fut_long"]  = buy
            result["fii_idx_fut_short"] = sell
            result["fii_idx_fut_oi"]    = oi
        elif "index option" in typ:
            result["fii_idx_opt_long"]  = buy
            result["fii_idx_opt_short"] = sell
            result["fii_idx_opt_oi"]    = oi

    if result:
        result["fii_net_fut"] = (result.get("fii_idx_fut_long", 0)
                                 - result.get("fii_idx_fut_short", 0))
        result["fii_net_opt"] = (result.get("fii_idx_opt_long", 0)
                                 - result.get("fii_idx_opt_short", 0))
        logger.info(
            "FII %s — Futures net: %+.0f  Options net: %+.0f",
            d, result["fii_net_fut"], result["fii_net_opt"],
        )
    else:
        logger.warning("No FII F&O rows parsed from CSV for %s", d)

    return result


def store_fii_data(d: date, data: dict) -> bool:
    """Insert or update fii_derivative_stats row for the given date."""
    if not data:
        return False
    try:
        with _get_conn() as conn:
            if conn is None:
                return False
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO fii_derivative_stats (
                        trade_date,
                        fii_idx_fut_long, fii_idx_fut_short, fii_idx_fut_oi,
                        fii_idx_opt_long, fii_idx_opt_short, fii_idx_opt_oi,
                        fii_net_fut, fii_net_opt
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (trade_date) DO UPDATE SET
                        fii_idx_fut_long  = EXCLUDED.fii_idx_fut_long,
                        fii_idx_fut_short = EXCLUDED.fii_idx_fut_short,
                        fii_idx_fut_oi    = EXCLUDED.fii_idx_fut_oi,
                        fii_idx_opt_long  = EXCLUDED.fii_idx_opt_long,
                        fii_idx_opt_short = EXCLUDED.fii_idx_opt_short,
                        fii_idx_opt_oi    = EXCLUDED.fii_idx_opt_oi,
                        fii_net_fut       = EXCLUDED.fii_net_fut,
                        fii_net_opt       = EXCLUDED.fii_net_opt,
                        fetched_at        = NOW()
                """, (
                    d,
                    data.get("fii_idx_fut_long"),
                    data.get("fii_idx_fut_short"),
                    data.get("fii_idx_fut_oi"),
                    data.get("fii_idx_opt_long"),
                    data.get("fii_idx_opt_short"),
                    data.get("fii_idx_opt_oi"),
                    data.get("fii_net_fut"),
                    data.get("fii_net_opt"),
                ))
        logger.info("Stored FII data for %s", d)
        return True
    except Exception as exc:
        logger.error("store_fii_data %s: %s", d, exc)
        return False


def main():
    parser = argparse.ArgumentParser(description="Collect NSE FII F&O statistics")
    parser.add_argument(
        "--date", default=None,
        help="Fetch for specific date (YYYY-MM-DD). Default = today.",
    )
    parser.add_argument(
        "--backfill", type=int, default=0,
        help="Backfill last N trading days (max 30).",
    )
    args = parser.parse_args()

    if not is_available():
        logger.error("Database not available. Check DATABASE_URL in .env")
        sys.exit(1)

    init_schema()

    if args.backfill > 0:
        n = min(args.backfill, 30)
        logger.info("Backfilling last %d days...", n)
        today = date.today()
        saved = 0
        for i in range(n, -1, -1):
            d = today - timedelta(days=i)
            if d.weekday() >= 5:   # skip weekends
                continue
            data = _fetch_nse_fii_csv(d)
            if data and store_fii_data(d, data):
                saved += 1
        logger.info("Backfill complete — stored %d days", saved)
        return

    target_date = date.today()
    if args.date:
        from datetime import datetime
        target_date = datetime.strptime(args.date, "%Y-%m-%d").date()

    data = _fetch_nse_fii_csv(target_date)
    if data:
        store_fii_data(target_date, data)
    else:
        logger.warning("No data fetched for %s — market closed or data not yet published", target_date)
        sys.exit(0)


if __name__ == "__main__":
    main()
