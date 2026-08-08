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
import queue
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
    if now.weekday() >= 5:          # Saturday/Sunday — MCX closed
        return False
    if today in MCX_HOLIDAYS:
        return False
    if today in MCX_EVENING_ONLY_DAYS:
        return time(17, 0) <= t <= time(23, 30)
    # 15-min warmup before 9:00 so WS is connected well before first tick
    return time(8, 45) <= t <= time(23, 30)


def _seconds_until_open() -> int:
    """Seconds until pre-connect warmup (8:45 AM IST next valid day)."""
    now  = datetime.now(IST)
    nxt  = now.replace(hour=8, minute=45, second=0, microsecond=0)
    if now.time() >= time(23, 30):      # past today's close — try tomorrow
        nxt += timedelta(days=1)
    if nxt <= now:
        nxt += timedelta(days=1)
    # Skip Saturday and Sunday
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return max(60, int((nxt - now).total_seconds()))


# ── Instrument token ───────────────────────────────────────────────────────────

def _get_crudeoil_token(kite) -> int:
    global _tradingsymbol, _contract_expiry
    df    = load_mcx_instruments(kite)
    today = date.today()
    fut   = df[(df["name"] == SYMBOL) & (df["instrument_type"] == "FUT")]
    near  = fut[fut["expiry"] >= today].sort_values("expiry")
    if near.empty:
        raise RuntimeError("No CRUDEOIL futures found in MCX instruments")
    row = near.iloc[0]
    _tradingsymbol = row["tradingsymbol"]
    _contract_expiry = row["expiry"]
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
    "last_quantity": 0,
    "average_traded_price": None,
    "total_buy_quantity": 0,
    "total_sell_quantity": 0,
    "oi_day_high": 0,
    "oi_day_low": 0,
    "last_trade_ts": None,
    "tick_count": 0,
    "l1_order_flow_imbalance": 0,
    "depth": None,    # latest full market-depth snapshot seen in this second
}
_prev_vol_cum: int = 0   # to compute per-second volume delta
_token_id: int    = 0
_tradingsymbol: str = ""
_contract_expiry = None
_previous_top: Optional[tuple[float, int, float, int]] = None
_running: bool    = True
_depth_queue: queue.Queue = queue.Queue(maxsize=5000)
_depth_drops: int = 0


def _normalise_depth(raw_depth: Optional[dict]) -> Optional[dict]:
    """Return compact arrays for every valid depth level supplied by Kite."""
    if not raw_depth:
        return None

    def _side(name: str) -> tuple[list, list, list]:
        prices: list = []
        quantities: list = []
        orders: list = []
        for level in raw_depth.get(name, []) or []:
            if not isinstance(level, dict):
                continue
            price = level.get("price")
            if price is None:
                continue
            try:
                if float(price) <= 0:
                    continue
            except (TypeError, ValueError):
                continue
            prices.append(price)
            quantities.append(level.get("quantity") or 0)
            orders.append(level.get("orders") or 0)
        return prices, quantities, orders

    bid_prices, bid_quantities, bid_orders = _side("buy")
    ask_prices, ask_quantities, ask_orders = _side("sell")
    if not bid_prices and not ask_prices:
        return None
    return {
        "bid_prices": bid_prices,
        "bid_quantities": bid_quantities,
        "bid_orders": bid_orders,
        "ask_prices": ask_prices,
        "ask_quantities": ask_quantities,
        "ask_orders": ask_orders,
    }


def _top_of_book(depth: Optional[dict]) -> Optional[tuple[float, int, float, int]]:
    """Return best bid price/qty and best ask price/qty when both sides exist."""
    if not depth or not depth.get("bid_prices") or not depth.get("ask_prices"):
        return None
    return (
        float(depth["bid_prices"][0]),
        int(depth["bid_quantities"][0]),
        float(depth["ask_prices"][0]),
        int(depth["ask_quantities"][0]),
    )


def _l1_ofi(previous, current) -> int:
    """Signed L1 order-flow imbalance from consecutive top-of-book updates."""
    if previous is None or current is None:
        return 0

    prev_bid, prev_bid_qty, prev_ask, prev_ask_qty = previous
    bid, bid_qty, ask, ask_qty = current

    if bid > prev_bid:
        bid_flow = bid_qty
    elif bid == prev_bid:
        bid_flow = bid_qty - prev_bid_qty
    else:
        bid_flow = -prev_bid_qty

    if ask > prev_ask:
        ask_flow = prev_ask_qty
    elif ask == prev_ask:
        ask_flow = prev_ask_qty - ask_qty
    else:
        ask_flow = -ask_qty

    return int(bid_flow + ask_flow)


def _depth_metrics(depth: Optional[dict]) -> dict:
    """Calculate compact, reproducible order-book features for one snapshot."""
    top = _top_of_book(depth)
    if top is None:
        return {
            "best_bid_price": None,
            "best_ask_price": None,
            "spread": None,
            "mid_price": None,
            "microprice": None,
            "bid_quantity_total": None,
            "ask_quantity_total": None,
            "book_imbalance_l1": None,
            "book_imbalance_l5": None,
        }

    bid, bid_qty, ask, ask_qty = top
    spread = ask - bid
    total_l1 = bid_qty + ask_qty
    bid_quantities = [int(x or 0) for x in depth.get("bid_quantities", [])[:5]]
    ask_quantities = [int(x or 0) for x in depth.get("ask_quantities", [])[:5]]
    bid_total = sum(bid_quantities)
    ask_total = sum(ask_quantities)

    weighted_bid = sum(qty / (level + 1) for level, qty in enumerate(bid_quantities))
    weighted_ask = sum(qty / (level + 1) for level, qty in enumerate(ask_quantities))
    weighted_total = weighted_bid + weighted_ask

    return {
        "best_bid_price": bid,
        "best_ask_price": ask,
        "spread": spread,
        "mid_price": (bid + ask) / 2.0,
        "microprice": ((ask * bid_qty) + (bid * ask_qty)) / total_l1 if total_l1 else None,
        "bid_quantity_total": bid_total,
        "ask_quantity_total": ask_total,
        "book_imbalance_l1": (bid_qty - ask_qty) / total_l1 if total_l1 else None,
        "book_imbalance_l5": (
            (weighted_bid - weighted_ask) / weighted_total if weighted_total else None
        ),
    }


def _exchange_timestamp(value):
    """Kite exchange timestamps are naive IST; store them timezone-aware."""
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=IST)
    return value


def _on_ticks(ws, ticks):
    global _previous_top
    for tick in ticks:
        if int(tick.get("instrument_token", 0)) != _token_id:
            continue
        ltp = tick.get("last_price") or 0
        if not ltp:
            continue
        vol = tick.get("volume_traded") or 0
        oi  = tick.get("oi") or 0
        received_at = datetime.now(IST)
        depth = _normalise_depth(tick.get("depth"))

        with _lock:
            _bar["tick_count"] += 1
            if _bar["open"] is None:
                _bar["ts"]   = received_at.replace(microsecond=0)
                _bar["open"] = ltp
            _bar["high"]    = max(_bar["high"] or ltp, ltp)
            _bar["low"]     = min(_bar["low"]  or ltp, ltp)
            _bar["close"]   = ltp
            _bar["vol_cum"] = vol
            _bar["oi"]      = oi
            _bar["last_quantity"] = tick.get("last_traded_quantity") or 0
            _bar["average_traded_price"] = tick.get("average_traded_price")
            _bar["total_buy_quantity"] = tick.get("total_buy_quantity") or 0
            _bar["total_sell_quantity"] = tick.get("total_sell_quantity") or 0
            _bar["oi_day_high"] = tick.get("oi_day_high") or 0
            _bar["oi_day_low"] = tick.get("oi_day_low") or 0
            _bar["last_trade_ts"] = _exchange_timestamp(tick.get("last_trade_time"))
            if depth:
                current_top = _top_of_book(depth)
                _bar["l1_order_flow_imbalance"] += _l1_ofi(_previous_top, current_top)
                _previous_top = current_top
                _bar["depth"] = {
                    **depth,
                    "last_price": ltp,
                    "exchange_ts": _exchange_timestamp(tick.get("exchange_timestamp")),
                    "received_at": received_at,
                }


def _reset_bar():
    _bar.update({"ts": None, "open": None, "high": None,
                 "low": None, "close": None, "oi": 0,
                 "last_quantity": 0, "average_traded_price": None,
                 "total_buy_quantity": 0, "total_sell_quantity": 0,
                 "oi_day_high": 0, "oi_day_low": 0, "last_trade_ts": None,
                 "tick_count": 0, "l1_order_flow_imbalance": 0,
                 "depth": None})


# ── DB writer (flush thread) ───────────────────────────────────────────────────

def _write_bar(conn, ts, open_, high, low, close, volume, oi, tradingsymbol=""):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO mcx_ohlc
                (ts, instrument, interval, tradingsymbol, open, high, low, close, volume, oi)
            VALUES (%s, %s, 'second', %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ts, instrument, interval) DO UPDATE SET
                tradingsymbol = EXCLUDED.tradingsymbol,
                open   = EXCLUDED.open,
                high   = EXCLUDED.high,
                low    = EXCLUDED.low,
                close  = EXCLUDED.close,
                volume = EXCLUDED.volume,
                oi     = EXCLUDED.oi
        """, (ts, SYMBOL, tradingsymbol, open_, high, low, close, volume, oi))
    conn.commit()


_DEPTH_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS mcx_futures_depth (
        ts                  TIMESTAMPTZ NOT NULL,
        instrument          VARCHAR(20) NOT NULL,
        tradingsymbol       VARCHAR(40) NOT NULL,
        instrument_token    BIGINT NOT NULL,
        expiry              DATE,
        exchange_ts         TIMESTAMPTZ,
        received_at         TIMESTAMPTZ NOT NULL,
        last_trade_ts       TIMESTAMPTZ,
        last_price          NUMERIC(12,2),
        last_quantity       BIGINT,
        average_traded_price NUMERIC(12,2),
        volume_traded_day   BIGINT,
        volume_delta        BIGINT,
        oi                  BIGINT,
        oi_day_high         BIGINT,
        oi_day_low          BIGINT,
        total_buy_quantity  BIGINT,
        total_sell_quantity BIGINT,
        tick_count          INTEGER,
        bid_prices          NUMERIC(12,2)[] NOT NULL,
        bid_quantities      BIGINT[] NOT NULL,
        bid_orders          INTEGER[] NOT NULL,
        ask_prices          NUMERIC(12,2)[] NOT NULL,
        ask_quantities      BIGINT[] NOT NULL,
        ask_orders          INTEGER[] NOT NULL,
        best_bid_price      NUMERIC(12,2),
        best_ask_price      NUMERIC(12,2),
        spread              NUMERIC(12,4),
        mid_price           NUMERIC(12,4),
        microprice          NUMERIC(14,6),
        bid_quantity_total  BIGINT,
        ask_quantity_total  BIGINT,
        book_imbalance_l1   DOUBLE PRECISION,
        book_imbalance_l5   DOUBLE PRECISION,
        l1_order_flow_imbalance BIGINT,
        available_at        TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (ts, instrument),
        CHECK (
            cardinality(bid_prices) = cardinality(bid_quantities)
            AND cardinality(bid_prices) = cardinality(bid_orders)
            AND cardinality(ask_prices) = cardinality(ask_quantities)
            AND cardinality(ask_prices) = cardinality(ask_orders)
        )
    );
    CREATE INDEX IF NOT EXISTS idx_mcx_futures_depth_contract
        ON mcx_futures_depth (tradingsymbol, ts DESC);
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS last_trade_ts TIMESTAMPTZ;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS last_quantity BIGINT;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS average_traded_price NUMERIC(12,2);
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS volume_traded_day BIGINT;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS volume_delta BIGINT;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS oi BIGINT;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS oi_day_high BIGINT;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS oi_day_low BIGINT;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS total_buy_quantity BIGINT;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS total_sell_quantity BIGINT;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS tick_count INTEGER;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS best_bid_price NUMERIC(12,2);
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS best_ask_price NUMERIC(12,2);
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS spread NUMERIC(12,4);
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS mid_price NUMERIC(12,4);
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS microprice NUMERIC(14,6);
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS bid_quantity_total BIGINT;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS ask_quantity_total BIGINT;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS book_imbalance_l1 DOUBLE PRECISION;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS book_imbalance_l5 DOUBLE PRECISION;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS l1_order_flow_imbalance BIGINT;
"""


def _ensure_depth_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(_DEPTH_TABLE_SQL)
    conn.commit()


def _write_depth(conn, snapshot: dict) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO mcx_futures_depth (
                ts, instrument, tradingsymbol, instrument_token, expiry,
                exchange_ts, received_at, last_trade_ts, last_price,
                last_quantity, average_traded_price,
                volume_traded_day, volume_delta, oi, oi_day_high, oi_day_low,
                total_buy_quantity, total_sell_quantity, tick_count,
                bid_prices, bid_quantities, bid_orders,
                ask_prices, ask_quantities, ask_orders,
                best_bid_price, best_ask_price, spread, mid_price, microprice,
                bid_quantity_total, ask_quantity_total,
                book_imbalance_l1, book_imbalance_l5, l1_order_flow_imbalance
            ) VALUES (
                %(ts)s, %(instrument)s, %(tradingsymbol)s, %(instrument_token)s, %(expiry)s,
                %(exchange_ts)s, %(received_at)s, %(last_trade_ts)s, %(last_price)s,
                %(last_quantity)s, %(average_traded_price)s,
                %(volume_traded_day)s, %(volume_delta)s, %(oi)s, %(oi_day_high)s, %(oi_day_low)s,
                %(total_buy_quantity)s, %(total_sell_quantity)s, %(tick_count)s,
                %(bid_prices)s, %(bid_quantities)s, %(bid_orders)s,
                %(ask_prices)s, %(ask_quantities)s, %(ask_orders)s,
                %(best_bid_price)s, %(best_ask_price)s, %(spread)s, %(mid_price)s, %(microprice)s,
                %(bid_quantity_total)s, %(ask_quantity_total)s,
                %(book_imbalance_l1)s, %(book_imbalance_l5)s, %(l1_order_flow_imbalance)s
            )
            ON CONFLICT (ts, instrument) DO UPDATE SET
                tradingsymbol    = EXCLUDED.tradingsymbol,
                instrument_token = EXCLUDED.instrument_token,
                expiry           = EXCLUDED.expiry,
                exchange_ts      = EXCLUDED.exchange_ts,
                received_at      = EXCLUDED.received_at,
                last_trade_ts    = EXCLUDED.last_trade_ts,
                last_price       = EXCLUDED.last_price,
                last_quantity    = EXCLUDED.last_quantity,
                average_traded_price = EXCLUDED.average_traded_price,
                volume_traded_day = EXCLUDED.volume_traded_day,
                volume_delta     = EXCLUDED.volume_delta,
                oi               = EXCLUDED.oi,
                oi_day_high      = EXCLUDED.oi_day_high,
                oi_day_low       = EXCLUDED.oi_day_low,
                total_buy_quantity = EXCLUDED.total_buy_quantity,
                total_sell_quantity = EXCLUDED.total_sell_quantity,
                tick_count       = EXCLUDED.tick_count,
                bid_prices       = EXCLUDED.bid_prices,
                bid_quantities   = EXCLUDED.bid_quantities,
                bid_orders       = EXCLUDED.bid_orders,
                ask_prices       = EXCLUDED.ask_prices,
                ask_quantities   = EXCLUDED.ask_quantities,
                ask_orders       = EXCLUDED.ask_orders,
                best_bid_price   = EXCLUDED.best_bid_price,
                best_ask_price   = EXCLUDED.best_ask_price,
                spread           = EXCLUDED.spread,
                mid_price        = EXCLUDED.mid_price,
                microprice       = EXCLUDED.microprice,
                bid_quantity_total = EXCLUDED.bid_quantity_total,
                ask_quantity_total = EXCLUDED.ask_quantity_total,
                book_imbalance_l1 = EXCLUDED.book_imbalance_l1,
                book_imbalance_l5 = EXCLUDED.book_imbalance_l5,
                l1_order_flow_imbalance = EXCLUDED.l1_order_flow_imbalance,
                available_at     = clock_timestamp()
        """, snapshot)
    conn.commit()


def _enqueue_depth(snapshot: dict) -> None:
    """Never block OHLC ingestion; discard the oldest depth row under pressure."""
    global _depth_drops
    try:
        _depth_queue.put_nowait(snapshot)
        return
    except queue.Full:
        pass

    try:
        _depth_queue.get_nowait()
        _depth_queue.task_done()
    except queue.Empty:
        pass

    _depth_drops += 1
    try:
        _depth_queue.put_nowait(snapshot)
    except queue.Full:
        _depth_drops += 1
    if _depth_drops == 1 or _depth_drops % 100 == 0:
        logger.warning(
            "[CRUDE-DEPTH] Queue pressure: %d oldest snapshots dropped; OHLC unaffected.",
            _depth_drops,
        )


def _depth_writer_thread() -> None:
    """Write depth independently so depth failures cannot interrupt OHLC bars."""
    conn = None
    schema_ready = False

    while _running:
        try:
            snapshot = _depth_queue.get(timeout=1)
        except queue.Empty:
            continue

        written = False
        for attempt in range(2):
            try:
                if conn is None or conn.closed:
                    import psycopg2
                    conn = psycopg2.connect(
                        os.environ.get("DATABASE_URL", ""),
                        connect_timeout=5,
                        application_name="crude_depth_writer",
                    )
                    conn.autocommit = False
                    schema_ready = False
                if not schema_ready:
                    _ensure_depth_table(conn)
                    schema_ready = True
                    logger.info("[CRUDE-DEPTH] Table ready; asynchronous writer active.")
                _write_depth(conn, snapshot)
                written = True
                break
            except Exception as exc:
                logger.error(
                    "[CRUDE-DEPTH] Write failed (attempt %d/2); OHLC unaffected: %s",
                    attempt + 1,
                    exc,
                )
                try:
                    if conn is not None:
                        conn.close()
                except Exception:
                    pass
                conn = None
                schema_ready = False
                if attempt == 0:
                    _time.sleep(0.25)

        if not written:
            logger.error("[CRUDE-DEPTH] Snapshot dropped after two failed writes.")
        _depth_queue.task_done()


def _flush_thread():
    """Runs in background. Writes completed 1-sec bars every time second changes."""
    global _prev_vol_cum, _running

    conn = None
    last_sec = None

    while _running:
        _time.sleep(0.1)

        try:
            # Lazy-connect / reconnect — use direct psycopg2 (NOT _get_conn()
            # which is a @contextmanager and closes the connection on GC).
            if conn is None or conn.closed:
                import psycopg2
                db_url = os.environ.get("DATABASE_URL", "")
                conn = psycopg2.connect(db_url)
                conn.autocommit = False
                logger.info("[CRUDE-WS] DB connected.")

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

                    depth_snapshot = None
                    if _bar["depth"]:
                        depth_snapshot = {
                            **_bar["depth"],
                            "last_trade_ts": _bar["last_trade_ts"],
                            "last_quantity": _bar["last_quantity"],
                            "average_traded_price": _bar["average_traded_price"],
                            "volume_traded_day": _bar["vol_cum"],
                            "volume_delta": vol_delta,
                            "oi": _bar["oi"],
                            "oi_day_high": _bar["oi_day_high"],
                            "oi_day_low": _bar["oi_day_low"],
                            "total_buy_quantity": _bar["total_buy_quantity"],
                            "total_sell_quantity": _bar["total_sell_quantity"],
                            "tick_count": _bar["tick_count"],
                            "l1_order_flow_imbalance": _bar["l1_order_flow_imbalance"],
                            **_depth_metrics(_bar["depth"]),
                        }

                    snap = (
                        _bar["ts"], _bar["open"], _bar["high"],
                        _bar["low"], _bar["close"], vol_delta, _bar["oi"],
                        depth_snapshot,
                    )
                    _reset_bar()

                else:
                    snap = None
                    # Keep _prev_vol_cum unchanged (no trade this second)

            if snap:
                ts, o, h, l, c, v, oi, depth = snap
                # Preserve the existing critical path: commit OHLC first.
                _write_bar(conn, ts, o, h, l, c, v, oi, _tradingsymbol)
                if depth:
                    _enqueue_depth({
                        "ts": ts,
                        "instrument": SYMBOL,
                        "tradingsymbol": _tradingsymbol,
                        "instrument_token": _token_id,
                        "expiry": _contract_expiry,
                        **depth,
                    })
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

def _start_global_reference_worker():
    """Start the optional reference feed without coupling it to MCX writes."""
    if not os.environ.get("TWELVE_DATA_API_KEY", "").strip():
        logger.warning(
            "[GLOBAL-REF] TWELVE_DATA_API_KEY absent; optional reference feed disabled."
        )
        return None

    def run_worker():
        try:
            from options.global_reference_ws import main as global_reference_main

            global_reference_main()
        except Exception as exc:
            logger.error(
                "[GLOBAL-REF] Worker stopped; CRUDEOIL collection is unaffected: %s",
                exc,
            )

    worker = threading.Thread(
        target=run_worker,
        daemon=True,
        name="global-reference-worker",
    )
    worker.start()
    return worker


def main():
    global _token_id, _running, _previous_top

    logger.info("[CRUDE-WS] CRUDEOIL 1-second WebSocket daemon starting.")

    _start_global_reference_worker()

    # Independent workers run for the process lifetime. Depth writes never share
    # the OHLC connection or block the OHLC flush thread.
    depth_writer = threading.Thread(
        target=_depth_writer_thread,
        daemon=True,
        name="depth-writer",
    )
    depth_writer.start()

    flusher = threading.Thread(target=_flush_thread, daemon=True, name="bar-flusher")
    flusher.start()

    consecutive_timeouts = 0

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
            with _lock:
                _previous_top = None
        except Exception as exc:
            logger.error("[CRUDE-WS] Token resolution failed: %s — retry in 5 min.", exc)
            _time.sleep(300)
            continue

        # ── Build WebSocket ────────────────────────────────────────────────────
        # reconnect=False: outer loop handles reconnection exclusively.
        # reconnect=True caused dual concurrent connections → Kite dropped both.
        kws = KiteTicker(API_KEY, access_token, reconnect=False)
        _connected_evt = threading.Event()

        def on_connect(ws, _resp):
            _connected_evt.set()
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

            # Wait until on_connect fires (up to 60s) — no fixed sleep.
            if not _connected_evt.wait(timeout=60):
                consecutive_timeouts += 1
                logger.warning(
                    "[CRUDE-WS] Connection timeout (60s) — attempt %d.",
                    consecutive_timeouts,
                )
                if consecutive_timeouts >= 3:
                    logger.error(
                        "[CRUDE-WS] 3 consecutive timeouts — restarting process "
                        "so Docker can recover cleanly."
                    )
                    sys.exit(1)
            else:
                consecutive_timeouts = 0
                # Monitor: disconnect cleanly at MCX close.
                while True:
                    if not _mcx_open():
                        logger.info("[CRUDE-WS] MCX closing — disconnecting WebSocket.")
                        try:
                            kws.close()
                        except Exception:
                            pass
                        once("crude_ws_end",
                             f"\U0001f534 CRUDEOIL 1-sec Ended\n"
                             f"{now_ist()}\n"
                             f"Bars today : {_today_rows():,}\n"
                             f"DB         : {db_size()}")
                        break
                    if not kws.is_connected():
                        logger.warning("[CRUDE-WS] Disconnected mid-session — will reconnect.")
                        break
                    _time.sleep(10)

        except Exception as exc:
            logger.error("[CRUDE-WS] Unexpected error: %s", exc)

        # Explicitly stop the old KiteTicker before reconnecting
        try:
            kws.close()
        except Exception:
            pass

        _time.sleep(30)  # 30s cooldown — prevents duplicate sessions on Kite


if __name__ == "__main__":
    main()
