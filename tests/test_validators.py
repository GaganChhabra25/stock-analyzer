"""
Tests for utils/validators.py — pure validation functions.
"""

import pytest
from core.exceptions import InvalidHoldingsError
from utils.validators import (
    validate_symbol,
    validate_positive_float,
    validate_scheme_code,
)


class TestValidateSymbol:
    def test_valid_symbols(self):
        assert validate_symbol("INFY")       == "INFY"
        assert validate_symbol("TCS")        == "TCS"
        assert validate_symbol("M&M")        == "M&M"
        assert validate_symbol("NIFTYBEES")  == "NIFTYBEES"
        assert validate_symbol(" infy ")     == "INFY"   # strips & uppercases

    def test_empty_raises(self):
        with pytest.raises(InvalidHoldingsError):
            validate_symbol("")

    def test_whitespace_raises(self):
        with pytest.raises(InvalidHoldingsError):
            validate_symbol("   ")

    def test_none_raises(self):
        with pytest.raises(InvalidHoldingsError):
            validate_symbol(None)

    def test_too_long_raises(self):
        with pytest.raises(InvalidHoldingsError):
            validate_symbol("TOOLONGSYMBOL")   # 13 chars

    def test_special_chars_raise(self):
        with pytest.raises(InvalidHoldingsError):
            validate_symbol("INFY@")
        with pytest.raises(InvalidHoldingsError):
            validate_symbol("IN FY")


class TestValidatePositiveFloat:
    def test_valid_values(self):
        assert validate_positive_float(100.0,  "price") == 100.0
        assert validate_positive_float(0,      "qty")   == 0.0
        assert validate_positive_float("42.5", "nav")   == 42.5

    def test_negative_raises(self):
        with pytest.raises(InvalidHoldingsError, match="must be >= 0"):
            validate_positive_float(-1, "price")

    def test_non_numeric_raises(self):
        with pytest.raises(InvalidHoldingsError, match="must be numeric"):
            validate_positive_float("abc", "price")

    def test_none_raises(self):
        with pytest.raises(InvalidHoldingsError):
            validate_positive_float(None, "price")


class TestValidateSchemeCode:
    def test_valid_codes(self):
        assert validate_scheme_code(119551) == 119551
        assert validate_scheme_code("119551") == 119551

    def test_zero_raises(self):
        with pytest.raises(InvalidHoldingsError, match="positive integer"):
            validate_scheme_code(0)

    def test_negative_raises(self):
        with pytest.raises(InvalidHoldingsError):
            validate_scheme_code(-100)

    def test_non_numeric_raises(self):
        with pytest.raises(InvalidHoldingsError):
            validate_scheme_code("abc")
