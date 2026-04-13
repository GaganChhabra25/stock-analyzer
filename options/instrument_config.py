"""
Instrument Configuration Registry
──────────────────────────────────
Single source of truth for all tradeable instruments.

Adding a new instrument = add one entry to REGISTRY.
Nothing else needs to change.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(frozen=True)
class InstrumentConfig:
    symbol:        str          # DB key, e.g. "NIFTY"
    display_name:  str          # Human label, e.g. "Nifty 50"
    exchange:      str          # "NFO" | "MCX"
    lot_size:      int          # units per lot
    strike_step:   int          # minimum strike interval
    default_wing:  int          # default Iron Fly wing width
    market_hours:  str          # human-readable market hours (IST)
    wing_choices:  List[int]    # valid wing sizes offered in UI


# ── Registry ────────────────────────────────────────────────────────────────────

REGISTRY: Dict[str, InstrumentConfig] = {
    "NIFTY": InstrumentConfig(
        symbol       = "NIFTY",
        display_name = "Nifty 50",
        exchange     = "NFO",
        lot_size     = 75,
        strike_step  = 50,
        default_wing = 200,
        market_hours = "9:15 AM – 3:30 PM IST",
        wing_choices = [100, 150, 200, 250, 300],
    ),
    "BANKNIFTY": InstrumentConfig(
        symbol       = "BANKNIFTY",
        display_name = "Bank Nifty",
        exchange     = "NFO",
        lot_size     = 15,
        strike_step  = 100,
        default_wing = 400,
        market_hours = "9:15 AM – 3:30 PM IST",
        wing_choices = [200, 300, 400, 500, 600],
    ),
    "CRUDEOIL": InstrumentConfig(
        symbol       = "CRUDEOIL",
        display_name = "Crude Oil",
        exchange     = "MCX",
        lot_size     = 100,
        strike_step  = 50,
        default_wing = 100,
        market_hours = "9:00 AM – 11:30 PM IST",
        wing_choices = [50, 100, 150, 200, 250],
    ),
    "NATURALGAS": InstrumentConfig(
        symbol       = "NATURALGAS",
        display_name = "Natural Gas",
        exchange     = "MCX",
        lot_size     = 1250,
        strike_step  = 5,
        default_wing = 20,
        market_hours = "9:00 AM – 11:30 PM IST",
        wing_choices = [10, 15, 20, 25, 30],
    ),
}


def get(symbol: str) -> Optional[InstrumentConfig]:
    """Return config for symbol (case-insensitive). None if unknown."""
    return REGISTRY.get(symbol.upper())


def all_symbols() -> List[str]:
    return list(REGISTRY.keys())


def as_json_list() -> list:
    """Serialise registry for embedding in HTML templates."""
    return [
        {
            "symbol":       cfg.symbol,
            "display_name": cfg.display_name,
            "exchange":     cfg.exchange,
            "lot_size":     cfg.lot_size,
            "strike_step":  cfg.strike_step,
            "default_wing": cfg.default_wing,
            "market_hours": cfg.market_hours,
            "wing_choices": cfg.wing_choices,
        }
        for cfg in REGISTRY.values()
    ]
