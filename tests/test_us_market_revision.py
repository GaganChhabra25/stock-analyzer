"""
Deterministic unit tests for options/us_market.py's revision/repaint fix
(CRUDE_DATA_REMEDIATION_20260921.md follow-up).

Pure logic only -- no network, no DB (same convention as
test_global_prices_intraday.py / test_us_market_health_check.py). Exercises
`_revision_fields()`, the executable spec of the `ON CONFLICT ... DO UPDATE`
CASE clauses actually run by `_upsert()`, so the revision-logging decision
can be proven correct without a live Postgres connection.
"""

import unittest
from datetime import datetime, timezone

from options.us_market import _revision_fields


def _utc(y, m, d, h, mi, s=0):
    return datetime(y, m, d, h, mi, s, tzinfo=timezone.utc)


class RevisionFieldsTests(unittest.TestCase):
    def test_first_write_has_no_prior_close_so_no_revision(self):
        # existing_close=None means this is the very first INSERT for the
        # key (the DO UPDATE branch never even runs on a true first write,
        # but the pure function must not misbehave if ever probed this way).
        received_at = _utc(2026, 9, 21, 10, 1)
        last_revised_at, revision_count = _revision_fields(
            existing_close=None,
            incoming_close=100.0,
            existing_last_revised_at=None,
            existing_revision_count=0,
            incoming_received_at=received_at,
        )
        self.assertIsNone(last_revised_at)
        self.assertEqual(revision_count, 0)

    def test_unchanged_close_on_reupsert_is_not_a_revision(self):
        # A re-fetch of the exact same value (common: intraday collector
        # re-pulls the same completed 5-min bar every tick within its
        # window) must not be logged as a revision.
        first_received_at = _utc(2026, 9, 21, 10, 1)
        last_revised_at, revision_count = _revision_fields(
            existing_close=100.0,
            incoming_close=100.0,
            existing_last_revised_at=None,
            existing_revision_count=0,
            incoming_received_at=_utc(2026, 9, 21, 10, 6),
        )
        self.assertIsNone(last_revised_at)
        self.assertEqual(revision_count, 0)

    def test_changed_close_on_reupsert_is_logged_as_a_revision(self):
        revision_at = _utc(2026, 9, 21, 10, 20)
        last_revised_at, revision_count = _revision_fields(
            existing_close=100.0,
            incoming_close=105.0,
            existing_last_revised_at=None,
            existing_revision_count=0,
            incoming_received_at=revision_at,
        )
        self.assertEqual(last_revised_at, revision_at)
        self.assertEqual(revision_count, 1)

    def test_second_revision_increments_count_and_moves_last_revised_at(self):
        first_revision_at = _utc(2026, 9, 21, 10, 20)
        second_revision_at = _utc(2026, 9, 21, 10, 40)
        last_revised_at, revision_count = _revision_fields(
            existing_close=105.0,
            incoming_close=107.0,
            existing_last_revised_at=first_revision_at,
            existing_revision_count=1,
            incoming_received_at=second_revision_at,
        )
        self.assertEqual(last_revised_at, second_revision_at)
        self.assertEqual(revision_count, 2)

    def test_reupsert_after_a_revision_with_same_value_keeps_prior_marker(self):
        # Once revised, subsequent re-fetches that agree with the revised
        # value must not keep bumping last_revised_at/revision_count.
        first_revision_at = _utc(2026, 9, 21, 10, 20)
        last_revised_at, revision_count = _revision_fields(
            existing_close=105.0,
            incoming_close=105.0,
            existing_last_revised_at=first_revision_at,
            existing_revision_count=1,
            incoming_received_at=_utc(2026, 9, 21, 10, 45),
        )
        self.assertEqual(last_revised_at, first_revision_at)
        self.assertEqual(revision_count, 1)


if __name__ == "__main__":
    unittest.main()
