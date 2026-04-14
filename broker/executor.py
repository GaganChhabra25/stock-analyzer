"""
Strategy Executor  —  broker/executor.py
─────────────────────────────────────────
Translates strategy definitions into broker orders.
Only imports BrokerBase — zero broker-specific code here.

Each supported strategy has:
  enter()  → places entry orders, returns List[LegSpec]
  check_sl() → returns exit_reason string or None
  exit()   → places exit orders for all open legs
"""

from __future__ import annotations

import logging
from datetime import date
from typing import List, Optional

from .base import BrokerBase, BrokerError, LegSpec, OrderError, PlacedOrder

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _sl_price_for_sell(entry_price: float) -> float:
    """SL for a sold leg: exit when LTP doubles (2× entry)."""
    return round(entry_price * 2.0, 1)


def _reverse_legs(broker: BrokerBase, legs: List[LegSpec]) -> List[PlacedOrder]:
    """Emergency reversal: close all placed legs at market (error recovery)."""
    closed = []
    for leg in legs:
        try:
            order = broker.place_market_order(
                leg.trading_symbol, leg.exchange, leg.close_direction, leg.qty
            )
            closed.append(order)
        except Exception as exc:
            logger.error("reversal failed for %s: %s", leg.trading_symbol, exc)
    return closed


# ── OI-Anchored Strangle ───────────────────────────────────────────────────────

def enter_oi_strangle(
    broker:      BrokerBase,
    instrument:  str,
    expiry:      date,
    lots:        int,
    lot_size:    int,
    strike_step: int,
    deployment_id: int,
) -> List[LegSpec]:
    """
    Sell max-OI CE above ATM + max-OI PE below ATM.
    Returns [ce_leg, pe_leg] LegSpecs.
    """
    spot = broker.get_spot(instrument)
    atm  = round(spot / strike_step) * strike_step

    chain = broker.get_option_chain(instrument, expiry)
    if not chain:
        raise BrokerError(f"Empty option chain for {instrument} {expiry}")

    # Max-OI CE above ATM
    ce_cands = [q for q in chain if q.option_type == "CE" and q.strike and q.strike > atm and q.oi > 0 and q.ltp > 0]
    # Max-OI PE below ATM
    pe_cands = [q for q in chain if q.option_type == "PE" and q.strike and q.strike < atm and q.oi > 0 and q.ltp > 0]

    if not ce_cands or not pe_cands:
        raise BrokerError(
            f"No qualifying CE/PE found. ATM={atm}, "
            f"CE candidates={len(ce_cands)}, PE candidates={len(pe_cands)}"
        )

    best_ce = max(ce_cands, key=lambda q: q.oi)
    best_pe = max(pe_cands, key=lambda q: q.oi)
    qty = lots * lot_size

    placed: List[LegSpec] = []
    try:
        ce_order = broker.place_market_order(best_ce.trading_symbol, "NFO", "SELL", qty)
        placed.append(LegSpec(
            deployment_id=deployment_id, leg_index=0,
            trading_symbol=best_ce.trading_symbol, exchange="NFO",
            direction="SELL", strike=best_ce.strike, option_type="CE",
            lots=lots, lot_size=lot_size,
            entry_price=best_ce.ltp,
            sl_price=_sl_price_for_sell(best_ce.ltp),
            order_id=ce_order.order_id,
        ))

        pe_order = broker.place_market_order(best_pe.trading_symbol, "NFO", "SELL", qty)
        placed.append(LegSpec(
            deployment_id=deployment_id, leg_index=1,
            trading_symbol=best_pe.trading_symbol, exchange="NFO",
            direction="SELL", strike=best_pe.strike, option_type="PE",
            lots=lots, lot_size=lot_size,
            entry_price=best_pe.ltp,
            sl_price=_sl_price_for_sell(best_pe.ltp),
            order_id=pe_order.order_id,
        ))
    except OrderError:
        logger.error("OI_STRANGLE entry failed — reversing %d placed legs", len(placed))
        _reverse_legs(broker, placed)
        raise

    return placed


# ── Iron Fly ───────────────────────────────────────────────────────────────────

def enter_iron_fly(
    broker:      BrokerBase,
    instrument:  str,
    expiry:      date,
    lots:        int,
    lot_size:    int,
    strike_step: int,
    wing_width:  int,
    deployment_id: int,
) -> List[LegSpec]:
    """
    Sell ATM CE + ATM PE, Buy (ATM+wing) CE + (ATM-wing) PE.
    Returns 4 LegSpecs: [sell_ce, sell_pe, buy_ce, buy_pe].
    """
    spot = broker.get_spot(instrument)
    atm  = round(spot / strike_step) * strike_step
    wing = round(wing_width / strike_step) * strike_step

    buy_ce_strike  = atm + wing
    buy_pe_strike  = atm - wing
    qty = lots * lot_size

    legs_plan = [
        (atm,          "CE", "SELL"),
        (atm,          "PE", "SELL"),
        (buy_ce_strike,"CE", "BUY"),
        (buy_pe_strike,"PE", "BUY"),
    ]

    chain = broker.get_option_chain(instrument, expiry)
    sym_map = {(q.strike, q.option_type): q for q in chain if q.strike}

    placed: List[LegSpec] = []
    try:
        for idx, (strike, opt_type, direction) in enumerate(legs_plan):
            quote = sym_map.get((strike, opt_type))
            if not quote:
                raise BrokerError(f"No quote for {instrument} {strike} {opt_type} {expiry}")
            order = broker.place_market_order(quote.trading_symbol, "NFO", direction, qty)
            placed.append(LegSpec(
                deployment_id=deployment_id, leg_index=idx,
                trading_symbol=quote.trading_symbol, exchange="NFO",
                direction=direction, strike=strike, option_type=opt_type,
                lots=lots, lot_size=lot_size,
                entry_price=quote.ltp,
                sl_price=_sl_price_for_sell(quote.ltp) if direction == "SELL" else 0.0,
                order_id=order.order_id,
            ))
    except (OrderError, BrokerError):
        logger.error("IRON_FLY entry failed — reversing %d placed legs", len(placed))
        _reverse_legs(broker, placed)
        raise

    return placed


# ── Zero DTE Straddle ──────────────────────────────────────────────────────────

def enter_zero_dte_straddle(
    broker:      BrokerBase,
    instrument:  str,
    expiry:      date,
    lots:        int,
    lot_size:    int,
    strike_step: int,
    deployment_id: int,
) -> List[LegSpec]:
    """
    Sell ATM CE + ATM PE on expiry day only.
    Validates DTE = 0 before entering.
    """
    today = date.today()
    if expiry != today:
        raise BrokerError(
            f"ZERO_DTE_STRADDLE requires expiry day. Today={today}, expiry={expiry}"
        )
    spot = broker.get_spot(instrument)
    atm  = round(spot / strike_step) * strike_step
    qty  = lots * lot_size

    chain = broker.get_option_chain(instrument, expiry)
    sym_map = {(q.strike, q.option_type): q for q in chain if q.strike}

    placed: List[LegSpec] = []
    try:
        for idx, opt_type in enumerate(["CE", "PE"]):
            quote = sym_map.get((atm, opt_type))
            if not quote:
                raise BrokerError(f"No quote for ATM {atm} {opt_type} on {expiry}")
            order = broker.place_market_order(quote.trading_symbol, "NFO", "SELL", qty)
            placed.append(LegSpec(
                deployment_id=deployment_id, leg_index=idx,
                trading_symbol=quote.trading_symbol, exchange="NFO",
                direction="SELL", strike=atm, option_type=opt_type,
                lots=lots, lot_size=lot_size,
                entry_price=quote.ltp,
                sl_price=_sl_price_for_sell(quote.ltp),
                order_id=order.order_id,
            ))
    except (OrderError, BrokerError):
        logger.error("ZERO_DTE entry failed — reversing %d placed legs", len(placed))
        _reverse_legs(broker, placed)
        raise

    return placed


# ── Entry dispatcher ──────────────────────────────────────────────────────────

def execute_entry(
    strategy_key: str,
    broker:       BrokerBase,
    instrument:   str,
    expiry:       date,
    lots:         int,
    lot_size:     int,
    strike_step:  int,
    wing_width:   int,
    deployment_id: int,
) -> List[LegSpec]:
    """Dispatch to the correct entry function based on strategy key."""
    key = strategy_key.upper()
    if key == "OI_STRANGLE":
        return enter_oi_strangle(broker, instrument, expiry, lots, lot_size, strike_step, deployment_id)
    if key == "IRON_FLY":
        return enter_iron_fly(broker, instrument, expiry, lots, lot_size, strike_step, wing_width, deployment_id)
    if key == "ZERO_DTE_STRADDLE":
        return enter_zero_dte_straddle(broker, instrument, expiry, lots, lot_size, strike_step, deployment_id)
    raise BrokerError(f"No executor for strategy: {strategy_key}")


# ── SL check ─────────────────────────────────────────────────────────────────

def check_sl(broker: BrokerBase, legs: List[LegSpec]) -> Optional[str]:
    """
    Check if any SELL leg's current LTP >= SL price.
    Returns 'SL' if triggered, None otherwise.
    """
    sell_legs = [l for l in legs if l.direction == "SELL"]
    for leg in sell_legs:
        try:
            ltp = broker.get_ltp(leg.trading_symbol, leg.exchange)
            if ltp >= leg.sl_price:
                logger.info("SL triggered: %s LTP=%.1f SL=%.1f", leg.trading_symbol, ltp, leg.sl_price)
                return "SL"
        except BrokerError as exc:
            logger.warning("check_sl get_ltp failed for %s: %s", leg.trading_symbol, exc)
    return None


# ── Exit ─────────────────────────────────────────────────────────────────────

def execute_exit(broker: BrokerBase, legs: List[LegSpec]) -> List[dict]:
    """
    Close all legs at market. Returns list of {leg_index, exit_price, order_id}.
    Partial failures are logged but not re-raised (best-effort exit).
    """
    results = []
    for leg in legs:
        try:
            ltp = broker.get_ltp(leg.trading_symbol, leg.exchange)
        except BrokerError:
            ltp = 0.0
        try:
            order = broker.place_market_order(leg.trading_symbol, leg.exchange, leg.close_direction, leg.qty)
            results.append({
                "leg_index":  leg.leg_index,
                "exit_price": ltp,
                "order_id":   order.order_id,
            })
        except OrderError as exc:
            logger.error("exit_leg %s failed: %s", leg.trading_symbol, exc)
            results.append({
                "leg_index":  leg.leg_index,
                "exit_price": ltp,
                "order_id":   "",
                "error":      str(exc),
            })
    return results
