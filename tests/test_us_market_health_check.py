"""
Deterministic unit tests for options/crude_collection_health.py's us_market
schedule-awareness helper (CRUDE_DATA_REMEDIATION_20260921.md Issue 1/6).

Pure logic only -- no network, no DB. Verifies _us_market_active_window()
correctly mirrors the deployed crontab.docker windows for options/us_market.py
(5 5 * * 1-6 daily; */5 15-22 * * 1-5 intraday, CEST == 13:00-20:00 UTC) so
the health monitor reports NON_TRADING (not a false DISCONNECTED) outside
those windows.
"""

import unittest
from datetime import datetime, timezone

from options.crude_collection_health import _us_market_active_window


def _utc(y, m, d, h, mi):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


class UsMarketActiveWindowTests(unittest.TestCase):
    # Monday 2026-09-21 used throughout (matches the certification's date).

    def test_inside_intraday_window_monday(self):
        active, _ = _us_market_active_window(_utc(2026, 9, 21, 15, 0))
        self.assertTrue(active)

    def test_intraday_window_boundaries_inclusive(self):
        self.assertTrue(_us_market_active_window(_utc(2026, 9, 21, 13, 0))[0])
        self.assertTrue(_us_market_active_window(_utc(2026, 9, 21, 20, 0))[0])

    def test_just_outside_intraday_window(self):
        self.assertFalse(_us_market_active_window(_utc(2026, 9, 21, 12, 59))[0])
        self.assertFalse(_us_market_active_window(_utc(2026, 9, 21, 20, 1))[0])

    def test_morning_mcx_session_is_not_active(self):
        # This is exactly the scenario the 2026-09-21 first-live-session
        # certification audited: MCX open ~03:16 UTC, audit ran ~03:56-04:00
        # UTC -- us_market's own cron has not fired yet for the day.
        active, reason = _us_market_active_window(_utc(2026, 9, 21, 4, 0))
        self.assertFalse(active)
        self.assertIn("idle by design", reason)

    def test_saturday_intraday_window_inactive(self):
        # Cron is Mon-Fri (1-5) for intraday; Saturday must not be active.
        active, _ = _us_market_active_window(_utc(2026, 9, 19, 15, 0))
        self.assertFalse(active)

    def test_daily_run_grace_window_saturday_active(self):
        # Daily backfill is Mon-Sat (1-6); Saturday 03:05-03:20 UTC active.
        active, reason = _us_market_active_window(_utc(2026, 9, 19, 3, 10))
        self.assertTrue(active)
        self.assertIn("daily-backfill", reason)

    def test_daily_run_grace_window_sunday_inactive(self):
        active, _ = _us_market_active_window(_utc(2026, 9, 20, 3, 10))
        self.assertFalse(active)

    def test_daily_run_grace_window_expires(self):
        active, _ = _us_market_active_window(_utc(2026, 9, 21, 3, 21))
        self.assertFalse(active)


if __name__ == "__main__":
    unittest.main()
