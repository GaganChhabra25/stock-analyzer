"""
User manager — persistent CRUD for allowed users.

Users are stored in data/users.json (created on first write).
If the file doesn't exist, config.py values are used as seed data.

Schema:
  [
    {"email": "foo@gmail.com", "is_admin": false},
    ...
  ]
"""

import json
import re
import threading
from pathlib import Path

_USERS_FILE = Path(__file__).parent.parent / "data" / "users.json"
_lock = threading.Lock()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _load_raw() -> list:
    """Return list of user dicts from file, or seed from config if file absent."""
    if _USERS_FILE.exists():
        try:
            return json.loads(_USERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    # Seed from config.py
    from config import ALLOWED_EMAILS, ADMIN_EMAILS
    return [
        {"email": e, "is_admin": e in ADMIN_EMAILS}
        for e in ALLOWED_EMAILS
    ]


def _save(users: list) -> None:
    _USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _USERS_FILE.write_text(json.dumps(users, indent=2), encoding="utf-8")


def list_users() -> list:
    with _lock:
        return _load_raw()


def get_allowed_emails() -> set:
    return {u["email"] for u in list_users()}


def get_admin_emails() -> set:
    return {u["email"] for u in list_users() if u.get("is_admin")}


def add_user(email: str, is_admin: bool = False) -> tuple[bool, str]:
    """Add a new user. Returns (ok, message)."""
    email = email.strip().lower()
    if not _EMAIL_RE.match(email):
        return False, "Invalid email address."
    with _lock:
        users = _load_raw()
        if any(u["email"] == email for u in users):
            return False, "User already exists."
        users.append({"email": email, "is_admin": bool(is_admin)})
        _save(users)
    return True, "User added."


def update_user(email: str, is_admin: bool) -> tuple[bool, str]:
    """Update admin status of an existing user. Returns (ok, message)."""
    email = email.strip().lower()
    with _lock:
        users = _load_raw()
        for u in users:
            if u["email"] == email:
                u["is_admin"] = bool(is_admin)
                _save(users)
                return True, "User updated."
    return False, "User not found."


def delete_user(email: str) -> tuple[bool, str]:
    """Remove a user. Returns (ok, message)."""
    email = email.strip().lower()
    with _lock:
        users = _load_raw()
        new = [u for u in users if u["email"] != email]
        if len(new) == len(users):
            return False, "User not found."
        _save(new)
    return True, "User deleted."
