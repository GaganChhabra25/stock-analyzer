"""
CRUDEOIL 1-second OHLC collector — Kite WebSocket daemon.

Subscribes to near-month CRUDEOIL futures and nearest-expiry ATM +/-10 options
through one Kite WebSocket. Stores futures OHLC/depth and one compact option
pressure snapshot per active second.

Runs as a persistent Docker service (restart: unless-stopped).
  - Active only during MCX hours (09:00–23:30 IST); sleeps otherwise.
  - Reconnects each morning automatically after kite_auto_login refreshes token.
  - No manual steps needed.

Architecture:
  Main thread    → KiteTicker WebSocket (futures critical path + option cache)
  Flush thread   → writes completed 1-sec futures bars to DB
  Option writer  → isolated DB connection; option failures cannot block futures
"""

import json
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
from options.mcx_instruments import atm_strike_mcx, load_mcx_instruments
from options.tg import once, send, db_size, table_rows, now_ist
from options.kite_auth import get_kite
from options.crude_tick_aggregator import (
    SecondAccumulator,
    classify_session_phase,
    classify_quality_flag,
)
from screener.db import _get_conn
from logging_config import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

IST    = ZoneInfo("Asia/Kolkata")
SYMBOL = "CRUDEOIL"
OPTION_WINGS = 10
OPTION_FRESHNESS_SECONDS = 15

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
    """Seconds until the next valid session open, honoring MCX_HOLIDAYS and
    MCX_EVENING_ONLY_DAYS the same way _mcx_open() does.

    2026-09-14: this previously always targeted 8:45 AM regardless of the
    calendar, so on an evening-only day (open 17:00, not 8:45) it produced a
    misleading "Sleeping N min" log implying an 8:45 AM wake. The outer
    loop's sleep is capped at 3600s and rechecks _mcx_open() every hour
    regardless, so this was not silently losing data across most of the
    day -- but it did cost up to ~59 minutes of missed collection right at
    the start of an evening-only session, depending on where 17:00 fell
    inside the hourly recheck cycle (observed live today: last recheck at
    13:20, session opened 17:00, next recheck would have been 17:20 --
    worked around in the moment with a manual container restart, fixed
    here for future evening-only days).
    """
    from config import MCX_HOLIDAYS, MCX_EVENING_ONLY_DAYS
    now = datetime.now(IST)
    day = now.date()
    for _ in range(14):  # bounded search; a valid open day is always <14 days out
        day_str = day.strftime("%Y-%m-%d")
        if day.weekday() < 5 and day_str not in MCX_HOLIDAYS:
            open_time = time(17, 0) if day_str in MCX_EVENING_ONLY_DAYS else time(8, 45)
            candidate = datetime.combine(day, open_time, tzinfo=IST)
            if candidate > now:
                return max(60, int((candidate - now).total_seconds()))
        day += timedelta(days=1)
    return 3600  # unreachable in practice; keeps the caller's cap meaningful


# ── Instrument token ───────────────────────────────────────────────────────────

def _get_crudeoil_token(kite) -> int:
    global _tradingsymbol, _contract_expiry, _option_expiry, _option_universe
    global _accum, _tick_prev_vol_cum, _tick_prev_ltp, _last_flushed_ts
    # Phase-1: never carry within-second/trade-classification accumulator
    # state or gap-recovery bookkeeping across a session/contract change --
    # each new session (and every reconnect, since this function re-runs on
    # every reconnect) starts these clean.
    with _lock:
        _accum = SecondAccumulator()
        _tick_prev_vol_cum = 0
        _tick_prev_ltp = None
        _last_flushed_ts = None
    df    = load_mcx_instruments(kite)
    today = date.today()
    fut   = df[(df["name"] == SYMBOL) & (df["instrument_type"] == "FUT")]
    near  = fut[fut["expiry"] >= today].sort_values("expiry")
    if near.empty:
        raise RuntimeError("No CRUDEOIL futures found in MCX instruments")
    row = near.iloc[0]
    _tradingsymbol = row["tradingsymbol"]
    _contract_expiry = row["expiry"]

    option_rows = df[
        (df["name"] == SYMBOL)
        & (df["instrument_type"].isin(["CE", "PE"]))
        & (df["expiry"] >= today)
    ]
    universe = {}
    option_expiry = None
    if not option_rows.empty:
        option_expiry = min(option_rows["expiry"])
        for _, option in option_rows[option_rows["expiry"] == option_expiry].iterrows():
            strike = int(option["strike"])
            option_type = str(option["instrument_type"])
            universe[(strike, option_type)] = {
                "instrument_token": int(option["instrument_token"]),
                "tradingsymbol": str(option["tradingsymbol"]),
                "expiry": option_expiry,
                "strike": strike,
                "option_type": option_type,
            }
    with _lock:
        _option_expiry = option_expiry
        _option_universe = universe

    logger.info("[CRUDE-WS] Token: %d  Contract: %s  Expiry: %s",
                int(row["instrument_token"]), row["tradingsymbol"], row["expiry"])
    logger.info(
        "[CRUDE-OPT] Expiry=%s available_contracts=%d wings=ATM+/-%d",
        option_expiry,
        len(universe),
        OPTION_WINGS,
    )
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
    "last_tick_received_at": None,  # Phase-1: for data_age_ms at flush time
}
_prev_vol_cum: int = 0   # to compute per-second volume delta
_token_id: int    = 0
_tradingsymbol: str = ""
_contract_expiry = None
_previous_top: Optional[tuple[float, int, float, int]] = None
_running: bool    = True
_depth_queue: queue.Queue = queue.Queue(maxsize=5000)
_depth_drops: int = 0
_option_expiry = None
_option_universe: dict = {}
_option_meta: dict = {}
_active_option_tokens: set = set()
_active_option_atm: Optional[int] = None
_option_latest_ticks: dict = {}
_option_prev_oi: dict = {}
_option_prev_volume: dict = {}
_option_tick_counts: dict = {}
_last_futures_ltp: Optional[float] = None
_last_futures_received_at: Optional[datetime] = None

# ── Phase-1 collection-health beacon state (Part 6/7) ───────────────────────
# Best-effort only -- a failure here must never affect futures/depth/option
# ingestion. websocket_connected/reconnect_count are process-lifetime facts
# only this process can observe; everything else in crude_collection_health
# is computed separately (read-only, DB-derived) by
# options/crude_collection_health.py.
_websocket_connected: bool = False
_connection_attempts: int = 0

# ── Phase-1 rich 1-second aggregation state (additive, runs alongside _bar) ──
# See CRUDE_DATA_PHASE1_IMPLEMENTATION.md (trade-bot repo) for full design.
# _accum never replaces _bar -- it only preserves within-second detail that
# _bar's last-tick-wins fields already discard. Reset at every session/
# reconnect boundary in _get_crudeoil_token() so no state leaks across
# sessions (Phase-1 Part 11 requirement).
_accum: SecondAccumulator = SecondAccumulator()
_tick_prev_vol_cum: int = 0     # per-TICK (not per-second) cumulative volume,
                                 # used only for INFERRED trade classification
_tick_prev_ltp: Optional[float] = None
_last_flushed_ts: Optional[datetime] = None   # for gap-recovery detection


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


def _select_option_contracts(atm: int) -> dict:
    """Select nearest-expiry CRUDEOIL options at ATM +/- configured wings."""
    step = 50
    strikes = {atm + offset * step for offset in range(-OPTION_WINGS, OPTION_WINGS + 1)}
    return {
        int(meta["instrument_token"]): dict(meta)
        for (strike, option_type), meta in _option_universe.items()
        if strike in strikes and option_type in ("CE", "PE")
    }


def _roll_options(ws, futures_price: float) -> None:
    """Move the subscribed option slice only when the futures ATM changes."""
    global _active_option_atm, _active_option_tokens, _option_meta

    if not _option_universe:
        return
    atm = atm_strike_mcx(futures_price, SYMBOL)
    with _lock:
        if atm == _active_option_atm and _active_option_tokens:
            return
        desired = _select_option_contracts(atm)
        desired_tokens = set(desired)
        added = desired_tokens - _active_option_tokens
        removed = _active_option_tokens - desired_tokens

    try:
        if removed:
            ws.unsubscribe(sorted(removed))
        if added:
            ws.subscribe(sorted(added))
            ws.set_mode(ws.MODE_FULL, sorted(added))
    except Exception as exc:
        logger.error("[CRUDE-OPT] Subscription roll failed; futures unaffected: %s", exc)
        return

    with _lock:
        for token in removed:
            _option_latest_ticks.pop(token, None)
            _option_prev_oi.pop(token, None)
            _option_prev_volume.pop(token, None)
            _option_tick_counts.pop(token, None)
        _option_meta = desired
        _active_option_tokens = desired_tokens
        _active_option_atm = atm

    logger.info(
        "[CRUDE-OPT] ATM=%d subscribed=%d added=%d removed=%d",
        atm,
        len(desired_tokens),
        len(added),
        len(removed),
    )


def _reset_option_session() -> None:
    global _active_option_atm, _active_option_tokens, _option_meta
    with _lock:
        _active_option_atm = None
        _active_option_tokens = set()
        _option_meta = {}
        _option_latest_ticks.clear()
        _option_prev_oi.clear()
        _option_prev_volume.clear()
        _option_tick_counts.clear()


def _on_ticks(ws, ticks):
    global _previous_top, _last_futures_ltp, _last_futures_received_at
    global _tick_prev_vol_cum, _tick_prev_ltp
    roll_price = None
    for tick in ticks:
        token = int(tick.get("instrument_token", 0))
        if token != _token_id:
            with _lock:
                if token in _option_meta:
                    copied = dict(tick)
                    copied["_received_at"] = datetime.now(IST)
                    _option_latest_ticks[token] = copied
                    _option_tick_counts[token] = _option_tick_counts.get(token, 0) + 1
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
            _last_futures_ltp = float(ltp)
            _last_futures_received_at = received_at
            _bar["last_quantity"] = tick.get("last_traded_quantity") or 0
            _bar["average_traded_price"] = tick.get("average_traded_price")
            _bar["total_buy_quantity"] = tick.get("total_buy_quantity") or 0
            _bar["total_sell_quantity"] = tick.get("total_sell_quantity") or 0
            _bar["oi_day_high"] = tick.get("oi_day_high") or 0
            _bar["oi_day_low"] = tick.get("oi_day_low") or 0
            _bar["last_trade_ts"] = _exchange_timestamp(tick.get("last_trade_time"))
            _bar["last_tick_received_at"] = received_at

            # ── Phase-1 additive aggregation: never replaces the block
            # above, only records within-second detail it would otherwise
            # discard. classify_trade() uses the PRE-update _previous_top
            # (the book state that actually prevailed before this print) --
            # computed before _previous_top is reassigned below.
            prevailing_bid = _previous_top[0] if _previous_top else None
            prevailing_ask = _previous_top[2] if _previous_top else None
            tick_vol_delta = max(0, vol - _tick_prev_vol_cum)
            _tick_prev_vol_cum = vol
            _accum.add_price_tick(float(ltp))
            _accum.classify_trade(
                traded_qty=tick_vol_delta,
                ltp=float(ltp),
                prevailing_best_bid=prevailing_bid,
                prevailing_best_ask=prevailing_ask,
                prev_ltp=_tick_prev_ltp,
            )
            _tick_prev_ltp = float(ltp)

            if depth:
                current_top = _top_of_book(depth)
                _bar["l1_order_flow_imbalance"] += _l1_ofi(_previous_top, current_top)
                bid_qty_l5 = sum(int(q or 0) for q in depth.get("bid_quantities", [])[:5])
                ask_qty_l5 = sum(int(q or 0) for q in depth.get("ask_quantities", [])[:5])
                if current_top:
                    _accum.add_depth_tick(
                        best_bid=current_top[0], best_bid_qty=current_top[1],
                        best_ask=current_top[2], best_ask_qty=current_top[3],
                        bid_qty_l5=bid_qty_l5, ask_qty_l5=ask_qty_l5,
                    )
                _previous_top = current_top
                _bar["depth"] = {
                    **depth,
                    "last_price": ltp,
                    "exchange_ts": _exchange_timestamp(tick.get("exchange_timestamp")),
                    "received_at": received_at,
                }
            roll_price = float(ltp)

    if roll_price:
        _roll_options(ws, roll_price)


def _reset_bar():
    _bar.update({"ts": None, "open": None, "high": None,
                 "low": None, "close": None, "oi": 0,
                 "last_quantity": 0, "average_traded_price": None,
                 "total_buy_quantity": 0, "total_sell_quantity": 0,
                 "oi_day_high": 0, "oi_day_low": 0, "last_trade_ts": None,
                 "tick_count": 0, "l1_order_flow_imbalance": 0,
                 "depth": None, "last_tick_received_at": None})


# ── DB writer (flush thread) ───────────────────────────────────────────────────

# Phase-1: additive columns on the pre-existing mcx_ohlc table. Self-healing
# bootstrap mirrors _ensure_depth_table()'s pattern -- ADD COLUMN IF NOT
# EXISTS is idempotent and safe to run on every (re)connect regardless of
# whether schema.sql has already been applied on this database.
_OHLC_COLUMNS_SQL = """
    ALTER TABLE mcx_ohlc ADD COLUMN IF NOT EXISTS session_phase VARCHAR(20);
    ALTER TABLE mcx_ohlc ADD COLUMN IF NOT EXISTS quality_flag VARCHAR(20);
"""


def _ensure_ohlc_columns(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(_OHLC_COLUMNS_SQL)
    conn.commit()


def _write_bar(conn, ts, open_, high, low, close, volume, oi, tradingsymbol="",
               session_phase=None, quality_flag=None):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO mcx_ohlc
                (ts, instrument, interval, tradingsymbol, open, high, low, close, volume, oi,
                 session_phase, quality_flag)
            VALUES (%s, %s, 'second', %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ts, instrument, interval) DO UPDATE SET
                tradingsymbol = EXCLUDED.tradingsymbol,
                open   = EXCLUDED.open,
                high   = EXCLUDED.high,
                low    = EXCLUDED.low,
                close  = EXCLUDED.close,
                volume = EXCLUDED.volume,
                oi     = EXCLUDED.oi,
                session_phase = EXCLUDED.session_phase,
                quality_flag  = EXCLUDED.quality_flag
        """, (ts, SYMBOL, tradingsymbol, open_, high, low, close, volume, oi,
              session_phase, quality_flag))
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

    -- Phase-1 (see CRUDE_DATA_PHASE1_IMPLEMENTATION.md, trade-bot repo): all
    -- additive/nullable. "_close" for spread/microprice/imbalance_l1/
    -- imbalance_l5/bid_depth_l5/ask_depth_l5 already exist above (spread,
    -- microprice, book_imbalance_l1, book_imbalance_l5, bid_quantity_total,
    -- ask_quantity_total) -- not duplicated. bid/ask_depth_l1 are genuinely
    -- new (no existing column stores raw top-of-book quantity, only the
    -- imbalance derived from it).
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS spread_open DOUBLE PRECISION;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS spread_min DOUBLE PRECISION;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS spread_max DOUBLE PRECISION;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS microprice_open DOUBLE PRECISION;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS microprice_min DOUBLE PRECISION;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS microprice_max DOUBLE PRECISION;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS microprice_change_1s DOUBLE PRECISION;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS imbalance_l1_open DOUBLE PRECISION;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS imbalance_l1_min DOUBLE PRECISION;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS imbalance_l1_max DOUBLE PRECISION;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS imbalance_l1_change_1s DOUBLE PRECISION;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS imbalance_l5_open DOUBLE PRECISION;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS imbalance_l5_min DOUBLE PRECISION;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS imbalance_l5_max DOUBLE PRECISION;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS imbalance_l5_change_1s DOUBLE PRECISION;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS bid_depth_l1_open BIGINT;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS bid_depth_l1_min BIGINT;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS bid_depth_l1_max BIGINT;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS bid_depth_l1_close BIGINT;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS ask_depth_l1_open BIGINT;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS ask_depth_l1_min BIGINT;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS ask_depth_l1_max BIGINT;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS ask_depth_l1_close BIGINT;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS bid_depth_l5_open BIGINT;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS bid_depth_l5_min BIGINT;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS bid_depth_l5_max BIGINT;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS bid_depth_l5_change_1s BIGINT;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS ask_depth_l5_open BIGINT;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS ask_depth_l5_min BIGINT;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS ask_depth_l5_max BIGINT;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS ask_depth_l5_change_1s BIGINT;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS depth_update_count INTEGER;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS best_bid_change_count INTEGER;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS best_ask_change_count INTEGER;
    -- INFERRED, not ground truth -- see options/crude_tick_aggregator.py
    -- module docstring and CRUDE_DATA_PHASE1_IMPLEMENTATION.md before using.
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS inferred_buy_volume_1s BIGINT;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS inferred_sell_volume_1s BIGINT;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS inferred_signed_volume_1s BIGINT;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS trade_classified_volume_1s BIGINT;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS trade_unclassified_volume_1s BIGINT;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS trade_classification_confidence DOUBLE PRECISION;
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS session_phase VARCHAR(20);
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS quality_flag VARCHAR(20);
    ALTER TABLE mcx_futures_depth ADD COLUMN IF NOT EXISTS data_age_ms INTEGER;
"""


# Phase-1 safety net: guarantees every column _write_depth's SQL references
# is present in the dict passed to it, even in the defensive/unexpected case
# where _accum.flush() returns None despite _bar having data this second
# (should not happen given both are fed by the same tick loop under the same
# lock, but a missing dict key would otherwise raise inside the critical
# flush path -- None values are always safe to insert into nullable columns).
_EMPTY_ACCUM_ROW = {
    "spread_open": None, "spread_min": None, "spread_max": None,
    "microprice_open": None, "microprice_min": None, "microprice_max": None,
    "microprice_change_1s": None,
    "imbalance_l1_open": None, "imbalance_l1_min": None, "imbalance_l1_max": None,
    "imbalance_l1_change_1s": None,
    "imbalance_l5_open": None, "imbalance_l5_min": None, "imbalance_l5_max": None,
    "imbalance_l5_change_1s": None,
    "bid_depth_l1_open": None, "bid_depth_l1_min": None, "bid_depth_l1_max": None,
    "bid_depth_l1_close": None,
    "ask_depth_l1_open": None, "ask_depth_l1_min": None, "ask_depth_l1_max": None,
    "ask_depth_l1_close": None,
    "bid_depth_l5_open": None, "bid_depth_l5_min": None, "bid_depth_l5_max": None,
    "bid_depth_l5_change_1s": None,
    "ask_depth_l5_open": None, "ask_depth_l5_min": None, "ask_depth_l5_max": None,
    "ask_depth_l5_change_1s": None,
    "depth_update_count": None, "best_bid_change_count": None, "best_ask_change_count": None,
    "inferred_buy_volume_1s": None, "inferred_sell_volume_1s": None,
    "inferred_signed_volume_1s": None,
    "trade_classified_volume_1s": None, "trade_unclassified_volume_1s": None,
    "trade_classification_confidence": None,
}


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
                book_imbalance_l1, book_imbalance_l5, l1_order_flow_imbalance,
                spread_open, spread_min, spread_max,
                microprice_open, microprice_min, microprice_max, microprice_change_1s,
                imbalance_l1_open, imbalance_l1_min, imbalance_l1_max, imbalance_l1_change_1s,
                imbalance_l5_open, imbalance_l5_min, imbalance_l5_max, imbalance_l5_change_1s,
                bid_depth_l1_open, bid_depth_l1_min, bid_depth_l1_max, bid_depth_l1_close,
                ask_depth_l1_open, ask_depth_l1_min, ask_depth_l1_max, ask_depth_l1_close,
                bid_depth_l5_open, bid_depth_l5_min, bid_depth_l5_max, bid_depth_l5_change_1s,
                ask_depth_l5_open, ask_depth_l5_min, ask_depth_l5_max, ask_depth_l5_change_1s,
                depth_update_count, best_bid_change_count, best_ask_change_count,
                inferred_buy_volume_1s, inferred_sell_volume_1s, inferred_signed_volume_1s,
                trade_classified_volume_1s, trade_unclassified_volume_1s,
                trade_classification_confidence,
                session_phase, quality_flag, data_age_ms
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
                %(book_imbalance_l1)s, %(book_imbalance_l5)s, %(l1_order_flow_imbalance)s,
                %(spread_open)s, %(spread_min)s, %(spread_max)s,
                %(microprice_open)s, %(microprice_min)s, %(microprice_max)s, %(microprice_change_1s)s,
                %(imbalance_l1_open)s, %(imbalance_l1_min)s, %(imbalance_l1_max)s, %(imbalance_l1_change_1s)s,
                %(imbalance_l5_open)s, %(imbalance_l5_min)s, %(imbalance_l5_max)s, %(imbalance_l5_change_1s)s,
                %(bid_depth_l1_open)s, %(bid_depth_l1_min)s, %(bid_depth_l1_max)s, %(bid_depth_l1_close)s,
                %(ask_depth_l1_open)s, %(ask_depth_l1_min)s, %(ask_depth_l1_max)s, %(ask_depth_l1_close)s,
                %(bid_depth_l5_open)s, %(bid_depth_l5_min)s, %(bid_depth_l5_max)s, %(bid_depth_l5_change_1s)s,
                %(ask_depth_l5_open)s, %(ask_depth_l5_min)s, %(ask_depth_l5_max)s, %(ask_depth_l5_change_1s)s,
                %(depth_update_count)s, %(best_bid_change_count)s, %(best_ask_change_count)s,
                %(inferred_buy_volume_1s)s, %(inferred_sell_volume_1s)s, %(inferred_signed_volume_1s)s,
                %(trade_classified_volume_1s)s, %(trade_unclassified_volume_1s)s,
                %(trade_classification_confidence)s,
                %(session_phase)s, %(quality_flag)s, %(data_age_ms)s
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
                spread_open = EXCLUDED.spread_open,
                spread_min = EXCLUDED.spread_min,
                spread_max = EXCLUDED.spread_max,
                microprice_open = EXCLUDED.microprice_open,
                microprice_min = EXCLUDED.microprice_min,
                microprice_max = EXCLUDED.microprice_max,
                microprice_change_1s = EXCLUDED.microprice_change_1s,
                imbalance_l1_open = EXCLUDED.imbalance_l1_open,
                imbalance_l1_min = EXCLUDED.imbalance_l1_min,
                imbalance_l1_max = EXCLUDED.imbalance_l1_max,
                imbalance_l1_change_1s = EXCLUDED.imbalance_l1_change_1s,
                imbalance_l5_open = EXCLUDED.imbalance_l5_open,
                imbalance_l5_min = EXCLUDED.imbalance_l5_min,
                imbalance_l5_max = EXCLUDED.imbalance_l5_max,
                imbalance_l5_change_1s = EXCLUDED.imbalance_l5_change_1s,
                bid_depth_l1_open = EXCLUDED.bid_depth_l1_open,
                bid_depth_l1_min = EXCLUDED.bid_depth_l1_min,
                bid_depth_l1_max = EXCLUDED.bid_depth_l1_max,
                bid_depth_l1_close = EXCLUDED.bid_depth_l1_close,
                ask_depth_l1_open = EXCLUDED.ask_depth_l1_open,
                ask_depth_l1_min = EXCLUDED.ask_depth_l1_min,
                ask_depth_l1_max = EXCLUDED.ask_depth_l1_max,
                ask_depth_l1_close = EXCLUDED.ask_depth_l1_close,
                bid_depth_l5_open = EXCLUDED.bid_depth_l5_open,
                bid_depth_l5_min = EXCLUDED.bid_depth_l5_min,
                bid_depth_l5_max = EXCLUDED.bid_depth_l5_max,
                bid_depth_l5_change_1s = EXCLUDED.bid_depth_l5_change_1s,
                ask_depth_l5_open = EXCLUDED.ask_depth_l5_open,
                ask_depth_l5_min = EXCLUDED.ask_depth_l5_min,
                ask_depth_l5_max = EXCLUDED.ask_depth_l5_max,
                ask_depth_l5_change_1s = EXCLUDED.ask_depth_l5_change_1s,
                depth_update_count = EXCLUDED.depth_update_count,
                best_bid_change_count = EXCLUDED.best_bid_change_count,
                best_ask_change_count = EXCLUDED.best_ask_change_count,
                inferred_buy_volume_1s = EXCLUDED.inferred_buy_volume_1s,
                inferred_sell_volume_1s = EXCLUDED.inferred_sell_volume_1s,
                inferred_signed_volume_1s = EXCLUDED.inferred_signed_volume_1s,
                trade_classified_volume_1s = EXCLUDED.trade_classified_volume_1s,
                trade_unclassified_volume_1s = EXCLUDED.trade_unclassified_volume_1s,
                trade_classification_confidence = EXCLUDED.trade_classification_confidence,
                session_phase = EXCLUDED.session_phase,
                quality_flag  = EXCLUDED.quality_flag,
                data_age_ms   = EXCLUDED.data_age_ms,
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


_OPTION_PRESSURE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS mcx_crude_option_pressure_second (
        ts                          TIMESTAMPTZ PRIMARY KEY,
        expiry                      DATE NOT NULL,
        futures_tradingsymbol       VARCHAR(40) NOT NULL,
        futures_ltp                 NUMERIC(12,2) NOT NULL,
        futures_received_at         TIMESTAMPTZ NOT NULL,
        futures_age_ms              INTEGER NOT NULL,
        atm_strike                  INTEGER NOT NULL,
        strike_step                 INTEGER NOT NULL DEFAULT 50,
        wings                       SMALLINT NOT NULL,
        subscribed_contracts        SMALLINT NOT NULL,
        fresh_contracts             SMALLINT NOT NULL,
        total_tick_count            INTEGER NOT NULL,
        max_option_age_ms           INTEGER,
        total_ce_oi                 BIGINT,
        total_pe_oi                 BIGINT,
        ce_oi_delta                 BIGINT,
        pe_oi_delta                 BIGINT,
        pcr_oi                      DOUBLE PRECISION,
        oi_imbalance                DOUBLE PRECISION,
        oi_delta_imbalance          DOUBLE PRECISION,
        ce_volume_delta             BIGINT,
        pe_volume_delta             BIGINT,
        volume_imbalance            DOUBLE PRECISION,
        call_wall_strike            INTEGER,
        put_wall_strike             INTEGER,
        call_wall_oi                BIGINT,
        put_wall_oi                 BIGINT,
        distance_to_call_wall       DOUBLE PRECISION,
        distance_to_put_wall        DOUBLE PRECISION,
        atm_ce_ltp                  NUMERIC(12,2),
        atm_pe_ltp                  NUMERIC(12,2),
        atm_straddle                NUMERIC(12,2),
        atm_premium_skew            DOUBLE PRECISION,
        ce_book_imbalance           DOUBLE PRECISION,
        pe_book_imbalance           DOUBLE PRECISION,
        directional_book_pressure   DOUBLE PRECISION,
        chain                       JSONB NOT NULL,
        available_at                TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
    );
    CREATE INDEX IF NOT EXISTS idx_mcx_crude_option_pressure_expiry
        ON mcx_crude_option_pressure_second (expiry, ts DESC);
"""


def _ensure_option_pressure_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(_OPTION_PRESSURE_TABLE_SQL)
    conn.commit()


def _copy_option_snapshot() -> Optional[dict]:
    observed_at = datetime.now(IST)
    with _lock:
        if (
            not _active_option_tokens
            or _active_option_atm is None
            or _option_expiry is None
            or _last_futures_ltp is None
            or _last_futures_received_at is None
        ):
            return None
        futures_age_ms = max(
            0,
            round((observed_at - _last_futures_received_at).total_seconds() * 1000),
        )
        if futures_age_ms > OPTION_FRESHNESS_SECONDS * 1000:
            return None
        options = [
            (
                token,
                dict(_option_meta[token]),
                dict(_option_latest_ticks[token]),
                int(_option_tick_counts.get(token, 0)),
            )
            for token in sorted(_active_option_tokens)
            if token in _option_meta and token in _option_latest_ticks
        ]
        for token in _active_option_tokens:
            _option_tick_counts[token] = 0
        return {
            "observed_at": observed_at,
            "futures_ltp": _last_futures_ltp,
            "futures_received_at": _last_futures_received_at,
            "futures_age_ms": futures_age_ms,
            "atm": _active_option_atm,
            "expiry": _option_expiry,
            "subscribed_contracts": len(_active_option_tokens),
            "options": options,
        }


def _option_depth_values(tick: dict) -> tuple:
    depth = _normalise_depth(tick.get("depth"))
    if not depth:
        return None, None, 0, 0, None
    bid_prices = depth.get("bid_prices") or []
    ask_prices = depth.get("ask_prices") or []
    bid_qty = sum(int(value or 0) for value in (depth.get("bid_quantities") or [])[:5])
    ask_qty = sum(int(value or 0) for value in (depth.get("ask_quantities") or [])[:5])
    total = bid_qty + ask_qty
    imbalance = (bid_qty - ask_qty) / total if total else None
    return (
        float(bid_prices[0]) if bid_prices else None,
        float(ask_prices[0]) if ask_prices else None,
        bid_qty,
        ask_qty,
        imbalance,
    )


def _ratio_difference(left: float, right: float) -> Optional[float]:
    total = abs(left) + abs(right)
    return (left - right) / total if total else None


def _build_option_pressure(ts: datetime, snapshot: dict) -> Optional[dict]:
    chain = []
    max_age_ms = 0
    now = snapshot["observed_at"]

    for token, meta, tick, tick_count in snapshot["options"]:
        received_at = tick.get("_received_at")
        if not isinstance(received_at, datetime):
            continue
        age_ms = max(0, round((now - received_at).total_seconds() * 1000))
        if age_ms > OPTION_FRESHNESS_SECONDS * 1000:
            continue

        oi = int(tick.get("oi") or 0)
        volume_day = int(tick.get("volume_traded") or 0)
        with _lock:
            previous_oi = _option_prev_oi.get(token, oi)
            previous_volume = _option_prev_volume.get(token, volume_day)
            _option_prev_oi[token] = oi
            _option_prev_volume[token] = volume_day
        bid, ask, bid_qty, ask_qty, book_imbalance = _option_depth_values(tick)
        chain.append({
            "instrument_token": token,
            "tradingsymbol": meta["tradingsymbol"],
            "strike": int(meta["strike"]),
            "option_type": meta["option_type"],
            "ltp": float(tick.get("last_price") or 0),
            "last_quantity": int(tick.get("last_traded_quantity") or 0),
            "oi": oi,
            "oi_delta": oi - previous_oi,
            "volume_day": volume_day,
            "volume_delta": max(0, volume_day - previous_volume),
            "best_bid": bid,
            "best_ask": ask,
            "bid_quantity_l5": bid_qty,
            "ask_quantity_l5": ask_qty,
            "book_imbalance": book_imbalance,
            "tick_count": tick_count,
            "age_ms": age_ms,
        })
        max_age_ms = max(max_age_ms, age_ms)

    if not chain:
        return None

    calls = [row for row in chain if row["option_type"] == "CE"]
    puts = [row for row in chain if row["option_type"] == "PE"]
    if not calls or not puts:
        return None

    total_ce_oi = sum(row["oi"] for row in calls)
    total_pe_oi = sum(row["oi"] for row in puts)
    ce_oi_delta = sum(row["oi_delta"] for row in calls)
    pe_oi_delta = sum(row["oi_delta"] for row in puts)
    ce_volume_delta = sum(row["volume_delta"] for row in calls)
    pe_volume_delta = sum(row["volume_delta"] for row in puts)

    def side_book(rows: list) -> Optional[float]:
        bid_qty = sum(row["bid_quantity_l5"] for row in rows)
        ask_qty = sum(row["ask_quantity_l5"] for row in rows)
        return _ratio_difference(bid_qty, ask_qty)

    ce_book = side_book(calls)
    pe_book = side_book(puts)
    call_wall = max(calls, key=lambda row: row["oi"])
    put_wall = max(puts, key=lambda row: row["oi"])
    atm = int(snapshot["atm"])
    atm_ce = next((row for row in calls if row["strike"] == atm), None)
    atm_pe = next((row for row in puts if row["strike"] == atm), None)
    atm_ce_ltp = atm_ce["ltp"] if atm_ce else None
    atm_pe_ltp = atm_pe["ltp"] if atm_pe else None
    straddle = (
        atm_ce_ltp + atm_pe_ltp
        if atm_ce_ltp is not None and atm_pe_ltp is not None
        else None
    )
    futures_ltp = float(snapshot["futures_ltp"])

    return {
        "ts": ts,
        "expiry": snapshot["expiry"],
        "futures_tradingsymbol": _tradingsymbol,
        "futures_ltp": futures_ltp,
        "futures_received_at": snapshot["futures_received_at"],
        "futures_age_ms": snapshot["futures_age_ms"],
        "atm_strike": atm,
        "strike_step": 50,
        "wings": OPTION_WINGS,
        "subscribed_contracts": snapshot["subscribed_contracts"],
        "fresh_contracts": len(chain),
        "total_tick_count": sum(row["tick_count"] for row in chain),
        "max_option_age_ms": max_age_ms,
        "total_ce_oi": total_ce_oi,
        "total_pe_oi": total_pe_oi,
        "ce_oi_delta": ce_oi_delta,
        "pe_oi_delta": pe_oi_delta,
        "pcr_oi": total_pe_oi / total_ce_oi if total_ce_oi else None,
        "oi_imbalance": _ratio_difference(total_pe_oi, total_ce_oi),
        "oi_delta_imbalance": _ratio_difference(pe_oi_delta, ce_oi_delta),
        "ce_volume_delta": ce_volume_delta,
        "pe_volume_delta": pe_volume_delta,
        "volume_imbalance": _ratio_difference(ce_volume_delta, pe_volume_delta),
        "call_wall_strike": call_wall["strike"],
        "put_wall_strike": put_wall["strike"],
        "call_wall_oi": call_wall["oi"],
        "put_wall_oi": put_wall["oi"],
        "distance_to_call_wall": call_wall["strike"] - futures_ltp,
        "distance_to_put_wall": futures_ltp - put_wall["strike"],
        "atm_ce_ltp": atm_ce_ltp,
        "atm_pe_ltp": atm_pe_ltp,
        "atm_straddle": straddle,
        "atm_premium_skew": (
            (atm_pe_ltp - atm_ce_ltp) / straddle if straddle else None
        ),
        "ce_book_imbalance": ce_book,
        "pe_book_imbalance": pe_book,
        "directional_book_pressure": (
            (ce_book - pe_book) / 2 if ce_book is not None and pe_book is not None else None
        ),
        "chain": json.dumps(chain, separators=(",", ":")),
    }


def _write_option_pressure(conn, row: dict) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO mcx_crude_option_pressure_second (
                ts, expiry, futures_tradingsymbol, futures_ltp,
                futures_received_at, futures_age_ms, atm_strike, strike_step,
                wings, subscribed_contracts, fresh_contracts, total_tick_count,
                max_option_age_ms, total_ce_oi, total_pe_oi, ce_oi_delta,
                pe_oi_delta, pcr_oi, oi_imbalance, oi_delta_imbalance,
                ce_volume_delta, pe_volume_delta, volume_imbalance,
                call_wall_strike, put_wall_strike, call_wall_oi, put_wall_oi,
                distance_to_call_wall, distance_to_put_wall,
                atm_ce_ltp, atm_pe_ltp, atm_straddle, atm_premium_skew,
                ce_book_imbalance, pe_book_imbalance, directional_book_pressure,
                chain
            ) VALUES (
                %(ts)s, %(expiry)s, %(futures_tradingsymbol)s, %(futures_ltp)s,
                %(futures_received_at)s, %(futures_age_ms)s, %(atm_strike)s, %(strike_step)s,
                %(wings)s, %(subscribed_contracts)s, %(fresh_contracts)s, %(total_tick_count)s,
                %(max_option_age_ms)s, %(total_ce_oi)s, %(total_pe_oi)s, %(ce_oi_delta)s,
                %(pe_oi_delta)s, %(pcr_oi)s, %(oi_imbalance)s, %(oi_delta_imbalance)s,
                %(ce_volume_delta)s, %(pe_volume_delta)s, %(volume_imbalance)s,
                %(call_wall_strike)s, %(put_wall_strike)s, %(call_wall_oi)s, %(put_wall_oi)s,
                %(distance_to_call_wall)s, %(distance_to_put_wall)s,
                %(atm_ce_ltp)s, %(atm_pe_ltp)s, %(atm_straddle)s, %(atm_premium_skew)s,
                %(ce_book_imbalance)s, %(pe_book_imbalance)s, %(directional_book_pressure)s,
                %(chain)s::jsonb
            )
            ON CONFLICT (ts) DO UPDATE SET
                expiry = EXCLUDED.expiry,
                futures_tradingsymbol = EXCLUDED.futures_tradingsymbol,
                futures_ltp = EXCLUDED.futures_ltp,
                futures_received_at = EXCLUDED.futures_received_at,
                futures_age_ms = EXCLUDED.futures_age_ms,
                atm_strike = EXCLUDED.atm_strike,
                subscribed_contracts = EXCLUDED.subscribed_contracts,
                fresh_contracts = EXCLUDED.fresh_contracts,
                total_tick_count = EXCLUDED.total_tick_count,
                max_option_age_ms = EXCLUDED.max_option_age_ms,
                total_ce_oi = EXCLUDED.total_ce_oi,
                total_pe_oi = EXCLUDED.total_pe_oi,
                ce_oi_delta = EXCLUDED.ce_oi_delta,
                pe_oi_delta = EXCLUDED.pe_oi_delta,
                pcr_oi = EXCLUDED.pcr_oi,
                oi_imbalance = EXCLUDED.oi_imbalance,
                oi_delta_imbalance = EXCLUDED.oi_delta_imbalance,
                ce_volume_delta = EXCLUDED.ce_volume_delta,
                pe_volume_delta = EXCLUDED.pe_volume_delta,
                volume_imbalance = EXCLUDED.volume_imbalance,
                call_wall_strike = EXCLUDED.call_wall_strike,
                put_wall_strike = EXCLUDED.put_wall_strike,
                call_wall_oi = EXCLUDED.call_wall_oi,
                put_wall_oi = EXCLUDED.put_wall_oi,
                distance_to_call_wall = EXCLUDED.distance_to_call_wall,
                distance_to_put_wall = EXCLUDED.distance_to_put_wall,
                atm_ce_ltp = EXCLUDED.atm_ce_ltp,
                atm_pe_ltp = EXCLUDED.atm_pe_ltp,
                atm_straddle = EXCLUDED.atm_straddle,
                atm_premium_skew = EXCLUDED.atm_premium_skew,
                ce_book_imbalance = EXCLUDED.ce_book_imbalance,
                pe_book_imbalance = EXCLUDED.pe_book_imbalance,
                directional_book_pressure = EXCLUDED.directional_book_pressure,
                chain = EXCLUDED.chain,
                available_at = clock_timestamp()
        """, row)
    conn.commit()


def _option_pressure_thread() -> None:
    """Persist one compact option-chain pressure snapshot per active second."""
    conn = None
    last_second = None
    while _running:
        _time.sleep(0.1)
        try:
            if conn is None or conn.closed:
                import psycopg2
                conn = psycopg2.connect(
                    os.environ.get("DATABASE_URL", ""),
                    connect_timeout=5,
                    application_name="crude_option_pressure_writer",
                )
                conn.autocommit = False
                _ensure_option_pressure_table(conn)
                logger.info("[CRUDE-OPT] Pressure table ready; isolated writer active.")

            current_second = datetime.now(IST).replace(microsecond=0)
            if current_second == last_second:
                continue
            last_second = current_second
            if not _mcx_open():
                continue
            snapshot = _copy_option_snapshot()
            if not snapshot:
                continue
            row = _build_option_pressure(current_second, snapshot)
            if row:
                _write_option_pressure(conn, row)
        except Exception as exc:
            logger.error("[CRUDE-OPT] Pressure write failed; futures unaffected: %s", exc)
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
            conn = None
            _time.sleep(1)


def _flush_thread():
    """Runs in background. Writes completed 1-sec bars every time second changes."""
    global _prev_vol_cum, _running, _accum, _last_flushed_ts

    conn = None
    last_sec = None
    ohlc_columns_ready = False

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
                ohlc_columns_ready = False
                logger.info("[CRUDE-WS] DB connected.")

            if not ohlc_columns_ready:
                # Phase-1 safety fix (found in the pre-commit safety review):
                # _ensure_depth_table()'s equivalent failure only affects the
                # already-isolated depth-writer thread, but this call sits
                # inside the core OHLC flush loop -- if it raised uncaught,
                # the outer except below would force conn=None and retry the
                # connect+ALTER every iteration forever, permanently blocking
                # existing mcx_ohlc writes (a regression this phase must not
                # introduce). Isolate it: log and proceed either way. If the
                # ALTER genuinely never succeeds, the resulting _write_bar()
                # failure is caught by the existing outer except exactly like
                # any other write error today -- not a new failure mode.
                try:
                    _ensure_ohlc_columns(conn)
                except Exception as exc:
                    logger.error(
                        "[CRUDE-WS] _ensure_ohlc_columns failed (non-fatal, "
                        "existing OHLC writes continue attempting): %s", exc,
                    )
                ohlc_columns_ready = True

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

                    # Phase-1: snapshot + reset the additive accumulator in
                    # the same critical section as _bar, so both describe
                    # exactly the same exchange-second.
                    #
                    # Safety fix (found in the pre-commit safety review): this
                    # whole block previously ran unguarded inside the same
                    # try/except as the pre-existing _bar-flush logic. Any
                    # exception here (a bug in the new aggregator/session-
                    # classification code) would have propagated to the
                    # outer except, forcing conn=None and skipping this
                    # cycle's _write_bar()/_write_depth() entirely -- i.e. a
                    # Phase-1 bug could silently stop existing OHLC/depth
                    # persistence, which Part 7 explicitly forbids. Isolated
                    # so a failure here degrades to NULL session/quality
                    # metadata for this one second, never blocks the
                    # pre-existing write path.
                    accum_row = None
                    session_phase = None
                    quality_flag = None
                    data_age_ms = None
                    try:
                        accum_row = _accum.flush()
                        from config import MCX_HOLIDAYS, MCX_EVENING_ONLY_DAYS
                        session_phase = classify_session_phase(
                            _bar["ts"], MCX_HOLIDAYS, MCX_EVENING_ONLY_DAYS
                        )
                        last_tick_at = _bar["last_tick_received_at"]
                        data_age_ms = (
                            max(0, round((now - last_tick_at).total_seconds() * 1000))
                            if last_tick_at is not None else None
                        )
                        gap_seconds = (
                            (_bar["ts"] - _last_flushed_ts).total_seconds()
                            if _last_flushed_ts is not None else None
                        )
                        quality_flag = classify_quality_flag(
                            session_phase=session_phase,
                            tick_count=_bar["tick_count"],
                            data_age_ms=data_age_ms,
                            gap_seconds=gap_seconds,
                        )
                    except Exception as exc:
                        logger.error(
                            "[CRUDE-WS] Phase-1 aggregation failed (non-fatal, "
                            "existing OHLC/depth write continues with NULL "
                            "session/quality metadata for this second): %s", exc,
                        )
                    finally:
                        # Always start the next second clean, even if flush()
                        # itself raised -- never let a failed second's state
                        # leak into the next one.
                        _accum = SecondAccumulator()

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
                            **_EMPTY_ACCUM_ROW,
                            **(accum_row or {}),
                            "session_phase": session_phase,
                            "quality_flag": quality_flag,
                            "data_age_ms": data_age_ms,
                        }
                        # accum_row already carries open/high/low/close keys
                        # (its own OHLC bookkeeping, kept for cross-check
                        # only) -- do not let them shadow the depth-side
                        # fields the INSERT actually reads by those same
                        # generic names.
                        for _k in ("open", "high", "low", "close", "tick_count"):
                            depth_snapshot.pop(_k, None)
                        depth_snapshot["tick_count"] = _bar["tick_count"]

                    snap = (
                        _bar["ts"], _bar["open"], _bar["high"],
                        _bar["low"], _bar["close"], vol_delta, _bar["oi"],
                        depth_snapshot, session_phase, quality_flag,
                    )
                    _reset_bar()
                    _last_flushed_ts = snap[0]

                else:
                    snap = None
                    # Keep _prev_vol_cum unchanged (no trade this second)

            if snap:
                ts, o, h, l, c, v, oi, depth, session_phase, quality_flag = snap
                # Preserve the existing critical path: commit OHLC first.
                _write_bar(conn, ts, o, h, l, c, v, oi, _tradingsymbol,
                           session_phase=session_phase, quality_flag=quality_flag)
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


# ── Phase-1 collection-health beacon (Part 6/7) ─────────────────────────────

_HEALTH_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS crude_collection_health (
        id                      BIGSERIAL       PRIMARY KEY,
        checked_at              TIMESTAMPTZ     NOT NULL DEFAULT clock_timestamp(),
        collector_name          VARCHAR(60)     NOT NULL,
        instrument              VARCHAR(20)     NOT NULL DEFAULT 'CRUDEOIL',
        session_phase           VARCHAR(20),
        last_source_ts          TIMESTAMPTZ,
        last_db_write_at        TIMESTAMPTZ,
        source_age_ms           BIGINT,
        window_start            TIMESTAMPTZ,
        window_end              TIMESTAMPTZ,
        expected_seconds        INTEGER,
        received_seconds        INTEGER,
        coverage_pct            DOUBLE PRECISION,
        gap_count               INTEGER,
        largest_gap_seconds     DOUBLE PRECISION,
        stale_rows              INTEGER,
        stale_pct               DOUBLE PRECISION,
        websocket_connected     BOOLEAN,
        reconnect_count         INTEGER,
        status                  VARCHAR(20)     NOT NULL,
        status_reason           TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_crude_collection_health_lookup
        ON crude_collection_health (collector_name, checked_at DESC);
"""

_HEALTH_BEACON_INTERVAL_SECONDS = 60


def _ensure_health_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(_HEALTH_TABLE_SQL)
    conn.commit()


def _health_beacon_thread() -> None:
    """Best-effort, isolated. A failure here NEVER affects futures/depth/
    option ingestion -- own connection, all exceptions swallowed, sleeps and
    retries. Only reports facts this process itself can observe
    (websocket_connected, reconnect_count); DB-derived coverage/gap/staleness
    metrics are computed separately by crude_collection_health.py, which can
    run even when this process is down.
    """
    conn = None
    schema_ready = False
    while _running:
        try:
            if conn is None or conn.closed:
                import psycopg2
                conn = psycopg2.connect(
                    os.environ.get("DATABASE_URL", ""),
                    connect_timeout=5,
                    application_name="crude_health_beacon",
                )
                conn.autocommit = False
                schema_ready = False
            if not schema_ready:
                _ensure_health_table(conn)
                schema_ready = True

            from config import MCX_HOLIDAYS, MCX_EVENING_ONLY_DAYS
            now = datetime.now(IST)
            phase = classify_session_phase(now, MCX_HOLIDAYS, MCX_EVENING_ONLY_DAYS)
            connected = _websocket_connected
            reconnects = max(0, _connection_attempts - 1)

            if phase in ("CLOSED",):
                status, reason = "NON_TRADING", "Outside MCX session per calendar."
            elif connected:
                status, reason = "HEALTHY", "WebSocket connected."
            else:
                status, reason = "DISCONNECTED", "WebSocket not connected during an active session."

            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO crude_collection_health
                        (collector_name, instrument, session_phase,
                         websocket_connected, reconnect_count, status, status_reason)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, ("crudeoil_ws_live", SYMBOL, phase, connected, reconnects, status, reason))
            conn.commit()
        except Exception as exc:
            logger.warning("[CRUDE-HEALTH] Beacon write failed (non-critical): %s", exc)
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
            conn = None
            schema_ready = False

        for _ in range(_HEALTH_BEACON_INTERVAL_SECONDS):
            if not _running:
                break
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
    global _token_id, _running, _previous_top, _connection_attempts, _websocket_connected

    logger.info("[CRUDE-WS] CRUDEOIL 1-second WebSocket daemon starting.")

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

    option_writer = threading.Thread(
        target=_option_pressure_thread,
        daemon=True,
        name="crude-option-pressure-writer",
    )
    option_writer.start()

    health_beacon = threading.Thread(
        target=_health_beacon_thread,
        daemon=True,
        name="crude-health-beacon",
    )
    health_beacon.start()

    consecutive_timeouts = 0

    while True:
        # ── Sleep when MCX is closed ───────────────────────────────────────────
        if not _mcx_open():
            _websocket_connected = False
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
            _reset_option_session()
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
            global _websocket_connected
            _connected_evt.set()
            _websocket_connected = True
            logger.info(
                "[CRUDE-WS] Connected — futures token %d; options select on first tick",
                _token_id,
            )
            ws.subscribe([_token_id])
            ws.set_mode(ws.MODE_FULL, [_token_id])
            once("crude_ws_start",
                 f"\U0001f7e2 CRUDEOIL 1-sec Started\n"
                 f"{now_ist()}\n"
                 f"Contract token: {_token_id}")

        def on_ticks(ws, ticks):
            _on_ticks(ws, ticks)

        def on_close(ws, code, reason):
            global _websocket_connected
            _websocket_connected = False
            logger.warning("[CRUDE-WS] Connection closed (%s): %s", code, reason)

        def on_error(ws, code, reason):
            global _websocket_connected
            _websocket_connected = False
            logger.error("[CRUDE-WS] Error (%s): %s", code, reason)

        kws.on_connect = on_connect
        kws.on_ticks   = on_ticks
        kws.on_close   = on_close
        kws.on_error   = on_error

        try:
            _connection_attempts += 1
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
                        _websocket_connected = False
                        once("crude_ws_end",
                             f"\U0001f534 CRUDEOIL 1-sec Ended\n"
                             f"{now_ist()}\n"
                             f"Bars today : {_today_rows():,}\n"
                             f"DB         : {db_size()}")
                        break
                    if not kws.is_connected():
                        logger.warning("[CRUDE-WS] Disconnected mid-session — will reconnect.")
                        _websocket_connected = False
                        break
                    _time.sleep(10)

        except Exception as exc:
            logger.error("[CRUDE-WS] Unexpected error: %s", exc)

        # Explicitly stop the old KiteTicker before reconnecting
        try:
            kws.close()
        except Exception:
            pass
        _websocket_connected = False
        _reset_option_session()

        _time.sleep(30)  # 30s cooldown — prevents duplicate sessions on Kite


if __name__ == "__main__":
    main()
