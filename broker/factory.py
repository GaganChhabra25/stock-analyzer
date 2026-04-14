"""
Broker factory  —  broker/factory.py
──────────────────────────────────────
Single seam for broker substitution.

To add a new broker (e.g. Upstox):
  1. Create broker/upstox.py implementing BrokerBase
  2. Add one elif below
  Nothing else in the codebase needs to change.
"""

from __future__ import annotations

import logging
from typing import Optional

from .base import AuthError, BrokerBase, BrokerError

logger = logging.getLogger(__name__)

# Default broker name — can be overridden per-deployment
DEFAULT_BROKER = "zerodha"


def get_broker(broker_name: str, user_id: str) -> BrokerBase:
    """
    Return an authenticated BrokerBase adapter.

    Args:
        broker_name: lowercase broker id, e.g. 'zerodha'
        user_id:     Zerodha user_id (or equivalent) for token lookup

    Raises:
        AuthError:   if no valid token found for the user
        BrokerError: if the broker name is unknown
    """
    name = broker_name.lower().strip()

    if name == "zerodha":
        from options.kite_auth import get_kite
        kite = get_kite(user_id)
        if kite is None:
            raise AuthError(
                f"No valid Zerodha session for user '{user_id}'. "
                "Please login via /kite/login first."
            )
        from .zerodha import ZerodhaAdapter
        return ZerodhaAdapter(kite)

    # ── Add future brokers here ─────────────────────────────────────────────────
    # elif name == "upstox":
    #     from .upstox import UpstoxAdapter
    #     ...

    raise BrokerError(f"Unknown broker: '{broker_name}'")
