"""
Centralised formatting helpers — single source of truth.

Replaces duplicated _cr(), _pct(), _action_badge() scattered across
report_generator.py, html_report.py, and screener/report.py.

Two flavours:
  - Plain text  (console / text-file reports)
  - HTML        (badge / table cell variants)
"""

from typing import Optional

from core.constants import CRORE, LAKH


# ── Currency ──────────────────────────────────────────────────────────────────

def format_currency(val, symbol: str = "₹", html: bool = False) -> str:
    """
    Format a numeric value as an Indian currency string.

    Examples:
        1_20_00_000  → "₹1.20 Cr"
        3_50_000     → "₹3.50 L"
        12_500       → "₹12,500"

    Args:
        val:    Number to format (int, float, or string-coercible).
        symbol: Currency symbol prefix ("₹" default; use "Rs." for plain text).
        html:   If True, use "&nbsp;" non-breaking space before unit suffix.
    """
    try:
        val = float(val)
    except (TypeError, ValueError):
        return "N/A"

    nbsp = "&nbsp;" if html else " "

    if val >= CRORE:
        return f"{symbol}{val / CRORE:.2f}{nbsp}Cr"
    if val >= LAKH:
        return f"{symbol}{val / LAKH:.2f}{nbsp}L"
    return f"{symbol}{val:,.0f}"


def format_currency_plain(val) -> str:
    """Shorthand for console/text-file output (uses 'Rs.' prefix, plain space)."""
    return format_currency(val, symbol="Rs.", html=False)


def format_currency_html(val) -> str:
    """Shorthand for HTML output (uses '₹' prefix, &nbsp;)."""
    return format_currency(val, symbol="₹", html=True)


# ── Percentages ───────────────────────────────────────────────────────────────

def format_pct(val: Optional[float], plus: bool = True) -> str:
    """
    Format a percentage value.

    Args:
        val:  Percentage as float (e.g., 12.5 for 12.5%).
        plus: If True, prefix positive values with "+".

    Returns "—" for None values.
    """
    if val is None:
        return "—"
    sign = "+" if (plus and val >= 0) else ""
    return f"{sign}{val:.1f}%"


# ── Actions — text ────────────────────────────────────────────────────────────

def format_action_text(action: str) -> str:
    """
    Return an ANSI-colored action string for console output.
    Requires colorama to be initialised by the caller.
    """
    try:
        from colorama import Fore, Style
    except ImportError:
        return action

    a = action.upper().strip()
    if a == "EXIT":
        return Fore.RED    + action + Style.RESET_ALL
    if a == "ADD MORE":
        return Fore.GREEN  + action + Style.RESET_ALL
    return     Fore.CYAN   + action + Style.RESET_ALL


def format_pnl_text(val: Optional[float]) -> str:
    """Return an ANSI-colored P&L percentage string for console output."""
    if val is None:
        return "N/A"
    try:
        from colorama import Fore, Style
        s = f"{val:+.1f}%"
        return (Fore.GREEN + s + Style.RESET_ALL) if val >= 0 else (Fore.RED + s + Style.RESET_ALL)
    except ImportError:
        return f"{val:+.1f}%"


# ── Actions — HTML ────────────────────────────────────────────────────────────

def format_action_badge(action: str) -> str:
    """Return an HTML <span> badge for the given action."""
    a = action.upper().strip()
    if a == "EXIT":
        return '<span class="badge sell">&#10005; EXIT</span>'
    if a == "ADD MORE":
        return '<span class="badge add">&#8593; ADD MORE</span>'
    return '<span class="badge hold">&#9679; HOLD</span>'


# ── Score ring — HTML ─────────────────────────────────────────────────────────

def format_score_ring(score: Optional[float], is_etf: bool = False) -> str:
    """Return an HTML score ring div. ETFs show 'ETF' instead of a number."""
    if score is None or is_etf:
        return '<div class="score-ring etf"><span>ETF</span></div>'

    s = float(score)
    from core.constants import SCORE_GREAT, SCORE_GOOD, SCORE_WARN, SCORE_BAD
    if s >= SCORE_GREAT:   cls = "great"
    elif s >= SCORE_GOOD:  cls = "good"
    elif s >= SCORE_WARN:  cls = "warn"
    elif s >= SCORE_BAD:   cls = "bad"
    else:                  cls = "ugly"
    return f'<div class="score-ring {cls}"><span>{s:.0f}</span></div>'


def format_pnl_cell(val: Optional[float]) -> str:
    """Return a complete HTML <td> cell with colour class for a P&L percentage."""
    if val is None:
        return "<td>—</td>"
    cls  = "pos" if val >= 0 else "neg"
    sign = "+" if val >= 0 else ""
    return f'<td class="{cls}">{sign}{val:.1f}%</td>'
