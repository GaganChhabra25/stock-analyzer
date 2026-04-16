"""
Broker abstraction layer  —  base.py
──────────────────────────────────────
All broker adapters implement BrokerBase.
StrategyExecutor uses only BrokerBase — no broker-specific imports outside adapters.

To add a new broker:
  1. Create broker/<name>.py implementing BrokerBase
  2. Add one elif in broker/factory.py
  Nothing else changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple


# ── Exceptions ─────────────────────────────────────────────────────────────────

class BrokerError(Exception):
    """Raised by any broker adapter for any trading/auth error."""


class AuthError(BrokerError):
    """Access token missing or expired."""


class OrderError(BrokerError):
    """Order placement or rejection error."""


# ── Value objects ──────────────────────────────────────────────────────────────

@dataclass
class Quote:
    trading_symbol: str          # broker's native symbol e.g. "NIFTY2441724500CE"
    exchange:       str          # "NFO" | "NSE" | "MCX"
    ltp:            float        # last traded price
    oi:             int          # open interest (0 if not applicable)
    strike:         Optional[int]   = None
    option_type:    Optional[str]   = None   # "CE" | "PE"
    expiry:         Optional[date]  = None
    underlying_ltp: Optional[float] = None


@dataclass
class PlacedOrder:
    order_id:        str
    trading_symbol:  str
    transaction_type: str        # "BUY" | "SELL"
    qty:             int
    status:          str         # "COMPLETE" | "PENDING" | "REJECTED"
    avg_price:       float = 0.0
    message:         str   = ""


@dataclass
class LegSpec:
    """Describes one leg of a deployed strategy (live state)."""
    deployment_id:   int
    leg_index:       int
    trading_symbol:  str
    exchange:        str
    direction:       str         # "SELL" | "BUY"
    strike:          int
    option_type:     str         # "CE" | "PE"
    lots:            int
    lot_size:        int
    entry_price:     float
    sl_price:        float       # exit if LTP crosses this
    expiry:          Optional[date] = None
    order_id:        str  = ""
    db_id:           int  = 0

    @property
    def qty(self) -> int:
        return self.lots * self.lot_size

    @property
    def close_direction(self) -> str:
        return "BUY" if self.direction == "SELL" else "SELL"


# ── Abstract base ──────────────────────────────────────────────────────────────

class BrokerBase(ABC):
    """
    Abstract broker interface.

    All methods must be implemented by concrete adapters.
    The executor and scheduler import only this class.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Lowercase broker identifier e.g. 'zerodha'."""

    # ── Auth ─────────────────────────────────────────────────────────────────────

    @abstractmethod
    def is_authenticated(self) -> bool:
        """Return True if the session token is valid right now."""

    @abstractmethod
    def get_login_url(self) -> str:
        """Return the OAuth URL for user to authenticate (open in browser)."""

    # ── Market data ──────────────────────────────────────────────────────────────

    @abstractmethod
    def get_spot(self, symbol: str) -> float:
        """Return current spot price of the index/stock.
        symbol: instrument name e.g. 'NIFTY', 'BANKNIFTY'
        Raises BrokerError on failure.
        """

    @abstractmethod
    def get_option_chain(self, symbol: str, expiry: date) -> List[Quote]:
        """Return quotes with LTP + OI for all strikes of symbol at expiry.
        Used by OI_STRANGLE to find max-OI walls.
        Raises BrokerError on failure.
        """

    @abstractmethod
    def get_ltp(self, trading_symbol: str, exchange: str) -> float:
        """Return current LTP for a specific trading symbol.
        Raises BrokerError on failure.
        """

    @abstractmethod
    def get_nearest_expiry(self, symbol: str) -> date:
        """Return the nearest upcoming expiry date for the symbol.
        Raises BrokerError if not determinable.
        """

    # ── Orders ───────────────────────────────────────────────────────────────────

    @abstractmethod
    def place_market_order(
        self,
        trading_symbol: str,
        exchange:        str,
        transaction_type: str,    # "BUY" | "SELL"
        qty:             int,
    ) -> PlacedOrder:
        """Place an intraday market order.
        Returns PlacedOrder with order_id.
        Raises OrderError on rejection.
        """

    @abstractmethod
    def get_positions(self) -> List[dict]:
        """Return list of current intraday net positions."""
