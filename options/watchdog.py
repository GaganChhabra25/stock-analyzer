"""
Pipeline Watchdog — Self-healing monitor for options data collection.

Runs every 10 minutes during market hours via cron.
Detects gaps in option_chain data and auto-fixes where possible.

Failure modes handled:
  1. Kite not authenticated → re-run kite_auto_login.py
  2. Kite authenticated but no data flowing → alert (collector may have crashed)
  3. Both fix attempts failed → Telegram alert with login URL
"""

import os
import sys
import subprocess
import logging
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from screener.db import _get_conn, is_available
from logging_config import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

IST     = ZoneInfo("Asia/Kolkata")
VENV_PY = str(Path(__file__).parent.parent / "venv/bin/python3")
APP_DIR = str(Path(__file__).parent.parent)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _ist_now() -> datetime:
    return datetime.now(IST)


def _send_telegram(msg: str) -> None:
    import requests
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat  = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": msg},
            timeout=10,
        )
    except Exception as exc:
        logger.warning("Telegram send failed: %s", exc)


def _has_recent_data(minutes: int = 10) -> bool:
    """True if option_chain has NIFTY rows in the last N minutes."""
    if not is_available():
        return False
    try:
        with _get_conn() as conn:
            if conn is None:
                return False
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) FROM option_chain
                    WHERE instrument = 'NIFTY'
                      AND ts >= NOW() - INTERVAL '1 minute' * %s
                """, (minutes,))
                return (cur.fetchone()[0] or 0) > 0
    except Exception as exc:
        logger.warning("_has_recent_data failed: %s", exc)
        return False


def _is_kite_authenticated() -> bool:
    """True if a valid Kite access token was saved today."""
    if not is_available():
        return False
    try:
        with _get_conn() as conn:
            if conn is None:
                return False
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 1 FROM kite_tokens
                    WHERE token_type = 'access_token'
                      AND DATE(updated_at AT TIME ZONE 'Asia/Kolkata') = CURRENT_DATE
                      AND token_value IS NOT NULL
                      AND token_value != ''
                    LIMIT 1
                """)
                return cur.fetchone() is not None
    except Exception as exc:
        logger.warning("_is_kite_authenticated failed: %s", exc)
        return False


def _try_auto_login() -> bool:
    """Run kite_auto_login.py. Returns True if access token saved successfully."""
    script = Path(__file__).parent / "kite_auto_login.py"
    try:
        result = subprocess.run(
            [VENV_PY, str(script)],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=APP_DIR,
        )
        combined = result.stdout + result.stderr
        if result.returncode == 0 and "Access token saved" in combined:
            logger.info("Auto re-login succeeded.")
            return True
        logger.warning("Auto re-login failed (rc=%d): %s", result.returncode, combined[-500:])
        return False
    except Exception as exc:
        logger.warning("_try_auto_login exception: %s", exc)
        return False


# ── Sentinel helpers (prevent alert spam) ─────────────────────────────────────

def _sentinel(name: str) -> Path:
    return Path(APP_DIR) / "data" / f"watchdog_{name}_{date.today()}.flag"


def _already_alerted(name: str) -> bool:
    return _sentinel(name).exists()


def _mark_alerted(name: str) -> None:
    _sentinel(name).touch()


def _clear_alert(name: str) -> None:
    p = _sentinel(name)
    if p.exists():
        p.unlink()


# ── Main ───────────────────────────────────────────────────────────────────────

def run_watchdog() -> None:
    now = _ist_now()

    # Only run during market hours (9:14 AM – 3:35 PM IST)
    open_time  = now.replace(hour=9,  minute=14, second=0, microsecond=0)
    close_time = now.replace(hour=15, minute=35, second=0, microsecond=0)
    if not (open_time <= now <= close_time):
        logger.debug("Outside market hours — watchdog idle.")
        return

    logger.info("Watchdog check at %s IST", now.strftime("%H:%M"))

    # ── Step 1: Is data flowing? ───────────────────────────────────────────────
    if _has_recent_data(minutes=10):
        logger.info("Data flowing — pipeline healthy.")
        _clear_alert("login_failed")
        _clear_alert("no_data")
        return

    logger.warning("No NIFTY option_chain data in last 10 minutes!")

    # ── Step 2: Is Kite authenticated? ────────────────────────────────────────
    if not _is_kite_authenticated():
        logger.warning("Kite access token missing — attempting auto re-login...")
        if _try_auto_login():
            logger.info("Auto re-login fixed the issue.")
            _send_telegram(
                f"🔧 Auto-fixed: Kite re-authenticated\n"
                f"Time: {now.strftime('%d-%b-%Y %I:%M %p IST')}\n"
                f"Data collection resuming shortly."
            )
            _clear_alert("login_failed")
        else:
            if not _already_alerted("login_failed"):
                _mark_alerted("login_failed")
                _send_telegram(
                    f"🚨 Alert: Kite login failed — could not auto-fix!\n"
                    f"Time: {now.strftime('%d-%b-%Y %I:%M %p IST')}\n"
                    f"Please login manually: https://185.211.6.5/kite/login"
                )
            else:
                logger.warning("Login-failed alert already sent today — skipping repeat.")
        return

    # ── Step 3: Kite OK but no data — collector issue ─────────────────────────
    if not _already_alerted("no_data"):
        _mark_alerted("no_data")
        _send_telegram(
            f"⚠️ Alert: Kite authenticated but no data in 10 min!\n"
            f"Time: {now.strftime('%d-%b-%Y %I:%M %p IST')}\n"
            f"Collector may have crashed. Check logs:\n"
            f"tail -50 /home/stockapp/stock-analyzer/logs/options.log"
        )
    else:
        logger.warning("No-data alert already sent today — skipping repeat.")


if __name__ == "__main__":
    run_watchdog()
