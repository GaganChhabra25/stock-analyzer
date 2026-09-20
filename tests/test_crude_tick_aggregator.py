"""
Deterministic unit tests for options/crude_tick_aggregator.py (Phase-1 CRUDE
data-collection improvement). Pure logic only -- no DB, no Kite, no
threading, no network. See CRUDE_DATA_PHASE1_IMPLEMENTATION.md (trade-bot
repo) for the full report this covers.
"""

import unittest
from datetime import datetime, timezone, timedelta

from options.crude_tick_aggregator import (
    SecondAccumulator,
    classify_session_phase,
    classify_quality_flag,
    SESSION_WARMUP,
    SESSION_LIVE,
    SESSION_SPECIAL,
    SESSION_CLOSED,
    QUALITY_GOOD,
    QUALITY_LOW_UPDATE_COUNT,
    QUALITY_STALE_SOURCE,
    QUALITY_GAP_RECOVERY,
    QUALITY_WARMUP,
    QUALITY_NON_TRADING,
)

IST = timezone(timedelta(hours=5, minutes=30))
HOLIDAYS = {"2026-01-26"}
EVENING_ONLY = {"2026-09-14"}


class SessionPhaseTests(unittest.TestCase):
    def test_warmup_window(self):
        ts = datetime(2026, 9, 15, 8, 50, tzinfo=IST)  # Tuesday
        self.assertEqual(classify_session_phase(ts, HOLIDAYS, EVENING_ONLY), SESSION_WARMUP)

    def test_normal_live_window(self):
        ts = datetime(2026, 9, 15, 11, 0, tzinfo=IST)
        self.assertEqual(classify_session_phase(ts, HOLIDAYS, EVENING_ONLY), SESSION_LIVE)

    def test_before_warmup_is_closed(self):
        ts = datetime(2026, 9, 15, 8, 0, tzinfo=IST)
        self.assertEqual(classify_session_phase(ts, HOLIDAYS, EVENING_ONLY), SESSION_CLOSED)

    def test_after_close_is_closed(self):
        ts = datetime(2026, 9, 15, 23, 45, tzinfo=IST)
        self.assertEqual(classify_session_phase(ts, HOLIDAYS, EVENING_ONLY), SESSION_CLOSED)

    def test_weekend_is_closed(self):
        ts = datetime(2026, 9, 13, 11, 0, tzinfo=IST)  # Sunday
        self.assertEqual(classify_session_phase(ts, HOLIDAYS, EVENING_ONLY), SESSION_CLOSED)

    def test_full_holiday_is_closed_even_during_normal_hours(self):
        ts = datetime(2026, 1, 26, 11, 0, tzinfo=IST)  # Monday, Republic Day
        self.assertEqual(classify_session_phase(ts, HOLIDAYS, EVENING_ONLY), SESSION_CLOSED)

    def test_evening_only_day_before_1700_is_closed_not_warmup(self):
        # 2026-09-14 is a Monday; current live collector has no pre-evening
        # warmup on these days (_seconds_until_open wakes exactly at 17:00).
        ts = datetime(2026, 9, 14, 8, 50, tzinfo=IST)
        self.assertEqual(classify_session_phase(ts, HOLIDAYS, EVENING_ONLY), SESSION_CLOSED)

    def test_evening_only_day_during_session_is_special(self):
        ts = datetime(2026, 9, 14, 18, 0, tzinfo=IST)
        self.assertEqual(classify_session_phase(ts, HOLIDAYS, EVENING_ONLY), SESSION_SPECIAL)

    def test_evening_only_day_after_close_is_closed(self):
        ts = datetime(2026, 9, 14, 23, 45, tzinfo=IST)
        self.assertEqual(classify_session_phase(ts, HOLIDAYS, EVENING_ONLY), SESSION_CLOSED)


class QualityFlagTests(unittest.TestCase):
    def test_warmup_session_overrides_everything(self):
        flag = classify_quality_flag(
            session_phase=SESSION_WARMUP, tick_count=50, data_age_ms=100, gap_seconds=0,
        )
        self.assertEqual(flag, QUALITY_WARMUP)

    def test_closed_session_is_non_trading(self):
        flag = classify_quality_flag(
            session_phase=SESSION_CLOSED, tick_count=0, data_age_ms=None, gap_seconds=None,
        )
        self.assertEqual(flag, QUALITY_NON_TRADING)

    def test_stale_source_detected(self):
        flag = classify_quality_flag(
            session_phase=SESSION_LIVE, tick_count=10, data_age_ms=5000, gap_seconds=0,
        )
        self.assertEqual(flag, QUALITY_STALE_SOURCE)

    def test_not_stale_under_threshold(self):
        flag = classify_quality_flag(
            session_phase=SESSION_LIVE, tick_count=10, data_age_ms=500, gap_seconds=0,
        )
        self.assertEqual(flag, QUALITY_GOOD)

    def test_gap_recovery_detected(self):
        flag = classify_quality_flag(
            session_phase=SESSION_LIVE, tick_count=10, data_age_ms=100, gap_seconds=3.0,
        )
        self.assertEqual(flag, QUALITY_GAP_RECOVERY)

    def test_low_update_count(self):
        flag = classify_quality_flag(
            session_phase=SESSION_LIVE, tick_count=1, data_age_ms=100, gap_seconds=0,
        )
        self.assertEqual(flag, QUALITY_LOW_UPDATE_COUNT)

    def test_zero_updates_is_low_update_count(self):
        flag = classify_quality_flag(
            session_phase=SESSION_LIVE, tick_count=0, data_age_ms=None, gap_seconds=None,
        )
        self.assertEqual(flag, QUALITY_LOW_UPDATE_COUNT)

    def test_healthy_second(self):
        flag = classify_quality_flag(
            session_phase=SESSION_LIVE, tick_count=8, data_age_ms=150, gap_seconds=0.0,
        )
        self.assertEqual(flag, QUALITY_GOOD)

    def test_special_session_not_treated_as_warmup_or_closed(self):
        flag = classify_quality_flag(
            session_phase=SESSION_SPECIAL, tick_count=8, data_age_ms=150, gap_seconds=0.0,
        )
        self.assertEqual(flag, QUALITY_GOOD)


class SecondAccumulatorPriceTests(unittest.TestCase):
    def test_zero_updates_flushes_none(self):
        acc = SecondAccumulator()
        self.assertIsNone(acc.flush())

    def test_single_update_open_high_low_close_equal(self):
        acc = SecondAccumulator()
        acc.add_price_tick(9800.0)
        row = acc.flush()
        self.assertEqual(row["open"], 9800.0)
        self.assertEqual(row["high"], 9800.0)
        self.assertEqual(row["low"], 9800.0)
        self.assertEqual(row["close"], 9800.0)
        self.assertEqual(row["tick_count"], 1)

    def test_multiple_updates_preserve_true_ohlc(self):
        acc = SecondAccumulator()
        for price in [9800.0, 9805.0, 9795.0, 9802.0]:
            acc.add_price_tick(price)
        row = acc.flush()
        self.assertEqual(row["open"], 9800.0)
        self.assertEqual(row["high"], 9805.0)
        self.assertEqual(row["low"], 9795.0)
        self.assertEqual(row["close"], 9802.0)
        self.assertEqual(row["tick_count"], 4)

    def test_reset_clears_prior_second_state(self):
        acc = SecondAccumulator()
        acc.add_price_tick(9800.0)
        acc.flush()
        acc.reset()
        self.assertIsNone(acc.flush())  # nothing added since reset


class SecondAccumulatorDepthTests(unittest.TestCase):
    def test_depth_open_min_max_captured(self):
        acc = SecondAccumulator()
        acc.add_price_tick(9800.0)
        # spread widens then narrows across the second
        acc.add_depth_tick(9799.0, 10, 9801.0, 10, 50, 50)   # spread 2
        acc.add_depth_tick(9798.0, 10, 9803.0, 10, 50, 50)   # spread 5 (max)
        acc.add_depth_tick(9799.5, 10, 9800.0, 10, 50, 50)   # spread 0.5 (min)
        row = acc.flush()
        self.assertAlmostEqual(row["spread_open"], 2.0)
        self.assertAlmostEqual(row["spread_max"], 5.0)
        self.assertAlmostEqual(row["spread_min"], 0.5)

    def test_imbalance_open_close_change(self):
        acc = SecondAccumulator()
        acc.add_price_tick(9800.0)
        acc.add_depth_tick(9799.0, 100, 9801.0, 50, 500, 400)   # imbalance_l1 = 50/150
        acc.add_depth_tick(9799.0, 20, 9801.0, 80, 300, 600)    # imbalance_l1 = -60/100
        row = acc.flush()
        self.assertAlmostEqual(row["imbalance_l1_open"], (100 - 50) / 150)
        self.assertAlmostEqual(row["imbalance_l1_close"] if "imbalance_l1_close" in row else row["imbalance_l1_max"], row["imbalance_l1_max"])
        change = row["imbalance_l1_change_1s"]
        expected_close = (20 - 80) / 100
        expected_open = (100 - 50) / 150
        self.assertAlmostEqual(change, expected_close - expected_open)

    def test_best_bid_ask_change_counts(self):
        acc = SecondAccumulator()
        acc.add_price_tick(9800.0)
        acc.add_depth_tick(9799.0, 10, 9801.0, 10, 50, 50)
        acc.add_depth_tick(9799.0, 10, 9801.0, 10, 50, 50)   # no change
        acc.add_depth_tick(9798.5, 10, 9801.0, 10, 50, 50)   # bid moved
        acc.add_depth_tick(9798.5, 10, 9802.0, 10, 50, 50)   # ask moved
        row = acc.flush()
        self.assertEqual(row["best_bid_change_count"], 1)
        self.assertEqual(row["best_ask_change_count"], 1)
        self.assertEqual(row["depth_update_count"], 4)

    def test_missing_depth_side_is_skipped_not_guessed(self):
        acc = SecondAccumulator()
        acc.add_price_tick(9800.0)
        acc.add_depth_tick(None, None, 9801.0, 10, 50, 50)  # no bid -> skipped entirely
        row = acc.flush()
        self.assertIsNone(row["spread_open"])
        self.assertEqual(row["depth_update_count"], 0)

    def test_l1_depth_open_min_max_close_are_new_fields(self):
        acc = SecondAccumulator()
        acc.add_price_tick(9800.0)
        acc.add_depth_tick(9799.0, 10, 9801.0, 20, 50, 60)
        acc.add_depth_tick(9799.0, 15, 9801.0, 25, 55, 65)
        row = acc.flush()
        self.assertEqual(row["bid_depth_l1_open"], 10)
        self.assertEqual(row["bid_depth_l1_close"], 15)
        self.assertEqual(row["bid_depth_l1_max"], 15)
        self.assertEqual(row["ask_depth_l1_open"], 20)
        self.assertEqual(row["ask_depth_l1_close"], 25)


class SecondAccumulatorTradeClassificationTests(unittest.TestCase):
    def test_trade_at_ask_classified_buy(self):
        acc = SecondAccumulator()
        acc.classify_trade(traded_qty=5, ltp=9801.0, prevailing_best_bid=9799.0,
                            prevailing_best_ask=9801.0, prev_ltp=9800.0)
        row = acc.flush() or acc.__dict__  # flush() needs a price tick; check counters directly
        self.assertEqual(acc.inferred_buy_volume, 5)
        self.assertEqual(acc.inferred_sell_volume, 0)

    def test_trade_at_bid_classified_sell(self):
        acc = SecondAccumulator()
        acc.classify_trade(traded_qty=7, ltp=9799.0, prevailing_best_bid=9799.0,
                            prevailing_best_ask=9801.0, prev_ltp=9800.0)
        self.assertEqual(acc.inferred_sell_volume, 7)
        self.assertEqual(acc.inferred_buy_volume, 0)

    def test_trade_inside_spread_falls_back_to_tick_rule_up(self):
        acc = SecondAccumulator()
        acc.classify_trade(traded_qty=3, ltp=9800.2, prevailing_best_bid=9799.0,
                            prevailing_best_ask=9801.0, prev_ltp=9800.0)
        self.assertEqual(acc.inferred_buy_volume, 3)

    def test_trade_inside_spread_falls_back_to_tick_rule_down(self):
        acc = SecondAccumulator()
        acc.classify_trade(traded_qty=3, ltp=9799.8, prevailing_best_bid=9799.0,
                            prevailing_best_ask=9801.0, prev_ltp=9800.0)
        self.assertEqual(acc.inferred_sell_volume, 3)

    def test_no_quote_and_no_price_change_is_unclassified(self):
        acc = SecondAccumulator()
        acc.classify_trade(traded_qty=4, ltp=9800.0, prevailing_best_bid=None,
                            prevailing_best_ask=None, prev_ltp=9800.0)
        self.assertEqual(acc.trade_unclassified_volume, 4)
        self.assertEqual(acc.inferred_buy_volume, 0)
        self.assertEqual(acc.inferred_sell_volume, 0)

    def test_zero_or_negative_qty_ignored(self):
        acc = SecondAccumulator()
        acc.classify_trade(traded_qty=0, ltp=9801.0, prevailing_best_bid=9799.0,
                            prevailing_best_ask=9801.0, prev_ltp=9800.0)
        acc.classify_trade(traded_qty=-5, ltp=9801.0, prevailing_best_bid=9799.0,
                            prevailing_best_ask=9801.0, prev_ltp=9800.0)
        self.assertEqual(acc.inferred_buy_volume, 0)
        self.assertEqual(acc.trade_unclassified_volume, 0)

    def test_flush_reports_signed_volume_and_confidence(self):
        acc = SecondAccumulator()
        acc.add_price_tick(9801.0)
        acc.classify_trade(traded_qty=10, ltp=9801.0, prevailing_best_bid=9799.0,
                            prevailing_best_ask=9801.0, prev_ltp=9800.0)   # buy
        acc.classify_trade(traded_qty=4, ltp=9799.0, prevailing_best_bid=9799.0,
                            prevailing_best_ask=9801.0, prev_ltp=9801.0)   # sell
        acc.classify_trade(traded_qty=2, ltp=9800.0, prevailing_best_bid=None,
                            prevailing_best_ask=None, prev_ltp=9800.0)     # unclassified
        row = acc.flush()
        self.assertEqual(row["inferred_buy_volume_1s"], 10)
        self.assertEqual(row["inferred_sell_volume_1s"], 4)
        self.assertEqual(row["inferred_signed_volume_1s"], 6)
        self.assertEqual(row["trade_classified_volume_1s"], 14)
        self.assertEqual(row["trade_unclassified_volume_1s"], 2)
        self.assertAlmostEqual(row["trade_classification_confidence"], 14 / 16)

    def test_confidence_none_when_no_trades(self):
        acc = SecondAccumulator()
        acc.add_price_tick(9800.0)
        row = acc.flush()
        self.assertIsNone(row["trade_classification_confidence"])
        self.assertEqual(row["inferred_signed_volume_1s"], 0)


if __name__ == "__main__":
    unittest.main()
