"""
Saved Strategies — DB persistence layer
────────────────────────────────────────
Each saved entry stores: strategy key, instrument, date range,
extra params, result summary JSON, and user notes.

Table is created automatically on first use (no manual migration needed).
"""

import json
import logging
from typing import Optional, List

from screener.db import _get_conn, is_available

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS saved_strategies (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(100)  NOT NULL,
    strategy_key  VARCHAR(50)   NOT NULL,
    instrument    VARCHAR(20)   NOT NULL,
    date_from     DATE,
    date_to       DATE,
    params        JSONB  NOT NULL DEFAULT '{}',
    summary       JSONB  NOT NULL DEFAULT '{}',
    notes         TEXT          DEFAULT '',
    created_at    TIMESTAMPTZ   DEFAULT NOW()
);
"""

_ready = False


def _ensure_table() -> bool:
    global _ready
    if _ready:
        return True
    if not is_available():
        return False
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_DDL)
            conn.commit()
        _ready = True
        return True
    except Exception as exc:
        logger.error("saved_strategies init: %s", exc)
        return False


# ── Public API ─────────────────────────────────────────────────────────────────

def save(
    name:         str,
    strategy_key: str,
    instrument:   str,
    date_from:    Optional[str],
    date_to:      Optional[str],
    params:       dict,
    summary:      dict,
    notes:        str = "",
) -> Optional[int]:
    """Insert a new saved strategy. Returns the new row id or None on failure."""
    if not _ensure_table():
        return None
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO saved_strategies
                        (name, strategy_key, instrument,
                         date_from, date_to, params, summary, notes)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                    RETURNING id
                    """,
                    (
                        name, strategy_key, instrument,
                        date_from, date_to,
                        json.dumps(params), json.dumps(summary), notes,
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        return row[0] if row else None
    except Exception as exc:
        logger.error("saved_strategies.save: %s", exc)
        return None


def list_all() -> List[dict]:
    """Return all saved strategies ordered newest-first (max 100 rows)."""
    if not _ensure_table():
        return []
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, name, strategy_key, instrument,
                           date_from::text, date_to::text,
                           params, summary, notes,
                           to_char(
                               created_at AT TIME ZONE 'Asia/Kolkata',
                               'DD Mon YYYY HH24:MI'
                           ) AS created_ist
                    FROM saved_strategies
                    ORDER BY created_at DESC
                    LIMIT 100
                    """
                )
                cols = [d[0] for d in cur.description]
                rows = cur.fetchall()
        return [dict(zip(cols, r)) for r in rows]
    except Exception as exc:
        logger.error("saved_strategies.list_all: %s", exc)
        return []


def delete(strategy_id: int) -> bool:
    """Delete a saved strategy by id. Returns True on success."""
    if not _ensure_table():
        return False
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM saved_strategies WHERE id = %s",
                    (strategy_id,),
                )
            conn.commit()
        return True
    except Exception as exc:
        logger.error("saved_strategies.delete: %s", exc)
        return False
