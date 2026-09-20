"""
Pure, dependency-free 1-second tick/depth aggregation for CRUDEOIL.

Phase-1 data-collection improvement (see CRUDE_DATA_PHASE1_IMPLEMENTATION.md
in trade-bot). Extracted out of crudeoil_ws.py so the aggregation math
(within-second OHLC, depth open/min/max/close, best-bid/ask change counts,
and the explicitly-labelled quote-rule trade-flow inference) can be
unit-tested without a live Kite connection, a database, or threading.

Nothing in this module performs I/O or touches global state. crudeoil_ws.py
owns all DB writes, WebSocket plumbing, and session lifecycle; this module
only accumulates one exchange-second of already-extracted tick primitives
and classifies session/quality state from a wall-clock timestamp.

IMPORTANT — inferred trade-flow fields are NOT ground truth. Kite does not
supply a per-print buy/sell aggressor flag. classify_trade() applies a
standard quote-rule (falls back to a tick-rule when the prevailing quote is
unknown) to each trade's incremental volume. This is a well-established but
imperfect microstructure technique — never treat inferred_buy_volume_1s /
inferred_sell_volume_1s / inferred_signed_volume_1s as exchange-confirmed
executed-side volume, and never accumulate them into a multi-hour "CVD"
without re-stating this caveat, since classification error compounds over
time. See docs: CRUDE_DATA_PHASE1_IMPLEMENTATION.md, "Fields Rejected".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Optional

# ── Session-phase classification ────────────────────────────────────────────
#
# Mirrors the exact calendar rules crudeoil_ws.py's _mcx_open() already uses
# (MCX_HOLIDAYS / MCX_EVENING_ONLY_DAYS from config.py) -- this does not
# introduce a new calendar or change when the collector runs. It only labels
# what that existing logic decides into a finer-grained state, so research
# can distinguish frozen 08:45-08:59 pre-market ticks from real live trading
# without deleting or altering the underlying rows.

WARMUP_START = time(8, 45)
NORMAL_OPEN = time(9, 0)
EVENING_OPEN = time(17, 0)
SESSION_CLOSE = time(23, 30)

SESSION_WARMUP = "WARMUP"
SESSION_LIVE = "LIVE"
SESSION_SPECIAL = "SPECIAL_SESSION"
SESSION_CLOSED = "CLOSED"


def classify_session_phase(
    now_ist: datetime,
    mcx_holidays: set,
    mcx_evening_only_days: set,
) -> str:
    """Classify one IST wall-clock timestamp into a session-state tag.

    Deliberately conservative: anything not clearly WARMUP/LIVE/SPECIAL is
    CLOSED rather than a guessed LIVE, since a stray row must never be
    silently treated as tradeable data. today's real evening-only/holiday
    behaviour already comes from the same MCX_HOLIDAYS/MCX_EVENING_ONLY_DAYS
    sets the live collector uses -- this function does not hardcode dates.
    """
    if now_ist.weekday() >= 5:
        return SESSION_CLOSED
    day_str = now_ist.date().strftime("%Y-%m-%d")
    t = now_ist.time()
    if day_str in mcx_holidays:
        return SESSION_CLOSED
    if day_str in mcx_evening_only_days:
        # Current collector behaviour has no pre-evening warmup window
        # (_seconds_until_open() wakes at exactly 17:00 on these days) --
        # mirror that: nothing before 17:00 counts as WARMUP on these days.
        if EVENING_OPEN <= t <= SESSION_CLOSE:
            return SESSION_SPECIAL
        return SESSION_CLOSED
    if NORMAL_OPEN <= t <= SESSION_CLOSE:
        return SESSION_LIVE
    if WARMUP_START <= t < NORMAL_OPEN:
        return SESSION_WARMUP
    return SESSION_CLOSED


# ── Quality flags ────────────────────────────────────────────────────────────
#
# Deliberately small and conservative. STALE_SOURCE and GAP_RECOVERY are the
# only two conditions this module can honestly detect from a single second's
# own data (last-tick latency, and the gap-to-previous-flushed-second already
# known to the flush loop) -- anything requiring cross-day history belongs in
# the crude_collection_health table (Part 6), not a per-row guess here.

QUALITY_GOOD = "GOOD"
QUALITY_LOW_UPDATE_COUNT = "LOW_UPDATE_COUNT"
QUALITY_STALE_SOURCE = "STALE_SOURCE"
QUALITY_GAP_RECOVERY = "GAP_RECOVERY"
QUALITY_WARMUP = "WARMUP"
QUALITY_NON_TRADING = "NON_TRADING"

# Conservative thresholds, documented here rather than tuned from outcomes
# (Part 7 explicitly forbids optimizing health thresholds from trading
# results). A normal live second typically carries several ticks; a fully
# stalled data_age well beyond one second's own window indicates the last
# tick that fed this bar was already old by the time it was flushed.
LOW_UPDATE_COUNT_THRESHOLD = 1          # <=1 tick in the second
STALE_SOURCE_AGE_MS_THRESHOLD = 2000    # last tick already >2s old at flush


def classify_quality_flag(
    *,
    session_phase: str,
    tick_count: int,
    data_age_ms: Optional[int],
    gap_seconds: Optional[float],
) -> str:
    """Deterministic, documented quality classification for one flushed row.

    Precedence: session state first (a WARMUP/CLOSED row's low tick count is
    expected, not a fault), then staleness, then gap-recovery, then update
    count, else GOOD. Never fabricates precision the inputs don't support --
    a None data_age_ms/gap_seconds simply skips that check.
    """
    if session_phase == SESSION_WARMUP:
        return QUALITY_WARMUP
    if session_phase == SESSION_CLOSED:
        return QUALITY_NON_TRADING
    if data_age_ms is not None and data_age_ms > STALE_SOURCE_AGE_MS_THRESHOLD:
        return QUALITY_STALE_SOURCE
    if gap_seconds is not None and gap_seconds > 1.0:
        return QUALITY_GAP_RECOVERY
    if tick_count <= LOW_UPDATE_COUNT_THRESHOLD:
        return QUALITY_LOW_UPDATE_COUNT
    return QUALITY_GOOD


# ── Within-second min/open/max/close tracker ────────────────────────────────

@dataclass
class _MinMaxTracker:
    open: Optional[float] = None
    close: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None

    def update(self, value: Optional[float]) -> None:
        if value is None:
            return
        if self.open is None:
            self.open = value
        self.close = value
        self.min = value if self.min is None else min(self.min, value)
        self.max = value if self.max is None else max(self.max, value)


def _change(tracker: _MinMaxTracker) -> Optional[float]:
    if tracker.open is None or tracker.close is None:
        return None
    return tracker.close - tracker.open


# ── The accumulator ──────────────────────────────────────────────────────────

@dataclass
class SecondAccumulator:
    """Accumulates one exchange-second of futures ticks.

    Call add_price_tick()/add_depth_tick()/classify_trade() once per
    WebSocket tick belonging to this second (in receipt order), then call
    flush() exactly once at the second boundary. Do not reuse an instance
    after flush() — construct a new one (or call reset()) for the next
    second. This mirrors the existing crudeoil_ws.py _bar/_reset_bar()
    lifecycle and runs alongside it without altering it.
    """

    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    tick_count: int = 0
    depth_update_count: int = 0
    best_bid_change_count: int = 0
    best_ask_change_count: int = 0

    spread: _MinMaxTracker = field(default_factory=_MinMaxTracker)
    microprice: _MinMaxTracker = field(default_factory=_MinMaxTracker)
    imbalance_l1: _MinMaxTracker = field(default_factory=_MinMaxTracker)
    imbalance_l5: _MinMaxTracker = field(default_factory=_MinMaxTracker)
    bid_depth_l1: _MinMaxTracker = field(default_factory=_MinMaxTracker)
    ask_depth_l1: _MinMaxTracker = field(default_factory=_MinMaxTracker)
    bid_depth_l5: _MinMaxTracker = field(default_factory=_MinMaxTracker)
    ask_depth_l5: _MinMaxTracker = field(default_factory=_MinMaxTracker)

    inferred_buy_volume: int = 0
    inferred_sell_volume: int = 0
    trade_unclassified_volume: int = 0

    _last_best_bid: Optional[float] = None
    _last_best_ask: Optional[float] = None

    def reset(self) -> None:
        self.__init__()  # dataclass default-refresh; cheap, no shared state

    def add_price_tick(self, ltp: float) -> None:
        self.tick_count += 1
        if self.open is None:
            self.open = ltp
        self.high = ltp if self.high is None else max(self.high, ltp)
        self.low = ltp if self.low is None else min(self.low, ltp)
        self.close = ltp

    def add_depth_tick(
        self,
        best_bid: Optional[float],
        best_bid_qty: Optional[int],
        best_ask: Optional[float],
        best_ask_qty: Optional[int],
        bid_qty_l5: Optional[int],
        ask_qty_l5: Optional[int],
    ) -> None:
        """Feed one tick's top-of-book + L5 depth summary into the tracker.

        Only true snapshot values already computed by the existing
        _depth_metrics()/_normalise_depth() helpers are accepted here — this
        function does not itself parse raw Kite depth payloads.
        """
        if best_bid is None or best_ask is None:
            return
        self.depth_update_count += 1

        spread = best_ask - best_bid
        total_l1 = (best_bid_qty or 0) + (best_ask_qty or 0)
        imbalance_l1 = (
            ((best_bid_qty or 0) - (best_ask_qty or 0)) / total_l1
            if total_l1 else None
        )
        microprice = (
            (best_ask * (best_bid_qty or 0) + best_bid * (best_ask_qty or 0)) / total_l1
            if total_l1 else None
        )
        total_l5 = (bid_qty_l5 or 0) + (ask_qty_l5 or 0)
        imbalance_l5 = (
            ((bid_qty_l5 or 0) - (ask_qty_l5 or 0)) / total_l5
            if total_l5 else None
        )

        self.spread.update(spread)
        self.microprice.update(microprice)
        self.imbalance_l1.update(imbalance_l1)
        self.imbalance_l5.update(imbalance_l5)
        self.bid_depth_l1.update(best_bid_qty)
        self.ask_depth_l1.update(best_ask_qty)
        self.bid_depth_l5.update(bid_qty_l5)
        self.ask_depth_l5.update(ask_qty_l5)

        if self._last_best_bid is not None and best_bid != self._last_best_bid:
            self.best_bid_change_count += 1
        if self._last_best_ask is not None and best_ask != self._last_best_ask:
            self.best_ask_change_count += 1
        self._last_best_bid = best_bid
        self._last_best_ask = best_ask

    def classify_trade(
        self,
        traded_qty: int,
        ltp: float,
        prevailing_best_bid: Optional[float],
        prevailing_best_ask: Optional[float],
        prev_ltp: Optional[float],
    ) -> None:
        """INFERRED quote-rule (tick-rule fallback) classification of one
        trade's incremental volume. See module docstring — never ground
        truth. `prevailing_*` must be the book state BEFORE this trade
        (i.e. the previous tick's top-of-book), not this tick's own depth,
        or the classification would use information not actually available
        at the moment of the trade.
        """
        if traded_qty <= 0:
            return
        if prevailing_best_ask is not None and ltp >= prevailing_best_ask:
            self.inferred_buy_volume += traded_qty
        elif prevailing_best_bid is not None and ltp <= prevailing_best_bid:
            self.inferred_sell_volume += traded_qty
        elif prev_ltp is not None and ltp > prev_ltp:
            self.inferred_buy_volume += traded_qty
        elif prev_ltp is not None and ltp < prev_ltp:
            self.inferred_sell_volume += traded_qty
        else:
            # Genuinely ambiguous (ltp inside the spread, no ltp change to
            # break the tie, or no prevailing quote yet this session) --
            # explicitly counted as unclassified rather than guessed.
            self.trade_unclassified_volume += traded_qty

    def flush(self) -> Optional[dict]:
        """Return the completed second's aggregate, or None if no price
        tick was ever received this second (mirrors _bar['open'] is None).
        """
        if self.open is None:
            return None

        classified = self.inferred_buy_volume + self.inferred_sell_volume
        universe = classified + self.trade_unclassified_volume
        confidence = (classified / universe) if universe else None

        return {
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "tick_count": self.tick_count,
            "depth_update_count": self.depth_update_count,
            "best_bid_change_count": self.best_bid_change_count,
            "best_ask_change_count": self.best_ask_change_count,
            "spread_open": self.spread.open,
            "spread_min": self.spread.min,
            "spread_max": self.spread.max,
            "microprice_open": self.microprice.open,
            "microprice_min": self.microprice.min,
            "microprice_max": self.microprice.max,
            "microprice_change_1s": _change(self.microprice),
            "imbalance_l1_open": self.imbalance_l1.open,
            "imbalance_l1_min": self.imbalance_l1.min,
            "imbalance_l1_max": self.imbalance_l1.max,
            "imbalance_l1_change_1s": _change(self.imbalance_l1),
            "imbalance_l5_open": self.imbalance_l5.open,
            "imbalance_l5_min": self.imbalance_l5.min,
            "imbalance_l5_max": self.imbalance_l5.max,
            "imbalance_l5_change_1s": _change(self.imbalance_l5),
            "bid_depth_l1_open": self.bid_depth_l1.open,
            "bid_depth_l1_min": self.bid_depth_l1.min,
            "bid_depth_l1_max": self.bid_depth_l1.max,
            "bid_depth_l1_close": self.bid_depth_l1.close,
            "ask_depth_l1_open": self.ask_depth_l1.open,
            "ask_depth_l1_min": self.ask_depth_l1.min,
            "ask_depth_l1_max": self.ask_depth_l1.max,
            "ask_depth_l1_close": self.ask_depth_l1.close,
            "bid_depth_l5_open": self.bid_depth_l5.open,
            "bid_depth_l5_min": self.bid_depth_l5.min,
            "bid_depth_l5_max": self.bid_depth_l5.max,
            "bid_depth_l5_change_1s": _change(self.bid_depth_l5),
            "ask_depth_l5_open": self.ask_depth_l5.open,
            "ask_depth_l5_min": self.ask_depth_l5.min,
            "ask_depth_l5_max": self.ask_depth_l5.max,
            "ask_depth_l5_change_1s": _change(self.ask_depth_l5),
            "inferred_buy_volume_1s": self.inferred_buy_volume,
            "inferred_sell_volume_1s": self.inferred_sell_volume,
            "inferred_signed_volume_1s": self.inferred_buy_volume - self.inferred_sell_volume,
            "trade_classified_volume_1s": classified,
            "trade_unclassified_volume_1s": self.trade_unclassified_volume,
            "trade_classification_confidence": confidence,
        }
