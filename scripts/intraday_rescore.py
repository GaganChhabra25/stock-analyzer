"""
Intraday Re-score (2:00 PM IST)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Re-evaluates today's active NIFTY predictions using fresh market data.
Dynamic signals (PCR, OI velocity, GEX, IV skew, VIX, max pain, IV pct, FII)
are re-fetched from the DB. Static signals (tech, hist_wr) are kept from the
morning run since they change slowly.

Updates market_predictions.rescore_dir / rescore_prob / rescored_at if:
  - Direction flips   (UP ↔ DOWN), OR
  - Probability shifts ≥ 5 percentage points

Sends a Telegram alert summarising all changes (especially direction flips).

Schedule: Run at 14:00 IST = 08:30 UTC
  Contabo cron (UTC+2 CEST): 30 10 * * 1-5
  Contabo cron (UTC+1 CET) : 30 13 * * 1-5
Usage:    venv/bin/python scripts/intraday_rescore.py [--dry-run]
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
from screener.telegram_notifier import send_rescore_summary, send_error, is_configured as tg_ok

# ── Signal weights (must mirror _NIFTY_W in market_overview.py) ───────────────

_NIFTY_W = {
    "pcr_trend":   0.15,
    "oi_velocity": 0.13,
    "max_pain":    0.15,
    "tech":        0.13,
    "gex":         0.12,
    "vix":         0.09,
    "fii":         0.08,
    "iv_skew":     0.07,
    "iv_pct":      0.04,
    "hist_wr":     0.04,
}

_PROB_SWING = 28.0  # must mirror _PROB_SWING in market_overview.py


# ── Re-score helpers ───────────────────────────────────────────────────────────

def _hist_wr_to_signal(win_rate: float) -> float:
    """Win rate 0..100 → signal -1..+1. Mirrors market_overview._hist_wr_to_signal."""
    return max(-1.0, min(1.0, (win_rate - 50.0) / 50.0))


def _multi_signal_prob(signals: dict, weights: dict) -> tuple:
    """
    Mirrors market_overview._multi_signal_prob (no time_decay needed for same-expiry
    re-score — morning already applied decay; we keep signals as-is).
    Returns (direction, probability, confidence, agreement_pct).
    """
    present = {k: v for k, v in signals.items() if k in weights}
    total_w = sum(weights[k] for k in present)
    if total_w < 0.01:
        return "UP", 50.0, "LOW", 50.0

    score = sum(present[k] * weights[k] for k in present) / total_w
    score = max(-1.0, min(1.0, score))

    prob_up = 50.0 + score * _PROB_SWING
    prob_up = max(22.0, min(78.0, prob_up))

    direction   = "UP" if prob_up >= 50.0 else "DOWN"
    probability = round(prob_up, 1) if direction == "UP" else round(100.0 - prob_up, 1)

    if direction == "UP":
        aligned_w = sum(weights[k] for k in present if present[k] > 0.05)
    else:
        aligned_w = sum(weights[k] for k in present if present[k] < -0.05)
    agreement_pct = round(aligned_w / total_w * 100, 1) if total_w > 0 else 50.0

    if   agreement_pct >= 75: confidence = "HIGH"
    elif agreement_pct >= 55: confidence = "MEDIUM"
    else:                     confidence = "LOW"

    return direction, probability, confidence, agreement_pct


def _fetch_active_predictions() -> list:
    """
    Return active NIFTY predictions (expiry_date >= today, no past expiry).
    Columns: (id, expiry_date, week_num, prediction_type,
               direction, probability, confidence, agreement_pct,
               tech_signal, hist_wr)
    """
    today = date.today()
    with _get_conn() as conn:
        if conn is None:
            return []
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, expiry_date, week_num, prediction_type,
                       direction, probability, confidence, agreement_pct,
                       tech_signal, hist_wr
                FROM market_predictions
                WHERE instrument    = 'NIFTY'
                  AND expiry_date  >= %s
                  AND prediction_type IN ('WEEKLY', 'MONTHLY')
                ORDER BY expiry_date, week_num
            """, (today,))
            return cur.fetchall()


def _nifty_cmp() -> float:
    """Fetch current Nifty price from yfinance."""
    try:
        import yfinance as yf
        df = yf.download("^NSEI", period="2d", interval="1d",
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            return 0.0
        if hasattr(df.columns, "get_level_values"):
            df.columns = df.columns.get_level_values(0)
        return float(df["Close"].dropna().iloc[-1])
    except Exception as exc:
        logger.warning("_nifty_cmp: %s", exc)
        return 0.0


def _next_expiry_from_db() -> Optional[date]:
    """Get the next (nearest) Nifty expiry from option_chain table."""
    today = date.today()
    try:
        with _get_conn() as conn:
            if conn is None:
                return None
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT MIN(expiry) FROM option_chain
                    WHERE instrument = 'NIFTY' AND expiry >= %s
                """, (today,))
                r = cur.fetchone()
        if r and r[0]:
            return r[0]
    except Exception as exc:
        logger.warning("_next_expiry_from_db: %s", exc)
    # Fallback: next Tuesday
    days = (1 - today.weekday()) % 7
    return today + timedelta(days=days if days else 7)


def _fetch_fresh_dynamic_signals(expiry: date, atm: int, cmp: float) -> dict:
    """
    Re-fetch all DB-driven dynamic signals for the given expiry.
    These can change intraday as new option_chain data arrives.
    """
    from screener.market_overview import (
        _db_pcr_trend_signal,
        _db_oi_velocity_signal,
        _db_gex_signal,
        _db_iv_skew_signal,
        _db_vix_signal,
        _db_max_pain_signal,
        _db_iv_percentile_signal,
        _db_fii_derivative_signal,
    )

    sigs = {}
    sigs["pcr_trend"]   = _db_pcr_trend_signal()
    sigs["oi_velocity"] = _db_oi_velocity_signal(expiry, cmp)
    sigs["gex"]         = _db_gex_signal(expiry, atm, cmp)
    sigs["iv_skew"]     = _db_iv_skew_signal(expiry, atm, cmp)
    sigs["vix"]         = _db_vix_signal()
    sigs["fii"]         = _db_fii_derivative_signal()

    mp = _db_max_pain_signal(expiry, cmp)
    if mp is not None:
        sigs["max_pain"] = mp

    iv_pct = _db_iv_percentile_signal(expiry, atm)
    if iv_pct != 0.0:
        sigs["iv_pct"] = iv_pct

    return sigs


def rescore_predictions(dry_run: bool = False) -> list:
    """
    Re-score all active NIFTY predictions with fresh 2 PM signals.
    Returns list of update dicts (for Telegram summary).
    """
    preds = _fetch_active_predictions()
    if not preds:
        logger.info("No active NIFTY predictions to re-score.")
        return []

    logger.info("Re-scoring %d active prediction(s)...", len(preds))

    cmp = _nifty_cmp()
    if cmp <= 0:
        logger.warning("Could not fetch Nifty CMP — aborting re-score.")
        return []

    atm = int(round(cmp / 50) * 50)
    logger.info("Nifty CMP: %.2f  ATM: %d", cmp, atm)

    # Use the nearest expiry for dynamic signal computation
    # (dynamic signals like GEX/OI-velocity are most meaningful for the nearest expiry)
    near_expiry = _next_expiry_from_db()
    logger.info("Nearest expiry: %s", near_expiry)

    # Fetch fresh dynamic signals once (they're the same regardless of which
    # prediction row we're updating — they're index-level signals)
    logger.info("Fetching fresh dynamic signals...")
    dynamic = _fetch_fresh_dynamic_signals(near_expiry, atm, cmp)
    logger.info("Dynamic signals: %s", {k: f"{v:+.3f}" for k, v in dynamic.items()})

    updates = []

    for row in preds:
        (pred_id, expiry_date, week_num, pred_type,
         orig_dir, orig_prob, orig_conf, orig_agree,
         tech_sig, hist_wr_raw) = row

        # Static signals (from yfinance, change slowly, keep from morning)
        static = {}
        if tech_sig is not None:
            static["tech"] = float(tech_sig)
        if hist_wr_raw is not None:
            static["hist_wr"] = _hist_wr_to_signal(float(hist_wr_raw))

        # Combine: fresh dynamic + static from DB
        all_sigs = {**dynamic, **static}

        new_dir, new_prob, new_conf, new_agree = _multi_signal_prob(all_sigs, _NIFTY_W)

        label = f"NIFTY {pred_type} W{week_num} {expiry_date}"
        orig_prob_f = float(orig_prob) if orig_prob else 0.0

        direction_flip = (new_dir != orig_dir)
        prob_shift     = abs(new_prob - orig_prob_f) >= 5.0

        if direction_flip:
            logger.info(
                "  %s  DIRECTION FLIP: %s→%s  prob=%.1f%%→%.1f%%  conf=%s→%s",
                label, orig_dir, new_dir, orig_prob_f, new_prob, orig_conf, new_conf,
            )
        elif prob_shift:
            logger.info(
                "  %s  PROB SHIFT: %s  %.1f%%→%.1f%%  conf=%s→%s",
                label, new_dir, orig_prob_f, new_prob, orig_conf, new_conf,
            )
        else:
            logger.info(
                "  %s  No significant change: %s %.1f%% (%s)",
                label, new_dir, new_prob, new_conf,
            )

        if direction_flip or prob_shift:
            update_rec = {
                "pred_id":       pred_id,
                "label":         label,
                "expiry_date":   expiry_date,
                "week_num":      week_num,
                "pred_type":     pred_type,
                "orig_dir":      orig_dir,
                "orig_prob":     orig_prob_f,
                "orig_conf":     orig_conf,
                "new_dir":       new_dir,
                "new_prob":      new_prob,
                "new_conf":      new_conf,
                "new_agree":     new_agree,
                "direction_flip": direction_flip,
            }
            updates.append(update_rec)

            if not dry_run:
                _write_rescore(pred_id, new_dir, new_prob, new_conf, new_agree)

    logger.info("Re-scored %d prediction(s), %d change(s) written.", len(preds), len(updates))
    return updates


def _write_rescore(pred_id: int, direction: str, probability: float,
                   confidence: str, agreement_pct: float) -> None:
    """Write rescore fields to DB."""
    try:
        with _get_conn() as conn:
            if conn is None:
                return
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE market_predictions
                    SET    rescore_dir   = %s,
                           rescore_prob  = %s,
                           rescored_at   = NOW()
                    WHERE  id = %s
                """, (direction, probability, pred_id))
        logger.debug("  Written rescore for pred_id=%d", pred_id)
    except Exception as exc:
        logger.error("  _write_rescore id=%d: %s", pred_id, exc)


def main():
    parser = argparse.ArgumentParser(description="Intraday re-score at 2 PM IST")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Compute re-scores but do not write to DB or send Telegram",
    )
    args = parser.parse_args()

    if not is_available():
        logger.error("Database not available. Check DATABASE_URL in .env")
        sys.exit(1)

    init_schema()

    updates = rescore_predictions(dry_run=args.dry_run)

    if args.dry_run:
        logger.info("[DRY RUN] Would write %d update(s).", len(updates))
        return

    # ── Telegram alert ────────────────────────────────────────────────────────
    if tg_ok():
        try:
            send_rescore_summary(updates)
            logger.info("Telegram re-score alert sent.")
        except Exception as exc:
            logger.warning("Telegram re-score alert failed: %s", exc)
            try:
                send_error("intraday_rescore.py", str(exc))
            except Exception:
                pass
    else:
        logger.info("Telegram not configured — skipping alert.")


if __name__ == "__main__":
    main()
