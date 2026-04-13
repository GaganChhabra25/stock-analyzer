"""
Strategy Registry
─────────────────
Single source of truth for all backtestable option strategies.
Each entry contains both UI metadata (description, rules) and
backtest parameters (entry/exit times, DTE filter, instruments).

To add a new strategy: add one entry to REGISTRY — nothing else changes.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class StrategyDef:
    key:           str
    display_name:  str
    badge:         str        # short label shown in UI chip
    badge_color:   str        # "blue" | "green" | "orange" | "purple"
    short_desc:    str        # one-liner shown in dropdown
    # ── Info panel content ─────────────────────────────────────────────────────
    what_it_does:  str
    entry_rule:    str
    exit_rule:     str
    sl_rule:       str
    best_when:     str
    avoid_when:    str
    # ── Backtest parameters ────────────────────────────────────────────────────
    entry_time:    str        # "09:20", "10:30", "09:30"
    exit_time:     str        # "15:10", "14:45", "13:30"
    dte_min:       int        # minimum days-to-expiry filter (inclusive)
    dte_max:       int        # 999 = no upper limit
    instruments:   List[str]
    has_wing:      bool       # show wing-width selector in UI?
    skip_high_vol: bool       # skip days where previous-day range > threshold?


REGISTRY: dict = {
    "IRON_FLY": StrategyDef(
        key          = "IRON_FLY",
        display_name = "Iron Fly",
        badge        = "Classic",
        badge_color  = "blue",
        short_desc   = "ATM straddle sell + OTM wings. Entry 9:20 AM.",
        what_it_does = (
            "Sell ATM Call and ATM Put at 9:20 AM to collect maximum premium. "
            "Buy OTM Call (ATM + wing) and OTM Put (ATM − wing) as protection. "
            "Net credit collected = (ATM CE + ATM PE) − (wing CE + wing PE). "
            "Hold all 4 legs until 3:10 PM and close at market."
        ),
        entry_rule   = "9:20 AM — sell ATM CE & PE, buy CE+wing & PE−wing",
        exit_rule    = "3:10 PM hard time exit on all 4 legs",
        sl_rule      = "If exit net ≥ 2× entry net → cap loss at 1× net premium",
        best_when    = "Low VIX (12–18), range-bound day expected, straddle premium > 150 pts",
        avoid_when   = "Event days, gap opens > 150 pts, trending / trending market",
        entry_time   = "09:20",
        exit_time    = "15:10",
        dte_min      = 0,
        dte_max      = 999,
        instruments  = ["NIFTY", "BANKNIFTY"],
        has_wing     = True,
        skip_high_vol= False,
    ),

    "OI_STRANGLE": StrategyDef(
        key          = "OI_STRANGLE",
        display_name = "OI-Anchored Strangle",
        badge        = "Expiry Week",
        badge_color  = "green",
        short_desc   = "Sell at max-OI resistance/support. Entry 10:30 AM.",
        what_it_does = (
            "Wait for morning volatility to settle. At 10:30 AM, scan Open Interest — "
            "the strike with highest CE OI above spot is where Call writers have built a "
            "resistance wall; the strike with highest PE OI below spot is Put writers' support. "
            "Sell CE at resistance and PE at support. "
            "Market participants with large OI positions actively defend these levels, "
            "making them natural magnets for price. "
            "Close at 2:45 PM or immediately when 50% of premium is captured."
        ),
        entry_rule   = "10:30 AM — sell highest-OI CE above spot + highest-OI PE below spot",
        exit_rule    = "50% profit target hit → exit; else 2:45 PM hard exit",
        sl_rule      = "Either sold leg doubles in value → exit both legs immediately",
        best_when    = (
            "Expiry week (DTE 0–5), market opened flat (<100 pts gap), "
            "previous day range < 350 pts, clear OI walls visible"
        ),
        avoid_when   = (
            "RBI / Budget / major macro event, previous day range > 400 pts, "
            "large gap-up or gap-down at open, BANKNIFTY"
        ),
        entry_time   = "10:30",
        exit_time    = "14:45",
        dte_min      = 0,
        dte_max      = 5,
        instruments  = ["NIFTY"],
        has_wing     = False,
        skip_high_vol= True,
    ),

    "ZERO_DTE_STRADDLE": StrategyDef(
        key          = "ZERO_DTE_STRADDLE",
        display_name = "0DTE Straddle",
        badge        = "Expiry Day",
        badge_color  = "orange",
        short_desc   = "ATM straddle only on expiry day. Maximum theta.",
        what_it_does = (
            "On expiry day (Thursday for NIFTY weekly), sell the ATM straddle at 9:30 AM. "
            "With zero days to expiry all options decay to intrinsic value by 3:30 PM. "
            "The entire time value premium you collect is yours if the market stays near ATM. "
            "Exit by 1:30 PM to avoid the afternoon directional risk that often spikes "
            "as expiry approaches and options delta approaches 0 or 1."
        ),
        entry_rule   = "9:30 AM on expiry day (DTE = 0) only — sell ATM CE & PE",
        exit_rule    = "50% profit target OR 1:30 PM hard exit, whichever comes first",
        sl_rule      = "Spot moves > 150 pts from ATM strike → exit both legs",
        best_when    = "Expiry Thursday, flat open (<80 pts gap), no known event, VIX < 20",
        avoid_when   = "Any non-expiry day, large gap-up/down, RBI or major event day",
        entry_time   = "09:30",
        exit_time    = "13:30",
        dte_min      = 0,
        dte_max      = 0,
        instruments  = ["NIFTY"],
        has_wing     = False,
        skip_high_vol= False,
    ),
}


def get(key: str) -> Optional[StrategyDef]:
    """Return StrategyDef for key (case-insensitive). None if unknown."""
    return REGISTRY.get(key.upper())


def all_keys() -> List[str]:
    return list(REGISTRY.keys())


def as_json_list() -> list:
    """Serialise registry for embedding in HTML templates."""
    return [
        {
            "key":           s.key,
            "display_name":  s.display_name,
            "badge":         s.badge,
            "badge_color":   s.badge_color,
            "short_desc":    s.short_desc,
            "what_it_does":  s.what_it_does,
            "entry_rule":    s.entry_rule,
            "exit_rule":     s.exit_rule,
            "sl_rule":       s.sl_rule,
            "best_when":     s.best_when,
            "avoid_when":    s.avoid_when,
            "entry_time":    s.entry_time,
            "exit_time":     s.exit_time,
            "dte_min":       s.dte_min,
            "dte_max":       s.dte_max,
            "instruments":   s.instruments,
            "has_wing":      s.has_wing,
            "skip_high_vol": s.skip_high_vol,
        }
        for s in REGISTRY.values()
    ]
