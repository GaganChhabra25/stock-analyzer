"""
FII Cash Market Flow Collector
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Downloads NSE daily FII/DII cash market buy-sell data via NSE API and stores
in fii_derivative_stats table.

Source: NSE public API — /api/fiidiiTradeReact (requires session cookie)
  Returns last ~5 trading days of FII + DII cash market net buy/sell (Crore)

Schedule: Run at 5:00 PM IST daily (Mon–Fri).
Usage:    venv/bin/python scripts/collect_fii_data.py [--backfill N]

What it stores:
  fii_net_fut  ← FII net cash market value (Cr), used as directional proxy
                 Note: 'fut' column repurposed for cash net — F&O OI not public
  fii_net_opt  ← DII net cash market value (Cr), used as secondary confirmation

Signal interpretation:
  FII net buyers  (+ve net value, Cr) → institutional accumulation → bullish
  FII net sellers (-ve net value, Cr) → institutional distribution  → bearish
  5-day trend matters more than single-day spike.

Typical range: ±3,000 Cr/day. Very high single-day values (>5,000) = strong signal.
"""

import sys
import os
import argparse
import logging
import time
from pathlib import Path
from datetime import date, timedelta, datetime

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
    logger.error("requests not installed")
    sys.exit(1)

from screener.db import _get_conn, is_available, init_schema

# ── NSE session ────────────────────────────────────────────────────────────────

_NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.nseindia.com/",
}


def _nse_session() -> requests.Session:
    """Build a warmed-up NSE session (needs homepage + option-chain cookies)."""
    s = requests.Session()
    s.headers.update(_NSE_HEADERS)
    try:
        s.get("https://www.nseindia.com", timeout=10)
        time.sleep(0.5)
        s.get("https://www.nseindia.com/option-chain", timeout=10)
        time.sleep(0.3)
    except Exception as exc:
        logger.warning("NSE session warm-up: %s", exc)
    return s


def _fetch_fii_cash(session: requests.Session) -> list:
    """
    Fetch FII/DII cash market data from NSE API.
    Returns list of dicts: [{category, date, buyValue, sellValue, netValue}, ...]
    """
    try:
        r = session.get(
            "https://www.nseindia.com/api/fiidiiTradeReact",
            timeout=15,
        )
        if r.status_code != 200:
            logger.warning("fiidiiTradeReact: HTTP %d", r.status_code)
            return []
        data = r.json()
        if not isinstance(data, list):
            return []
        return data
    except Exception as exc:
        logger.warning("_fetch_fii_cash: %s", exc)
        return []


def _parse_date(s: str) -> date:
    """Parse '15-Apr-2026' → date."""
    return datetime.strptime(s.strip(), "%d-%b-%Y").date()


def store_fii_rows(rows: list) -> int:
    """
    Parse and store FII/DII rows into fii_derivative_stats.
    Repurposes columns:
      fii_idx_fut_long/short  → FII buy/sell value (Cr)
      fii_idx_opt_long/short  → DII buy/sell value (Cr)
      fii_net_fut             → FII net (Cr)
      fii_net_opt             → DII net (Cr)
    Returns number of rows stored.
    """
    by_date: dict = {}

    for row in rows:
        cat = row.get("category", "").upper()
        raw_date = row.get("date", "")
        if not raw_date:
            continue
        try:
            d = _parse_date(raw_date)
        except ValueError:
            continue

        if d not in by_date:
            by_date[d] = {}

        try:
            buy = float(str(row.get("buyValue", 0) or 0).replace(",", ""))
            sell = float(str(row.get("sellValue", 0) or 0).replace(",", ""))
            net = float(str(row.get("netValue", 0) or 0).replace(",", ""))
        except (ValueError, TypeError):
            continue

        if "FII" in cat or "FPI" in cat:
            by_date[d]["fii_buy"] = buy
            by_date[d]["fii_sell"] = sell
            by_date[d]["fii_net"] = net
        elif "DII" in cat:
            by_date[d]["dii_buy"] = buy
            by_date[d]["dii_sell"] = sell
            by_date[d]["dii_net"] = net

    stored = 0
    for d, vals in sorted(by_date.items()):
        fii_net = vals.get("fii_net", 0.0)
        dii_net = vals.get("dii_net", 0.0)

        logger.info(
            "FII %s — cash net FII: %+.0f Cr  DII: %+.0f Cr",
            d, fii_net, dii_net,
        )

        try:
            with _get_conn() as conn:
                if conn is None:
                    break
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO fii_derivative_stats (
                            trade_date,
                            fii_idx_fut_long, fii_idx_fut_short, fii_net_fut,
                            fii_idx_opt_long, fii_idx_opt_short, fii_net_opt
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (trade_date) DO UPDATE SET
                            fii_idx_fut_long  = EXCLUDED.fii_idx_fut_long,
                            fii_idx_fut_short = EXCLUDED.fii_idx_fut_short,
                            fii_net_fut       = EXCLUDED.fii_net_fut,
                            fii_idx_opt_long  = EXCLUDED.fii_idx_opt_long,
                            fii_idx_opt_short = EXCLUDED.fii_idx_opt_short,
                            fii_net_opt       = EXCLUDED.fii_net_opt,
                            fetched_at        = NOW()
                    """, (
                        d,
                        vals.get("fii_buy"), vals.get("fii_sell"), fii_net,
                        vals.get("dii_buy"), vals.get("dii_sell"), dii_net,
                    ))
            stored += 1
        except Exception as exc:
            logger.error("store_fii_rows %s: %s", d, exc)

    return stored


def main():
    parser = argparse.ArgumentParser(description="Collect NSE FII cash market data")
    parser.add_argument(
        "--backfill", type=int, default=0,
        help="Fetch last N days from NSE API (API returns ~5 most recent days; use ≤5)",
    )
    args = parser.parse_args()

    if not is_available():
        logger.error("Database not available. Check DATABASE_URL in .env")
        sys.exit(1)

    init_schema()

    logger.info("Building NSE session...")
    session = _nse_session()

    logger.info("Fetching FII/DII cash data from NSE...")
    rows = _fetch_fii_cash(session)

    if not rows:
        logger.error("No data returned from NSE API")
        sys.exit(1)

    logger.info("Got %d rows from NSE API", len(rows))
    n = store_fii_rows(rows)
    logger.info("Stored %d new/updated row(s) in fii_derivative_stats", n)

    # Show current DB state
    try:
        with _get_conn() as conn:
            if conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT trade_date, fii_net_fut, fii_net_opt
                        FROM fii_derivative_stats
                        ORDER BY trade_date DESC LIMIT 7
                    """)
                    db_rows = cur.fetchall()
        print()
        print(f"{'Date':12s}  {'FII Net (Cr)':>14s}  {'DII Net (Cr)':>14s}")
        print("-" * 44)
        for r in db_rows:
            print(f"{str(r[0]):12s}  {float(r[1] or 0):>+14.0f}  {float(r[2] or 0):>+14.0f}")
        print()
    except Exception:
        pass


if __name__ == "__main__":
    main()
