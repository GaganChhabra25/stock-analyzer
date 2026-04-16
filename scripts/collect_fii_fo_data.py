"""
FII F&O Participant OI Collector
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Downloads NSE daily F&O participant-wise Open Interest data and stores
FII net index futures position in fii_derivative_stats table.

Source: NSE archives — fao_participant_oi_DDMMYYYY.csv
  Published by NSE after market close (~6–7 PM IST daily).
  Contains long/short contracts per client type (FII, DII, PRO, CLIENT).

Schedule: Run at 7:00 PM IST = CEST 15:30 (server cron: 30 15 * * 1-5)
Usage:    venv/bin/python scripts/collect_fii_fo_data.py [--date YYYY-MM-DD]

Why this matters:
  Cash market FII flow (what we had before) = indirect proxy.
  F&O net contracts = DIRECT Nifty futures bet by largest institutional players.

  FII net long contracts → 10,000  → strongly bullish
  FII net short contracts → -10,000 → strongly bearish

  Typical total Nifty futures OI: ~1,50,000 contracts.
  Net ±30,000 contracts = significant positioning.

Columns stored (in fii_derivative_stats):
  fo_fii_fut_long   ← FII index futures long contracts
  fo_fii_fut_short  ← FII index futures short contracts
  fo_fii_net_fut    ← net (long - short)
  fo_fii_call_long  ← FII index call long contracts
  fo_fii_call_short ← FII index call short contracts
  fo_fii_put_long   ← FII index put long contracts
  fo_fii_put_short  ← FII index put short contracts
  fo_fii_pc_ratio   ← FII put long / call long (institutional options sentiment)
"""

import sys
import os
import argparse
import logging
import time
import io
from pathlib import Path
from datetime import date, timedelta
from typing import Optional

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

import warnings
warnings.filterwarnings("ignore")

try:
    import requests
    import pandas as pd
    _DEPS_OK = True
except ImportError as e:
    logger.error("Missing dependency: %s", e)
    sys.exit(1)

from screener.db import _get_conn, is_available, init_schema
from screener.telegram_notifier import send_fii_fo_status, send_error, is_configured as tg_ok


# ── NSE session ─────────────────────────────────────────────────────────────────

_NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.nseindia.com/",
}


def _nse_session() -> requests.Session:
    """Build a warmed-up NSE session."""
    s = requests.Session()
    s.headers.update(_NSE_HEADERS)
    try:
        s.get("https://www.nseindia.com", timeout=12)
        time.sleep(0.5)
        s.get("https://www.nseindia.com/market-data/live-equity-market", timeout=10)
        time.sleep(0.3)
    except Exception as exc:
        logger.warning("NSE session warm-up: %s", exc)
    return s


# ── CSV downloader ───────────────────────────────────────────────────────────────

def _fo_csv_url(d: date) -> str:
    """NSE archive URL for F&O participant OI CSV."""
    return (
        f"https://archives.nseindia.com/content/nsccl/"
        f"fao_participant_oi_{d.strftime('%d%m%Y')}.csv"
    )


def _download_fo_csv(d: date, session: requests.Session) -> Optional[pd.DataFrame]:
    """
    Download and parse participant OI CSV for date d.
    Returns DataFrame with columns normalised to lowercase stripped names.
    Returns None if not available (holiday / not yet published).
    """
    url = _fo_csv_url(d)
    logger.info("Fetching: %s", url)
    try:
        resp = session.get(url, timeout=20)
        if resp.status_code == 404:
            logger.warning("CSV not found (holiday or not yet published): %s", url)
            return None
        if resp.status_code != 200:
            logger.warning("HTTP %d for %s", resp.status_code, url)
            return None

        content = resp.content.decode("utf-8", errors="replace")
        df = pd.read_csv(io.StringIO(content))
        df.columns = [c.strip().lower() for c in df.columns]
        logger.info("Downloaded %d rows, columns: %s", len(df), list(df.columns))
        return df
    except Exception as exc:
        logger.warning("_download_fo_csv %s: %s", d, exc)
        return None


# ── Parser ───────────────────────────────────────────────────────────────────────

def _safe_int(val) -> Optional[int]:
    try:
        return int(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _find_col(df: pd.DataFrame, *keywords) -> Optional[str]:
    """Find first column that contains ALL keywords (case-insensitive)."""
    kws = [k.lower() for k in keywords]
    for col in df.columns:
        if all(k in col for k in kws):
            return col
    return None


def _parse_fii_row(df: pd.DataFrame) -> Optional[dict]:
    """
    Extract FII index futures + options data from the participant OI DataFrame.
    NSE CSV column names are inconsistent across years — we use fuzzy matching.

    Expected row: client type = 'FII' or 'FPI'
    """
    # Find the client-type column
    ct_col = _find_col(df, "client") or _find_col(df, "type")
    if ct_col is None:
        logger.warning("Cannot find 'client type' column. Columns: %s", list(df.columns))
        return None

    # Find FII row (may be labelled FII or FPI)
    mask = df[ct_col].astype(str).str.upper().str.contains(r"FII|FPI", regex=True)
    fii_rows = df[mask]
    if fii_rows.empty:
        logger.warning("No FII/FPI row found in CSV")
        return None
    fii = fii_rows.iloc[0]

    def _get(*keywords) -> Optional[int]:
        col = _find_col(df, *keywords)
        return _safe_int(fii[col]) if col else None

    # Index futures
    fut_long  = _get("future", "index", "long")
    fut_short = _get("future", "index", "short")

    # Index options
    call_long  = _get("option", "index", "call", "long")
    call_short = _get("option", "index", "call", "short")
    put_long   = _get("option", "index", "put", "long")
    put_short  = _get("option", "index", "put", "short")

    if fut_long is None and fut_short is None:
        logger.warning("Could not parse futures columns. Raw FII row: %s", fii.to_dict())
        return None

    net_fut = (fut_long or 0) - (fut_short or 0)

    # Put/Call ratio for options (FII options positioning)
    pc_ratio = None
    if call_long and call_long > 0:
        pc_ratio = round((put_long or 0) / call_long, 3)

    result = {
        "fut_long":  fut_long,
        "fut_short": fut_short,
        "net_fut":   net_fut,
        "call_long": call_long,
        "call_short": call_short,
        "put_long":  put_long,
        "put_short": put_short,
        "pc_ratio":  pc_ratio,
    }
    logger.info(
        "FII F&O — Idx Fut Long: %s  Short: %s  Net: %+d  "
        "Call Long: %s  Put Long: %s  P/C: %s",
        fut_long, fut_short, net_fut, call_long, put_long, pc_ratio,
    )
    return result


# ── DB writer ────────────────────────────────────────────────────────────────────

def _store(d: date, data: dict) -> bool:
    """
    Upsert FII F&O data into fii_derivative_stats for date d.
    Only touches the fo_* columns — cash market columns remain untouched.
    """
    try:
        with _get_conn() as conn:
            if conn is None:
                return False
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO fii_derivative_stats (
                        trade_date,
                        fo_fii_fut_long, fo_fii_fut_short, fo_fii_net_fut,
                        fo_fii_call_long, fo_fii_call_short,
                        fo_fii_put_long,  fo_fii_put_short,
                        fo_fii_pc_ratio
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (trade_date) DO UPDATE SET
                        fo_fii_fut_long   = EXCLUDED.fo_fii_fut_long,
                        fo_fii_fut_short  = EXCLUDED.fo_fii_fut_short,
                        fo_fii_net_fut    = EXCLUDED.fo_fii_net_fut,
                        fo_fii_call_long  = EXCLUDED.fo_fii_call_long,
                        fo_fii_call_short = EXCLUDED.fo_fii_call_short,
                        fo_fii_put_long   = EXCLUDED.fo_fii_put_long,
                        fo_fii_put_short  = EXCLUDED.fo_fii_put_short,
                        fo_fii_pc_ratio   = EXCLUDED.fo_fii_pc_ratio,
                        fetched_at        = NOW()
                """, (
                    d,
                    data["fut_long"], data["fut_short"], data["net_fut"],
                    data["call_long"], data["call_short"],
                    data["put_long"],  data["put_short"],
                    data["pc_ratio"],
                ))
        return True
    except Exception as exc:
        logger.error("_store %s: %s", d, exc)
        return False


# ── Main ─────────────────────────────────────────────────────────────────────────

def collect(target_date: date) -> Optional[dict]:
    """Download, parse, and store F&O participant data for target_date."""
    session = _nse_session()
    df = _download_fo_csv(target_date, session)
    if df is None:
        return None

    data = _parse_fii_row(df)
    if data is None:
        return None

    ok = _store(target_date, data)
    if ok:
        logger.info("Stored FII F&O data for %s", target_date)
    return data if ok else None


def _last_n_trading_days(n: int) -> list:
    """Return last n weekdays (Mon-Fri) from today."""
    days = []
    d = date.today()
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return days


def main():
    parser = argparse.ArgumentParser(
        description="Collect NSE F&O participant OI (FII net futures)"
    )
    parser.add_argument(
        "--date", type=str, default="",
        help="Specific date YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--backfill", type=int, default=0,
        help="Backfill last N trading days",
    )
    args = parser.parse_args()

    if not is_available():
        logger.error("Database not available. Check DATABASE_URL in .env")
        sys.exit(1)

    init_schema()

    # Determine which dates to collect
    if args.backfill > 0:
        dates = _last_n_trading_days(args.backfill)
        logger.info("Backfilling %d trading day(s): %s → %s",
                    len(dates), dates[-1], dates[0])
    elif args.date:
        from datetime import datetime
        dates = [datetime.strptime(args.date, "%Y-%m-%d").date()]
    else:
        dates = [date.today()]

    results = []
    for d in dates:
        logger.info("─── %s ───", d)
        data = collect(d)
        if data:
            results.append((d, data))

    if not results:
        logger.warning("No data collected.")
        if tg_ok():
            try:
                send_error("collect_fii_fo_data.py",
                           "No F&O participant data collected — CSV not available yet?")
            except Exception:
                pass
        return

    # Fetch last 5 days from DB for display
    db_rows = []
    try:
        with _get_conn() as conn:
            if conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT trade_date, fo_fii_net_fut,
                               fo_fii_call_long, fo_fii_put_long, fo_fii_pc_ratio
                        FROM fii_derivative_stats
                        WHERE fo_fii_net_fut IS NOT NULL
                        ORDER BY trade_date DESC LIMIT 7
                    """)
                    db_rows = cur.fetchall()

        print()
        print(f"{'Date':12s}  {'Net Fut (ctr)':>14s}  {'Call Long':>10s}  "
              f"{'Put Long':>10s}  {'P/C':>6s}")
        print("-" * 60)
        for r in db_rows:
            net = int(r[1]) if r[1] is not None else 0
            cl  = int(r[2]) if r[2] is not None else 0
            pl  = int(r[3]) if r[3] is not None else 0
            pc  = float(r[4]) if r[4] is not None else 0
            sign = "+" if net >= 0 else ""
            print(f"{str(r[0]):12s}  {sign}{net:>13,d}  {cl:>10,d}  {pl:>10,d}  {pc:>6.2f}")
        print()
    except Exception as exc:
        logger.warning("Display error: %s", exc)

    # Telegram alert
    if tg_ok():
        try:
            send_fii_fo_status(db_rows, len(results))
            logger.info("Telegram FII F&O alert sent.")
        except Exception as exc:
            logger.warning("Telegram FII F&O alert failed: %s", exc)
            try:
                send_error("collect_fii_fo_data.py", str(exc))
            except Exception:
                pass


if __name__ == "__main__":
    main()
