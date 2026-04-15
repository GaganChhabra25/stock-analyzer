"""
Signal diagnostic — check exactly why oi_change / max_pain / iv_pct return 0.
Usage: venv/bin/python scripts/diag_signals.py
"""
import sys
from pathlib import Path
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from screener.db import _get_conn

SEP = "-" * 65

with _get_conn() as conn:
    cur = conn.cursor()

    # ── What expiries exist in option_chain? ──────────────────────
    print(SEP)
    print("NIFTY expiries in option_chain (last 3 days)")
    print(SEP)
    cur.execute("""
        SELECT expiry, option_type,
               COUNT(*) AS rows,
               SUM(CASE WHEN oi_change IS NOT NULL THEN 1 ELSE 0 END) AS oi_change_rows,
               SUM(ABS(COALESCE(oi_change, 0))) AS abs_oi_change
        FROM option_chain
        WHERE instrument = 'NIFTY'
          AND ts >= NOW() - INTERVAL '3 days'
        GROUP BY expiry, option_type
        ORDER BY expiry, option_type
    """)
    for r in cur.fetchall():
        print(f"  expiry={r[0]}  type={r[1]}  rows={r[2]}  oi_change_rows={r[3]}  abs_oi_change={r[4]:.0f}")

    # ── Compute next Thursday (what our code uses as expiry) ──────
    today = date.today()
    days_ahead = (3 - today.weekday()) % 7
    nifty_expiry = today + timedelta(days=days_ahead if days_ahead else 7)
    print(f"\n  Code uses expiry = {nifty_expiry} (next Thursday from {today})")

    # ── Exact OI change query used in code ────────────────────────
    print()
    print(SEP)
    print(f"OI change signal query for expiry={nifty_expiry}")
    print(SEP)
    cur.execute("""
        SELECT option_type, SUM(oi_change) AS net
        FROM option_chain
        WHERE instrument = 'NIFTY'
          AND expiry = %s
          AND oi_change IS NOT NULL
          AND ts >= NOW() - INTERVAL '3 days'
        GROUP BY option_type
    """, (nifty_expiry,))
    rows = cur.fetchall()
    if rows:
        ce = pe = 0.0
        for r in rows:
            print(f"  option_type={r[0]}  net_oi_change={r[1]:.0f}")
            if r[0] == "CE": ce = float(r[1])
            if r[0] == "PE": pe = float(r[1])
        total = abs(ce) + abs(pe)
        raw = (pe - ce) / (total + 1e-9)
        sig = round(max(-1.0, min(1.0, raw * 1.5)), 3)
        print(f"\n  → PE={pe:.0f}  CE={ce:.0f}  total={total:.0f}")
        print(f"  → raw={raw:.4f}  signal={sig}")
    else:
        print(f"  *** NO ROWS — expiry {nifty_expiry} not found in option_chain!")
        # Try all expiries to see what IS there
        cur.execute("""
            SELECT DISTINCT expiry FROM option_chain
            WHERE instrument = 'NIFTY'
              AND ts >= NOW() - INTERVAL '3 days'
            ORDER BY expiry
        """)
        print("  Available expiries:", [r[0] for r in cur.fetchall()])

    # ── Max Pain query ─────────────────────────────────────────────
    print()
    print(SEP)
    print(f"Max Pain query for expiry={nifty_expiry}")
    print(SEP)
    cur.execute("""
        SELECT COUNT(*) AS n, MIN(strike) AS min_k, MAX(strike) AS max_k,
               SUM(oi) AS total_oi
        FROM option_chain
        WHERE instrument = 'NIFTY' AND expiry = %s AND oi > 0
    """, (nifty_expiry,))
    r = cur.fetchone()
    print(f"  rows={r[0]}  strikes={r[1]}–{r[2]}  total_oi={r[3]:.0f}")

    # ── IV percentile query ────────────────────────────────────────
    import yfinance as yf
    import warnings
    warnings.filterwarnings("ignore")
    nifty_raw = yf.download("^NSEI", period="2d", interval="1d", progress=False)
    if nifty_raw is not None and not nifty_raw.empty:
        if hasattr(nifty_raw.columns, 'get_level_values'):
            nifty_raw.columns = nifty_raw.columns.get_level_values(0)
        nifty_cmp = float(nifty_raw["Close"].dropna().iloc[-1])
    else:
        nifty_cmp = 24231.0
    atm = int(round(nifty_cmp / 50) * 50)

    print()
    print(SEP)
    print(f"IV percentile  expiry={nifty_expiry}  ATM={atm}  (Nifty CMP={nifty_cmp:.0f})")
    print(SEP)
    cur.execute("""
        SELECT COUNT(*) AS n, AVG(iv) AS avg_iv, MAX(ts) AS latest
        FROM option_chain
        WHERE instrument = 'NIFTY' AND expiry = %s
          AND strike = %s AND iv > 0
          AND ts >= NOW() - INTERVAL '4 days'
    """, (nifty_expiry, atm))
    r = cur.fetchone()
    print(f"  rows={r[0]}  avg_iv={r[1]}  latest={r[2]}")

    # 52-week IV range
    cur.execute("""
        SELECT MIN(iv), MAX(iv)
        FROM option_chain
        WHERE instrument = 'NIFTY' AND strike = %s AND iv > 0
          AND ts >= NOW() - INTERVAL '365 days'
    """, (atm,))
    r = cur.fetchone()
    print(f"  52-week iv_min={r[0]}  iv_max={r[1]}")

    # ── market_snapshot — VIX and PCR ─────────────────────────────
    print()
    print(SEP)
    print("Latest VIX + PCR from market_snapshot")
    print(SEP)
    cur.execute("""
        SELECT ts, pcr_oi, vix FROM market_snapshot
        WHERE instrument = 'NIFTY' AND (pcr_oi IS NOT NULL OR vix IS NOT NULL)
        ORDER BY ts DESC LIMIT 3
    """)
    for r in cur.fetchall():
        print(f"  ts={r[0]}  PCR={r[1]}  VIX={r[2]}")

    # ── mcx_ohlc OI ───────────────────────────────────────────────
    print()
    print(SEP)
    print("MCX latest 5 rows with OI")
    print(SEP)
    for sym in ("CRUDEOIL", "NATURALGAS"):
        cur.execute("""
            SELECT ts, close, volume, oi FROM mcx_ohlc
            WHERE instrument = %s AND interval = 'day'
            ORDER BY ts DESC LIMIT 5
        """, (sym,))
        rows = cur.fetchall()
        print(f"  {sym}:")
        for r in rows:
            print(f"    ts={r[0]}  close={r[1]}  vol={r[2]}  oi={r[3]}")

    cur.close()

print()
print(SEP)
print("Done.")
