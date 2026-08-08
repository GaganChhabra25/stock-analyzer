"""
Synchronized WTI + USDINR per-second reference collector.

The collector uses a dedicated Twelve Data WebSocket and database connection.
It can run standalone or as an isolated daemon worker inside crudeoil_ws.py;
provider, network, or database failures here cannot interrupt MCX collection.

Required environment:
  TWELVE_DATA_API_KEY

Optional environment:
  TWELVE_DATA_WTI_SYMBOL=WTI/USD
  TWELVE_DATA_USDINR_SYMBOL=USD/INR
  GLOBAL_REFERENCE_MAX_AGE_SECONDS=10

Standalone run:
  python options/global_reference_ws.py
"""

import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, time as clock_time, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from logging_config import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
WS_URL = "wss://ws.twelvedata.com/v1/quotes/price"
PROVIDER = "TWELVEDATA"

WTI_SOURCE_SYMBOL = os.environ.get("TWELVE_DATA_WTI_SYMBOL", "WTI/USD").strip()
USDINR_SOURCE_SYMBOL = os.environ.get("TWELVE_DATA_USDINR_SYMBOL", "USD/INR").strip()
try:
    MAX_AGE_SECONDS = max(
        1.0,
        float(os.environ.get("GLOBAL_REFERENCE_MAX_AGE_SECONDS", "10")),
    )
except ValueError:
    MAX_AGE_SECONDS = 10.0

_lock = threading.Lock()
_latest: dict[str, dict] = {}
_running = True


_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS global_reference_second (
        ts                  TIMESTAMPTZ PRIMARY KEY,
        wti_price           DOUBLE PRECISION NOT NULL,
        usdinr_price        DOUBLE PRECISION NOT NULL,
        theoretical_mcx     DOUBLE PRECISION NOT NULL,
        wti_source_ts       TIMESTAMPTZ NOT NULL,
        usdinr_source_ts    TIMESTAMPTZ NOT NULL,
        wti_received_at     TIMESTAMPTZ NOT NULL,
        usdinr_received_at  TIMESTAMPTZ NOT NULL,
        wti_age_ms          INTEGER NOT NULL,
        usdinr_age_ms       INTEGER NOT NULL,
        source              VARCHAR(30) NOT NULL,
        wti_source_symbol   VARCHAR(40) NOT NULL,
        usdinr_source_symbol VARCHAR(40) NOT NULL,
        available_at        TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
    );
    CREATE INDEX IF NOT EXISTS idx_global_reference_second_available
        ON global_reference_second (available_at DESC);
"""


def _collection_window_open(now: Optional[datetime] = None) -> bool:
    """Store only during the broad MCX research window."""
    current = (now or datetime.now(IST)).astimezone(IST)
    if current.weekday() >= 5:
        return False
    return clock_time(8, 45) <= current.time() <= clock_time(23, 35)


def _as_float(value):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _source_timestamp(value, received_at: datetime) -> datetime:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return received_at


def _canonical_symbol(provider_symbol: str) -> Optional[str]:
    normalized = str(provider_symbol or "").strip().upper()
    if normalized == WTI_SOURCE_SYMBOL.upper():
        return "WTI"
    if normalized == USDINR_SOURCE_SYMBOL.upper():
        return "USDINR"
    return None


def _handle_message(raw_message) -> None:
    try:
        payload = json.loads(raw_message)
    except (TypeError, json.JSONDecodeError):
        return

    if payload.get("event") != "price":
        if payload.get("status") == "error" or payload.get("event") == "error":
            logger.error("[GLOBAL-REF] Provider error: %s", payload)
        return

    canonical = _canonical_symbol(payload.get("symbol"))
    price = _as_float(payload.get("price"))
    if canonical is None or price is None:
        return

    received_at = datetime.now(timezone.utc)
    point = {
        "price": price,
        "source_ts": _source_timestamp(payload.get("timestamp"), received_at),
        "received_at": received_at,
    }
    with _lock:
        _latest[canonical] = point


def _build_snapshot(sample_ts: datetime, observed_at: datetime) -> Optional[dict]:
    with _lock:
        wti = dict(_latest.get("WTI") or {})
        usdinr = dict(_latest.get("USDINR") or {})

    if not wti or not usdinr:
        return None

    wti_age = max(0.0, (observed_at - wti["source_ts"]).total_seconds())
    usdinr_age = max(0.0, (observed_at - usdinr["source_ts"]).total_seconds())
    if wti_age > MAX_AGE_SECONDS or usdinr_age > MAX_AGE_SECONDS:
        return None

    return {
        "ts": sample_ts,
        "wti_price": wti["price"],
        "usdinr_price": usdinr["price"],
        "theoretical_mcx": wti["price"] * usdinr["price"],
        "wti_source_ts": wti["source_ts"],
        "usdinr_source_ts": usdinr["source_ts"],
        "wti_received_at": wti["received_at"],
        "usdinr_received_at": usdinr["received_at"],
        "wti_age_ms": round(wti_age * 1000),
        "usdinr_age_ms": round(usdinr_age * 1000),
        "source": PROVIDER,
        "wti_source_symbol": WTI_SOURCE_SYMBOL,
        "usdinr_source_symbol": USDINR_SOURCE_SYMBOL,
    }


def _write_snapshot(conn, row: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO global_reference_second (
                ts, wti_price, usdinr_price, theoretical_mcx,
                wti_source_ts, usdinr_source_ts,
                wti_received_at, usdinr_received_at,
                wti_age_ms, usdinr_age_ms, source,
                wti_source_symbol, usdinr_source_symbol
            ) VALUES (
                %(ts)s, %(wti_price)s, %(usdinr_price)s, %(theoretical_mcx)s,
                %(wti_source_ts)s, %(usdinr_source_ts)s,
                %(wti_received_at)s, %(usdinr_received_at)s,
                %(wti_age_ms)s, %(usdinr_age_ms)s, %(source)s,
                %(wti_source_symbol)s, %(usdinr_source_symbol)s
            )
            ON CONFLICT (ts) DO NOTHING
            """,
            row,
        )
    conn.commit()


def _flush_loop() -> None:
    """Persist one causal, synchronized snapshot per completed second."""
    conn = None
    schema_ready = False
    last_second = datetime.now(timezone.utc).replace(microsecond=0)

    while _running:
        time.sleep(0.1)
        observed_at = datetime.now(timezone.utc)
        current_second = observed_at.replace(microsecond=0)
        if current_second == last_second:
            continue

        sample_ts = last_second
        last_second = current_second
        if not _collection_window_open(observed_at):
            continue

        row = _build_snapshot(sample_ts, observed_at)
        if row is None:
            continue

        try:
            if conn is None or conn.closed:
                import psycopg2

                conn = psycopg2.connect(
                    os.environ.get("DATABASE_URL", ""),
                    connect_timeout=5,
                    application_name="global_reference_second_writer",
                )
                conn.autocommit = False
                schema_ready = False
            if not schema_ready:
                with conn.cursor() as cur:
                    cur.execute(_TABLE_SQL)
                conn.commit()
                schema_ready = True
                logger.info("[GLOBAL-REF] Table ready; synchronized writer active.")
            _write_snapshot(conn, row)
        except Exception as exc:
            logger.error("[GLOBAL-REF] Snapshot write failed; feed continues: %s", exc)
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
            conn = None
            schema_ready = False


def main() -> None:
    api_key = os.environ.get("TWELVE_DATA_API_KEY", "").strip()
    if not api_key:
        logger.error("[GLOBAL-REF] TWELVE_DATA_API_KEY is missing; collector disabled.")
        return

    try:
        import websocket
    except ImportError:
        logger.error("[GLOBAL-REF] websocket-client is not installed; collector disabled.")
        return

    threading.Thread(
        target=_flush_loop,
        daemon=True,
        name="global-reference-flusher",
    ).start()

    reconnect_delay = 5
    url = f"{WS_URL}?apikey={api_key}"

    while True:
        def on_open(ws):
            nonlocal reconnect_delay
            reconnect_delay = 5
            message = {
                "action": "subscribe",
                "params": {"symbols": f"{WTI_SOURCE_SYMBOL},{USDINR_SOURCE_SYMBOL}"},
            }
            ws.send(json.dumps(message))
            logger.info(
                "[GLOBAL-REF] Connected; subscribed to %s and %s.",
                WTI_SOURCE_SYMBOL,
                USDINR_SOURCE_SYMBOL,
            )

        def on_message(_ws, message):
            _handle_message(message)

        def on_error(_ws, error):
            logger.error("[GLOBAL-REF] WebSocket error: %s", error)

        def on_close(_ws, code, reason):
            logger.warning("[GLOBAL-REF] WebSocket closed (%s): %s", code, reason)

        client = websocket.WebSocketApp(
            url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        client.run_forever(ping_interval=20, ping_timeout=10)
        logger.warning("[GLOBAL-REF] Reconnecting in %d seconds.", reconnect_delay)
        time.sleep(reconnect_delay)
        reconnect_delay = min(60, reconnect_delay * 2)


if __name__ == "__main__":
    main()
