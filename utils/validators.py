"""
Input validation helpers.

Called after CSV parsing to catch invalid data early with informative
error messages, rather than letting bad values silently propagate into
scoring or projection calculations.

All functions raise InvalidHoldingsError on failure so callers can
decide whether to abort or log-and-skip individual rows.
"""

import re
from typing import Any

from core.exceptions import InvalidHoldingsError

# NSE symbols: 1–10 alphanumeric characters (allows & for M&M, etc.)
_SYMBOL_RE = re.compile(r"^[A-Z0-9&]{1,10}$")


# ── Symbol ────────────────────────────────────────────────────────────────────

def validate_symbol(symbol: Any) -> str:
    """
    Normalise and validate an NSE stock symbol.

    Returns the uppercased, stripped symbol string.
    Raises InvalidHoldingsError if blank or contains invalid characters.
    """
    if not isinstance(symbol, str) or not symbol.strip():
        raise InvalidHoldingsError(f"Symbol must be a non-empty string, got {symbol!r}")

    sym = symbol.strip().upper()
    if not _SYMBOL_RE.match(sym):
        raise InvalidHoldingsError(
            f"Symbol {sym!r} contains invalid characters. "
            "Expected 1-10 alphanumeric characters (e.g. INFY, M&M, NIFTYBEES)."
        )
    return sym


# ── Numeric fields ────────────────────────────────────────────────────────────

def validate_positive_float(value: Any, field_name: str) -> float:
    """
    Validate that *value* is a finite, non-negative float.

    Returns the float value.
    Raises InvalidHoldingsError if value is missing, non-numeric, or negative.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise InvalidHoldingsError(
            f"Field '{field_name}' must be numeric, got {value!r}"
        )
    if v < 0:
        raise InvalidHoldingsError(
            f"Field '{field_name}' must be >= 0, got {v}"
        )
    return v


def validate_scheme_code(code: Any) -> int:
    """
    Validate a mutual fund scheme code.

    Returns the integer code.
    Raises InvalidHoldingsError if code is not a positive integer.
    """
    try:
        c = int(float(code))
    except (TypeError, ValueError):
        raise InvalidHoldingsError(
            f"Scheme code must be an integer, got {code!r}"
        )
    if c <= 0:
        raise InvalidHoldingsError(
            f"Scheme code must be a positive integer, got {c}. "
            "Use find_scheme.py to look up the correct code."
        )
    return c


# ── Holdings row validators ───────────────────────────────────────────────────

def validate_holdings_row(row: dict) -> None:
    """
    Validate a single parsed row from zerodha_holdings.csv.

    Does NOT raise — logs a warning and returns so the caller can
    decide whether to skip the row or proceed with caution.
    """
    import logging
    log = logging.getLogger(__name__)

    try:
        validate_symbol(row.get("Symbol", ""))
    except InvalidHoldingsError as e:
        log.warning("Holdings row skipped — %s", e)
        return

    for field in ("Quantity", "Avg_Cost"):
        try:
            validate_positive_float(row.get(field), field)
        except InvalidHoldingsError as e:
            log.warning("Holdings row %s — %s", row.get("Symbol"), e)


def validate_mf_row(row: dict) -> None:
    """
    Validate a single parsed row from mf_holdings.csv.

    Logs warnings instead of raising so a single bad row does not
    abort the entire MF analysis.
    """
    import logging
    log = logging.getLogger(__name__)

    for field in ("Total_Units", "Avg_Purchase_NAV", "Monthly_SIP"):
        try:
            validate_positive_float(row.get(field), field)
        except InvalidHoldingsError as e:
            log.warning("MF row '%s' — %s", row.get("Fund_Name", "?"), e)

    if row.get("Scheme_Code", 0):
        try:
            validate_scheme_code(row.get("Scheme_Code"))
        except InvalidHoldingsError as e:
            log.warning("MF row '%s' — %s", row.get("Fund_Name", "?"), e)
