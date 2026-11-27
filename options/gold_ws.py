"""
MCX GOLD 1-second OHLC collector -- Kite WebSocket daemon.

Added 2026-11-25. Stores front GOLD (1 kg) futures 1-second bars in mcx_ohlc
with instrument='GOLD', interval='second' -- the exact row format the
CRUDEOIL daemon writes, so the same research/backtest queries work by only
changing the instrument filter.

Fully isolated from crudeoil_ws.py: own process/container, own Kite
WebSocket connection, own DB connection. No options, no depth, no Telegram
(front + next futures OHLC only).

Front-contract rule (rollover): MCX GOLD expires bi-monthly and liquidity
moves to the next contract several days before expiry, so the "nearest
expiry" rule used for crude would keep a dying contract. Instead, on every
(re)connect the two nearest-expiry GOLD futures are quoted and the one with
the higher open interest is collected. The contract actually collected is
stored per row in mcx_ohlc.tradingsymbol.

Next contract (added 2026-11-27): the GOLD future with the first expiry after
the front one is also collected every second, all session, into
mcx_ohlc_next_contract (instrument='GOLD', contract_role='next', expiry set) --
the same table/format crudeoil_ws uses for CRUDEOIL. When OI rolls the front
to that contract, the next table moves on to the one after it. Research only;
a next-contract failure never blocks or alters front collection.
"""

import logging
import os
import sys
import threading
import time as _time
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from kiteconnect import KiteTicker

from options.kite_auth import API_KEY, get_kite, load_access_token
from options.crude_tick_aggregator import classify_session_phase, classify_quality_flag
from options.collector_control import is_paused
from logging_config import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
SYMBOL = "GOLD"

# MCX closes 23:30 IST in US summer and 23:55 IST in US winter. Staying
# connected until 23:59 costs nothing (no ticks -> no rows) and never misses
# the winter close.
WARMUP_OPEN = time(8, 45)
NORMAL_OPEN = time(9, 0)
EVENING_OPEN = time(17, 0)
SESSION_END = time(23, 59)


# -- Market hours --------------------------------------------------------------

def _mcx_open() -> bool:
    from config import MCX_HOLIDAYS, MCX_EVENING_ONLY_DAYS
    now = datetime.now(IST)
    day = now.date().strftime("%Y-%m-%d")
    t = now.time()
    if now.weekday() >= 5 or day in MCX_HOLIDAYS:
        return False
    if day in MCX_EVENING_ONLY_DAYS:
        return EVENING_OPEN <= t <= SESSION_END
    return WARMUP_OPEN <= t <= SESSION_END


def _session_open_today() -> Optional[datetime]:
    from config import MCX_EVENING_ONLY_DAYS
    now = datetime.now(IST)
    day = now.date()
    open_t = EVENING_OPEN if day.strftime("%Y-%m-%d") in MCX_EVENING_ONLY_DAYS else NORMAL_OPEN
    return datetime.combine(day, open_t, tzinfo=IST)


def _seconds_until_open() -> int:
    from config import MCX_HOLIDAYS, MCX_EVENING_ONLY_DAYS
    now = datetime.now(IST)
    day = now.date()
    for _ in range(14):
        day_str = day.strftime("%Y-%m-%d")
        if day.weekday() < 5 and day_str not in MCX_HOLIDAYS:
            open_t = EVENING_OPEN if day_str in MCX_EVENING_ONLY_DAYS else WARMUP_OPEN
            candidate = datetime.combine(day, open_t, tzinfo=IST)
            if candidate > now:
                return max(60, int((candidate - now).total_seconds()))
        day += timedelta(days=1)
    return 3600


# -- Contract selection --------------------------------------------------------

_token_id: Optional[int] = None
_tradingsymbol: str = ""
_next_token_id: Optional[int] = None
_next_tradingsymbol: str = ""
_next_expiry: Optional[date] = None


def _select_contracts(kite) -> tuple[tuple, Optional[tuple]]:
    """Return (front, next) as (expiry, instrument_token, tradingsymbol).
    Front = of the two nearest unexpired contracts, the one with higher OI.
    Next  = first contract expiring after the front (None if not listed)."""
    instruments = kite.instruments("MCX")
    today = date.today()
    futs = []
    for row in instruments:
        if row.get("name") != SYMBOL or row.get("instrument_type") != "FUT":
            continue
        expiry = row.get("expiry")
        if isinstance(expiry, datetime):
            expiry = expiry.date()
        if expiry and expiry >= today:
            futs.append((expiry, int(row["instrument_token"]), row["tradingsymbol"]))
    if not futs:
        raise RuntimeError("No GOLD futures found in MCX instruments")
    futs.sort()
    candidates = futs[:2]
    chosen = candidates[0]
    if len(candidates) == 2:
        try:
            quotes = kite.quote([f"MCX:{c[2]}" for c in candidates])
            ois = [int((quotes.get(f"MCX:{c[2]}") or {}).get("oi") or 0) for c in candidates]
            if ois[1] > ois[0]:
                chosen = candidates[1]
            logger.info("[GOLD-WS] OI %s=%d, %s=%d", candidates[0][2], ois[0],
                        candidates[1][2], ois[1])
        except Exception as exc:
            logger.warning("[GOLD-WS] OI quote failed, using nearest expiry: %s", exc)
    logger.info("[GOLD-WS] Front contract: %s (token %d, expiry %s)",
                chosen[2], chosen[1], chosen[0])
    nxt = next((f for f in futs if f[0] > chosen[0]), None)
    if nxt:
        logger.info("[GOLD-WS] Next contract: %s (token %d, expiry %s)", nxt[2], nxt[1], nxt[0])
    else:
        logger.warning("[GOLD-WS] No GOLD contract listed after the front -- next collection off.")
    return chosen, nxt


# -- Tick -> 1-second bar ------------------------------------------------------

_lock = threading.Lock()
_running = True
# Per role ("front" / "next"): the bar being built, the previous cumulative
# volume (None = unknown baseline -> volume NULL) and the last flushed second.
_bars: dict = {"front": {}, "next": {}}
_prev_vol_cum: dict = {"front": None, "next": None}
_last_flushed_ts: dict = {"front": None, "next": None}


def _reset_bar(role: str) -> None:
    b = _bars[role]
    b.clear()
    b.update({"ts": None, "open": None, "high": None, "low": None, "close": None,
              "vol_cum": None, "oi": 0, "tick_count": 0, "last_tick_at": None,
              "symbol": "", "expiry": None})


_reset_bar("front")
_reset_bar("next")


def _on_ticks(_ws, ticks) -> None:
    for tick in ticks:
        tok = int(tick.get("instrument_token", 0))
        if tok == _token_id:
            role, symbol, expiry = "front", _tradingsymbol, None
        elif _next_token_id is not None and tok == _next_token_id:
            role, symbol, expiry = "next", _next_tradingsymbol, _next_expiry
        else:
            continue
        ltp = tick.get("last_price") or 0
        if not ltp:
            continue
        received_at = datetime.now(IST)
        with _lock:
            bar = _bars[role]
            if bar["open"] is None:
                bar["ts"] = received_at.replace(microsecond=0)
                bar["open"] = ltp
                bar["symbol"] = symbol
                bar["expiry"] = expiry
            bar["high"] = max(bar["high"] or ltp, ltp)
            bar["low"] = min(bar["low"] or ltp, ltp)
            bar["close"] = ltp
            bar["vol_cum"] = tick.get("volume_traded")
            bar["oi"] = tick.get("oi") or 0
            bar["tick_count"] += 1
            bar["last_tick_at"] = received_at


def _write_bar(conn, ts, o, h, l, c, volume, oi, symbol, session_phase, quality_flag) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO mcx_ohlc
                (ts, instrument, interval, tradingsymbol, open, high, low, close, volume, oi,
                 session_phase, quality_flag)
            VALUES (%s, %s, 'second', %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ts, instrument, interval) DO UPDATE SET
                tradingsymbol = EXCLUDED.tradingsymbol,
                open = EXCLUDED.open, high = EXCLUDED.high,
                low = EXCLUDED.low, close = EXCLUDED.close,
                volume = EXCLUDED.volume, oi = EXCLUDED.oi,
                session_phase = EXCLUDED.session_phase,
                quality_flag = EXCLUDED.quality_flag
        """, (ts, SYMBOL, symbol, o, h, l, c, volume, oi, session_phase, quality_flag))
    conn.commit()


# Same DDL as crudeoil_ws.py (table normally exists already; IF NOT EXISTS).
_NEXT_OHLC_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS mcx_ohlc_next_contract (
        ts              TIMESTAMPTZ NOT NULL,
        instrument      VARCHAR(20) NOT NULL,
        interval        VARCHAR(10) NOT NULL,
        tradingsymbol   VARCHAR(40) NOT NULL,
        expiry          DATE,
        contract_role   VARCHAR(10) NOT NULL DEFAULT 'next',
        open            NUMERIC(12,2),
        high            NUMERIC(12,2),
        low             NUMERIC(12,2),
        close           NUMERIC(12,2),
        volume          BIGINT,
        oi              BIGINT,
        session_phase   VARCHAR(20),
        quality_flag    VARCHAR(20),
        available_at    TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (ts, instrument, interval)
    );
    CREATE INDEX IF NOT EXISTS idx_mcx_ohlc_next_contract_symbol
        ON mcx_ohlc_next_contract (tradingsymbol, ts DESC);
"""


def _write_next_bar(conn, ts, o, h, l, c, volume, oi, symbol, expiry,
                    session_phase, quality_flag) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO mcx_ohlc_next_contract
                (ts, instrument, interval, tradingsymbol, expiry, open, high, low, close,
                 volume, oi, session_phase, quality_flag)
            VALUES (%s, %s, 'second', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ts, instrument, interval) DO UPDATE SET
                tradingsymbol = EXCLUDED.tradingsymbol, expiry = EXCLUDED.expiry,
                open = EXCLUDED.open, high = EXCLUDED.high,
                low = EXCLUDED.low, close = EXCLUDED.close,
                volume = EXCLUDED.volume, oi = EXCLUDED.oi,
                session_phase = EXCLUDED.session_phase,
                quality_flag = EXCLUDED.quality_flag
        """, (ts, SYMBOL, symbol, expiry, o, h, l, c, volume, oi, session_phase, quality_flag))
    conn.commit()


def _take_snapshot(role: str) -> Optional[dict]:
    """Pop the completed bar for ``role``. Caller holds _lock."""
    bar = _bars[role]
    if bar["open"] is None:
        return None
    vol_cum = bar["vol_cum"]
    prev = _prev_vol_cum[role]
    if vol_cum is None or prev is None or vol_cum < prev:
        volume = None          # baseline unknown -> honest NULL
    else:
        volume = vol_cum - prev
    if vol_cum is not None:
        _prev_vol_cum[role] = vol_cum
    snap = dict(bar, volume=volume)
    _reset_bar(role)
    return snap


def _tags(snap: dict, role: str, now: datetime, holidays, evening_only) -> tuple:
    session_phase = quality_flag = None
    try:
        session_phase = classify_session_phase(snap["ts"], holidays, evening_only)
        last = _last_flushed_ts[role]
        gap = (snap["ts"] - last).total_seconds() if last is not None else None
        age_ms = max(0, round((now - snap["last_tick_at"]).total_seconds() * 1000))
        quality_flag = classify_quality_flag(
            session_phase=session_phase, tick_count=snap["tick_count"],
            data_age_ms=age_ms, gap_seconds=gap)
    except Exception as exc:
        logger.error("[GOLD-WS] %s session/quality tag failed (row still written): %s", role, exc)
    return session_phase, quality_flag


def _flush_thread() -> None:
    """Write each completed 1-second bar as soon as the wall-clock second changes.
    Front -> mcx_ohlc; next -> mcx_ohlc_next_contract. Next-contract errors are
    logged and dropped -- they never affect the front row or the connection."""
    import psycopg2
    from config import MCX_HOLIDAYS, MCX_EVENING_ONLY_DAYS

    conn = None
    next_ready = False
    last_sec = None
    while _running:
        _time.sleep(0.1)
        try:
            if conn is None or conn.closed:
                conn = psycopg2.connect(os.environ.get("DATABASE_URL", ""))
                conn.autocommit = False
                next_ready = False
                logger.info("[GOLD-WS] DB connected.")

            now = datetime.now(IST)
            current_sec = now.replace(microsecond=0)
            if last_sec is None or current_sec == last_sec:
                last_sec = current_sec
                continue
            last_sec = current_sec

            with _lock:
                snap = _take_snapshot("front")
                nsnap = _take_snapshot("next")

            if snap:
                phase, flag = _tags(snap, "front", now, MCX_HOLIDAYS, MCX_EVENING_ONLY_DAYS)
                _write_bar(conn, snap["ts"], snap["open"], snap["high"], snap["low"],
                           snap["close"], snap["volume"], snap["oi"], snap["symbol"],
                           phase, flag)
                _last_flushed_ts["front"] = snap["ts"]

            if nsnap:
                try:
                    if not next_ready:
                        with conn.cursor() as cur:
                            cur.execute(_NEXT_OHLC_TABLE_SQL)
                        conn.commit()
                        next_ready = True
                    phase, flag = _tags(nsnap, "next", now, MCX_HOLIDAYS, MCX_EVENING_ONLY_DAYS)
                    _write_next_bar(conn, nsnap["ts"], nsnap["open"], nsnap["high"], nsnap["low"],
                                    nsnap["close"], nsnap["volume"], nsnap["oi"], nsnap["symbol"],
                                    nsnap["expiry"], phase, flag)
                    _last_flushed_ts["next"] = nsnap["ts"]
                except Exception as exc:
                    logger.error("[GOLD-WS] Next-contract write failed (front unaffected): %s", exc)
                    try:
                        conn.rollback()
                    except Exception:
                        pass
        except Exception as exc:
            logger.error("[GOLD-WS] Flush error: %s", exc)
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
            conn = None
            _time.sleep(2)


def _start_session_baseline(connected_at: datetime) -> None:
    """Volume baseline for the new connection. Connected before the session
    opened -> cumulative volume starts at 0, so the first bar's volume is
    exact. Connected mid-session -> unknown, first bar gets NULL volume."""
    open_at = _session_open_today()
    with _lock:
        for role in ("front", "next"):
            _reset_bar(role)
            _prev_vol_cum[role] = 0 if open_at and connected_at < open_at else None
            _last_flushed_ts[role] = None


# -- Main loop -----------------------------------------------------------------

def main() -> None:
    global _token_id, _tradingsymbol, _next_token_id, _next_tradingsymbol, _next_expiry

    logger.info("[GOLD-WS] GOLD 1-second WebSocket daemon starting.")
    threading.Thread(target=_flush_thread, daemon=True, name="gold-bar-flusher").start()

    consecutive_timeouts = 0
    pause_logged = False
    while True:
        if is_paused("gold_1s"):
            if not pause_logged:
                logger.info("[GOLD-WS] Paused from Server page -- idle until resumed.")
                pause_logged = True
            _time.sleep(30)
            continue
        if pause_logged:
            logger.info("[GOLD-WS] Resumed from Server page.")
            pause_logged = False

        if not _mcx_open():
            wait = _seconds_until_open()
            logger.info("[GOLD-WS] MCX closed. Sleeping %d min.", wait // 60)
            _time.sleep(min(wait, 3600))
            continue

        kite = get_kite()
        access_token = load_access_token(os.environ.get("KITE_ADMIN_USER_ID", ""))
        if not kite or not access_token or not API_KEY:
            logger.warning("[GOLD-WS] Kite not authorised yet -- retry in 5 min.")
            _time.sleep(300)
            continue

        try:
            front, nxt = _select_contracts(kite)
        except Exception as exc:
            logger.error("[GOLD-WS] Contract selection failed: %s -- retry in 5 min.", exc)
            _time.sleep(300)
            continue
        with _lock:
            _token_id, _tradingsymbol = front[1], front[2]
            if nxt:
                _next_expiry, _next_token_id, _next_tradingsymbol = nxt
            else:
                _next_expiry, _next_token_id, _next_tradingsymbol = None, None, ""
        _start_session_baseline(datetime.now(IST))

        # reconnect=False: this loop owns reconnection (same as crudeoil_ws).
        kws = KiteTicker(API_KEY, access_token, reconnect=False)
        connected = threading.Event()

        def on_connect(ws, _resp):
            connected.set()
            tokens = [_token_id] + ([_next_token_id] if _next_token_id else [])
            ws.subscribe(tokens)
            ws.set_mode(ws.MODE_FULL, tokens)   # FULL carries OI
            logger.info("[GOLD-WS] Connected -- subscribed front %s (%d), next %s (%s)",
                        _tradingsymbol, _token_id, _next_tradingsymbol or "-", _next_token_id)

        def on_close(_ws, code, reason):
            logger.warning("[GOLD-WS] Connection closed (%s): %s", code, reason)

        def on_error(_ws, code, reason):
            logger.error("[GOLD-WS] Error (%s): %s", code, reason)

        kws.on_connect = on_connect
        kws.on_ticks = _on_ticks
        kws.on_close = on_close
        kws.on_error = on_error

        try:
            kws.connect(threaded=True)
            if not connected.wait(timeout=60):
                consecutive_timeouts += 1
                logger.warning("[GOLD-WS] Connection timeout -- attempt %d.", consecutive_timeouts)
                if consecutive_timeouts >= 3:
                    logger.error("[GOLD-WS] 3 consecutive timeouts -- exiting for Docker restart.")
                    sys.exit(1)
            else:
                consecutive_timeouts = 0
                session_day = date.today()
                while True:
                    if not _mcx_open():
                        logger.info("[GOLD-WS] MCX closing -- disconnecting.")
                        break
                    if is_paused("gold_1s"):
                        logger.info("[GOLD-WS] Paused from Server page -- disconnecting.")
                        break
                    if date.today() != session_day:
                        break   # new day -> re-select contracts
                    if not kws.is_connected():
                        logger.warning("[GOLD-WS] Disconnected mid-session -- reconnecting.")
                        break
                    _time.sleep(10)
        except Exception as exc:
            logger.error("[GOLD-WS] Unexpected error: %s", exc)

        try:
            kws.close()
        except Exception:
            pass
        # KiteTicker's Twisted reactor does not reconnect reliably inside one
        # process (observed 2026-11-25: resume after pause -> connect timeout).
        # Exit instead; Docker (restart: unless-stopped) starts a fresh process,
        # which idles while paused / closed and connects cleanly when needed.
        logger.info("[GOLD-WS] Exiting for a clean restart (fresh WebSocket next time).")
        _time.sleep(5)
        sys.exit(0)


if __name__ == "__main__":
    main()
