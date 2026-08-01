import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from options.nifty_retention import RETENTION_DAYS, TARGETS
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


if __name__ == "__main__":
    unittest.main()
