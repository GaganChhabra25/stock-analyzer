"""
Kite Connect authentication and daily token management.

Flow:
  1. User visits /kite/login  → redirected to Zerodha login
  2. After login, Zerodha redirects to /kite/callback?request_token=XXX
  3. App exchanges request_token → access_token → saved in DB
  4. Collector loads access_token from DB each run
  5. Telegram reminder sent at 9:00 AM IST to trigger step 1
"""

import os
import logging
from datetime import date
from typing import Optional

from kiteconnect import KiteConnect

logger = logging.getLogger(__name__)

API_KEY    = os.environ.get("KITE_API_KEY", "")
API_SECRET = os.environ.get("KITE_API_SECRET", "")


def get_kite() -> Optional[KiteConnect]:
    """Return an authenticated KiteConnect instance, or None if not authorized today."""
    token = load_access_token()
    if not token:
        logger.warning("No Kite access token for today (%s). Visit /kite/login.", date.today())
        return None
    kite = KiteConnect(api_key=API_KEY)
    kite.set_access_token(token)
    return kite


def load_access_token() -> Optional[str]:
    """Load today's access token from DB. Returns None if not yet authorized."""
    from screener.db import _get_conn
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT access_token FROM kite_tokens WHERE token_date = %s",
                    (date.today(),)
                )
                row = cur.fetchone()
                return row[0] if row else None
    except Exception as exc:
        logger.error("Could not load access token from DB: %s", exc)
        return None


def save_access_token(access_token: str) -> None:
    """Persist today's access token in DB (upsert)."""
    from screener.db import _get_conn
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO kite_tokens (token_date, access_token, created_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (token_date)
                DO UPDATE SET access_token = EXCLUDED.access_token,
                              created_at   = NOW()
            """, (date.today(), access_token))
        conn.commit()
    logger.info("Kite access token saved for %s", date.today())


def generate_access_token(request_token: str) -> str:
    """Exchange a request_token for an access_token, save it, and return it."""
    if not API_KEY or not API_SECRET:
        raise RuntimeError("KITE_API_KEY / KITE_API_SECRET not set in environment.")
    kite = KiteConnect(api_key=API_KEY)
    data = kite.generate_session(request_token, api_secret=API_SECRET)
    access_token = data["access_token"]
    save_access_token(access_token)
    return access_token


def get_login_url() -> str:
    """Return the Zerodha login URL for this app."""
    kite = KiteConnect(api_key=API_KEY)
    return kite.login_url()


def send_auth_success_telegram() -> None:
    """Send Telegram confirming Kite auth succeeded. Called from /kite/callback."""
    import requests
    from datetime import datetime
    from zoneinfo import ZoneInfo
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat  = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return
    now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
    msg = (
        "\u2705 Zerodha Kite authenticated successfully!\n"
        f"Time: {now_ist.strftime('%d-%b-%Y %I:%M %p IST')}\n"
        "Options data collection will start at 9:15 AM IST."
    )
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": msg},
            timeout=10,
        )
        logger.info("Telegram auth-success notification sent.")
    except Exception as exc:
        logger.warning("Telegram auth-success send failed: %s", exc)


def send_daily_reminder() -> None:
    """Send Telegram message at 9:00 AM IST reminding user to authorize Kite."""
    import requests
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat  = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        logger.warning("Telegram credentials not set — skipping reminder.")
        return

    login_url = get_login_url()
    msg = (
        "Good morning! \U0001f4c8\n\n"
        "Click the link below to authorize Kite Connect for today's options data collection:\n\n"
        f"{login_url}\n\n"
        "Market opens at 9:15 AM. Please authorize before that."
    )
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat, "text": msg, "parse_mode": "HTML"},
        timeout=10
    )
    if resp.ok:
        logger.info("Telegram reminder sent.")
    else:
        logger.warning("Telegram reminder failed: %s", resp.text)
