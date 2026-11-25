"""Pause/resume flags for data collectors, set from the trade-bot Server page.

One row per collector in `collector_control`. Collectors call is_paused(id)
and skip their work while it is true. Fail-open: any DB problem (table not
created yet, DB down) reads as NOT paused, so this switch can never stop
collection by accident.

Collector ids (keep in sync with trade-bot app/api/server_router.py):
  crude_1s       crudeoil-ws daemon (CRUDEOIL futures 1-sec, depth, option pressure)
  gold_1s        gold-ws daemon (GOLD futures 1-sec)
  crude_options  cron options/collector.py (CRUDEOIL option chain)
  global_prices  cron options/global_prices.py + global_prices_intraday.py
  us_market      cron options/us_market.py (daily + intraday)
  crude_features_v2  standalone crude-features-v2 container (lives in trade-bot repo)
"""

import logging
import os
import time

logger = logging.getLogger(__name__)

TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS collector_control (
        collector_id TEXT PRIMARY KEY,
        paused       BOOLEAN NOT NULL DEFAULT FALSE,
        updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_by   TEXT
    )
"""

_CACHE_SECONDS = 15
_cache: dict = {}


def is_paused(collector_id: str) -> bool:
    now = time.monotonic()
    hit = _cache.get(collector_id)
    if hit and now - hit[0] < _CACHE_SECONDS:
        return hit[1]
    paused = False
    try:
        import psycopg2
        conn = psycopg2.connect(os.environ.get("DATABASE_URL", ""), connect_timeout=5)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT paused FROM collector_control WHERE collector_id = %s",
                            (collector_id,))
                row = cur.fetchone()
                paused = bool(row and row[0])
        finally:
            conn.close()
    except Exception as exc:  # fail-open
        logger.debug("collector_control read failed for %s (treated as running): %s",
                     collector_id, exc)
    _cache[collector_id] = (now, paused)
    return paused
