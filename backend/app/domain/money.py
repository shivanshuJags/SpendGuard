"""Money value object.

Never use float for money: 0.1 + 0.2 == 0.30000000000000004.
Decimal is exact, and carrying the currency makes USD + EUR an error instead of
a silent bug.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# Digits after the decimal point, per ISO 4217. PayPal rejects e.g. "10.50 JPY".
_MINOR_UNITS = {"JPY": 0, "HUF": 0, "TWD": 0}


class CurrencyMismatch(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        currency = self.currency.upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError(f"not an ISO currency code: {self.currency!r}")
        try:
            amount = Decimal(self.amount)
        except InvalidOperation as error:
            raise ValueError(f"not a number: {self.amount!r}") from error
        if not amount.is_finite():
            raise ValueError(f"not a finite amount: {self.amount!r}")
        exponent = Decimal(1).scaleb(-_MINOR_UNITS.get(currency, 2))
        # frozen dataclass: bypass immutability once, during construction.
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "amount", amount.quantize(exponent, ROUND_HALF_UP))

    @classmethod
    def of(cls, amount: str | int | Decimal, currency: str) -> "Money":
        # str, not float: Money.of("19.99", "USD"). A float is already inexact.
        if isinstance(amount, float):
            raise TypeError("use a string or Decimal for money, not float")
        return cls(Decimal(amount), currency)

    @classmethod
    def zero(cls, currency: str) -> "Money":
        return cls(Decimal(0), currency)

    def _check(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise CurrencyMismatch(f"{self.currency} vs {other.currency}")

    def __add__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.amount - other.amount, self.currency)

    def __lt__(self, other: "Money") -> bool:
        self._check(other)
        return self.amount < other.amount

    def __le__(self, other: "Money") -> bool:
        self._check(other)
        return self.amount <= other.amount

    def __gt__(self, other: "Money") -> bool:
        self._check(other)
        return self.amount > other.amount

    def __ge__(self, other: "Money") -> bool:
        self._check(other)
        return self.amount >= other.amount

    @property
    def is_positive(self) -> bool:
        return self.amount > 0

    def to_paypal(self) -> dict[str, str]:
        """PayPal's amount shape: {"currency_code": "USD", "value": "19.99"}."""
        return {"currency_code": self.currency, "value": str(self.amount)}

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"
