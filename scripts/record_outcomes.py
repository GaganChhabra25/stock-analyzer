"""
Prediction Outcomes Recorder
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Records actual market results for predictions whose expiry date has passed.
Fills the market_outcomes table — required for v_model_accuracy to work and
for the system to self-learn which signals are actually predictive.

Schedule: Run at 6:30 PM IST (after market close, prices settled).
Usage:    venv/bin/python scripts/record_outcomes.py [--lookback N]

How it works:
  1. Find market_predictions rows where expiry_date <= today AND no outcome yet
  2. For NIFTY:   fetch actual close from yfinance (^NSEI)
  3. For CRUDE:   fetch from mcx_ohlc DB table
  4. For NATGAS:  fetch from mcx_ohlc DB table
  5. Find the week-open (Monday of expiry week, or month-open for monthly)
  6. Compare close vs open → actual_direction
  7. Insert into market_outcomes (skip if already exists)

After ~4 weeks of data, run in psql:
  SELECT * FROM v_model_accuracy;      -- see which instruments/weeks we predict best
  SELECT * FROM v_signal_importance;   -- see which signals actually drive outcomes
  SELECT * FROM v_prob_calibration;    -- check if probability scores are calibrated
"""

import sys
import os
import argparse
import logging
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

from screener.db import _get_conn, is_available, init_schema

# ── Price fetchers ─────────────────────────────────────────────────────────────

def _nifty_close_on(d: date) -> Optional[float]:
    """Fetch Nifty 50 closing price on a specific date via yfinance."""
    try:
        import yfinance as yf
        next_day = d + timedelta(days=3)
        df = yf.download("^NSEI", start=d.isoformat(), end=next_day.isoformat(),
                         interval="1d", progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        if hasattr(df.columns, "get_level_values"):
            df.columns = df.columns.get_level_values(0)
        target = df[df.index.date == d]
        if target.empty:
            return None
        return float(target["Close"].iloc[0])
    except Exception as exc:
        logger.warning("_nifty_close_on %s: %s", d, exc)
        return None


def _mcx_close_on(instrument: str, d: date) -> Optional[float]:
    """Fetch MCX instrument close on a specific date from mcx_ohlc DB table."""
    try:
        with _get_conn() as conn:
            if conn is None:
                return None
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT close FROM mcx_ohlc
                    WHERE instrument = %s AND interval = 'day'
                      AND ts::date = %s
                    ORDER BY ts DESC LIMIT 1
                """, (instrument, d))
                r = cur.fetchone()
        return float(r[0]) if r and r[0] else None
    except Exception as exc:
        logger.warning("_mcx_close_on %s %s: %s", instrument, d, exc)
        return None


def _nifty_open_for_week(expiry_date: date, prediction_type: str) -> Optional[float]:
    """
    Return the week-open price for a Nifty prediction:
      WEEKLY  → first available close on/after Monday of expiry week
      MONTHLY → first trading day close of expiry month
    """
    try:
        import yfinance as yf
        if prediction_type == "WEEKLY":
            mon = expiry_date - timedelta(days=expiry_date.weekday())
            start = mon
        else:
            start = date(expiry_date.year, expiry_date.month, 1)

        end = start + timedelta(days=5)
        df = yf.download("^NSEI", start=start.isoformat(), end=end.isoformat(),
                         interval="1d", progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        if hasattr(df.columns, "get_level_values"):
            df.columns = df.columns.get_level_values(0)
        return float(df["Open"].iloc[0])
    except Exception as exc:
        logger.warning("_nifty_open_for_week %s: %s", expiry_date, exc)
        return None


def _mcx_open_for_period(instrument: str, expiry_date: date,
                          prediction_type: str) -> Optional[float]:
    """MCX period-open from mcx_ohlc table."""
    try:
        if prediction_type == "MONTHLY":
            period_start = date(expiry_date.year, expiry_date.month, 1)
        else:
            period_start = expiry_date - timedelta(days=expiry_date.weekday())

        with _get_conn() as conn:
            if conn is None:
                return None
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT open FROM mcx_ohlc
                    WHERE instrument = %s AND interval = 'day'
                      AND ts::date >= %s AND ts::date <= %s
                    ORDER BY ts ASC LIMIT 1
                """, (instrument, period_start, expiry_date))
                r = cur.fetchone()
        return float(r[0]) if r and r[0] else None
    except Exception as exc:
        logger.warning("_mcx_open_for_period %s %s: %s", instrument, expiry_date, exc)
        return None


# ── Main recorder ──────────────────────────────────────────────────────────────

_INST_CODE_TO_TICKER = {
    "NIFTY":  "nifty",
    "CRUDE":  "CRUDEOIL",
    "NATGAS": "NATURALGAS",
}


def record_outcomes(lookback_days: int = 7) -> int:
    """
    Find all predictions with expiry in the last `lookback_days` days
    that have no outcome yet, and record their actual results.
    Returns number of outcomes recorded.
    """
    today = date.today()
    cutoff = today - timedelta(days=lookback_days)

    with _get_conn() as conn:
        if conn is None:
            return 0
        with conn.cursor() as cur:
            # Find pending predictions (expiry passed, no outcome yet)
            cur.execute("""
                SELECT DISTINCT
                    p.instrument,
                    p.expiry_date,
                    p.week_num,
                    p.prediction_type
                FROM market_predictions p
                WHERE p.expiry_date <= %s
                  AND p.expiry_date >= %s
                  AND NOT EXISTS (
                      SELECT 1 FROM market_outcomes o
                      WHERE o.instrument              = p.instrument
                        AND o.expiry_date             = p.expiry_date
                        AND COALESCE(o.week_num, -1)  = COALESCE(p.week_num, -1)
                        AND o.prediction_type         = p.prediction_type
                  )
                ORDER BY p.expiry_date
            """, (today, cutoff))
            pending = cur.fetchall()

    if not pending:
        logger.info("No pending outcomes to record (lookback=%d days)", lookback_days)
        return 0

    logger.info("Found %d pending outcome(s) to record", len(pending))
    recorded = 0

    for instrument, expiry_date, week_num, prediction_type in pending:
        logger.info("Recording %s %s %s expiry=%s ...",
                    instrument, prediction_type,
                    f"W{week_num}" if week_num else "", expiry_date)

        # Fetch actual close on expiry date
        if instrument == "NIFTY":
            actual_close = _nifty_close_on(expiry_date)
            period_open  = _nifty_open_for_week(expiry_date, prediction_type)
        else:
            mcx_sym      = _INST_CODE_TO_TICKER.get(instrument, instrument)
            actual_close = _mcx_close_on(mcx_sym, expiry_date)
            period_open  = _mcx_open_for_period(mcx_sym, expiry_date, prediction_type)

        if actual_close is None:
            logger.warning("  No close price found for %s on %s — skip", instrument, expiry_date)
            continue

        actual_pct = None
        actual_dir = None
        if period_open and period_open > 0:
            actual_pct = round((actual_close - period_open) / period_open * 100, 2)
            actual_dir = "UP" if actual_close >= period_open else "DOWN"

        try:
            with _get_conn() as conn:
                if conn is None:
                    continue
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO market_outcomes (
                            instrument, expiry_date, week_num, prediction_type,
                            actual_direction, actual_close, actual_pct, week_open
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (instrument, expiry_date,
                                     COALESCE(week_num, -1), prediction_type)
                        DO NOTHING
                    """, (
                        instrument, expiry_date, week_num, prediction_type,
                        actual_dir, actual_close, actual_pct,
                        period_open,
                    ))

            logger.info(
                "  ✓ %s close=%.2f  open=%.2f  dir=%s  pct=%s%%",
                expiry_date, actual_close,
                period_open or 0,
                actual_dir or "?",
                actual_pct or "?",
            )
            recorded += 1

        except Exception as exc:
            logger.error("  Insert failed for %s %s: %s", instrument, expiry_date, exc)

    return recorded


def print_accuracy():
    """Print current accuracy summary from v_model_accuracy."""
    try:
        with _get_conn() as conn:
            if conn is None:
                return
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT instrument, prediction_type, week_num,
                           total_predictions, correct, accuracy_pct,
                           avg_predicted_prob
                    FROM v_model_accuracy
                    ORDER BY instrument, prediction_type, week_num
                """)
                rows = cur.fetchall()

        if not rows:
            logger.info("No accuracy data yet — need more expiries.")
            return

        print()
        print("-" * 65)
        print(f"{'Inst':8s} {'Type':8s} {'Wk':4s} {'N':>4s}  {'Correct':>7s}  {'Acc%':>6s}  {'AvgProb':>8s}")
        print("-" * 65)
        for instrument, pred_type, week_num, total, correct, acc, avg_prob in rows:
            wk_label = str(week_num) if week_num else "M"
            print(f"{instrument:8s} {pred_type:8s} {wk_label:4s} {total:>4d}  "
                  f"{correct or 0:>7d}  {acc or 0:>5.1f}%  {avg_prob or 0:>7.1f}%")
        print("-" * 65)
        print()
    except Exception as exc:
        logger.warning("Could not fetch accuracy: %s", exc)


def main():
    parser = argparse.ArgumentParser(description="Record prediction outcomes")
    parser.add_argument(
        "--lookback", type=int, default=7,
        help="Check predictions expiring in last N days (default: 7)",
    )
    parser.add_argument(
        "--accuracy", action="store_true",
        help="Print accuracy summary after recording",
    )
    args = parser.parse_args()

    if not is_available():
        logger.error("Database not available. Check DATABASE_URL in .env")
        sys.exit(1)

    init_schema()

    n = record_outcomes(args.lookback)
    logger.info("Recorded %d new outcome(s).", n)

    if args.accuracy or True:   # always show accuracy
        print_accuracy()


if __name__ == "__main__":
    main()
