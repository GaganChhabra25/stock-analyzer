"""
App user manager — DB-backed CRUD for allowed Google OAuth users.

Table: app_users (email PK, is_admin, created_at, last_login_at)
Seeds from config.py on first use if table is empty.
"""

import logging
import re
from datetime import datetime, timezone

from screener.db import _get_conn

logger = logging.getLogger(__name__)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _ensure_table() -> None:
    """Create app_users table if it doesn't exist, then seed from config if empty."""
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS app_users (
                    email          TEXT        PRIMARY KEY,
                    is_admin       BOOLEAN     NOT NULL DEFAULT FALSE,
                    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_login_at  TIMESTAMPTZ
                )
            """)
            # Seed from config if table is empty
            cur.execute("SELECT COUNT(*) FROM app_users")
            if cur.fetchone()[0] == 0:
                from config import ALLOWED_EMAILS, ADMIN_EMAILS
                for email in ALLOWED_EMAILS:
                    cur.execute("""
                        INSERT INTO app_users (email, is_admin)
                        VALUES (%s, %s) ON CONFLICT DO NOTHING
                    """, (email.lower(), email in ADMIN_EMAILS))
        conn.commit()


def list_users() -> list:
    _ensure_table()
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT email, is_admin, created_at, last_login_at
                FROM app_users ORDER BY created_at
            """)
            return [
                {
                    "email":         r[0],
                    "is_admin":      r[1],
                    "created_at":    r[2].isoformat() if r[2] else None,
                    "last_login_at": r[3].isoformat() if r[3] else None,
                }
                for r in cur.fetchall()
            ]


def get_allowed_emails() -> set:
    _ensure_table()
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT email FROM app_users")
            return {r[0] for r in cur.fetchall()}


def get_admin_emails() -> set:
    _ensure_table()
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT email FROM app_users WHERE is_admin = TRUE")
            return {r[0] for r in cur.fetchall()}


def record_login(email: str) -> None:
    """Update last_login_at for a user on successful login."""
    try:
        _ensure_table()
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE app_users SET last_login_at = NOW() WHERE email = %s
                """, (email.lower(),))
            conn.commit()
    except Exception as exc:
        logger.warning("record_login failed for %s: %s", email, exc)


def add_user(email: str, is_admin: bool = False) -> tuple:
    email = email.strip().lower()
    if not _EMAIL_RE.match(email):
        return False, "Invalid email address."
    try:
        _ensure_table()
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM app_users WHERE email = %s", (email,))
                if cur.fetchone():
                    return False, "User already exists."
                cur.execute(
                    "INSERT INTO app_users (email, is_admin) VALUES (%s, %s)",
                    (email, bool(is_admin))
                )
            conn.commit()
        return True, "User added."
    except Exception as exc:
        logger.error("add_user failed: %s", exc)
        return False, str(exc)


def update_user(email: str, is_admin: bool) -> tuple:
    email = email.strip().lower()
    try:
        _ensure_table()
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE app_users SET is_admin = %s WHERE email = %s",
                    (bool(is_admin), email)
                )
                if cur.rowcount == 0:
                    return False, "User not found."
            conn.commit()
        return True, "User updated."
    except Exception as exc:
        logger.error("update_user failed: %s", exc)
        return False, str(exc)


def delete_user(email: str) -> tuple:
    email = email.strip().lower()
    try:
        _ensure_table()
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM app_users WHERE email = %s", (email,))
                if cur.rowcount == 0:
                    return False, "User not found."
            conn.commit()
        return True, "User deleted."
    except Exception as exc:
        logger.error("delete_user failed: %s", exc)
        return False, str(exc)
