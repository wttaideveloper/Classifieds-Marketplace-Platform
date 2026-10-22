"""Exact money: rounding rules, currency precision and the gateway's smallest-unit integers."""
from decimal import Decimal

import pytest

from app.utils.money import default_decimals, from_minor, money_str, quantize, to_decimal, to_minor


@pytest.mark.parametrize("code,expected", [("JPY", 0), ("KRW", 0), ("VND", 0), ("USD", 2), ("INR", 2), ("EUR", 2), ("KWD", 3), ("BHD", 3), ("OMR", 3), ("jpy", 0)])
def test_each_currency_has_its_usual_precision(code, expected):
    assert default_decimals(code) == expected


@pytest.mark.parametrize("mode,value,decimals,expected", [
    ("half_up", "2.5", 0, "3"), ("half_up", "2.4", 0, "2"), ("half_up", "1.005", 2, "1.01"), ("half_up", "-2.5", 0, "-3"),
    ("half_even", "2.5", 0, "2"), ("half_even", "3.5", 0, "4"), ("half_even", "1.005", 2, "1.00"),
    ("up", "1.001", 2, "1.01"), ("up", "1.000", 2, "1.00"), ("down", "1.999", 2, "1.99"), ("down", "-1.999", 2, "-1.99"),
    ("half_up", "10.333", 3, "10.333"), ("half_up", "0.0005", 3, "0.001"), ("nonsense", "2.5", 0, "3"),
])
def test_rounding_modes(mode, value, decimals, expected):
    assert str(quantize(Decimal(value), decimals, mode)) == expected


@pytest.mark.parametrize("amount,decimals,minor", [("1180.00", 2, 118000), ("14.16", 2, 1416), ("2124", 0, 2124), ("4.366", 3, 4366), ("0.01", 2, 1), ("0", 2, 0), ("19.999", 2, 2000)])
def test_the_gateway_amount_is_an_exact_integer(amount, decimals, minor):
    assert to_minor(Decimal(amount), decimals) == minor
    assert isinstance(to_minor(Decimal(amount), decimals), int)


def test_minor_units_round_trip():
    for amount, decimals in (("1180.00", 2), ("2124", 0), ("4.366", 3), ("0.07", 2)):
        assert from_minor(to_minor(Decimal(amount), decimals), decimals) == Decimal(amount)
    assert str(from_minor(118000, 2)) == "1180.00" and str(from_minor(5, 0)) == "5" and str(from_minor(4366, 3)) == "4.366"


def test_floating_point_noise_never_reaches_an_amount():
    assert to_decimal(0.1) + to_decimal(0.2) == Decimal("0.3")
    assert to_decimal("1e2") == Decimal("100")
    assert to_decimal("abc", Decimal(0)) == Decimal(0) and to_decimal("NaN", Decimal(0)) == Decimal(0) and to_decimal("Infinity", Decimal(7)) == Decimal(7)
    with pytest.raises(ValueError):
        to_decimal("abc")
    assert sum((to_decimal(0.1) for _ in range(10)), Decimal(0)) == Decimal("1.0")


def test_money_is_shown_with_the_currencys_decimals():
    assert money_str(Decimal("1180"), 2) == "1,180.00" and money_str(Decimal("1234567.891"), 2) == "1,234,567.89" and money_str(Decimal("2124"), 0) == "2,124"
    assert money_str(Decimal("4.3666"), 3) == "4.367"
