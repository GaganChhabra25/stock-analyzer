"""
Zerodha Kite Connect adapter  —  broker/zerodha.py
────────────────────────────────────────────────────
Uses the existing options.kite_auth.get_kite() for session management.
API key / secret / token lifecycle is handled by kite_auth — not repeated here.

All kiteconnect exceptions are caught and re-raised as BrokerError / OrderError
so the executor layer never sees SDK-specific exceptions.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import List, Optional

from .base import AuthError, BrokerBase, BrokerError, OrderError, PlacedOrder, Quote

logger = logging.getLogger(__name__)

# Spot index symbols as Zerodha exchange:symbol pairs
_INDEX_QUOTE_KEY = {
    "NIFTY":     "NSE:NIFTY 50",
    "BANKNIFTY": "NSE:NIFTY BANK",
    "CRUDEOIL":  "MCX:CRUDEOIL",
    "NATURALGAS": "MCX:NATURALGAS",
}


class ZerodhaAdapter(BrokerBase):
    """
    Broker adapter wrapping kiteconnect.KiteConnect.
    Constructed with an already-authenticated KiteConnect instance.
    """

    def __init__(self, kite):
        """
        kite: authenticated kiteconnect.KiteConnect instance
              (obtained via options.kite_auth.get_kite(user_id))
        """
        self._kite = kite

    @property
    def name(self) -> str:
        return "zerodha"

    # ── Auth ─────────────────────────────────────────────────────────────────────

    def is_authenticated(self) -> bool:
        try:
            self._kite.profile()
            return True
        except Exception:
            return False

    def get_login_url(self) -> str:
        return self._kite.login_url()

    # ── Market data ──────────────────────────────────────────────────────────────

    def get_spot(self, symbol: str) -> float:
        key = _INDEX_QUOTE_KEY.get(symbol.upper())
        if not key:
            raise BrokerError(f"Unknown symbol for spot: {symbol}")
        try:
            q = self._kite.quote([key])
            return float(q[key]["last_price"])
        except KeyError:
            raise BrokerError(f"No quote data for {symbol}")
        except Exception as exc:
            raise BrokerError(f"get_spot({symbol}): {exc}") from exc

    def get_option_chain(self, symbol: str, expiry: date) -> List[Quote]:
        """Fetch all option strikes for symbol+expiry via batch quote call."""
        try:
            insts = self._kite.instruments("NFO")
        except Exception as exc:
            raise BrokerError(f"instruments() failed: {exc}") from exc

        # Filter to relevant options
        relevant = [
            i for i in insts
            if i.get("name", "").upper() == symbol.upper()
            and i.get("expiry") == expiry
            and i.get("instrument_type") in ("CE", "PE")
        ]
        if not relevant:
            raise BrokerError(f"No instruments found for {symbol} expiry {expiry}")

        # Batch quote (Zerodha allows up to 500 per call)
        keys = [f"NFO:{i['tradingsymbol']}" for i in relevant]
        try:
            quotes_raw = self._kite.quote(keys)
        except Exception as exc:
            raise BrokerError(f"quote() batch failed: {exc}") from exc

        result: List[Quote] = []
        for inst in relevant:
            key = f"NFO:{inst['tradingsymbol']}"
            q = quotes_raw.get(key)
            if not q:
                continue
            result.append(Quote(
                trading_symbol=inst["tradingsymbol"],
                exchange="NFO",
                ltp=float(q.get("last_price", 0)),
                oi=int(q.get("oi", 0)),
                strike=int(inst["strike"]),
                option_type=inst["instrument_type"],   # "CE" | "PE"
                expiry=expiry,
            ))
        return result

    def get_ltp(self, trading_symbol: str, exchange: str = "NFO") -> float:
        key = f"{exchange}:{trading_symbol}"
        try:
            q = self._kite.quote([key])
            return float(q[key]["last_price"])
        except KeyError:
            raise BrokerError(f"No LTP data for {trading_symbol}")
        except Exception as exc:
            raise BrokerError(f"get_ltp({trading_symbol}): {exc}") from exc

    def get_nearest_expiry(self, symbol: str) -> date:
        """Return the nearest weekly/monthly expiry for symbol from today."""
        try:
            insts = self._kite.instruments("NFO")
        except Exception as exc:
            raise BrokerError(f"instruments() failed: {exc}") from exc

        today = date.today()
        expiries = sorted({
            i["expiry"] for i in insts
            if i.get("name", "").upper() == symbol.upper()
            and i.get("expiry") and i["expiry"] >= today
        })
        if not expiries:
            raise BrokerError(f"No upcoming expiry found for {symbol}")
        return expiries[0]

    # ── Orders ───────────────────────────────────────────────────────────────────

    def place_market_order(
        self,
        trading_symbol: str,
        exchange: str,
        transaction_type: str,
        qty: int,
    ) -> PlacedOrder:
        from kiteconnect import KiteConnect
        try:
            order_id = self._kite.place_order(
                variety=KiteConnect.VARIETY_REGULAR,
                exchange=exchange,
                tradingsymbol=trading_symbol,
                transaction_type=transaction_type,
                quantity=qty,
                product=KiteConnect.PRODUCT_MIS,
                order_type=KiteConnect.ORDER_TYPE_MARKET,
            )
            return PlacedOrder(
                order_id=str(order_id),
                trading_symbol=trading_symbol,
                transaction_type=transaction_type,
                qty=qty,
                status="PENDING",
            )
        except Exception as exc:
            raise OrderError(
                f"place_order({transaction_type} {qty} {trading_symbol}): {exc}"
            ) from exc

    def get_positions(self) -> List[dict]:
        try:
            return self._kite.positions().get("day", [])
        except Exception as exc:
            raise BrokerError(f"get_positions(): {exc}") from exc
