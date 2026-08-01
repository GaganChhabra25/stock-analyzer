"""
End-of-day database summary — sent via Telegram at market close.

Usage:
  python options/db_summary.py --exchange NFO   # 3:35 PM IST cron
  python options/db_summary.py --exchange MCX   # 11:35 PM IST cron
  python options/db_summary.py                  # shows all instruments
"""

import argparse
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import requests
from screener.db import _get_conn

# Expected trading minutes per day per instrument (for missed-minute calc)
_EXPECTED_MINUTES = {
    "NIFTY":       375,   # 9:15 AM – 3:30 PM IST
    "NATURALGAS":  870,   # 9:00 AM – 11:30 PM IST  (14.5 hrs × 60)
    "CRUDEOIL":    870,
}

# Which instruments belong to which exchange label
_EXCHANGE_INSTRUMENTS = {
    "NFO": ["NIFTY"],
    "MCX": ["NATURALGAS", "CRUDEOIL"],
}


def _market_close_label(exchange: str) -> str:
    return "3:30 PM IST" if exchange == "NFO" else "11:30 PM IST"


def send_summary(exchange: str = "") -> None:
    today = date.today()

    # Determine which instruments to query
    if exchange:
        instruments = _EXCHANGE_INSTRUMENTS.get(exchange.upper(), [])
        if not instruments:
            print(f"Unknown exchange '{exchange}'. Use NFO or MCX.")
            return
        title  = f"{exchange.upper()} Options DB Summary"
        close  = _market_close_label(exchange.upper())
    else:
        instruments = []   # empty = all
        title  = "Options DB Summary (All Exchanges)"
        close  = "varies by exchange"

    with _get_conn() as conn:
        with conn.cursor() as cur:

            # Per-instrument breakdown
            if instruments:
                cur.execute("""
                    SELECT
                        instrument,
                        COUNT(*)                                        AS rows,
                        MIN(ts) AT TIME ZONE 'Asia/Kolkata'             AS first_ts,
                        MAX(ts) AT TIME ZONE 'Asia/Kolkata'             AS last_ts,
                        COUNT(DISTINCT ts)                              AS minutes_captured
                    FROM option_chain
                    WHERE ts::date = %s
                      AND instrument = ANY(%s)
                    GROUP BY instrument
                    ORDER BY instrument
                """, (today, instruments))
            else:
                cur.execute("""
                    SELECT
                        instrument,
                        COUNT(*)                                        AS rows,
                        MIN(ts) AT TIME ZONE 'Asia/Kolkata'             AS first_ts,
                        MAX(ts) AT TIME ZONE 'Asia/Kolkata'             AS last_ts,
                        COUNT(DISTINCT ts)                              AS minutes_captured
                    FROM option_chain
                    WHERE ts::date = %s
                    GROUP BY instrument
                    ORDER BY instrument
                """, (today,))
            oc_rows = cur.fetchall()

            # Totals
            if instruments:
                cur.execute(
                    "SELECT COUNT(*) FROM option_chain WHERE ts::date = %s AND instrument = ANY(%s)",
                    (today, instruments)
                )
            else:
                cur.execute("SELECT COUNT(*) FROM option_chain WHERE ts::date = %s", (today,))
            total_oc = cur.fetchone()[0]

            if instruments:
                cur.execute(
                    "SELECT COUNT(*) FROM market_snapshot WHERE ts::date = %s AND instrument = ANY(%s)",
                    (today, instruments)
                )
            else:
                cur.execute("SELECT COUNT(*) FROM market_snapshot WHERE ts::date = %s", (today,))
            total_ms = cur.fetchone()[0]

            # All-time totals (always across all instruments)
            cur.execute("SELECT COUNT(*) FROM option_chain")
            all_time_oc = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM market_snapshot")
            all_time_ms = cur.fetchone()[0]

    # ── Build message ──────────────────────────────────────────────────────────
    lines = [
        f"{title} — {today.strftime('%d-%b-%Y')}",
        f"Market close: {close}",
        "",
        "TODAY'S COLLECTION",
    ]

    if oc_rows:
        for inst, rows, first, last, minutes in oc_rows:
            expected = _EXPECTED_MINUTES.get(inst, 375)
            missed   = max(0, expected - minutes)
            lines += [
                f"  {inst}",
                f"    Rows captured  : {rows:,}",
                f"    Minutes active : {minutes} / {expected} (missed ~{missed})",
                f"    First capture  : {first.strftime('%I:%M %p IST') if first else 'N/A'}",
                f"    Last capture   : {last.strftime('%I:%M %p IST') if last else 'N/A'}",
            ]
    else:
        lines += [
            "  No data collected today.",
            "  (Was Kite authorised? Visit /kite/login)",
        ]

    lines += [
        "",
        f"  Total option rows today  : {total_oc:,}",
        f"  Total market snapshots   : {total_ms:,}",
        "",
        "ALL-TIME TOTALS",
        f"  option_chain rows   : {all_time_oc:,}",
        f"  market_snapshot rows: {all_time_ms:,}",
        "",
        f"Storage used (est): {all_time_oc * 300 / 1024 / 1024:.1f} MB",
    ]

    msg = "\n".join(lines)

    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    tg_chat  = os.environ.get("TELEGRAM_CHAT_ID", "")
    if tg_token and tg_chat:
        requests.post(
            f"https://api.telegram.org/bot{tg_token}/sendMessage",
            json={"chat_id": tg_chat, "text": msg},
            timeout=10,
        )
        print("Summary sent to Telegram.")
    else:
        print(msg)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--exchange",
        choices=["NFO", "MCX"],
        default="",
        help="Filter summary to a specific exchange (default: all)",
    )
    args = parser.parse_args()
    send_summary(args.exchange)
