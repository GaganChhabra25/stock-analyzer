"""NIFTY 1-second WebSocket collector.

Stores the nearest weekly NIFTY option chain at ATM ±10 strikes plus one
near-month NIFTY futures snapshot every second. This service is isolated from
MCX ingestion and runs in its own Docker container.
"""

from __future__ import annotations

import logging
import math
import os
import statistics
import sys
import threading
import time as _time
from collections import deque
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from kiteconnect import KiteTicker
from psycopg2.extras import execute_values

from logging_config import configure_logging
from options.greeks import calculate_greeks, implied_volatility
from options.kite_auth import API_KEY, get_kite, load_access_token

configure_logging()
logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
SYMBOL = "NIFTY"
SPOT_TRADING_SYMBOL = "NIFTY 50"
VIX_TRADING_SYMBOL = "INDIA VIX"
STRIKE_STEP = 50
N_STRIKES = 10
RISK_FREE_RATE = 0.07

_lock = threading.RLock()
_latest_ticks: dict[int, dict] = {}
_option_meta: dict[int, dict] = {}
_option_universe: dict[tuple[int, str], dict] = {}
_active_option_tokens: set[int] = set()
_last_oi: dict[tuple[date, int, str], int] = {}
_feature_history: deque[dict] = deque(maxlen=2_000)
_previous_feature_totals: dict[str, float] = {}
_spot_token = 0
_vix_token = 0
_future_meta: dict = {}
_active_atm: int | None = None
_session_active = False

FEATURE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS nifty_features (
    ts TIMESTAMPTZ PRIMARY KEY, trade_date DATE NOT NULL,
    underlying_ltp DOUBLE PRECISION, dte INTEGER,
    return_1min DOUBLE PRECISION, return_5min DOUBLE PRECISION,
    return_15min DOUBLE PRECISION, rolling_vol_5 DOUBLE PRECISION,
    rolling_vol_15 DOUBLE PRECISION, time_bucket_enc SMALLINT,
    atm_strike DOUBLE PRECISION, atm_ce_ltp DOUBLE PRECISION, atm_pe_ltp DOUBLE PRECISION,
    atm_ce_iv DOUBLE PRECISION, atm_pe_iv DOUBLE PRECISION,
    atm_ce_oi DOUBLE PRECISION, atm_pe_oi DOUBLE PRECISION,
    atm_ce_delta DOUBLE PRECISION, atm_pe_delta DOUBLE PRECISION,
    atm_ce_gamma DOUBLE PRECISION, atm_pe_gamma DOUBLE PRECISION,
    atm_ce_vega DOUBLE PRECISION, atm_pe_vega DOUBLE PRECISION,
    atm_ce_theta DOUBLE PRECISION, atm_pe_theta DOUBLE PRECISION,
    iv_skew DOUBLE PRECISION, straddle_price DOUBLE PRECISION, pcr DOUBLE PRECISION,
    oi_imbalance DOUBLE PRECISION, ce_oi_buildup SMALLINT, pe_oi_buildup SMALLINT,
    ce_oi_wt_strike DOUBLE PRECISION, pe_oi_wt_strike DOUBLE PRECISION,
    total_ce_oi DOUBLE PRECISION, total_pe_oi DOUBLE PRECISION,
    ce_oi_change DOUBLE PRECISION, pe_oi_change DOUBLE PRECISION,
    ce_delta_sum DOUBLE PRECISION, pe_delta_sum DOUBLE PRECISION,
    gamma_sum DOUBLE PRECISION, vega_sum DOUBLE PRECISION, theta_sum DOUBLE PRECISION,
    delta_imbalance DOUBLE PRECISION, gamma_spike SMALLINT,
    vega_change DOUBLE PRECISION, theta_decay DOUBLE PRECISION,
    dist_from_atm DOUBLE PRECISION, pe_max_oi_strike DOUBLE PRECISION,
    ce_max_oi_strike DOUBLE PRECISION, dist_from_support DOUBLE PRECISION,
    dist_from_resistance DOUBLE PRECISION,
    target_direction SMALLINT, target_move DOUBLE PRECISION,
    target_30min DOUBLE PRECISION, eod_close DOUBLE PRECISION,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    ms_vix DOUBLE PRECISION, ms_pcr_oi DOUBLE PRECISION,
    ms_atm_straddle DOUBLE PRECISION, ms_expected_move DOUBLE PRECISION,
    ms_call_oi_wall DOUBLE PRECISION, ms_put_oi_wall DOUBLE PRECISION,
    ms_dist_call_wall DOUBLE PRECISION, ms_dist_put_wall DOUBLE PRECISION,
    prev_usdinr DOUBLE PRECISION, prev_wti DOUBLE PRECISION,
    prev_brent DOUBLE PRECISION, prev_natgas DOUBLE PRECISION,
    fii_net_fut DOUBLE PRECISION, fii_net_opt DOUBLE PRECISION,
    fii_pc_ratio DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_nifty_features_trade_date_ts
    ON nifty_features (trade_date, ts DESC);

CREATE TABLE IF NOT EXISTS nifty_expiry_features (
    ts TIMESTAMPTZ NOT NULL, expiry_date DATE NOT NULL, trade_date DATE NOT NULL,
    underlying_ltp DOUBLE PRECISION, dte INTEGER, atm_strike DOUBLE PRECISION,
    total_ce_oi DOUBLE PRECISION, total_pe_oi DOUBLE PRECISION,
    oi_imbalance DOUBLE PRECISION, pcr_oi DOUBLE PRECISION,
    ce_oi_strike_1 DOUBLE PRECISION, ce_oi_strike_2 DOUBLE PRECISION,
    ce_oi_strike_3 DOUBLE PRECISION, pe_oi_strike_1 DOUBLE PRECISION,
    pe_oi_strike_2 DOUBLE PRECISION, pe_oi_strike_3 DOUBLE PRECISION,
    ce_oi_wt_strike DOUBLE PRECISION, pe_oi_wt_strike DOUBLE PRECISION,
    max_pain_strike DOUBLE PRECISION, dist_from_max_pain DOUBLE PRECISION,
    atm_ce_iv DOUBLE PRECISION, atm_pe_iv DOUBLE PRECISION,
    iv_skew DOUBLE PRECISION, atm_iv_change_30min DOUBLE PRECISION,
    straddle_price DOUBLE PRECISION, implied_move_pct DOUBLE PRECISION,
    gex_ce DOUBLE PRECISION, gex_pe DOUBLE PRECISION, gex_net DOUBLE PRECISION,
    delta_imbalance DOUBLE PRECISION,
    ms_vix DOUBLE PRECISION, ms_pcr_oi DOUBLE PRECISION,
    ms_atm_straddle DOUBLE PRECISION, ms_expected_move DOUBLE PRECISION,
    ms_call_oi_wall DOUBLE PRECISION, ms_put_oi_wall DOUBLE PRECISION,
    ms_dist_call_wall DOUBLE PRECISION, ms_dist_put_wall DOUBLE PRECISION,
    prev_usdinr DOUBLE PRECISION, prev_wti DOUBLE PRECISION,
    prev_brent DOUBLE PRECISION, prev_natgas DOUBLE PRECISION,
    fii_net_fut DOUBLE PRECISION, fii_net_opt DOUBLE PRECISION,
    fii_pc_ratio DOUBLE PRECISION,
    target_expiry_price DOUBLE PRECISION, target_range_low DOUBLE PRECISION,
    target_range_high DOUBLE PRECISION, target_pin SMALLINT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (ts, expiry_date)
);
CREATE INDEX IF NOT EXISTS idx_nifty_expiry_features_expiry_ts
    ON nifty_expiry_features (expiry_date, ts DESC);
"""


def _nse_open() -> bool:
    from config import NSE_HOLIDAYS

    now = datetime.now(IST)
    if now.weekday() >= 5 or now.date().isoformat() in NSE_HOLIDAYS:
        return False
    return time(9, 14) <= now.time() <= time(15, 31)


def _seconds_until_open() -> int:
    from config import NSE_HOLIDAYS

    now = datetime.now(IST)
    nxt = now.replace(hour=9, minute=14, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    while nxt.weekday() >= 5 or nxt.date().isoformat() in NSE_HOLIDAYS:
        nxt += timedelta(days=1)
    return max(60, int((nxt - now).total_seconds()))


def _nearest_row(frame: pd.DataFrame, instrument_type: str, today: date):
    rows = frame[
        (frame["instrument_type"] == instrument_type)
        & (frame["expiry"] >= today)
    ].sort_values("expiry")
    if rows.empty:
        raise RuntimeError(f"No upcoming NIFTY {instrument_type} instrument")
    return rows.iloc[0]


def select_option_contracts(
    universe: dict[tuple[int, str], dict], atm: int, wings: int = N_STRIKES
) -> dict[int, dict]:
    """Return exactly the available ATM ±wings CE/PE contracts."""
    strikes = {atm + offset * STRIKE_STEP for offset in range(-wings, wings + 1)}
    return {
        int(meta["instrument_token"]): meta
        for (strike, option_type), meta in universe.items()
        if strike in strikes and option_type in ("CE", "PE")
    }


def nifty_contract_mask(frame: pd.DataFrame) -> pd.Series:
    """Match NIFTY derivatives even when Kite leaves the name column blank."""
    names = frame["name"].fillna("") if "name" in frame else pd.Series("", index=frame.index)
    symbols = frame["tradingsymbol"].fillna("")
    return names.eq(SYMBOL) | symbols.str.match(r"^NIFTY\d", na=False)


def _resolve_universe(kite) -> None:
    global _spot_token, _vix_token, _future_meta, _option_universe

    today = datetime.now(IST).date()
    nfo = pd.DataFrame(kite.instruments("NFO"))
    nfo["expiry"] = pd.to_datetime(nfo["expiry"]).dt.date
    nifty = nfo[nifty_contract_mask(nfo)].copy()

    option_rows = nifty[
        nifty["instrument_type"].isin(["CE", "PE"])
        & (nifty["expiry"] >= today)
    ]
    if option_rows.empty:
        raise RuntimeError("No upcoming NIFTY options")
    option_expiry = min(option_rows["expiry"])
    option_rows = option_rows[option_rows["expiry"] == option_expiry]

    future = _nearest_row(nifty, "FUT", today)
    nse = pd.DataFrame(kite.instruments("NSE"))
    spot = nse[nse["tradingsymbol"].eq(SPOT_TRADING_SYMBOL)]
    vix = nse[nse["tradingsymbol"].eq(VIX_TRADING_SYMBOL)]
    if spot.empty or vix.empty:
        raise RuntimeError("NIFTY 50 or INDIA VIX token missing from NSE instruments")

    universe: dict[tuple[int, str], dict] = {}
    for _, row in option_rows.iterrows():
        strike = int(row["strike"])
        option_type = str(row["instrument_type"])
        universe[(strike, option_type)] = {
            "instrument_token": int(row["instrument_token"]),
            "tradingsymbol": str(row["tradingsymbol"]),
            "expiry": option_expiry,
            "strike": strike,
            "option_type": option_type,
        }

    with _lock:
        _option_universe = universe
        _future_meta = {
            "instrument_token": int(future["instrument_token"]),
            "tradingsymbol": str(future["tradingsymbol"]),
            "expiry": future["expiry"],
        }
        _spot_token = int(spot.iloc[0]["instrument_token"])
        _vix_token = int(vix.iloc[0]["instrument_token"])

    logger.info(
        "[NIFTY-WS] Resolved weekly expiry=%s future=%s (%s) option_contracts=%d",
        option_expiry,
        _future_meta["tradingsymbol"],
        _future_meta["expiry"],
        len(universe),
    )


def _roll_options(ws, spot_price: float) -> None:
    global _active_atm, _active_option_tokens, _option_meta

    atm = int(round(spot_price / STRIKE_STEP) * STRIKE_STEP)
    with _lock:
        if atm == _active_atm:
            return
        desired = select_option_contracts(_option_universe, atm)
        desired_tokens = set(desired)
        added = desired_tokens - _active_option_tokens
        removed = _active_option_tokens - desired_tokens

    if removed:
        ws.unsubscribe(sorted(removed))
    if added:
        ws.subscribe(sorted(added))
        ws.set_mode(ws.MODE_FULL, sorted(added))

    with _lock:
        for token in removed:
            _latest_ticks.pop(token, None)
        _option_meta = desired
        _active_option_tokens = desired_tokens
        _active_atm = atm

    logger.info(
        "[NIFTY-WS] ATM=%d options=%d added=%d removed=%d",
        atm, len(desired_tokens), len(added), len(removed),
    )


def _on_ticks(ws, ticks) -> None:
    spot_price = None
    received_at = datetime.now(IST)
    with _lock:
        for tick in ticks:
            token = int(tick.get("instrument_token", 0))
            if not token:
                continue
            copied = dict(tick)
            copied["_received_at"] = received_at
            _latest_ticks[token] = copied
            if token == _spot_token:
                spot_price = float(tick.get("last_price") or 0)
    if spot_price:
        _roll_options(ws, spot_price)


def _depth_arrays(tick: dict, side: str):
    levels = (tick.get("depth") or {}).get(side) or []
    return (
        [float(level.get("price") or 0) for level in levels],
        [int(level.get("quantity") or 0) for level in levels],
        [int(level.get("orders") or 0) for level in levels],
    )


def _aware(value):
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=IST)
    return value.astimezone(IST)


def _copy_snapshot():
    with _lock:
        if not _session_active or not _active_option_tokens:
            return None
        spot = dict(_latest_ticks.get(_spot_token) or {})
        if not spot.get("last_price"):
            return None
        options = [
            (dict(_option_meta[token]), dict(_latest_ticks[token]))
            for token in sorted(_active_option_tokens)
            if token in _latest_ticks
        ]
        future_tick = dict(_latest_ticks.get(int(_future_meta.get("instrument_token", 0))) or {})
        return {
            "spot": spot,
            "vix": dict(_latest_ticks.get(_vix_token) or {}),
            "options": options,
            "future_meta": dict(_future_meta),
            "future_tick": future_tick,
            "atm": _active_atm,
        }


def _option_values(ts: datetime, snapshot: dict):
    spot = float(snapshot["spot"].get("last_price") or 0)
    rows = []
    call_oi: dict[int, int] = {}
    put_oi: dict[int, int] = {}

    for meta, tick in snapshot["options"]:
        ltp = float(tick.get("last_price") or 0)
        if not ltp:
            continue
        strike = int(meta["strike"])
        option_type = meta["option_type"]
        oi = int(tick.get("oi") or 0)
        key = (meta["expiry"], strike, option_type)
        previous_oi = _last_oi.get(key, oi)
        _last_oi[key] = oi
        buy = (tick.get("depth") or {}).get("buy") or []
        sell = (tick.get("depth") or {}).get("sell") or []
        bid = float(buy[0].get("price") or 0) if buy else None
        ask = float(sell[0].get("price") or 0) if sell else None
        dte = max((meta["expiry"] - ts.date()).days / 365.0, 1 / 365.0)
        iv = implied_volatility(ltp, spot, strike, dte, RISK_FREE_RATE, option_type)
        sigma = (iv / 100.0) if iv else 0.20
        greeks = calculate_greeks(spot, strike, dte, RISK_FREE_RATE, sigma, option_type)
        rows.append((
            ts, SYMBOL, meta["expiry"], strike, option_type,
            ltp, bid, ask, oi, oi - previous_oi,
            int(tick.get("volume_traded") or 0), iv,
            greeks["delta"], greeks["gamma"], greeks["theta"], greeks["vega"], spot,
        ))
        (call_oi if option_type == "CE" else put_oi)[strike] = oi
    return rows, call_oi, put_oi


def _safe_ratio(numerator, denominator):
    if numerator is None or denominator in (None, 0):
        return None
    return float(numerator) / float(denominator)


def _sign(value: float) -> int:
    return 1 if value > 0 else (-1 if value < 0 else 0)


def _history_value(ts: datetime, minutes: int, field: str):
    cutoff = ts - timedelta(minutes=minutes)
    for row in reversed(_feature_history):
        if row["ts"] <= cutoff:
            return row.get(field)
    return None


def _rolling_vol(ts: datetime, minutes: int):
    cutoff = ts - timedelta(minutes=minutes)
    prices = [
        float(row["spot"])
        for row in _feature_history
        if row["ts"] >= cutoff and row.get("spot")
    ]
    if len(prices) < 3:
        return None
    returns = [math.log(current / previous) for previous, current in zip(prices, prices[1:])]
    return statistics.pstdev(returns) if len(returns) >= 2 else None


def _top_strikes(rows: list[dict], option_type: str, count: int = 3) -> list[float | None]:
    ranked = sorted(
        (row for row in rows if row["option_type"] == option_type),
        key=lambda row: (row["oi"], -abs(row["strike"] - row["spot"])),
        reverse=True,
    )
    strikes: list[float | None] = [float(row["strike"]) for row in ranked[:count]]
    return strikes + [None] * (count - len(strikes))


def _max_pain(rows: list[dict]):
    strikes = sorted({row["strike"] for row in rows})
    if not strikes:
        return None
    calls = [row for row in rows if row["option_type"] == "CE"]
    puts = [row for row in rows if row["option_type"] == "PE"]
    pain = {}
    for settlement in strikes:
        call_pain = sum(max(settlement - row["strike"], 0) * row["oi"] for row in calls)
        put_pain = sum(max(row["strike"] - settlement, 0) * row["oi"] for row in puts)
        pain[settlement] = call_pain + put_pain
    return float(min(pain, key=pain.get))


def _weighted_strike(rows: list[dict], option_type: str):
    selected = [row for row in rows if row["option_type"] == option_type]
    total_oi = sum(row["oi"] for row in selected)
    if not total_oi:
        return None
    return sum(row["strike"] * row["oi"] for row in selected) / total_oi


def _option_records(option_rows: list[tuple]) -> list[dict]:
    return [
        {
            "expiry": row[2], "strike": float(row[3]), "option_type": row[4],
            "ltp": float(row[5]), "bid": row[6], "ask": row[7],
            "oi": float(row[8] or 0), "oi_change": float(row[9] or 0),
            "volume": float(row[10] or 0), "iv": float(row[11]) if row[11] is not None else None,
            "delta": float(row[12] or 0), "gamma": float(row[13] or 0),
            "theta": float(row[14] or 0), "vega": float(row[15] or 0),
            "spot": float(row[16]),
        }
        for row in option_rows
    ]


def build_feature_payloads(ts: datetime, option_rows: list[tuple], market: dict):
    """Build causal per-second NIFTY features; future labels are never populated live."""
    rows = _option_records(option_rows)
    if not rows:
        return None, None

    spot = float(market["spot"])
    atm = float(market["atm"])
    expiry = min(row["expiry"] for row in rows)
    dte = max((expiry - ts.date()).days, 0)
    calls = [row for row in rows if row["option_type"] == "CE"]
    puts = [row for row in rows if row["option_type"] == "PE"]
    total_ce_oi = sum(row["oi"] for row in calls)
    total_pe_oi = sum(row["oi"] for row in puts)
    total_oi = total_ce_oi + total_pe_oi
    atm_ce = next((row for row in calls if row["strike"] == atm), None)
    atm_pe = next((row for row in puts if row["strike"] == atm), None)
    atm_iv_values = [row["iv"] for row in (atm_ce, atm_pe) if row and row["iv"] is not None]
    atm_iv = statistics.mean(atm_iv_values) if atm_iv_values else None
    prior_1m = _history_value(ts, 1, "spot")
    prior_5m = _history_value(ts, 5, "spot")
    prior_15m = _history_value(ts, 15, "spot")
    prior_atm_iv = _history_value(ts, 30, "atm_iv")
    ce_delta_sum = sum(row["delta"] * row["oi"] for row in calls)
    pe_delta_sum = sum(row["delta"] * row["oi"] for row in puts)
    gamma_sum = sum(row["gamma"] * row["oi"] for row in rows)
    vega_sum = sum(row["vega"] * row["oi"] for row in rows)
    theta_sum = sum(row["theta"] * row["oi"] for row in rows)
    ce_oi_change = total_ce_oi - _previous_feature_totals.get("total_ce_oi", total_ce_oi)
    pe_oi_change = total_pe_oi - _previous_feature_totals.get("total_pe_oi", total_pe_oi)
    previous_gamma = _previous_feature_totals.get("gamma_sum")
    previous_vega = _previous_feature_totals.get("vega_sum", vega_sum)
    previous_theta = _previous_feature_totals.get("theta_sum", theta_sum)
    ce_weighted = _weighted_strike(rows, "CE")
    pe_weighted = _weighted_strike(rows, "PE")
    max_pain = _max_pain(rows)
    ce_top = _top_strikes(rows, "CE")
    pe_top = _top_strikes(rows, "PE")
    pcr = _safe_ratio(total_pe_oi, total_ce_oi)
    imbalance = _safe_ratio(total_pe_oi - total_ce_oi, total_oi)
    delta_imbalance = _safe_ratio(ce_delta_sum + pe_delta_sum, total_oi)
    market_fields = {
        "ms_vix": market.get("vix"), "ms_pcr_oi": market.get("pcr"),
        "ms_atm_straddle": market.get("straddle"),
        "ms_expected_move": market.get("expected_move"),
        "ms_call_oi_wall": market.get("call_wall"),
        "ms_put_oi_wall": market.get("put_wall"),
        "ms_dist_call_wall": (
            float(market["call_wall"]) - spot if market.get("call_wall") is not None else None
        ),
        "ms_dist_put_wall": (
            spot - float(market["put_wall"]) if market.get("put_wall") is not None else None
        ),
    }

    common = {
        "ts": ts, "trade_date": ts.date(), "underlying_ltp": spot, "dte": dte,
        "atm_strike": atm, "total_ce_oi": total_ce_oi, "total_pe_oi": total_pe_oi,
        "oi_imbalance": imbalance, "iv_skew": (
            (atm_pe["iv"] - atm_ce["iv"])
            if atm_ce and atm_pe and atm_ce["iv"] is not None and atm_pe["iv"] is not None
            else None
        ),
        "straddle_price": market.get("straddle"), "delta_imbalance": delta_imbalance,
        **market_fields,
    }
    nifty = {
        **common,
        "return_1min": (spot / prior_1m - 1) if prior_1m else None,
        "return_5min": (spot / prior_5m - 1) if prior_5m else None,
        "return_15min": (spot / prior_15m - 1) if prior_15m else None,
        "rolling_vol_5": _rolling_vol(ts, 5),
        "rolling_vol_15": _rolling_vol(ts, 15),
        "time_bucket_enc": max(0, int((ts.hour * 60 + ts.minute - (9 * 60 + 15)) / 5)),
        "atm_ce_ltp": atm_ce["ltp"] if atm_ce else None,
        "atm_pe_ltp": atm_pe["ltp"] if atm_pe else None,
        "atm_ce_iv": atm_ce["iv"] if atm_ce else None,
        "atm_pe_iv": atm_pe["iv"] if atm_pe else None,
        "atm_ce_oi": atm_ce["oi"] if atm_ce else None,
        "atm_pe_oi": atm_pe["oi"] if atm_pe else None,
        "atm_ce_delta": atm_ce["delta"] if atm_ce else None,
        "atm_pe_delta": atm_pe["delta"] if atm_pe else None,
        "atm_ce_gamma": atm_ce["gamma"] if atm_ce else None,
        "atm_pe_gamma": atm_pe["gamma"] if atm_pe else None,
        "atm_ce_vega": atm_ce["vega"] if atm_ce else None,
        "atm_pe_vega": atm_pe["vega"] if atm_pe else None,
        "atm_ce_theta": atm_ce["theta"] if atm_ce else None,
        "atm_pe_theta": atm_pe["theta"] if atm_pe else None,
        "pcr": pcr, "ce_oi_buildup": _sign(ce_oi_change), "pe_oi_buildup": _sign(pe_oi_change),
        "ce_oi_wt_strike": ce_weighted, "pe_oi_wt_strike": pe_weighted,
        "ce_oi_change": ce_oi_change, "pe_oi_change": pe_oi_change,
        "ce_delta_sum": ce_delta_sum, "pe_delta_sum": pe_delta_sum,
        "gamma_sum": gamma_sum, "vega_sum": vega_sum, "theta_sum": theta_sum,
        "gamma_spike": int(
            previous_gamma is not None
            and abs(gamma_sum) > max(abs(previous_gamma) * 1.5, 1e-12)
        ),
        "vega_change": vega_sum - previous_vega,
        "theta_decay": theta_sum - previous_theta,
        "dist_from_atm": spot - atm,
        "pe_max_oi_strike": market.get("put_wall"),
        "ce_max_oi_strike": market.get("call_wall"),
        "dist_from_support": (
            spot - float(market["put_wall"]) if market.get("put_wall") is not None else None
        ),
        "dist_from_resistance": (
            float(market["call_wall"]) - spot if market.get("call_wall") is not None else None
        ),
    }
    gex_scale = spot * spot * 0.01 / 10_000_000
    expiry_features = {
        **common, "expiry_date": expiry, "pcr_oi": pcr,
        "ce_oi_strike_1": ce_top[0], "ce_oi_strike_2": ce_top[1], "ce_oi_strike_3": ce_top[2],
        "pe_oi_strike_1": pe_top[0], "pe_oi_strike_2": pe_top[1], "pe_oi_strike_3": pe_top[2],
        "ce_oi_wt_strike": ce_weighted, "pe_oi_wt_strike": pe_weighted,
        "max_pain_strike": max_pain,
        "dist_from_max_pain": spot - max_pain if max_pain is not None else None,
        "atm_ce_iv": atm_ce["iv"] if atm_ce else None,
        "atm_pe_iv": atm_pe["iv"] if atm_pe else None,
        "atm_iv_change_30min": (
            atm_iv - prior_atm_iv if atm_iv is not None and prior_atm_iv is not None else None
        ),
        "implied_move_pct": market.get("expected_move"),
        "gex_ce": sum(row["gamma"] * row["oi"] for row in calls) * gex_scale,
        "gex_pe": -sum(row["gamma"] * row["oi"] for row in puts) * gex_scale,
    }
    expiry_features["gex_net"] = expiry_features["gex_ce"] + expiry_features["gex_pe"]

    _feature_history.append({"ts": ts, "spot": spot, "atm_iv": atm_iv})
    _previous_feature_totals.update({
        "total_ce_oi": total_ce_oi, "total_pe_oi": total_pe_oi,
        "gamma_sum": gamma_sum, "vega_sum": vega_sum, "theta_sum": theta_sum,
    })
    return nifty, expiry_features


def _upsert_payload(cur, table: str, payload: dict, conflict_columns: tuple[str, ...]) -> None:
    columns = tuple(payload)
    placeholders = ", ".join(["%s"] * len(columns))
    updates = ", ".join(
        f"{column} = EXCLUDED.{column}" for column in columns if column not in conflict_columns
    )
    cur.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({', '.join(conflict_columns)}) DO UPDATE SET {updates}",
        tuple(payload[column] for column in columns),
    )


def _ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS nifty_futures (
                ts TIMESTAMPTZ PRIMARY KEY,
                tradingsymbol VARCHAR(40) NOT NULL,
                instrument_token BIGINT NOT NULL,
                expiry DATE NOT NULL,
                exchange_ts TIMESTAMPTZ,
                received_at TIMESTAMPTZ NOT NULL,
                last_price NUMERIC(12,2), last_quantity BIGINT,
                average_price NUMERIC(12,2), volume BIGINT,
                total_buy_quantity BIGINT, total_sell_quantity BIGINT,
                open NUMERIC(12,2), high NUMERIC(12,2), low NUMERIC(12,2),
                previous_close NUMERIC(12,2), oi BIGINT, oi_day_high BIGINT, oi_day_low BIGINT,
                bid_prices NUMERIC(12,2)[] NOT NULL DEFAULT '{}',
                bid_quantities BIGINT[] NOT NULL DEFAULT '{}', bid_orders INTEGER[] NOT NULL DEFAULT '{}',
                ask_prices NUMERIC(12,2)[] NOT NULL DEFAULT '{}',
                ask_quantities BIGINT[] NOT NULL DEFAULT '{}', ask_orders INTEGER[] NOT NULL DEFAULT '{}',
                available_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_nifty_futures_expiry_ts ON nifty_futures (expiry, ts DESC)")
        cur.execute(FEATURE_SCHEMA_SQL)
    conn.commit()


def _write_snapshot(conn, ts: datetime, snapshot: dict) -> int:
    option_rows, call_oi, put_oi = _option_values(ts, snapshot)
    if not option_rows:
        return 0

    spot = float(snapshot["spot"].get("last_price") or 0)
    vix = snapshot["vix"].get("last_price")
    atm = int(snapshot["atm"])
    total_call = sum(call_oi.values())
    total_put = sum(put_oi.values())
    pcr = total_put / total_call if total_call else None
    option_by_key = {(row[3], row[4]): row for row in option_rows}
    atm_ce = option_by_key.get((atm, "CE"))
    atm_pe = option_by_key.get((atm, "PE"))
    straddle = (float(atm_ce[5]) + float(atm_pe[5])) if atm_ce and atm_pe else None
    expected_move = (straddle / spot * 100) if straddle and spot else None
    call_wall = max(call_oi, key=call_oi.get) if call_oi else None
    put_wall = max(put_oi, key=put_oi.get) if put_oi else None
    market = {
        "spot": spot, "vix": float(vix) if vix is not None else None, "atm": atm,
        "pcr": pcr, "straddle": straddle, "expected_move": expected_move,
        "call_wall": call_wall, "put_wall": put_wall,
    }

    with conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO option_chain
                (ts, instrument, expiry, strike, option_type, ltp, bid, ask,
                 oi, oi_change, volume, iv, delta, gamma, theta, vega, underlying_ltp)
            VALUES %s
        """, option_rows, page_size=100)
        cur.execute("""
            INSERT INTO market_snapshot
                (ts, instrument, spot_price, vix, pcr_oi, atm_strike,
                 atm_straddle, expected_move, call_oi_wall, put_oi_wall)
            VALUES (%s, 'NIFTY', %s, %s, %s, %s, %s, %s, %s, %s)
        """, (ts, spot, vix, pcr, atm, straddle, expected_move, call_wall, put_wall))

        future = snapshot["future_tick"]
        meta = snapshot["future_meta"]
        if future.get("last_price"):
            bid_p, bid_q, bid_o = _depth_arrays(future, "buy")
            ask_p, ask_q, ask_o = _depth_arrays(future, "sell")
            ohlc = future.get("ohlc") or {}
            cur.execute("""
                INSERT INTO nifty_futures
                    (ts, tradingsymbol, instrument_token, expiry, exchange_ts, received_at,
                     last_price, last_quantity, average_price, volume,
                     total_buy_quantity, total_sell_quantity, open, high, low, previous_close,
                     oi, oi_day_high, oi_day_low, bid_prices, bid_quantities, bid_orders,
                     ask_prices, ask_quantities, ask_orders)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ts) DO UPDATE SET
                    tradingsymbol = EXCLUDED.tradingsymbol,
                    instrument_token = EXCLUDED.instrument_token,
                    expiry = EXCLUDED.expiry,
                    exchange_ts = EXCLUDED.exchange_ts,
                    received_at = EXCLUDED.received_at,
                    last_price = EXCLUDED.last_price,
                    last_quantity = EXCLUDED.last_quantity,
                    average_price = EXCLUDED.average_price,
                    volume = EXCLUDED.volume,
                    total_buy_quantity = EXCLUDED.total_buy_quantity,
                    total_sell_quantity = EXCLUDED.total_sell_quantity,
                    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                    previous_close = EXCLUDED.previous_close,
                    oi = EXCLUDED.oi, oi_day_high = EXCLUDED.oi_day_high, oi_day_low = EXCLUDED.oi_day_low,
                    bid_prices = EXCLUDED.bid_prices, bid_quantities = EXCLUDED.bid_quantities,
                    bid_orders = EXCLUDED.bid_orders, ask_prices = EXCLUDED.ask_prices,
                    ask_quantities = EXCLUDED.ask_quantities, ask_orders = EXCLUDED.ask_orders,
                    available_at = clock_timestamp()
            """, (
                ts, meta["tradingsymbol"], meta["instrument_token"], meta["expiry"],
                _aware(future.get("exchange_timestamp")), future.get("_received_at") or ts,
                future.get("last_price"), future.get("last_traded_quantity"),
                future.get("average_traded_price"), future.get("volume_traded"),
                future.get("total_buy_quantity"), future.get("total_sell_quantity"),
                ohlc.get("open"), ohlc.get("high"), ohlc.get("low"), ohlc.get("close"),
                future.get("oi"), future.get("oi_day_high"), future.get("oi_day_low"),
                bid_p, bid_q, bid_o, ask_p, ask_q, ask_o,
            ))

        cur.execute("SAVEPOINT nifty_feature_write")
        try:
            nifty_payload, expiry_payload = build_feature_payloads(ts, option_rows, market)
            if nifty_payload and expiry_payload:
                _upsert_payload(cur, "nifty_features", nifty_payload, ("ts",))
                _upsert_payload(
                    cur, "nifty_expiry_features", expiry_payload, ("ts", "expiry_date")
                )
            cur.execute("RELEASE SAVEPOINT nifty_feature_write")
        except Exception as exc:
            cur.execute("ROLLBACK TO SAVEPOINT nifty_feature_write")
            cur.execute("RELEASE SAVEPOINT nifty_feature_write")
            logger.error("[NIFTY-WS] Feature write failed; raw snapshot retained: %s", exc)
    conn.commit()
    return len(option_rows)


def _flush_loop() -> None:
    import psycopg2

    conn = None
    last_second = None
    while True:
        _time.sleep(0.05)
        current_second = datetime.now(IST).replace(microsecond=0)
        if current_second == last_second:
            continue
        last_second = current_second
        if not _nse_open():
            continue
        snapshot = _copy_snapshot()
        if not snapshot:
            continue
        try:
            if conn is None or conn.closed:
                conn = psycopg2.connect(
                    os.environ["DATABASE_URL"],
                    application_name="nifty_second_websocket",
                )
                conn.autocommit = False
                _ensure_schema(conn)
                logger.info("[NIFTY-WS] DB connected")
            rows = _write_snapshot(conn, current_second, snapshot)
            logger.debug("[NIFTY-WS] %s rows=%d", current_second.time(), rows)
        except Exception as exc:
            logger.error("[NIFTY-WS] Flush failed: %s", exc)
            if conn is not None:
                try:
                    conn.rollback()
                    conn.close()
                except Exception:
                    pass
            conn = None
            _time.sleep(1)


def _clear_session() -> None:
    global _session_active, _active_atm, _active_option_tokens, _option_meta
    with _lock:
        _session_active = False
        _active_atm = None
        _active_option_tokens = set()
        _option_meta = {}
        _latest_ticks.clear()
        _last_oi.clear()
        _feature_history.clear()
        _previous_feature_totals.clear()


def main() -> None:
    global _session_active

    logger.info("[NIFTY-WS] Starting ATM ±%d options + futures service", N_STRIKES)
    threading.Thread(target=_flush_loop, daemon=True, name="nifty-db-flusher").start()

    while True:
        if not _nse_open():
            wait = _seconds_until_open()
            logger.info("[NIFTY-WS] NSE closed; sleeping %d minutes", wait // 60)
            _time.sleep(min(wait, 3600))
            continue

        kite = get_kite()
        access_token = load_access_token(os.environ.get("KITE_ADMIN_USER_ID", ""))
        if not kite or not access_token or not API_KEY:
            logger.warning("[NIFTY-WS] Kite credentials unavailable; retry in 5 minutes")
            _time.sleep(300)
            continue

        try:
            _resolve_universe(kite)
        except Exception as exc:
            logger.error("[NIFTY-WS] Instrument resolution failed: %s", exc)
            _time.sleep(300)
            continue

        kws = KiteTicker(API_KEY, access_token, reconnect=False)

        def on_connect(ws, _response):
            global _session_active
            base_tokens = [_spot_token, _vix_token, int(_future_meta["instrument_token"])]
            ws.subscribe(base_tokens)
            ws.set_mode(ws.MODE_QUOTE, [_spot_token, _vix_token])
            ws.set_mode(ws.MODE_FULL, [int(_future_meta["instrument_token"])])
            with _lock:
                _session_active = True
            logger.info("[NIFTY-WS] Connected; waiting for spot tick to select ATM ±%d", N_STRIKES)

        kws.on_connect = on_connect
        kws.on_ticks = _on_ticks
        kws.on_close = lambda _ws, code, reason: logger.warning(
            "[NIFTY-WS] Closed (%s): %s", code, reason
        )
        kws.on_error = lambda _ws, code, reason: logger.error(
            "[NIFTY-WS] Error (%s): %s", code, reason
        )

        try:
            kws.connect(threaded=True)
            _time.sleep(2)
            while _nse_open() and kws.is_connected():
                _time.sleep(5)
        except Exception as exc:
            logger.error("[NIFTY-WS] Connection failure: %s", exc)
        finally:
            _clear_session()
            try:
                kws.close()
            except Exception:
                pass
        _time.sleep(30)


if __name__ == "__main__":
    main()
