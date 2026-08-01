import unittest

from options.nifty_retention import RETENTION_DAYS, TARGETS
from options.nifty_ws import N_STRIKES, select_option_contracts


class NiftyIngestionTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
