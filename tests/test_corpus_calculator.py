"""
Tests for utils/corpus_calculator.py — pure math functions with no external deps.
"""

import pytest
from utils.corpus_calculator import (
    calc_future_value,
    calc_sip_future_value,
    calc_required_sip,
    calc_cagr,
    project_corpus,
)


class TestCalcFutureValue:
    def test_basic_compounding(self):
        # 1 lakh at 10% for 10 years
        fv = calc_future_value(1_00_000, 0.10, 10)
        assert abs(fv - 2_59_374) < 1000   # ~2.59 lakh

    def test_zero_rate(self):
        fv = calc_future_value(5_00_000, 0.0, 5)
        assert abs(fv - 5_00_000) < 1

    def test_zero_pv(self):
        assert calc_future_value(0, 0.15, 15) == 0

    def test_one_year(self):
        fv = calc_future_value(1_00_000, 0.15, 1)
        assert abs(fv - 1_15_000) < 10


class TestCalcSipFutureValue:
    def test_basic_sip(self):
        # ₹10,000/mo for 15 years at 15% should grow significantly
        fv = calc_sip_future_value(10_000, 0.15, 15)
        assert fv > 50_00_000   # must be more than 50 lakh

    def test_zero_sip(self):
        assert calc_sip_future_value(0, 0.15, 15) == 0

    def test_one_month_sip(self):
        # One month SIP: just the principal with one month of growth
        fv = calc_sip_future_value(10_000, 0.12, 1/12)
        assert fv > 10_000


class TestCalcRequiredSip:
    def test_required_sip_positive(self):
        # To accumulate 6 crore in 15 years at 15%, need a positive SIP
        sip = calc_required_sip(6_00_00_000, 0.15, 15)
        assert sip > 0

    def test_already_at_target(self):
        # If current portfolio already equals target, required SIP should be near 0
        sip = calc_required_sip(0, 0.15, 15, current_corpus=6_00_00_000)
        assert sip <= 0

    def test_higher_rate_needs_less_sip(self):
        sip_15 = calc_required_sip(6_00_00_000, 0.15, 15)
        sip_18 = calc_required_sip(6_00_00_000, 0.18, 15)
        assert sip_18 < sip_15


class TestCalcCagr:
    def test_doubling(self):
        # Value doubles in 1 year = 100% CAGR
        cagr = calc_cagr(1_00_000, 2_00_000, 1)
        assert abs(cagr - 100.0) < 0.01

    def test_flat(self):
        cagr = calc_cagr(1_00_000, 1_00_000, 5)
        assert abs(cagr) < 0.01

    def test_negative(self):
        cagr = calc_cagr(1_00_000, 50_000, 1)
        assert cagr < 0


class TestProjectCorpus:
    def test_returns_expected_keys(self):
        result = project_corpus(
            current_portfolio_value = 10_00_000,
            monthly_sip             = 10_000,
            annual_rate             = 0.15,
            years                   = 15,
        )
        required_keys = {
            "current_portfolio", "fv_portfolio", "monthly_sip",
            "fv_sip", "total_projected", "target_corpus",
            "gap", "on_track", "required_sip", "scenarios",
        }
        assert required_keys.issubset(result.keys())

    def test_on_track_flag_true(self):
        # With a large enough portfolio, should be on track
        result = project_corpus(
            current_portfolio_value = 5_00_00_000,   # 5 crore
            monthly_sip             = 1_00_000,
            annual_rate             = 0.15,
            years                   = 15,
        )
        assert result["on_track"] is True

    def test_on_track_flag_false(self):
        # With tiny corpus and no SIP, should not be on track
        result = project_corpus(
            current_portfolio_value = 10_000,
            monthly_sip             = 0,
            annual_rate             = 0.15,
            years                   = 15,
        )
        assert result["on_track"] is False
        assert result["required_sip"] > 0

    def test_scenarios_has_three_entries(self):
        result = project_corpus(
            current_portfolio_value = 10_00_000,
            monthly_sip             = 10_000,
            annual_rate             = 0.15,
            years                   = 15,
        )
        assert len(result["scenarios"]) == 3
