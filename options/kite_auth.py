"""
Kite Connect authentication and daily token management.

Flow:
  1. User visits /kite/login  → redirected to Zerodha login
  2. After login, Zerodha redirects to /kite/callback?request_token=XXX
  3. App calls generate_access_token(request_token, zerodha_user_id)
     → upserts user row → saves access_token for that user
  4. Per-user kite instance via get_kite(zerodha_user_id)
  5. Collector (shared market data) uses KITE_ADMIN_USER_ID from env
"""

import os
import logging
from datetime import date
from typing import Optional

from kiteconnect import KiteConnect

logger = logging.getLogger(__name__)

API_KEY    = os.environ.get("KITE_API_KEY", "")
API_SECRET = os.environ.get("KITE_API_SECRET", "")

# Used by collector/watchdog (shared market data process)
ADMIN_USER_ID = os.environ.get("KITE_ADMIN_USER_ID", "")


def upsert_user(zerodha_user_id: str, email: str, full_name: str) -> None:
    """Insert or update user row on login."""
    from screener.db import _get_conn
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO users (zerodha_user_id, email, full_name, created_at, last_login_at)
                VALUES (%s, %s, %s, NOW(), NOW())
                ON CONFLICT (zerodha_user_id)
                DO UPDATE SET
                    email         = EXCLUDED.email,
                    full_name     = EXCLUDED.full_name,
                    last_login_at = NOW()
            """, (zerodha_user_id, email, full_name))
        conn.commit()
    logger.info("User upserted: %s (%s)", zerodha_user_id, email)


def get_kite(user_id: Optional[str] = None) -> Optional[KiteConnect]:
    """Return an authenticated KiteConnect instance for the given user.

    If user_id is None, falls back to KITE_ADMIN_USER_ID (used by collector).
    Returns None if no valid token found.
    """
    uid = user_id or ADMIN_USER_ID
    if not uid:
        logger.warning("get_kite() called with no user_id and KITE_ADMIN_USER_ID not set.")
        return None
    token = load_access_token(uid)
    if not token:
        logger.warning("No Kite token for user %s on %s. Visit /kite/login.", uid, date.today())
        return None
    kite = KiteConnect(api_key=API_KEY)
    kite.set_access_token(token)
    return kite


def load_access_token(user_id: str) -> Optional[str]:
    """Load today's access token for the given user. Returns None if not authorized."""
    from screener.db import _get_conn
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT access_token FROM kite_tokens WHERE zerodha_user_id = %s AND token_date = %s",
                    (user_id, date.today())
                )
                row = cur.fetchone()
                return row[0] if row else None
    except Exception as exc:
        logger.error("Could not load access token for %s: %s", user_id, exc)
        return None


def save_access_token(user_id: str, access_token: str) -> None:
    """Persist today's access token for a user (upsert)."""
    from screener.db import _get_conn
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO kite_tokens (zerodha_user_id, token_date, access_token, created_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (zerodha_user_id, token_date)
                DO UPDATE SET access_token = EXCLUDED.access_token,
                              created_at   = NOW()
            """, (user_id, date.today(), access_token))
        conn.commit()
    logger.info("Kite token saved for user %s on %s", user_id, date.today())


def generate_access_token(request_token: str, user_id: str = "") -> str:
    """Exchange request_token for access_token, upsert user profile, save token.

    Returns the access_token string. The caller should read zerodha_user_id
    from kite.profile() after this if needed (or just read it from session).
    """
    if not API_KEY or not API_SECRET:
        raise RuntimeError("KITE_API_KEY / KITE_API_SECRET not set in environment.")
    kite = KiteConnect(api_key=API_KEY)
    data = kite.generate_session(request_token, api_secret=API_SECRET)
    access_token = data["access_token"]

    # Fetch profile to get canonical Zerodha user id
    kite.set_access_token(access_token)
    profile = kite.profile()
    uid       = profile.get("user_id") or user_id or "UNKNOWN"
    email     = profile.get("email", "")
    full_name = profile.get("user_name", uid)

    upsert_user(uid, email, full_name)
    save_access_token(uid, access_token)
    return access_token


def get_login_url() -> str:
    """Return the Zerodha login URL for this app."""
    kite = KiteConnect(api_key=API_KEY)
    return kite.login_url()


def send_auth_success_telegram(user_name: str = "") -> None:
    """Send Telegram confirming Kite auth succeeded. Called from /kite/callback."""
    import requests
    from datetime import datetime
    from zoneinfo import ZoneInfo
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat  = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return
    now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
    who = f" ({user_name})" if user_name else ""
    msg = (
        f"\u2705 Zerodha Kite authenticated{who}!\n"
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
    """Send Telegram message at 9:00 AM IST reminding users to authorize Kite."""
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
