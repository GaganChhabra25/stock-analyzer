"""
MFApiProvider — fetches mutual fund NAV history from mfapi.in.

Implements MFDataProvider with an in-memory TTL cache so repeated calls
within the same session don't hit the network unnecessarily.

Cache is instance-level (not module-level global) to avoid stale data
persisting across multiple StockScreener / MFAnalyzer instantiations.
"""

import logging
import time
from typing import Dict, Optional, Tuple

import requests

from core.interfaces import MFDataProvider
from core.exceptions import DataFetchError

logger = logging.getLogger(__name__)

MFAPI_BASE    = "https://api.mfapi.in/mf"
CACHE_TTL_SEC = 3600   # 1 hour


class MFApiProvider(MFDataProvider):
    """
    Fetches NAV history from mfapi.in with a TTL-aware in-memory cache.

    Cache entries expire after CACHE_TTL_SEC seconds. Once expired, the
    next call re-fetches and replaces the stale entry.
    """

    def __init__(self, cache_ttl: int = CACHE_TTL_SEC):
        self._cache: Dict[int, Tuple[Dict, float]] = {}  # scheme_code → (data, timestamp)
        self._ttl = cache_ttl

    # ── Public ────────────────────────────────────────────────────────────────

    def fetch_nav_history(self, scheme_code: int) -> Optional[Dict]:
        """
        Returns the mfapi.in response dict or None on failure.

        Raises DataFetchError only on HTTP errors; returns None for
        "no data" responses so callers can fall back to category advice.
        """
        cached = self._from_cache(scheme_code)
        if cached is not None:
            return cached

        try:
            resp = requests.get(f"{MFAPI_BASE}/{scheme_code}", timeout=10)
            resp.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            logger.warning("mfapi.in HTTP error for scheme %s: %s", scheme_code, exc)
            raise DataFetchError(f"HTTP error fetching scheme {scheme_code}: {exc}") from exc
        except requests.exceptions.RequestException as exc:
            logger.warning("mfapi.in network error for scheme %s: %s", scheme_code, exc)
            return None   # Treat network failures as "unavailable" (not fatal)

        try:
            data = resp.json()
        except ValueError as exc:
            logger.warning("mfapi.in returned non-JSON for scheme %s: %s", scheme_code, exc)
            return None

        if data.get("status") != "SUCCESS":
            logger.info("mfapi.in returned non-SUCCESS status for scheme %s", scheme_code)
            return None

        self._to_cache(scheme_code, data)
        logger.debug("Fetched NAV history for scheme %s (%d entries)",
                     scheme_code, len(data.get("data", [])))
        return data

    def clear_cache(self) -> None:
        """Remove all cached entries (useful in tests or long-running servers)."""
        self._cache.clear()

    # ── Cache helpers ─────────────────────────────────────────────────────────

    def _from_cache(self, scheme_code: int) -> Optional[Dict]:
        entry = self._cache.get(scheme_code)
        if entry is None:
            return None
        data, ts = entry
        if time.time() - ts > self._ttl:
            del self._cache[scheme_code]
            logger.debug("Cache expired for scheme %s", scheme_code)
            return None
        return data

    def _to_cache(self, scheme_code: int, data: Dict) -> None:
        self._cache[scheme_code] = (data, time.time())
