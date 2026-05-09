"""
CRUDEOIL 1-second OHLC collector — Kite WebSocket daemon.

Subscribes to near-month CRUDEOIL futures tick stream via Kite WebSocket.
Aggregates ticks into 1-second OHLC bars → mcx_ohlc (interval='second').

Runs as a persistent Docker service (restart: unless-stopped).
  - Active only during MCX hours (09:00–23:30 IST); sleeps otherwise.
  - Reconnects each morning automatically after kite_auto_login refreshes token.
  - No manual steps needed.

Architecture:
  Main thread  → KiteTicker WebSocket (receives ticks, updates _bar buffer)
  Flush thread → wakes every 100 ms, writes completed 1-sec bars to DB
"""

import logging
import os
import sys
import threading
import time as _time
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from kiteconnect import KiteTicker

from options.kite_auth import API_KEY, load_access_token
from options.mcx_instruments import load_mcx_instruments
from options.tg import once, send, db_size, table_rows, now_ist
from options.kite_auth import get_kite
from screener.db import _get_conn
from logging_config import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

IST    = ZoneInfo("Asia/Kolkata")
SYMBOL = "CRUDEOIL"

# ── Market hours ───────────────────────────────────────────────────────────────

def _mcx_open() -> bool:
    from config import MCX_HOLIDAYS, MCX_EVENING_ONLY_DAYS
    now   = datetime.now(IST)
    today = now.date().strftime("%Y-%m-%d")
    t     = now.time()
    if now.weekday() == 6:          # Sunday — MCX closed
        return False
    if today in MCX_HOLIDAYS:
        return False
    if today in MCX_EVENING_ONLY_DAYS:
        return time(17, 0) <= t <= time(23, 30)
    return time(9, 0) <= t <= time(23, 30)


def _seconds_until_open() -> int:
    """Seconds until next MCX open (9:00 AM IST next valid day)."""
    now  = datetime.now(IST)
    nxt  = now.replace(hour=9, minute=0, second=0, microsecond=0)
    if now.time() >= time(23, 30):      # past today's close — try tomorrow
        nxt += timedelta(days=1)
    if nxt <= now:
        nxt += timedelta(days=1)
    # Skip Sunday
    while nxt.weekday() == 6:
        nxt += timedelta(days=1)
    return max(60, int((nxt - now).total_seconds()))


# ── Instrument token ───────────────────────────────────────────────────────────

def _get_crudeoil_token(kite) -> int:
    df    = load_mcx_instruments(kite)
    today = date.today()
    fut   = df[(df["name"] == SYMBOL) & (df["instrument_type"] == "FUT")]
    near  = fut[fut["expiry"] >= today].sort_values("expiry")
    if near.empty:
        raise RuntimeError("No CRUDEOIL futures found in MCX instruments")
    row = near.iloc[0]
    logger.info("[CRUDE-WS] Token: %d  Contract: %s  Expiry: %s",
                int(row["instrument_token"]), row["tradingsymbol"], row["expiry"])
    return int(row["instrument_token"])


# ── 1-second bar buffer ────────────────────────────────────────────────────────

_lock = threading.Lock()
_bar: dict = {
    "ts":    None,   # datetime (second boundary) for this bar
    "open":  None,
    "high":  None,
    "low":   None,
    "close": None,
    "vol_cum": 0,    # cumulative volume_traded from Kite (day total)
    "oi":    0,
}
_prev_vol_cum: int = 0   # to compute per-second volume delta
_token_id: int    = 0
_running: bool    = True


def _on_ticks(ws, ticks):
    global _prev_vol_cum
    for tick in ticks:
        if int(tick.get("instrument_token", 0)) != _token_id:
            continue
        ltp = tick.get("last_price") or 0
        if not ltp:
            continue
        vol = tick.get("volume_traded") or 0
        oi  = tick.get("oi") or 0

        with _lock:
            if _bar["open"] is None:
                _bar["ts"]   = datetime.now(IST).replace(microsecond=0)
                _bar["open"] = ltp
            _bar["high"]    = max(_bar["high"] or ltp, ltp)
            _bar["low"]     = min(_bar["low"]  or ltp, ltp)
            _bar["close"]   = ltp
            _bar["vol_cum"] = vol
            _bar["oi"]      = oi


def _reset_bar():
    _bar.update({"ts": None, "open": None, "high": None,
                 "low": None, "close": None, "oi": 0})


# ── DB writer (flush thread) ───────────────────────────────────────────────────

def _write_bar(conn, ts, open_, high, low, close, volume, oi):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO mcx_ohlc
                (ts, instrument, interval, open, high, low, close, volume, oi)
            VALUES (%s, %s, 'second', %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ts, instrument, interval) DO UPDATE SET
                open   = EXCLUDED.open,
                high   = EXCLUDED.high,
                low    = EXCLUDED.low,
                close  = EXCLUDED.close,
                volume = EXCLUDED.volume,
                oi     = EXCLUDED.oi
        """, (ts, SYMBOL, open_, high, low, close, volume, oi))
    conn.commit()


def _flush_thread():
    """Runs in background. Writes completed 1-sec bars every time second changes."""
    global _prev_vol_cum, _running

    conn = None
    last_sec = None

    while _running:
        _time.sleep(0.1)

        try:
            # Lazy-connect (or reconnect after error)
            if conn is None or conn.closed:
                conn = _get_conn().__enter__()

            now         = datetime.now(IST)
            current_sec = now.replace(microsecond=0)

            if last_sec is None:
                last_sec = current_sec
                continue

            if current_sec == last_sec:
                continue

            # Second boundary crossed — flush last_sec's bar
            with _lock:
                if _bar["open"] is not None and _bar["ts"] is not None:
                    vol_delta = max(0, _bar["vol_cum"] - _prev_vol_cum)
                    _prev_vol_cum = _bar["vol_cum"]

                    snap = (
                        _bar["ts"], _bar["open"], _bar["high"],
                        _bar["low"], _bar["close"], vol_delta, _bar["oi"]
                    )
                    _reset_bar()

                else:
                    snap = None
                    # Keep _prev_vol_cum unchanged (no trade this second)

            if snap:
                ts, o, h, l, c, v, oi = snap
                _write_bar(conn, ts, o, h, l, c, v, oi)
                logger.debug("[CRUDE-WS] %s O=%.2f H=%.2f L=%.2f C=%.2f V=%d",
                             ts.strftime("%H:%M:%S"), o, h, l, c, v)

            last_sec = current_sec

        except Exception as exc:
            logger.error("[CRUDE-WS] Flush error: %s", exc)
            conn = None       # force reconnect on next iteration
            _time.sleep(1)


# ── Today's row count ──────────────────────────────────────────────────────────

def _today_rows() -> int:
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) FROM mcx_ohlc
                    WHERE instrument = 'CRUDEOIL'
                      AND interval   = 'second'
                      AND ts::date   = CURRENT_DATE
                """)
                return cur.fetchone()[0] or 0
    except Exception:
        return 0


# ── Main loop ──────────────────────────────────────────────────────────────────

def main():
    global _token_id, _running

    logger.info("[CRUDE-WS] CRUDEOIL 1-second WebSocket daemon starting.")

    # Start flush thread (runs for lifetime of process)
    flusher = threading.Thread(target=_flush_thread, daemon=True, name="bar-flusher")
    flusher.start()

    while True:
        # ── Sleep when MCX is closed ───────────────────────────────────────────
        if not _mcx_open():
            wait = _seconds_until_open()
            logger.info("[CRUDE-WS] MCX closed. Sleeping %d min.", wait // 60)
            _time.sleep(min(wait, 3600))
            continue

        # ── Get Kite credentials ───────────────────────────────────────────────
        kite = get_kite()
        if not kite:
            logger.warning("[CRUDE-WS] Kite not authorised — retry in 5 min.")
            _time.sleep(300)
            continue

        access_token = load_access_token(
            os.environ.get("KITE_ADMIN_USER_ID", "")
        )
        if not access_token or not API_KEY:
            logger.warning("[CRUDE-WS] Missing API_KEY or access_token — retry in 5 min.")
            _time.sleep(300)
            continue

        # ── Resolve near-month CRUDEOIL token ─────────────────────────────────
        try:
            _token_id = _get_crudeoil_token(kite)
        except Exception as exc:
            logger.error("[CRUDE-WS] Token resolution failed: %s — retry in 5 min.", exc)
            _time.sleep(300)
            continue

        # ── Build WebSocket ────────────────────────────────────────────────────
        kws = KiteTicker(API_KEY, access_token, reconnect=True)

        def on_connect(ws, _resp):
            logger.info("[CRUDE-WS] Connected — subscribing token %d", _token_id)
            ws.subscribe([_token_id])
            ws.set_mode(ws.MODE_FULL, [_token_id])
            once("crude_ws_start",
                 f"\U0001f7e2 CRUDEOIL 1-sec Started\n"
                 f"{now_ist()}\n"
                 f"Contract token: {_token_id}")

        def on_ticks(ws, ticks):
            _on_ticks(ws, ticks)

        def on_close(ws, code, reason):
            logger.warning("[CRUDE-WS] Connection closed (%s): %s", code, reason)

        def on_error(ws, code, reason):
            logger.error("[CRUDE-WS] Error (%s): %s", code, reason)

        kws.on_connect = on_connect
        kws.on_ticks   = on_ticks
        kws.on_close   = on_close
        kws.on_error   = on_error

        try:
            kws.connect(threaded=True)
            logger.info("[CRUDE-WS] WebSocket thread started.")

            # Monitor: disconnect cleanly at MCX close
            while True:
                _time.sleep(10)
                if not _mcx_open():
                    logger.info("[CRUDE-WS] MCX closing — disconnecting WebSocket.")
                    kws.close()
                    once("crude_ws_end",
                         f"\U0001f534 CRUDEOIL 1-sec Ended\n"
                         f"{now_ist()}\n"
                         f"Bars today : {_today_rows():,}\n"
                         f"DB         : {db_size()}")
                    break
                if not kws.is_connected():
                    logger.warning("[CRUDE-WS] Disconnected mid-session — will reconnect.")
                    break

        except Exception as exc:
            logger.error("[CRUDE-WS] Unexpected error: %s", exc)

        _time.sleep(5)   # brief pause before next connect attempt


if __name__ == "__main__":
    main()
