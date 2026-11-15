"""Money tests: exact decimal arithmetic, currency rules and the refusal to accept floats."""

from decimal import Decimal

import pytest

from eod_close.domain.money import CurrencyMismatchError, Money


def test_addition_is_exact_where_float_is_not() -> None:
    """0.1 + 0.2 is the classic float failure; Money must get it right."""
    assert Money.of("0.10") + Money.of("0.20") == Money.of("0.30")


def test_float_amounts_are_rejected() -> None:
    with pytest.raises(TypeError):
        Money.of(0.1)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Money(0.1)  # type: ignore[arg-type]


def test_bool_is_not_accepted_as_an_amount() -> None:
    with pytest.raises(TypeError):
        Money.of(True)


def test_excess_precision_is_rejected_not_rounded() -> None:
    with pytest.raises(ValueError, match="decimal places"):
        Money.of("12.345")


def test_whole_numbers_are_normalised_to_two_places() -> None:
    assert str(Money.of(5).amount) == "5.00"


def test_unknown_currency_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported currency"):
        Money.of("1.00", "XYZ")


def test_non_finite_values_are_rejected() -> None:
    with pytest.raises(ValueError, match="finite"):
        Money.of("NaN")


def test_unparseable_text_is_rejected() -> None:
    with pytest.raises(ValueError, match="valid amount"):
        Money.of("twelve")


def test_adding_different_currencies_raises() -> None:
    with pytest.raises(CurrencyMismatchError):
        _ = Money.of("1.00", "GBP") + Money.of("1.00", "EUR")


def test_comparing_different_currencies_raises() -> None:
    with pytest.raises(CurrencyMismatchError):
        _ = Money.of("1.00", "GBP") < Money.of("2.00", "EUR")


def test_equality_across_currencies_is_false_rather_than_an_error() -> None:
    assert Money.of("1.00", "GBP") != Money.of("1.00", "EUR")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1234.56", "£1,234.56"),
        ("-0.20", "-£0.20"),
        ("0", "£0.00"),
        ("1000000", "£1,000,000.00"),
    ],
)
def test_gbp_formatting(value: str, expected: str) -> None:
    assert Money.of(value).format() == expected


def test_negated_zero_formats_without_a_sign() -> None:
    assert str(-Money.zero()) == "£0.00"
    assert str((-Money.zero()).amount) == "0.00"


def test_multiply_rounds_half_up() -> None:
    assert Money.of("0.05").multiply(Decimal("0.5")) == Money.of("0.03")
    assert Money.of("12.34").multiply(Decimal("0.014")) == Money.of("0.17")


def test_multiply_rejects_float_factor() -> None:
    with pytest.raises(TypeError):
        Money.of("1.00").multiply(0.5)  # type: ignore[arg-type]


def test_convert_rounds_to_target_currency() -> None:
    assert Money.of("100.00", "EUR").convert(Decimal("0.8567"), "GBP") == Money.of("85.67")
    assert Money.of("10.00", "EUR").convert(Decimal("162.345"), "JPY") == Money.of("1623", "JPY")


def test_convert_rejects_non_positive_rate() -> None:
    with pytest.raises(ValueError, match="positive"):
        Money.of("1.00").convert(Decimal(0), "EUR")


def test_minor_units_round_trip() -> None:
    assert Money.of("-12.34").minor_units == -1234
    assert Money.from_minor_units(-1234) == Money.of("-12.34")
    assert Money.from_minor_units(500, "JPY") == Money.of("500", "JPY")


def test_total_of_nothing_is_zero() -> None:
    assert Money.total([]) == Money.zero()


def test_very_large_values_stay_exact() -> None:
    big = Money.of("999999999999999999999999999999.99")
    assert big + Money.of("0.01") == Money.of("1000000000000000000000000000000.00")


def test_values_beyond_supported_precision_raise_rather_than_round() -> None:
    with pytest.raises(ValueError, match="too large"):
        Money.of("1e40")
