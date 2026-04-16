"""
Deployments DB layer  —  broker/deployments.py
────────────────────────────────────────────────
Persists live strategy deployments and their legs.

Tables:
  deployments      — one row per deployed strategy run
  deployment_legs  — one row per order leg within a deployment

Tables are created automatically on first use.
"""

from __future__ import annotations

import json
import logging
from typing import List, Optional

from screener.db import _get_conn, is_available

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS deployments (
    id            BIGSERIAL    PRIMARY KEY,
    user_id       VARCHAR(30)  NOT NULL,
    strategy_key  VARCHAR(30)  NOT NULL,
    instrument    VARCHAR(20)  NOT NULL,
    expiry        DATE,
    broker        VARCHAR(20)  NOT NULL DEFAULT 'zerodha',
    lots          INTEGER      NOT NULL DEFAULT 1,
    wing_width    INTEGER,
    status        VARCHAR(15)  NOT NULL DEFAULT 'PENDING',
    entry_at      TIMESTAMPTZ,
    exit_at       TIMESTAMPTZ,
    exit_reason   VARCHAR(20),
    net_entry_pts NUMERIC(10,2),
    net_exit_pts  NUMERIC(10,2),
    pnl_pts       NUMERIC(10,2),
    pnl_inr       NUMERIC(12,2),
    error         TEXT,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS deployment_legs (
    id             BIGSERIAL   PRIMARY KEY,
    deployment_id  BIGINT      NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
    leg_index      SMALLINT    NOT NULL,
    expiry         DATE        NOT NULL,
    strike         INTEGER     NOT NULL,
    option_type    CHAR(2)     NOT NULL,
    direction      VARCHAR(4)  NOT NULL,
    lots           INTEGER     NOT NULL,
    lot_size       INTEGER     NOT NULL,
    trading_symbol VARCHAR(40),
    broker_order_id VARCHAR(30),
    entry_price    NUMERIC(10,2),
    exit_price     NUMERIC(10,2),
    sl_price       NUMERIC(10,2),
    status         VARCHAR(15) NOT NULL DEFAULT 'OPEN',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dep_user_status
    ON deployments (user_id, status);
CREATE INDEX IF NOT EXISTS idx_dep_leg_dep
    ON deployment_legs (deployment_id);
"""

_ready = False


def _ensure_schema() -> bool:
    global _ready
    if _ready:
        return True
    if not is_available():
        return False
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                for stmt in _DDL.strip().split(";"):
                    s = stmt.strip()
                    if s:
                        cur.execute(s)
            conn.commit()
        _ready = True
        return True
    except Exception as exc:
        logger.error("deployments schema init: %s", exc)
        return False


# ── Public API ─────────────────────────────────────────────────────────────────

def create_deployment(
    user_id:      str,
    strategy_key: str,
    instrument:   str,
    expiry,               # date or None
    broker:       str,
    lots:         int,
    wing_width:   Optional[int] = None,
) -> Optional[int]:
    """Insert a new PENDING deployment. Returns new row id or None."""
    if not _ensure_schema():
        return None
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO deployments
                        (user_id, strategy_key, instrument, expiry, broker,
                         lots, wing_width, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'PENDING')
                    RETURNING id
                    """,
                    (user_id, strategy_key, instrument, expiry, broker,
                     lots, wing_width),
                )
                row = cur.fetchone()
            conn.commit()
        return row[0] if row else None
    except Exception as exc:
        logger.error("create_deployment: %s", exc)
        return None


def activate_deployment(dep_id: int, legs: List) -> bool:
    """Set status=ACTIVE, record entry time, insert leg rows."""
    if not _ensure_schema():
        return False
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE deployments
                    SET status='ACTIVE', entry_at=NOW()
                    WHERE id=%s
                    """,
                    (dep_id,),
                )
                for leg in legs:
                    cur.execute(
                        """
                        INSERT INTO deployment_legs
                            (deployment_id, leg_index, expiry, strike,
                             option_type, direction, lots, lot_size,
                             trading_symbol, broker_order_id, entry_price,
                             sl_price, status)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'OPEN')
                        """,
                        (dep_id, leg.leg_index, leg.expiry if hasattr(leg, 'expiry') else None,
                         leg.strike, leg.option_type, leg.direction,
                         leg.lots, leg.lot_size, leg.trading_symbol,
                         leg.order_id, leg.entry_price, leg.sl_price),
                    )
            conn.commit()
        return True
    except Exception as exc:
        logger.error("activate_deployment %d: %s", dep_id, exc)
        return False


def close_deployment(
    dep_id:     int,
    exit_legs:  List[dict],
    reason:     str,
    pnl_pts:    Optional[float] = None,
    pnl_inr:    Optional[float] = None,
) -> bool:
    """Set status=CLOSED, record exit prices and reason."""
    if not _ensure_schema():
        return False
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE deployments
                    SET status='CLOSED', exit_at=NOW(),
                        exit_reason=%s, pnl_pts=%s, pnl_inr=%s
                    WHERE id=%s
                    """,
                    (reason, pnl_pts, pnl_inr, dep_id),
                )
                for el in exit_legs:
                    cur.execute(
                        """
                        UPDATE deployment_legs
                        SET exit_price=%s, status='CLOSED'
                        WHERE deployment_id=%s AND leg_index=%s
                        """,
                        (el.get("exit_price"), dep_id, el.get("leg_index")),
                    )
            conn.commit()
        return True
    except Exception as exc:
        logger.error("close_deployment %d: %s", dep_id, exc)
        return False


def fail_deployment(dep_id: int, error: str) -> bool:
    """Mark deployment as FAILED with error message."""
    if not _ensure_schema():
        return False
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE deployments SET status='FAILED', error=%s WHERE id=%s",
                    (error[:500], dep_id),
                )
            conn.commit()
        return True
    except Exception as exc:
        logger.error("fail_deployment %d: %s", dep_id, exc)
        return False


def reset_stuck_entering() -> None:
    """On startup, reset any ENTERING rows back to PENDING (crashed mid-entry)."""
    if not _ensure_schema():
        return
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE deployments SET status='PENDING' WHERE status='ENTERING'"
                )
                n = cur.rowcount
            conn.commit()
        if n:
            logger.warning("Reset %d stuck ENTERING deployment(s) to PENDING", n)
    except Exception as exc:
        logger.error("reset_stuck_entering: %s", exc)


def claim_deployment(dep_id: int) -> bool:
    """Atomically transition PENDING → ENTERING.
    Only ONE worker can win — others get False and must skip entry.
    """
    if not _ensure_schema():
        return False
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE deployments SET status='ENTERING' WHERE id=%s AND status='PENDING'",
                    (dep_id,),
                )
                claimed = cur.rowcount > 0
            conn.commit()
        return claimed
    except Exception as exc:
        logger.error("claim_deployment %d: %s", dep_id, exc)
        return False


def cancel_deployment(dep_id: int) -> bool:
    """Cancel a PENDING deployment (cannot cancel ACTIVE)."""
    if not _ensure_schema():
        return False
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE deployments SET status='CANCELLED'
                    WHERE id=%s AND status='PENDING'
                    """,
                    (dep_id,),
                )
                updated = cur.rowcount
            conn.commit()
        return updated > 0
    except Exception as exc:
        logger.error("cancel_deployment %d: %s", dep_id, exc)
        return False


def get_deployment(dep_id: int) -> Optional[dict]:
    """Return deployment row + its legs as a dict."""
    if not _ensure_schema():
        return None
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, user_id, strategy_key, instrument,
                           expiry::text, broker, lots, wing_width, status,
                           entry_at AT TIME ZONE 'Asia/Kolkata' AS entry_ist,
                           exit_at  AT TIME ZONE 'Asia/Kolkata' AS exit_ist,
                           exit_reason, pnl_pts, pnl_inr, error,
                           created_at AT TIME ZONE 'Asia/Kolkata' AS created_ist
                    FROM deployments WHERE id=%s
                    """,
                    (dep_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                cols = [d[0] for d in cur.description]
                dep = dict(zip(cols, row))

                cur.execute(
                    """
                    SELECT leg_index, strike, option_type, direction,
                           lots, lot_size, trading_symbol, broker_order_id,
                           entry_price, exit_price, sl_price, status
                    FROM deployment_legs
                    WHERE deployment_id=%s ORDER BY leg_index
                    """,
                    (dep_id,),
                )
                lcols = [d[0] for d in cur.description]
                dep["legs"] = [dict(zip(lcols, r)) for r in cur.fetchall()]
        return dep
    except Exception as exc:
        logger.error("get_deployment %d: %s", dep_id, exc)
        return None


def get_pending_and_active(user_id: Optional[str] = None) -> List[dict]:
    """Return all PENDING and ACTIVE deployments (optionally filtered by user)."""
    if not _ensure_schema():
        return []
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                if user_id:
                    cur.execute(
                        """
                        SELECT id, user_id, strategy_key, instrument,
                               expiry::text, broker, lots, wing_width, status,
                               to_char(entry_at AT TIME ZONE 'Asia/Kolkata',
                                       'HH24:MI') AS entry_ist,
                               exit_reason, pnl_pts, pnl_inr, error,
                               to_char(created_at AT TIME ZONE 'Asia/Kolkata',
                                       'DD Mon HH24:MI') AS created_ist
                        FROM deployments
                        WHERE status IN ('PENDING','ACTIVE')
                          AND user_id=%s
                        ORDER BY created_at DESC
                        LIMIT 20
                        """,
                        (user_id,),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, user_id, strategy_key, instrument,
                               expiry::text, broker, lots, wing_width, status,
                               to_char(entry_at AT TIME ZONE 'Asia/Kolkata',
                                       'HH24:MI') AS entry_ist,
                               exit_reason, pnl_pts, pnl_inr, error,
                               to_char(created_at AT TIME ZONE 'Asia/Kolkata',
                                       'DD Mon HH24:MI') AS created_ist
                        FROM deployments
                        WHERE status IN ('PENDING','ACTIVE')
                        ORDER BY created_at DESC LIMIT 20
                        """
                    )
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as exc:
        logger.error("get_pending_and_active: %s", exc)
        return []


def get_recent_deployments(user_id: str, limit: int = 10) -> List[dict]:
    """Return recent deployments (all statuses) for display."""
    if not _ensure_schema():
        return []
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, strategy_key, instrument, expiry::text,
                           lots, status, exit_reason,
                           pnl_pts, pnl_inr,
                           to_char(entry_at AT TIME ZONE 'Asia/Kolkata',
                                   'HH24:MI') AS entry_ist,
                           to_char(created_at AT TIME ZONE 'Asia/Kolkata',
                                   'DD Mon HH24:MI') AS created_ist,
                           error
                    FROM deployments
                    WHERE user_id=%s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as exc:
        logger.error("get_recent_deployments: %s", exc)
        return []


def get_open_legs(dep_id: int) -> List[dict]:
    """Return OPEN legs for a deployment."""
    if not _ensure_schema():
        return []
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT leg_index, strike, option_type, direction,
                           lots, lot_size, trading_symbol, broker_order_id,
                           entry_price, sl_price
                    FROM deployment_legs
                    WHERE deployment_id=%s AND status='OPEN'
                    ORDER BY leg_index
                    """,
                    (dep_id,),
                )
                cols = [d[0] for d in cur.description]
                return [dict(zip(lcols, r)) for lcols, r in [(cols, row) for row in cur.fetchall()]]
    except Exception as exc:
        logger.error("get_open_legs %d: %s", dep_id, exc)
        return []
