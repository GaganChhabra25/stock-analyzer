"""
Deterministic unit tests for options/global_prices_intraday.py (Phase-2).
Pure logic only -- no network, no DB. See
CRUDE_DATA_PHASE2_IMPLEMENTATION.md (trade-bot repo).
"""

import unittest

from options.global_prices_intraday import (
    classify_price_quality,
    QUALITY_DELAYED,
    QUALITY_STALE,
    STALE_AGE_MS_THRESHOLD,
)


class ClassifyPriceQualityTests(unittest.TestCase):
    def test_fresh_row_is_delayed_not_realtime(self):
        # Never claim REALTIME -- Yahoo's actual latency is undocumented.
        self.assertEqual(classify_price_quality(1000), QUALITY_DELAYED)

    def test_zero_age_is_delayed(self):
        self.assertEqual(classify_price_quality(0), QUALITY_DELAYED)

    def test_none_age_is_delayed(self):
        self.assertEqual(classify_price_quality(None), QUALITY_DELAYED)

    def test_just_under_threshold_is_delayed(self):
        self.assertEqual(classify_price_quality(STALE_AGE_MS_THRESHOLD), QUALITY_DELAYED)

    def test_over_threshold_is_stale(self):
        self.assertEqual(classify_price_quality(STALE_AGE_MS_THRESHOLD + 1), QUALITY_STALE)


if __name__ == "__main__":
    unittest.main()
