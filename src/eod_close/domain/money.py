"""Exact monetary amounts.

``Money`` is the single type used for amounts of currency throughout the
project. Entries, processors, the case generator and the matching strategies
all pass ``Money`` values rather than bare numbers, so the rules about
precision, rounding and currency live in one place.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import (
    ROUND_HALF_UP,
    Context,
    Decimal,
    Inexact,
    InvalidOperation,
    Overflow,
)
from typing import Final

# Decimal places per ISO 4217 currency. Only the currencies the fictional
# processors use are listed. Unknown codes are rejected rather than guessed,
# because assuming two decimal places is how yen amounts end up 100 times wrong.
_MINOR_UNITS: Final[dict[str, int]] = {"GBP": 2, "EUR": 2, "USD": 2, "JPY": 0}
_SYMBOLS: Final[dict[str, str]] = {"GBP": "£", "EUR": "€", "USD": "$", "JPY": "¥"}

# The default decimal context keeps only 28 significant digits and rounds
# silently beyond that. Addition and subtraction use a context that traps
# Inexact instead, so an overflow of precision raises rather than losing pence.
_EXACT: Final = Context(prec=40, traps=[InvalidOperation, Overflow, Inexact])

# Rounding is half-up: it is how the fictional processors publish their fee
# schedules and how UK customer-facing amounts are conventionally rounded.
# Banker's rounding would be equally valid but must then be used consistently.
_ROUNDING: Final = Context(prec=40, rounding=ROUND_HALF_UP, traps=[InvalidOperation, Overflow])


class CurrencyMismatchError(ValueError):
    """Raised when an operation combines amounts in different currencies."""


def _quantum(currency: str) -> Decimal:
    return Decimal(1).scaleb(-_MINOR_UNITS[currency])


def _require_decimal_factor(value: Decimal | int, name: str) -> Decimal:
    # bool is a subclass of int, and True * £5 is never what the caller meant.
    if isinstance(value, bool) or not isinstance(value, Decimal | int):
        raise TypeError(f"{name} must be a Decimal or int, not {type(value).__name__}")
    return Decimal(value)


@dataclass(frozen=True, slots=True)
class Money:
    """An exact amount in a single currency.

    The amount is a ``Decimal`` held at exactly the currency's minor-unit
    precision (pence for GBP). Floats are refused: binary floating point cannot
    represent most decimal fractions, so ``0.1 + 0.2 != 0.3``, and in
    reconciliation a one-penny drift is a real break that someone has to
    investigate. Values with more precision than the currency allows are also
    refused rather than rounded silently; rounding happens only in
    ``multiply`` and ``convert``, where it is explicit.

    Attributes:
        amount: The amount, quantised to the currency's minor units.
        currency: Upper-case ISO 4217 code, for example ``"GBP"``.

    Raises:
        TypeError: If ``amount`` is not a ``Decimal``.
        ValueError: If the currency is unsupported, or the amount is not
            finite, too large to hold exactly, or has more decimal places than
            the currency allows.
    """

    amount: Decimal
    currency: str = "GBP"

    def __post_init__(self) -> None:
        # Checked at runtime as well as by the type checker, because CSV
        # parsing and untyped callers are exactly where a float would sneak in.
        if not isinstance(self.amount, Decimal):
            raise TypeError(f"Money amount must be a Decimal, not {type(self.amount).__name__}")
        currency = self.currency.upper()
        if currency not in _MINOR_UNITS:
            raise ValueError(f"Unsupported currency: {self.currency!r}")
        if not self.amount.is_finite():
            raise ValueError(f"Money amount must be finite, not {self.amount}")
        try:
            quantised = self.amount.quantize(_quantum(currency), context=_ROUNDING)
        except InvalidOperation as exc:
            raise ValueError(f"{self.amount} is too large to hold exactly") from exc
        if quantised != self.amount:
            raise ValueError(f"{self.amount} has more decimal places than {currency} allows")
        if quantised.is_zero():
            # Negating zero yields Decimal("-0.00"), which would print and
            # serialise as "-0.00" even though it equals zero.
            quantised = quantised.copy_abs()
        object.__setattr__(self, "amount", quantised)
        object.__setattr__(self, "currency", currency)

    @classmethod
    def of(cls, value: Decimal | int | str, currency: str = "GBP") -> Money:
        """Build a ``Money`` from a Decimal, an integer or a decimal string.

        Args:
            value: The amount in major units, for example ``"12.34"``.
            currency: ISO 4217 code. Defaults to ``"GBP"``.

        Returns:
            The amount as ``Money``.

        Raises:
            TypeError: If ``value`` is a float, a bool or another type.
            ValueError: If ``value`` is not a valid decimal number, or fails
                the checks described on the class.
        """
        if isinstance(value, bool) or not isinstance(value, Decimal | int | str):
            raise TypeError(f"Money value must be Decimal, int or str, not {type(value).__name__}")
        try:
            amount = value if isinstance(value, Decimal) else Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"Not a valid amount: {value!r}") from exc
        return cls(amount, currency)

    @classmethod
    def from_minor_units(cls, units: int, currency: str = "GBP") -> Money:
        """Build a ``Money`` from a whole number of minor units, such as pence.

        Args:
            units: The amount in minor units; ``1234`` is £12.34.
            currency: ISO 4217 code. Defaults to ``"GBP"``.

        Returns:
            The amount as ``Money``.

        Raises:
            TypeError: If ``units`` is not an int.
            ValueError: If the currency is unsupported.
        """
        if isinstance(units, bool) or not isinstance(units, int):
            raise TypeError(f"Minor units must be an int, not {type(units).__name__}")
        code = currency.upper()
        if code not in _MINOR_UNITS:
            raise ValueError(f"Unsupported currency: {currency!r}")
        return cls(Decimal(units).scaleb(-_MINOR_UNITS[code], context=_EXACT), code)

    @classmethod
    def zero(cls, currency: str = "GBP") -> Money:
        """Return zero in the given currency."""
        return cls(Decimal(0), currency)

    @classmethod
    def total(cls, amounts: Iterable[Money], currency: str = "GBP") -> Money:
        """Sum amounts that share a currency.

        Args:
            amounts: The amounts to add. May be empty.
            currency: The currency of the result, and of every amount.

        Returns:
            The exact total, or zero in ``currency`` when ``amounts`` is empty.

        Raises:
            CurrencyMismatchError: If any amount is in another currency.
        """
        result = cls.zero(currency)
        for amount in amounts:
            result = result + amount
        return result

    @property
    def minor_units(self) -> int:
        """The amount as a whole number of minor units, such as pence."""
        return int(self.amount.scaleb(_MINOR_UNITS[self.currency], context=_EXACT))

    def is_zero(self) -> bool:
        """Return True if the amount is exactly zero."""
        return self.amount.is_zero()

    def is_positive(self) -> bool:
        """Return True if the amount is greater than zero."""
        return self.amount > 0

    def is_negative(self) -> bool:
        """Return True if the amount is less than zero."""
        return self.amount < 0

    def multiply(self, factor: Decimal | int) -> Money:
        """Multiply by a factor and round half-up to the currency's minor units.

        Used for percentage fees, where the exact product (1.4% of £12.34 is
        £0.17276) has to be rounded to a payable amount.

        Args:
            factor: The multiplier, for example ``Decimal("0.014")``.

        Returns:
            The rounded product in the same currency.

        Raises:
            TypeError: If ``factor`` is a float or bool.
        """
        multiplier = _require_decimal_factor(factor, "factor")
        product = _EXACT.multiply(self.amount, multiplier)
        return Money(product.quantize(_quantum(self.currency), context=_ROUNDING), self.currency)

    def convert(self, rate: Decimal, to_currency: str) -> Money:
        """Convert to another currency at the given rate, rounding half-up.

        Args:
            rate: Units of ``to_currency`` per one unit of this currency.
            to_currency: ISO 4217 code of the result.

        Returns:
            The converted amount.

        Raises:
            TypeError: If ``rate`` is a float.
            ValueError: If ``rate`` is not positive, or the currency is
                unsupported.
        """
        multiplier = _require_decimal_factor(rate, "rate")
        if multiplier <= 0:
            raise ValueError(f"Exchange rate must be positive, not {rate}")
        code = to_currency.upper()
        if code not in _MINOR_UNITS:
            raise ValueError(f"Unsupported currency: {to_currency!r}")
        product = _EXACT.multiply(self.amount, multiplier)
        return Money(product.quantize(_quantum(code), context=_ROUNDING), code)

    def format(self) -> str:
        """Format for display, for example ``£1,234.56`` or ``-£0.20``."""
        places = _MINOR_UNITS[self.currency]
        sign = "-" if self.is_negative() else ""
        return f"{sign}{_SYMBOLS[self.currency]}{self.amount.copy_abs():,.{places}f}"

    def __str__(self) -> str:
        return self.format()

    def _check_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(
                f"Cannot combine {self.currency} and {other.currency} amounts"
            )

    def __add__(self, other: object) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._check_currency(other)
        return Money(_EXACT.add(self.amount, other.amount), self.currency)

    def __sub__(self, other: object) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._check_currency(other)
        return Money(_EXACT.subtract(self.amount, other.amount), self.currency)

    def __neg__(self) -> Money:
        return Money(self.amount.copy_negate(), self.currency)

    def __abs__(self) -> Money:
        return Money(self.amount.copy_abs(), self.currency)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._check_currency(other)
        return self.amount < other.amount

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._check_currency(other)
        return self.amount <= other.amount

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._check_currency(other)
        return self.amount > other.amount

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._check_currency(other)
        return self.amount >= other.amount
