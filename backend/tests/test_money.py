from decimal import Decimal

import pytest

from app.domain.money import CurrencyMismatch, Money


def test_decimal_arithmetic_is_exact():
    assert Money.of("0.10", "USD") + Money.of("0.20", "USD") == Money.of("0.30", "USD")


def test_float_is_rejected():
    with pytest.raises(TypeError):
        Money.of(0.1, "USD")  # type: ignore[arg-type]


def test_quantizes_to_currency_minor_units():
    assert Money.of("10.005", "USD").amount == Decimal("10.01")
    assert Money.of("1000.4", "JPY").amount == Decimal("1000")


def test_currency_is_normalized_and_validated():
    assert Money.of("1", "usd").currency == "USD"
    with pytest.raises(ValueError):
        Money.of("1", "DOLLARS")


def test_mixing_currencies_is_an_error():
    with pytest.raises(CurrencyMismatch):
        Money.of("1", "USD") + Money.of("1", "EUR")


def test_paypal_shape():
    assert Money.of("19.9", "USD").to_paypal() == {"currency_code": "USD", "value": "19.90"}
