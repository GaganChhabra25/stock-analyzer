"""
Deployment Scheduler  —  broker/scheduler.py
──────────────────────────────────────────────
Background daemon thread that:
  - Watches PENDING deployments and enters at strategy entry_time
  - Watches ACTIVE deployments and exits on SL or at exit_time
  - Survives Flask restarts by reloading PENDING/ACTIVE state from DB

Thread safety:
  - Single RLock guards _state dict mutations
  - Broker I/O happens OUTSIDE the lock (long network calls won't block Flask)
  - All DB writes happen after broker calls succeed

Usage:
  scheduler = DeploymentScheduler.get_instance()
  scheduler.start()                         # called once in app.py on boot
  dep_id = scheduler.add(user_id, ...)     # from POST /api/deploy
  scheduler.cancel(dep_id)                 # from DELETE /api/deployments/<id>
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from .base import BrokerBase, BrokerError, LegSpec
from . import deployments as dep_db
from . import executor
from .factory import get_broker
from options.strategies import REGISTRY as STRAT_REGISTRY
from options.instrument_config import REGISTRY as INSTR_REGISTRY

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

POLL_INTERVAL_SEC = 30   # check every 30 seconds


@dataclass
class _DepState:
    dep_id:       int
    user_id:      str
    strategy_key: str
    instrument:   str
    expiry:       Optional[date]
    broker_name:  str
    lots:         int
    wing_width:   int
    status:       str            # PENDING | ACTIVE | DONE
    legs:         List[LegSpec] = field(default_factory=list)


class DeploymentScheduler:
    """Singleton scheduler. Use DeploymentScheduler.get_instance()."""

    _instance: Optional["DeploymentScheduler"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "DeploymentScheduler":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._state: Dict[int, _DepState] = {}
        self._lock  = threading.RLock()
        self._stop  = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────────

    def start(self):
        """Start the background thread. Safe to call multiple times."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._reload_from_db()
            self._thread = threading.Thread(
                target=self._loop, name="DeploymentScheduler", daemon=True
            )
            self._thread.start()
            logger.info("DeploymentScheduler started (poll every %ds)", POLL_INTERVAL_SEC)

    def stop(self):
        self._stop.set()

    # ── Public API ────────────────────────────────────────────────────────────────

    def add(
        self,
        user_id:      str,
        strategy_key: str,
        instrument:   str,
        expiry:       Optional[date],
        broker_name:  str,
        lots:         int,
        wing_width:   int = 0,
    ) -> Optional[int]:
        """Create a new PENDING deployment and register it with the scheduler."""
        dep_id = dep_db.create_deployment(
            user_id=user_id,
            strategy_key=strategy_key,
            instrument=instrument,
            expiry=expiry,
            broker=broker_name,
            lots=lots,
            wing_width=wing_width if wing_width else None,
        )
        if dep_id is None:
            return None
        state = _DepState(
            dep_id=dep_id, user_id=user_id,
            strategy_key=strategy_key, instrument=instrument,
            expiry=expiry, broker_name=broker_name,
            lots=lots, wing_width=wing_width or 0, status="PENDING",
        )
        with self._lock:
            self._state[dep_id] = state
        logger.info("Deployment %d queued: %s %s lots=%d", dep_id, strategy_key, instrument, lots)
        return dep_id

    def cancel(self, dep_id: int) -> bool:
        """Cancel a PENDING deployment. Returns False if already ACTIVE."""
        with self._lock:
            state = self._state.get(dep_id)
            if state and state.status == "ACTIVE":
                logger.warning("Cannot cancel ACTIVE deployment %d — force-exit instead", dep_id)
                return False
            cancelled = dep_db.cancel_deployment(dep_id)
            if cancelled:
                self._state.pop(dep_id, None)
            return cancelled

    def force_exit(self, dep_id: int, user_id: str) -> bool:
        """Force-exit an ACTIVE deployment immediately (manual close)."""
        with self._lock:
            state = self._state.get(dep_id)
            if not state or state.status != "ACTIVE" or state.user_id != user_id:
                return False
        # Do broker work OUTSIDE lock
        self._exit_deployment(state, reason="MANUAL")
        return True

    def list_for_user(self, user_id: str) -> List[dict]:
        """Return recent deployments for a user (from DB)."""
        return dep_db.get_recent_deployments(user_id, limit=10)

    # ── Background thread ─────────────────────────────────────────────────────────

    def _loop(self):
        logger.info("DeploymentScheduler loop started")
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as exc:
                logger.exception("DeploymentScheduler tick error: %s", exc)
            self._stop.wait(POLL_INTERVAL_SEC)
        logger.info("DeploymentScheduler loop stopped")

    def _tick(self):
        now_ist = datetime.now(IST)
        now_time = now_ist.time()

        # Snapshot state (release lock before I/O)
        with self._lock:
            snapshot = list(self._state.values())

        for state in snapshot:
            try:
                if state.status == "PENDING":
                    self._check_pending(state, now_time)
                elif state.status == "ACTIVE":
                    self._check_active(state, now_time)
            except Exception as exc:
                logger.error("tick error dep %d: %s", state.dep_id, exc)

    def _check_pending(self, state: _DepState, now_time: dtime):
        strat = STRAT_REGISTRY.get(state.strategy_key)
        if not strat:
            logger.error("Unknown strategy %s for dep %d", state.strategy_key, state.dep_id)
            dep_db.fail_deployment(state.dep_id, f"Unknown strategy: {state.strategy_key}")
            with self._lock:
                self._state.pop(state.dep_id, None)
            return

        entry_h, entry_m = map(int, strat.entry_time.split(":"))
        entry_time = dtime(entry_h, entry_m)

        if now_time >= entry_time:
            logger.info("Entering deployment %d (%s %s)", state.dep_id, state.strategy_key, state.instrument)
            self._enter_deployment(state)

    def _check_active(self, state: _DepState, now_time: dtime):
        strat = STRAT_REGISTRY.get(state.strategy_key)
        if not strat:
            return

        exit_h, exit_m = map(int, strat.exit_time.split(":"))
        exit_time = dtime(exit_h, exit_m)

        # Check time exit first
        if now_time >= exit_time:
            logger.info("Time exit for dep %d", state.dep_id)
            self._exit_deployment(state, reason="TIME")
            return

        # Check SL
        try:
            broker = get_broker(state.broker_name, state.user_id)
            reason = executor.check_sl(broker, state.legs)
            if reason:
                logger.info("SL triggered for dep %d", state.dep_id)
                self._exit_deployment(state, reason=reason)
        except BrokerError as exc:
            logger.warning("SL check failed dep %d: %s", state.dep_id, exc)

    def _enter_deployment(self, state: _DepState):
        """Place entry orders and transition to ACTIVE."""
        try:
            broker = get_broker(state.broker_name, state.user_id)
            instr_cfg = INSTR_REGISTRY.get(state.instrument)
            if not instr_cfg:
                raise BrokerError(f"Unknown instrument: {state.instrument}")

            # Resolve expiry if not set
            expiry = state.expiry or broker.get_nearest_expiry(state.instrument)

            legs = executor.execute_entry(
                strategy_key  = state.strategy_key,
                broker        = broker,
                instrument    = state.instrument,
                expiry        = expiry,
                lots          = state.lots,
                lot_size      = instr_cfg.lot_size,
                strike_step   = instr_cfg.strike_step,
                wing_width    = state.wing_width,
                deployment_id = state.dep_id,
            )
        except (BrokerError, Exception) as exc:
            logger.error("Entry failed for dep %d: %s", state.dep_id, exc)
            dep_db.fail_deployment(state.dep_id, str(exc))
            with self._lock:
                self._state.pop(state.dep_id, None)
            return

        # Persist to DB
        dep_db.activate_deployment(state.dep_id, legs)

        # Update in-memory state
        with self._lock:
            if state.dep_id in self._state:
                self._state[state.dep_id].status = "ACTIVE"
                self._state[state.dep_id].legs   = legs
        logger.info("Deployment %d ACTIVE with %d legs", state.dep_id, len(legs))

    def _exit_deployment(self, state: _DepState, reason: str):
        """Place exit orders and close deployment."""
        try:
            broker = get_broker(state.broker_name, state.user_id)
            exit_results = executor.execute_exit(broker, state.legs)
        except BrokerError as exc:
            logger.error("Exit failed for dep %d: %s", state.dep_id, exc)
            dep_db.fail_deployment(state.dep_id, f"Exit error: {exc}")
            with self._lock:
                self._state[state.dep_id].status = "DONE"
            return

        # Compute P&L
        entry_net = sum(l.entry_price for l in state.legs if l.direction == "SELL") \
                  - sum(l.entry_price for l in state.legs if l.direction == "BUY")
        exit_net  = sum(r.get("exit_price", 0) for r in exit_results)
        pnl_pts   = round(entry_net - exit_net, 1)
        instr_cfg = INSTR_REGISTRY.get(state.instrument)
        lot_size  = instr_cfg.lot_size if instr_cfg else 1
        pnl_inr   = round(pnl_pts * state.lots * lot_size, 0)

        dep_db.close_deployment(state.dep_id, exit_results, reason, pnl_pts, pnl_inr)

        with self._lock:
            self._state[state.dep_id].status = "DONE"
            # Keep in state for a short while so UI can show final status
            # Will be cleaned up on next startup reload

        logger.info(
            "Deployment %d CLOSED reason=%s pnl=%.1f pts ₹%.0f",
            state.dep_id, reason, pnl_pts, pnl_inr,
        )

    # ── Recovery on restart ───────────────────────────────────────────────────────

    def _reload_from_db(self):
        """On startup, reload any PENDING or ACTIVE deployments from DB."""
        rows = dep_db.get_pending_and_active()
        for row in rows:
            dep_id = row["id"]
            state = _DepState(
                dep_id=dep_id, user_id=row["user_id"],
                strategy_key=row["strategy_key"], instrument=row["instrument"],
                expiry=row.get("expiry"), broker_name=row.get("broker", "zerodha"),
                lots=row.get("lots", 1), wing_width=row.get("wing_width") or 0,
                status=row.get("status", "PENDING"),
            )
            if state.status == "ACTIVE":
                # Rebuild legs from DB
                legs_raw = dep_db.get_open_legs(dep_id)
                state.legs = [
                    LegSpec(
                        deployment_id=dep_id,
                        leg_index=l["leg_index"],
                        trading_symbol=l["trading_symbol"] or "",
                        exchange="NFO",
                        direction=l["direction"],
                        strike=l["strike"],
                        option_type=l["option_type"],
                        lots=l["lots"],
                        lot_size=l["lot_size"],
                        entry_price=float(l["entry_price"] or 0),
                        sl_price=float(l["sl_price"] or 0),
                        order_id=l.get("broker_order_id") or "",
                    )
                    for l in legs_raw
                ]
            self._state[dep_id] = state
            logger.info("Reloaded deployment %d (%s)", dep_id, state.status)
