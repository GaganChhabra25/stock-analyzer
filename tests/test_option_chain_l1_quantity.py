"""
Regression tests for Task 1 (2026-09-21): wiring Kite's already-fetched
`depth.buy[0].quantity` / `depth.sell[0].quantity` into `option_chain`'s
existing `bid_quantities` / `ask_quantities` array columns (L1 only).

Covers:
  (a) the new quantity fields populate correctly from a mocked `kite.quote()`
      payload that mirrors the real Kite response shape (same `depth.buy`/
      `depth.sell` list-of-dict structure `crudeoil_ws.py`'s futures-depth
      `_normalise_depth()` already reads `quantity` from).
  (b) price/OI/IV/Greeks computation and output are unchanged by this edit —
      asserted directly against the real `implied_volatility`/
      `calculate_greeks` functions on the same inputs, and against the raw
      quote payload for ltp/bid/ask/oi/volume.

No DB or network access: `_insert_option_rows`/`_insert_snapshot`/
`_fill_oi_changes` are monkeypatched to capture the constructed rows instead
of touching Postgres.
"""

import unittest
from datetime import date, datetime
from unittest.mock import patch

import pandas as pd

from options.exchange.mcx import MCXCollector
from options.greeks import implied_volatility, calculate_greeks

RISK_FREE_RATE = 0.07


def _make_quote(last_price, volume, oi, bid_price, bid_qty, ask_price, ask_qty,
                 last_trade_time=None):
    """Shape matches Kite's real quote() depth payload: list of dicts with
    price/quantity/orders per level, identical to what crudeoil_ws.py's
    futures-depth `_normalise_depth()` already parses for the WS pipeline."""
    return {
        "last_price": last_price,
        "volume": volume,
        "oi": oi,
        "last_trade_time": last_trade_time,
        "depth": {
            "buy": [
                {"price": bid_price, "quantity": bid_qty, "orders": 3},
                {"price": bid_price - 0.1, "quantity": 40, "orders": 2},
            ],
            "sell": [
                {"price": ask_price, "quantity": ask_qty, "orders": 5},
                {"price": ask_price + 0.1, "quantity": 60, "orders": 1},
            ],
        },
    }


class OptionChainL1QuantityTests(unittest.TestCase):
    def setUp(self):
        self.collector = MCXCollector(symbols=["CRUDEOIL"])
        self.spot = 6500.0
        self.strike = 6500
        self.expiry = date(2026, 10, 20)
        self.tradingsymbol = "CRUDEOIL26OCT6500CE"

        self.quote = _make_quote(
            last_price=120.5, volume=500, oi=1200,
            bid_price=120.0, bid_qty=25,
            ask_price=121.0, ask_qty=17,
            last_trade_time=datetime(2026, 9, 20, 21, 0, 0),
        )

        # Wire up the collector's abstract-method dependencies with fixed
        # single-contract fixtures so collect() runs end-to-end without
        # touching Kite or Postgres.
        patch.object(self.collector, "load_instruments",
                     return_value=pd.DataFrame()).start()
        patch.object(self.collector, "get_spot", return_value=self.spot).start()
        patch.object(self.collector, "get_expiries",
                     return_value=[self.expiry]).start()
        patch.object(self.collector, "get_option_tokens", return_value={
            (self.strike, "CE"): self.tradingsymbol,
        }).start()
        patch.object(self.collector, "atm_strike", return_value=self.strike).start()
        self.addCleanup(patch.stopall)

        self.captured_rows = []
        patch(
            "options.exchange.base.ExchangeCollector._insert_option_rows",
            side_effect=lambda rows: self.captured_rows.extend(rows),
        ).start()
        patch(
            "options.exchange.base.ExchangeCollector._insert_snapshot",
        ).start()
        patch(
            "options.exchange.base.ExchangeCollector._fill_oi_changes",
        ).start()

        self.kite = type("FakeKite", (), {})()
        self.kite.quote = lambda keys: {f"MCX:{self.tradingsymbol}": self.quote}

    def test_l1_bid_ask_quantity_populates_from_depth_payload(self):
        inserted = self.collector.collect(self.kite)

        self.assertEqual(inserted, 1)
        self.assertEqual(len(self.captured_rows), 1)
        row = self.captured_rows[0]

        # (a) new fields populate correctly, L1 only, matching depth.buy[0]/
        # depth.sell[0].quantity exactly (not the L2 level, not a sum).
        self.assertEqual(row["bid_quantities"], [25])
        self.assertEqual(row["ask_quantities"], [17])

    def test_price_oi_iv_greeks_unchanged_by_the_quantity_wiring(self):
        inserted = self.collector.collect(self.kite)
        self.assertEqual(inserted, 1)
        row = self.captured_rows[0]

        # (b) existing fields are byte-for-byte what they were before this
        # change: raw scalars straight from the quote payload...
        self.assertEqual(row["ltp"], 120.5)
        self.assertEqual(row["bid"], 120.0)
        self.assertEqual(row["ask"], 121.0)
        self.assertEqual(row["oi"], 1200)
        self.assertEqual(row["volume"], 500)
        self.assertEqual(row["underlying_ltp"], self.spot)
        self.assertEqual(row["strike"], self.strike)
        self.assertEqual(row["option_type"], "CE")
        self.assertEqual(row["expiry"], self.expiry)

        # ...and IV/Greeks computed by the exact same, untouched call as
        # before — recomputed here independently via the real functions on
        # identical inputs, proving the extraction edit didn't perturb them.
        T = self.collector._days_to_expiry(self.expiry)
        expected_iv = implied_volatility(
            120.5, self.spot, self.strike, T, RISK_FREE_RATE, "CE"
        )
        sigma = (expected_iv / 100.0) if expected_iv else 0.20
        expected_greeks = calculate_greeks(
            self.spot, self.strike, T, RISK_FREE_RATE, sigma, "CE"
        )

        self.assertEqual(row["iv"], expected_iv)
        self.assertEqual(row["delta"], expected_greeks["delta"])
        self.assertEqual(row["gamma"], expected_greeks["gamma"])
        self.assertEqual(row["theta"], expected_greeks["theta"])
        self.assertEqual(row["vega"], expected_greeks["vega"])
        self.assertEqual(row["iv_is_fallback"], expected_iv is None)

    def test_missing_depth_leaves_quantity_fields_none_not_erroring(self):
        """A contract with no depth (e.g. illiquid/no book) must not crash
        and must leave the new fields None rather than fabricating a value —
        same fallback shape as the pre-existing bid/ask None handling."""
        quote_no_depth = dict(self.quote)
        quote_no_depth["depth"] = {}
        self.kite.quote = lambda keys: {f"MCX:{self.tradingsymbol}": quote_no_depth}

        inserted = self.collector.collect(self.kite)
        self.assertEqual(inserted, 1)
        row = self.captured_rows[0]

        self.assertIsNone(row["bid"])
        self.assertIsNone(row["ask"])
        self.assertIsNone(row["bid_quantities"])
        self.assertIsNone(row["ask_quantities"])


if __name__ == "__main__":
    unittest.main()
