"""
Quick DB data health check — run on Contabo to see what's in each table.
Usage: venv/bin/python scripts/check_db_data.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from screener.db import _get_conn

SEP = "-" * 65

with _get_conn() as conn:
    cur = conn.cursor()

    # ── 1. option_chain ───────────────────────────────────────────
    print(SEP)
    print("option_chain")
    print(SEP)
    cur.execute("""
        SELECT instrument,
               COUNT(*)             AS rows,
               MAX(ts)              AS latest,
               MIN(ts)              AS earliest,
               COUNT(DISTINCT expiry) AS expiries
        FROM option_chain
        GROUP BY instrument
        ORDER BY instrument
    """)
    rows = cur.fetchall()
    if rows:
        for r in rows:
            print(f"  {r[0]:12s}  rows={r[1]:>7}  latest={r[2]}  earliest={r[3]}  expiries={r[4]}")
    else:
        print("  *** EMPTY ***")

    # OI change coverage for NIFTY last 3 days
    cur.execute("""
        SELECT option_type, COUNT(*) AS n, SUM(oi_change) AS net_oi
        FROM option_chain
        WHERE instrument = 'NIFTY'
          AND oi_change IS NOT NULL
          AND ts >= NOW() - INTERVAL '3 days'
        GROUP BY option_type
    """)
    oi_rows = cur.fetchall()
    print()
    print("  NIFTY OI change (last 3 days):")
    if oi_rows:
        for r in oi_rows:
            print(f"    option_type={r[0]}  rows={r[1]}  net_oi_change={r[2]:.0f}")
    else:
        print("    *** NO DATA — oi_change signal will be 0 ***")

    # IV coverage
    cur.execute("""
        SELECT COUNT(*) AS n, MAX(ts) AS latest
        FROM option_chain
        WHERE instrument = 'NIFTY' AND iv > 0
          AND ts >= NOW() - INTERVAL '4 days'
    """)
    r = cur.fetchone()
    print(f"  NIFTY IV rows (last 4 days): {r[0]}  latest={r[1]}")

    # ── 2. market_snapshot ────────────────────────────────────────
    print()
    print(SEP)
    print("market_snapshot")
    print(SEP)
    cur.execute("""
        SELECT instrument,
               COUNT(*)  AS rows,
               MAX(ts)   AS latest,
               MIN(ts)   AS earliest
        FROM market_snapshot
        GROUP BY instrument
        ORDER BY instrument
    """)
    rows = cur.fetchall()
    if rows:
        for r in rows:
            print(f"  {r[0]:12s}  rows={r[1]:>6}  latest={r[2]}  earliest={r[3]}")
    else:
        print("  *** EMPTY ***")

    # Latest VIX + PCR values
    cur.execute("""
        SELECT ts, pcr_oi, vix
        FROM market_snapshot
        WHERE instrument = 'NIFTY' AND (pcr_oi IS NOT NULL OR vix IS NOT NULL)
        ORDER BY ts DESC LIMIT 5
    """)
    snap = cur.fetchall()
    print()
    print("  Latest NIFTY snapshots (VIX + PCR):")
    if snap:
        for r in snap:
            print(f"    ts={r[0]}  pcr_oi={r[1]}  vix={r[2]}")
    else:
        print("    *** NO DATA ***")

    # ── 3. mcx_ohlc ──────────────────────────────────────────────
    print()
    print(SEP)
    print("mcx_ohlc")
    print(SEP)
    cur.execute("""
        SELECT instrument, interval,
               COUNT(*)   AS rows,
               MAX(ts)    AS latest,
               MIN(ts)    AS earliest,
               COUNT(CASE WHEN oi > 0 THEN 1 END) AS rows_with_oi
        FROM mcx_ohlc
        GROUP BY instrument, interval
        ORDER BY instrument, interval
    """)
    rows = cur.fetchall()
    if rows:
        for r in rows:
            print(f"  {r[0]:14s} [{r[1]:10s}]  rows={r[2]:>4}  latest={r[3]}  earliest={r[4]}  oi_rows={r[5]}")
    else:
        print("  *** EMPTY ***")

    # ── 4. market_predictions ─────────────────────────────────────
    print()
    print(SEP)
    print("market_predictions (current)")
    print(SEP)
    cur.execute("""
        SELECT instrument, prediction_type, direction,
               probability, expiry_date,
               tech_signal, vix_signal, pcr_value,
               max_pain, atm_iv, run_date
        FROM market_predictions
        ORDER BY instrument, expiry_date
    """)
    rows = cur.fetchall()
    if rows:
        for r in rows:
            print(f"  {r[0]:6s} {r[1]:8s}  dir={r[2]}  prob={r[3]}%  expiry={r[4]}")
            print(f"         tech={r[5]}  vix={r[6]}  pcr={r[7]}  max_pain={r[8]}  iv={r[9]}  run={r[10]}")
    else:
        print("  *** EMPTY ***")

    cur.close()

print()
print(SEP)
print("Done.")
