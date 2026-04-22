"""
Pipeline Watchdog — self-healing monitor for all options data collection.

Runs every 10 minutes during all market hours via cron.

Market windows monitored:
  NFO (NIFTY/BANKNIFTY) : 09:14 – 15:35 IST  (NSE holidays respected)
  MCX (NatGas/Crude)     : 09:00 – 23:35 IST  (MCX holidays + evening-only days respected)

Uses the same is_open() from each ExchangeCollector — single source of truth.
No checks or Telegram messages fire outside actual market hours.

Logic per active window:
  1. Data flowing                   → healthy, clear any stale alerts
  2. No data + Kite not auth'd      → auto re-login via kite_auto_login.py
       Re-login succeeded           → Telegram "auto-fixed"
       Re-login failed              → Telegram ALERT (once per day)
  3. No data + Kite auth'd          → stale MCX cache cleared (auto-fix attempt)
       Still no data                → Telegram ALERT (once per day)

Sentinel files (data/watchdog_*.flag) prevent Telegram spam — one alert per issue per day.
"""

import os
import sys
import subprocess
import logging
from datetime import datetime, date, time
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
VENV_PY = sys.executable  # works in both Docker (/app/venv/...) and host (venv/bin/python3)
APP_DIR = str(Path(__file__).parent.parent)

# Each entry: (label, instrument_to_check, collector_class)
# is_open() on the collector is the single source of truth for market hours.
def _build_windows():
    from options.exchange.nfo import NFOCollector
    from options.exchange.mcx import MCXCollector
    return [
        ("NFO", "NIFTY",      NFOCollector()),
        ("MCX", "NATURALGAS", MCXCollector()),
    ]


# ── Core checks ────────────────────────────────────────────────────────────────

def _ist_now() -> datetime:
    return datetime.now(IST)


def _has_recent_data(instrument: str, minutes: int = 10) -> bool:
    """True if option_chain has rows for instrument in the last N minutes."""
    if not is_available():
        return False
    try:
        with _get_conn() as conn:
            if conn is None:
                return False
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) FROM option_chain
                    WHERE instrument = %s
                      AND ts >= NOW() - INTERVAL '1 minute' * %s
                """, (instrument, minutes))
                return (cur.fetchone()[0] or 0) > 0
    except Exception as exc:
        logger.warning("_has_recent_data(%s) failed: %s", instrument, exc)
        return False


def _is_kite_authenticated() -> bool:
    """True if a valid Kite access token exists in DB for today."""
    if not is_available():
        return False
    try:
        with _get_conn() as conn:
            if conn is None:
                return False
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 1 FROM kite_tokens
                    WHERE token_date     = CURRENT_DATE
                      AND access_token  IS NOT NULL
                      AND access_token  != ''
                    LIMIT 1
                """)
                return cur.fetchone() is not None
    except Exception as exc:
        logger.warning("_is_kite_authenticated failed: %s", exc)
        return False


# ── Auto-fix helpers ───────────────────────────────────────────────────────────

def _try_auto_login() -> bool:
    """Run kite_auto_login.py. Returns True if access token saved successfully."""
    script = Path(__file__).parent / "kite_auto_login.py"
    try:
        result = subprocess.run(
            [VENV_PY, str(script)],
            capture_output=True, text=True, timeout=60, cwd=APP_DIR,
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


def _clear_mcx_cache() -> bool:
    """
    Delete stale MCX instruments cache so it's re-fetched on next collection.
    Returns True if cache was deleted.
    """
    cache = Path(APP_DIR) / "data" / "kite_cache" / "mcx_instruments.json"
    if cache.exists():
        cache.unlink()
        logger.info("Cleared stale MCX instruments cache.")
        return True
    return False


# ── Telegram ───────────────────────────────────────────────────────────────────

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


# ── Sentinel helpers (one alert per issue per day) ────────────────────────────

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


# ── Per-window health check ────────────────────────────────────────────────────

def _check_window(label: str, instrument: str, now: datetime) -> None:
    """Run one health check cycle for a market window."""
    logger.info("[%s] Watchdog check at %s IST", label, now.strftime("%H:%M"))

    if _has_recent_data(instrument, minutes=10):
        logger.info("[%s] Data flowing — pipeline healthy.", label)
        _clear_alert(f"{label}_login_failed")
        _clear_alert(f"{label}_no_data")
        return

    logger.warning("[%s] No %s data in last 10 minutes!", label, instrument)
    now_str = now.strftime("%d-%b-%Y %I:%M %p IST")

    # ── Fix attempt 1: Kite not authenticated ──────────────────────────────────
    if not _is_kite_authenticated():
        logger.warning("[%s] Kite token missing — attempting auto re-login...", label)
        if _try_auto_login():
            _send_telegram(
                f"\U0001f527 [{label}] Auto-fixed: Kite re-authenticated\n"
                f"Time: {now_str}\n"
                f"Data collection will resume within 1 minute."
            )
            _clear_alert(f"{label}_login_failed")
            logger.info("[%s] Auto re-login fixed the issue.", label)
        else:
            if not _already_alerted(f"{label}_login_failed"):
                _mark_alerted(f"{label}_login_failed")
                _send_telegram(
                    f"\U0001f6a8 [{label}] ALERT: Kite login FAILED — could not auto-fix!\n"
                    f"Time: {now_str}\n"
                    f"Please login manually:\n"
                    f"https://stock-screener-app.duckdns.org/kite/login"
                )
        return

    # ── Fix attempt 2: Kite OK but no data → clear MCX cache (MCX only) ────────
    if label == "MCX":
        cleared = _clear_mcx_cache()
        if cleared:
            logger.info("[MCX] Cache cleared — next cron run will re-fetch instruments.")
            _send_telegram(
                f"\U0001f527 [MCX] Auto-fix attempted: stale instruments cache cleared\n"
                f"Time: {now_str}\n"
                f"Collection should resume within 1-2 minutes."
            )
            return   # wait for next watchdog run to see if it fixed it

    # ── No auto-fix available → alert ─────────────────────────────────────────
    if not _already_alerted(f"{label}_no_data"):
        _mark_alerted(f"{label}_no_data")
        _send_telegram(
            f"\u26a0\ufe0f [{label}] ALERT: Kite auth'd but NO DATA for 10 min!\n"
            f"Time: {now_str}\n"
            f"Instrument checked: {instrument}\n"
            f"Check collector logs:\n"
            f"tail -50 /home/stockapp/stock-analyzer/logs/options.log"
        )
    else:
        logger.warning("[%s] No-data alert already sent today — not repeating.", label)


# ── Main ───────────────────────────────────────────────────────────────────────

def run_watchdog() -> None:
    now     = _ist_now()
    windows = _build_windows()

    active = [
        (label, instrument)
        for (label, instrument, collector) in windows
        if collector.is_open()
    ]

    if not active:
        logger.debug("Outside all market hours — watchdog idle.")
        return

    for label, instrument in active:
        _check_window(label, instrument, now)


if __name__ == "__main__":
    run_watchdog()
