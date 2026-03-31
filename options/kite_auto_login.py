"""
Automated Kite Connect daily login.
Runs at 9:00 AM IST via cron — no manual steps needed.

Credentials are read from environment variables only.
Never hardcoded, never logged, never committed to git.
"""

import os
import sys
import logging
import requests
import pyotp
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from options.kite_auth import generate_access_token, send_daily_reminder
from logging_config import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

LOGIN_URL  = "https://kite.zerodha.com/api/login"
TWOFA_URL  = "https://kite.zerodha.com/api/twofa"
HEADERS    = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "Content-Type": "application/x-www-form-urlencoded",
    "X-Kite-Version": "3",
}


def auto_login() -> bool:
    """
    Perform full Zerodha login and save access token.
    Returns True on success, False on failure.
    """
    user_id     = os.environ.get("ZERODHA_USER_ID", "")
    password    = os.environ.get("ZERODHA_PASSWORD", "")
    totp_secret = os.environ.get("ZERODHA_TOTP_SECRET", "")
    api_key     = os.environ.get("KITE_API_KEY", "")

    if not all([user_id, password, totp_secret, api_key]):
        logger.error("Missing credentials. Check ZERODHA_* and KITE_API_KEY in .env")
        return False

    session = requests.Session()
    session.headers.update(HEADERS)

    # ── Step 1: Username + Password ────────────────────────────────────────────
    logger.info("Logging in to Zerodha...")
    try:
        resp = session.post(LOGIN_URL, data={
            "user_id":  user_id,
            "password": password,
        }, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error("Login step 1 failed: %s", exc)
        return False

    if data.get("status") != "success":
        logger.error("Login failed: %s", data.get("message", "Unknown error"))
        return False

    request_id  = data["data"]["request_id"]
    twofa_type  = data["data"].get("twofa_type", "totp")
    logger.info("Step 1 OK. request_id received.")

    # ── Step 2: TOTP ───────────────────────────────────────────────────────────
    totp = pyotp.TOTP(totp_secret)
    otp  = totp.now()

    try:
        resp = session.post(TWOFA_URL, data={
            "user_id":     user_id,
            "request_id":  request_id,
            "twofa_value": otp,
            "twofa_type":  twofa_type,
        }, timeout=15, allow_redirects=True)
    except Exception as exc:
        logger.error("Login step 2 (TOTP) failed: %s", exc)
        return False

    if resp.status_code == 403:
        logger.error("TOTP rejected: %s", resp.json().get("message", ""))
        return False

    logger.info("Step 2 OK. Session established.")

    # ── Step 3: Hit Kite Connect auth URL — intercept redirect before Flask ──────
    import urllib.parse as up
    import re
    kite_auth_url = f"https://kite.trade/connect/login?api_key={api_key}&v=3"
    request_token = None

    try:
        # Follow redirects manually so we can capture request_token from the
        # Location header *before* the redirect hits our Flask /kite/callback
        # (which would reject it due to @login_required and redirect to /login).
        current_url = kite_auth_url
        for _ in range(10):
            resp = session.get(current_url, allow_redirects=False, timeout=15)
            location = resp.headers.get("Location", "")
            logger.debug("Step 3 redirect: %s -> %s (status %s)",
                         current_url[:80], location[:120], resp.status_code)

            # Check both the Location header and the current URL for request_token
            for candidate in [location, current_url]:
                m = re.search(r"[?&]request_token=([A-Za-z0-9]+)", candidate)
                if m:
                    request_token = m.group(1)
                    break

            if request_token:
                break

            if resp.status_code in (301, 302, 303, 307, 308) and location:
                # Make relative URLs absolute
                current_url = up.urljoin(current_url, location)
            else:
                # No more redirects — check final response body as last resort
                m = re.search(r"request_token=([A-Za-z0-9]+)", resp.text[:1000])
                if m:
                    request_token = m.group(1)
                break

    except Exception as exc:
        logger.error("Step 3 redirect failed: %s", exc)
        return False

    if not request_token:
        logger.error("Could not extract request_token. Last URL: %s", current_url[:200])
        return False

    logger.info("Step 3 OK. request_token received.")

    # ── Step 4: Generate and save access token ─────────────────────────────────
    try:
        generate_access_token(request_token)
        logger.info("Access token saved. Kite ready for today.")
        return True
    except Exception as exc:
        logger.error("Failed to generate access token: %s", exc)
        return False


def notify_telegram(success: bool) -> None:
    """Send Telegram notification about login status."""
    import requests as req
    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return

    if success:
        msg = "Kite Connect authorized automatically. Options data collection active from 9:15 AM."
    else:
        api_key   = os.environ.get("KITE_API_KEY", "")
        login_url = f"https://kite.trade/connect/login?api_key={api_key}&v=3"
        msg = (
            "Auto-login failed. Please authorize manually:\n\n"
            f"{login_url}\n\n"
            "Visit: https://stock-screener-app.duckdns.org/kite/login"
        )

    req.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": msg},
        timeout=10
    )


if __name__ == "__main__":
    success = auto_login()
    notify_telegram(success)
    sys.exit(0 if success else 1)
