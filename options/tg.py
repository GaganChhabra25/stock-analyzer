"""
Minimal Telegram helper for data collection notifications.

Uses TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID from .env (same as existing notifier).
Sentinel files in data/ ensure start/end messages fire exactly once per day.
"""

import logging
import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

logger       = logging.getLogger(__name__)
IST          = ZoneInfo("Asia/Kolkata")
_SENTINEL_DIR = Path(__file__).parent.parent / "data"


# ── Core send ─────────────────────────────────────────────────────────────────

def send(msg: str) -> None:
    """Send a Telegram message. Silent no-op if credentials not configured."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat  = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        logger.debug("[TG] Credentials not set — skipping.")
        return
    try:
        import requests
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": msg},
            timeout=10,
        )
        if not resp.ok:
            logger.warning("[TG] Send failed (%s): %s", resp.status_code, resp.text[:100])
    except Exception as exc:
        logger.warning("[TG] Send error: %s", exc)


# ── Sentinel helpers ──────────────────────────────────────────────────────────

def _flag(tag: str) -> Path:
    return _SENTINEL_DIR / f"tg_{tag}_{date.today()}.flag"


def once(tag: str, msg: str) -> bool:
    """Send msg exactly once today (sentinel-protected). Returns True if sent."""
    flag = _flag(tag)
    if flag.exists():
        return False
    flag.touch()
    send(msg)
    return True


# ── DB helpers ────────────────────────────────────────────────────────────────

def db_size() -> str:
    """Return human-readable total DB size, e.g. '4.2 GB'."""
    try:
        from screener.db import _get_conn
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_size_pretty(pg_database_size(current_database()))"
                )
                return cur.fetchone()[0]
    except Exception as exc:
        logger.debug("[TG] db_size failed: %s", exc)
        return "N/A"


def table_rows(table: str) -> int:
    """Fast approximate row count for a table (pg_stat_user_tables)."""
    try:
        from screener.db import _get_conn
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT n_live_tup FROM pg_stat_user_tables WHERE relname = %s",
                    (table,)
                )
                row = cur.fetchone()
                return int(row[0]) if row else 0
    except Exception as exc:
        logger.debug("[TG] table_rows failed: %s", exc)
        return 0


def now_ist() -> str:
    return datetime.now(IST).strftime("%d-%b %I:%M %p")
