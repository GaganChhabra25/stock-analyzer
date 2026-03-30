"""
End-of-day database summary — sent via Telegram at 3:35 PM IST.
Shows how much options data was collected today.
"""

import os
import sys
import requests
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from screener.db import _get_conn


def send_summary():
    today = date.today()

    with _get_conn() as conn:
        with conn.cursor() as cur:

            # Option chain rows today
            cur.execute("""
                SELECT
                    instrument,
                    COUNT(*)            AS rows,
                    MIN(ts)::time(0)    AS first_capture,
                    MAX(ts)::time(0)    AS last_capture,
                    COUNT(DISTINCT ts)  AS minutes_captured
                FROM option_chain
                WHERE ts::date = %s
                GROUP BY instrument
                ORDER BY instrument
            """, (today,))
            oc_rows = cur.fetchall()

            # Total option chain rows today
            cur.execute("SELECT COUNT(*) FROM option_chain WHERE ts::date = %s", (today,))
            total_oc = cur.fetchone()[0]

            # Market snapshot rows today
            cur.execute("SELECT COUNT(*) FROM market_snapshot WHERE ts::date = %s", (today,))
            total_ms = cur.fetchone()[0]

            # All-time totals
            cur.execute("SELECT COUNT(*) FROM option_chain")
            all_time_oc = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM market_snapshot")
            all_time_ms = cur.fetchone()[0]

            # Missing minutes estimate (market is ~375 min/day)
            total_expected = 375 * 2  # NIFTY + BANKNIFTY
            missing = max(0, total_expected - total_oc) if total_oc > 0 else total_expected

    # Build message
    lines = [
        f"Options DB Summary - {today.strftime('%d-%b-%Y')}",
        f"Market close: 3:30 PM IST",
        "",
        "TODAY'S COLLECTION",
    ]

    if oc_rows:
        for row in oc_rows:
            inst, rows, first, last, minutes = row
            lines.append(f"  {inst}")
            lines.append(f"    Rows captured  : {rows:,}")
            lines.append(f"    Minutes active : {minutes}")
            lines.append(f"    First capture  : {first}")
            lines.append(f"    Last capture   : {last}")
    else:
        lines.append("  No data collected today.")
        lines.append("  (Was Kite authorized? Visit /kite/login)")

    lines += [
        "",
        f"  Total option rows today  : {total_oc:,}",
        f"  Total market snapshots   : {total_ms:,}",
        f"  Missed minutes (approx)  : {missing}",
        "",
        "ALL-TIME TOTALS",
        f"  option_chain rows  : {all_time_oc:,}",
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
            timeout=10
        )
        print("Summary sent to Telegram.")
    else:
        print(msg)


if __name__ == "__main__":
    send_summary()
