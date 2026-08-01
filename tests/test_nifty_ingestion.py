import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from options.nifty_retention import RETENTION_DAYS, TARGETS
from options.nifty_feature_backfill import (
    SOURCE_INTERVAL_SECONDS,
    _build_day_payloads,
    _market_from_option_rows,
    _minute_close,
)
from options.nifty_ws import (
    N_STRIKES,
    _feature_history,
    _previous_feature_totals,
    build_feature_payloads,
    nifty_contract_mask,
    select_option_contracts,
)


class NiftyIngestionTests(unittest.TestCase):
    def tearDown(self):
        _feature_history.clear()
        _previous_feature_totals.clear()

    def test_atm_plus_minus_ten_selects_42_contracts(self):
        universe = {}
        token = 1
        for strike in range(24000, 26100, 50):
            for option_type in ("CE", "PE"):
                universe[(strike, option_type)] = {
                    "instrument_token": token,
                    "strike": strike,
                    "option_type": option_type,
                }
                token += 1
        selected = select_option_contracts(universe, 25000)
        self.assertEqual(N_STRIKES, 10)
        self.assertEqual(len(selected), 42)
        self.assertEqual(min(row["strike"] for row in selected.values()), 24500)
        self.assertEqual(max(row["strike"] for row in selected.values()), 25500)

    def test_blank_kite_name_still_matches_only_nifty_derivatives(self):
        frame = pd.DataFrame({
            "name": ["", "", "NIFTY", ""],
            "tradingsymbol": [
                "NIFTY26804CE", "NIFTY26AUGFUT", "NIFTY26804PE", "NIFTYIT26AUGFUT"
            ],
        })
        self.assertEqual(nifty_contract_mask(frame).tolist(), [True, True, True, False])

    def test_retention_is_nifty_scoped_and_never_mentions_mcx(self):
        self.assertEqual(RETENTION_DAYS, 30)
        sql = " ".join(f"{target.table} {target.predicate}" for target in TARGETS).upper()
        self.assertNotIn("CRUDEOIL", sql)
        self.assertNotIn("NATURALGAS", sql)
        self.assertNotIn("MCX_", sql)
        self.assertIn("INSTRUMENT = 'NIFTY'", sql)
        self.assertIn("SYMBOL = 'NIFTY50'", sql)
        self.assertIn("NIFTY_FEATURES", sql)
        self.assertIn("NIFTY_EXPIRY_FEATURES", sql)
        self.assertTrue(all("BANKNIFTY" not in target.predicate for target in TARGETS))

    def test_per_second_feature_payloads_are_causal_and_complete(self):
        ts = datetime(2026, 8, 3, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        expiry = date(2026, 8, 6)
        rows = []
        for strike in range(24500, 25501, 50):
            distance = abs(strike - 25000) / 50
            for option_type in ("CE", "PE"):
                oi = 1000 + (10 - distance) * 100
                if strike == 25200 and option_type == "CE":
                    oi = 9000
                if strike == 24800 and option_type == "PE":
                    oi = 8000
                rows.append((
                    ts, "NIFTY", expiry, strike, option_type,
                    100 + distance, 99.5, 100.5, oi, 5, 10000,
                    14.0 + (0.5 if option_type == "PE" else 0),
                    0.5 if option_type == "CE" else -0.5,
                    0.001, -5.0, 8.0, 25000.0,
                ))
        market = {
            "spot": 25000.0, "vix": 13.2, "atm": 25000,
            "pcr": 1.1, "straddle": 200.0, "expected_move": 0.8,
            "call_wall": 25200, "put_wall": 24800,
        }

        nifty, expiry_features = build_feature_payloads(ts, rows, market)

        self.assertEqual(nifty["atm_strike"], 25000)
        self.assertEqual(nifty["ce_max_oi_strike"], 25200)
        self.assertEqual(nifty["pe_max_oi_strike"], 24800)
        self.assertEqual(expiry_features["expiry_date"], expiry)
        self.assertEqual(expiry_features["ce_oi_strike_1"], 25200)
        self.assertEqual(expiry_features["pe_oi_strike_1"], 24800)
        self.assertIsNotNone(expiry_features["max_pain_strike"])
        self.assertAlmostEqual(
            expiry_features["gex_net"],
            expiry_features["gex_ce"] + expiry_features["gex_pe"],
        )
        self.assertNotIn("target_direction", nifty)
        self.assertNotIn("target_30min", nifty)
        self.assertNotIn("target_expiry_price", expiry_features)
        self.assertEqual(nifty["source_interval_seconds"], 1)
        self.assertEqual(expiry_features["source_interval_seconds"], 1)

    def test_minute_backfill_reuses_causal_features_and_marks_granularity(self):
        source_ts = datetime(2026, 7, 31, 9, 15, 4, tzinfo=ZoneInfo("Asia/Kolkata"))
        expiry = date(2026, 8, 6)
        rows = []
        for strike in range(24500, 25501, 50):
            for option_type in ("CE", "PE"):
                oi = 1000
                rows.append((
                    source_ts, 25000.0, 13.0, 25000,
                    expiry, strike, option_type, 100.0, 99.5, 100.5,
                    oi, 5, 100, 14.0,
                    0.5 if option_type == "CE" else -0.5,
                    0.001, -5.0, 8.0, 25000.0,
                ))

        nifty, expiry_rows = _build_day_payloads(rows)

        self.assertEqual(len(nifty), 1)
        self.assertEqual(len(expiry_rows), 1)
        self.assertEqual(nifty[0]["ts"], datetime(2026, 7, 31, 9, 16, tzinfo=ZoneInfo("Asia/Kolkata")))
        self.assertEqual(nifty[0]["source_interval_seconds"], SOURCE_INTERVAL_SECONDS)
        self.assertEqual(expiry_rows[0]["source_interval_seconds"], SOURCE_INTERVAL_SECONDS)
        self.assertNotIn("target_direction", nifty[0])
        self.assertNotIn("target_expiry_price", expiry_rows[0])

    def test_minute_backfill_market_is_current_expiry_selected_range_only(self):
        ts = datetime(2026, 7, 31, 9, 16, tzinfo=ZoneInfo("Asia/Kolkata"))
        expiry = date(2026, 8, 6)
        rows = [
            (ts, "NIFTY", expiry, 25000, "CE", 100, 99, 101, 1000, 0, 10, 14, .5, .001, -5, 8, 25000),
            (ts, "NIFTY", expiry, 25000, "PE", 120, 119, 121, 1500, 0, 10, 15, -.5, .001, -5, 8, 25000),
            (ts, "NIFTY", expiry, 25100, "CE", 60, 59, 61, 5000, 0, 10, 13, .4, .001, -5, 8, 25000),
            (ts, "NIFTY", expiry, 24900, "PE", 70, 69, 71, 6000, 0, 10, 16, -.4, .001, -5, 8, 25000),
        ]

        market = _market_from_option_rows(25000, 13.2, 25000, rows)

        self.assertEqual(market["straddle"], 220)
        self.assertEqual(market["call_wall"], 25100)
        self.assertEqual(market["put_wall"], 24900)
        self.assertAlmostEqual(market["pcr"], 7500 / 6000)

    def test_minute_close_is_regular_and_never_precedes_source(self):
        source = datetime(2026, 7, 31, 9, 15, 58, 999999, tzinfo=ZoneInfo("Asia/Kolkata"))
        feature_ts = _minute_close(source)
        self.assertEqual(feature_ts, datetime(2026, 7, 31, 9, 16, tzinfo=ZoneInfo("Asia/Kolkata")))
        self.assertGreaterEqual(feature_ts, source)


if __name__ == "__main__":
    unittest.main()
