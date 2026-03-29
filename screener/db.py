"""
PostgreSQL persistence layer — market predictions and outcomes.

Gracefully no-ops when DATABASE_URL is not set, so the screener
continues to work without a database (e.g. on Oracle Cloud server).

Tables written:
  market_predictions  — one row per prediction per screener run
  market_outcomes     — actual result after each expiry (written once)

Views (read-only, created by schema.sql):
  v_model_accuracy    — directional accuracy per instrument / week
  v_signal_importance — which signals correlate most with outcomes
  v_prob_calibration  — does stated probability match actual win rate
"""

import logging
import os
from contextlib import contextmanager
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger(__name__)

# ── Dependency check ───────────────────────────────────────────────────────────

try:
    import psycopg2
    import psycopg2.extras
    _PSYCOPG2_OK = True
except ImportError:
    _PSYCOPG2_OK = False

_DB_URL: str = os.environ.get("DATABASE_URL", "")


def is_available() -> bool:
    """True only when psycopg2 is installed AND DATABASE_URL is set."""
    return _PSYCOPG2_OK and bool(_DB_URL)


# ── Connection helper ──────────────────────────────────────────────────────────

@contextmanager
def _get_conn():
    """Yield an open psycopg2 connection, or None if unavailable."""
    if not is_available():
        yield None
        return
    conn = None
    try:
        conn = psycopg2.connect(_DB_URL)
        conn.autocommit = False
        yield conn
        conn.commit()
    except Exception as exc:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.warning("DB error: %s", exc)
        yield None
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


# ── Schema init ────────────────────────────────────────────────────────────────

def init_schema() -> bool:
    """
    Ensure tables exist.  Safe to call on every run — uses IF NOT EXISTS.
    Returns True if successful, False if DB unavailable or error.
    Preferred: run schema.sql once in pgAdmin instead.
    """
    with _get_conn() as conn:
        if conn is None:
            return False
        try:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS market_predictions (
                    id              BIGSERIAL      PRIMARY KEY,
                    run_at          TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
                    run_date        DATE           NOT NULL DEFAULT CURRENT_DATE,
                    instrument      VARCHAR(10)    NOT NULL,
                    expiry_date     DATE           NOT NULL,
                    week_num        SMALLINT,
                    prediction_type VARCHAR(10)    NOT NULL,
                    direction       VARCHAR(4)     NOT NULL,
                    probability     NUMERIC(5,2)   NOT NULL,
                    cmp             NUMERIC(12,2)  NOT NULL,
                    target_price    NUMERIC(12,2),
                    range_hi        NUMERIC(12,2),
                    range_lo        NUMERIC(12,2),
                    hist_wr         NUMERIC(5,1),
                    tech_signal     NUMERIC(6,3),
                    vix_signal      NUMERIC(6,3),
                    pcr_value       NUMERIC(6,3),
                    max_pain        NUMERIC(12,2),
                    atm_iv          NUMERIC(6,2),
                    global_signal   NUMERIC(6,3),
                    fii_signal      NUMERIC(6,3),
                    seasonal_signal NUMERIC(6,3),
                    put_oi_wall     NUMERIC(12,2),
                    call_oi_wall    NUMERIC(12,2)
                );
                CREATE INDEX IF NOT EXISTS idx_pred_lookup
                    ON market_predictions (instrument, expiry_date, run_date DESC);

                CREATE TABLE IF NOT EXISTS market_outcomes (
                    id               BIGSERIAL     PRIMARY KEY,
                    instrument       VARCHAR(10)   NOT NULL,
                    expiry_date      DATE          NOT NULL,
                    week_num         SMALLINT,
                    prediction_type  VARCHAR(10)   NOT NULL,
                    actual_direction VARCHAR(4),
                    actual_close     NUMERIC(12,2),
                    actual_pct       NUMERIC(6,2),
                    week_open        NUMERIC(12,2),
                    recorded_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW()
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_outcomes_unique
                    ON market_outcomes (
                        instrument, expiry_date,
                        COALESCE(week_num, -1), prediction_type
                    );
            """)
            cur.close()
            return True
        except Exception as exc:
            logger.warning("init_schema error: %s", exc)
            return False


# ── Private helpers ────────────────────────────────────────────────────────────

def _f(v) -> Optional[float]:
    """Safe float — returns None for missing/non-numeric values."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_date(s: str) -> Optional[date]:
    """Parse '27 Mar 2026' → date object."""
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%d %b %Y").date()
    except Exception:
        return None


def _outcome_exists(cur, instrument: str, expiry_date: date,
                    week_num: Optional[int], prediction_type: str) -> bool:
    cur.execute("""
        SELECT 1 FROM market_outcomes
        WHERE instrument     = %s
          AND expiry_date    = %s
          AND COALESCE(week_num, -1)   = COALESCE(%s, -1)
          AND prediction_type = %s
        LIMIT 1
    """, (instrument, expiry_date, week_num, prediction_type))
    return cur.fetchone() is not None


# ── Public API ─────────────────────────────────────────────────────────────────

def save_market_overview(overview: dict) -> int:
    """
    Persist all predictions and outcomes from a fetch_market_overview() result.

    - Future/current weeks → inserted as market_predictions
    - Past weeks with actual data → inserted as market_outcomes (skip if exists)

    Returns the number of prediction rows inserted.
    """
    if not is_available() or not overview:
        return 0

    INST_MAP = {
        "nifty":     "NIFTY",
        "crude_oil": "CRUDE",
        "nat_gas":   "NATGAS",
    }
    saved = 0

    with _get_conn() as conn:
        if conn is None:
            return 0
        cur = conn.cursor()

        for key, code in INST_MAP.items():
            inst = overview.get(key)
            if not inst or inst.get("error"):
                continue

            cmp  = _f(inst.get("cmp")) or 0.0
            sigs = inst.get("signals", {})

            # Pull all signal values (None-safe)
            tech  = _f(sigs.get("tech"))
            vix   = _f(sigs.get("vix"))
            glo   = _f(sigs.get("global"))
            fii   = _f(sigs.get("fii"))
            seas  = _f(sigs.get("seasonal"))
            pcr_v = _f(sigs.get("pcr"))            # raw PCR ratio e.g. 1.24
            mp    = _f(sigs.get("max_pain"))        # price level
            iv    = _f(sigs.get("atm_iv"))
            sup   = _f(sigs.get("support"))         # PE OI wall
            res   = _f(sigs.get("resistance"))      # CE OI wall

            # ── Weekly schedule ────────────────────────────────────────────
            for w in inst.get("weekly_schedule", []):
                exp_d = _parse_date(w.get("expiry_date"))
                if exp_d is None:
                    continue
                wk = w.get("week_num")

                if w.get("is_past"):
                    # Record actual outcome (once only)
                    if (w.get("actual_dir")
                            and not _outcome_exists(cur, code, exp_d, wk, "WEEKLY")):
                        cur.execute("""
                            INSERT INTO market_outcomes
                                (instrument, expiry_date, week_num, prediction_type,
                                 actual_direction, actual_close, actual_pct)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """, (
                            code, exp_d, wk, "WEEKLY",
                            w["actual_dir"],
                            _f(w.get("actual_close")),
                            _f(w.get("actual_pct")),
                        ))
                else:
                    # Insert prediction for this run
                    cur.execute("""
                        INSERT INTO market_predictions
                            (instrument, expiry_date, week_num, prediction_type,
                             direction, probability, cmp,
                             target_price, range_hi, range_lo,
                             hist_wr, tech_signal, vix_signal, pcr_value,
                             max_pain, atm_iv, global_signal, fii_signal,
                             seasonal_signal, put_oi_wall, call_oi_wall)
                        VALUES (%s,%s,%s,%s, %s,%s,%s, %s,%s,%s,
                                %s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s)
                    """, (
                        code, exp_d, wk, "WEEKLY",
                        w["direction"], _f(w["probability"]), cmp,
                        _f(w.get("target")), _f(w.get("range_hi")), _f(w.get("range_lo")),
                        _f(w.get("hist_wr")), tech, vix, pcr_v,
                        mp, iv, glo, fii,
                        seas, sup, res,
                    ))
                    saved += 1

            # ── Monthly prediction ─────────────────────────────────────────
            monthly = inst.get("monthly", {})
            if monthly and monthly.get("expiry_date"):
                exp_d = _parse_date(monthly["expiry_date"])
                if exp_d and exp_d >= date.today():
                    cur.execute("""
                        INSERT INTO market_predictions
                            (instrument, expiry_date, week_num, prediction_type,
                             direction, probability, cmp, target_price,
                             hist_wr, tech_signal, vix_signal, pcr_value,
                             max_pain, atm_iv, global_signal, fii_signal,
                             seasonal_signal, put_oi_wall, call_oi_wall)
                        VALUES (%s,%s,%s,%s, %s,%s,%s,%s,
                                %s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s)
                    """, (
                        code, exp_d, None, "MONTHLY",
                        monthly["direction"], _f(monthly["probability"]), cmp,
                        _f(monthly.get("expected_price")),
                        _f(monthly.get("hist_wr")), tech, vix, pcr_v,
                        mp, iv, glo, fii,
                        seas, sup, res,
                    ))
                    saved += 1

        cur.close()
    return saved


def get_accuracy_summary() -> list:
    """
    Return rows from v_model_accuracy as list of dicts.
    Returns [] if DB unavailable or no data yet.
    """
    rows = []
    with _get_conn() as conn:
        if conn is None:
            return rows
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM v_model_accuracy ORDER BY instrument, week_num")
            rows = [dict(r) for r in cur.fetchall()]
            cur.close()
        except Exception as exc:
            logger.warning("get_accuracy_summary: %s", exc)
    return rows


def get_signal_importance() -> list:
    """Return rows from v_signal_importance."""
    rows = []
    with _get_conn() as conn:
        if conn is None:
            return rows
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM v_signal_importance")
            rows = [dict(r) for r in cur.fetchall()]
            cur.close()
        except Exception as exc:
            logger.warning("get_signal_importance: %s", exc)
    return rows


def get_recent_predictions(instrument: str = None, limit: int = 50) -> list:
    """Return recent prediction rows, optionally filtered by instrument."""
    rows = []
    with _get_conn() as conn:
        if conn is None:
            return rows
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            if instrument:
                cur.execute("""
                    SELECT * FROM market_predictions
                    WHERE instrument = %s
                    ORDER BY run_at DESC LIMIT %s
                """, (instrument.upper(), limit))
            else:
                cur.execute("""
                    SELECT * FROM market_predictions
                    ORDER BY run_at DESC LIMIT %s
                """, (limit,))
            rows = [dict(r) for r in cur.fetchall()]
            cur.close()
        except Exception as exc:
            logger.warning("get_recent_predictions: %s", exc)
    return rows
