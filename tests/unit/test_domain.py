"""Domain model tests: the rules entries, statements, processors and reports enforce."""

from datetime import date

import pytest

from eod_close.domain import (
    BankEntry,
    BankStatement,
    DuplicateEntryIdError,
    EntryKind,
    ExceptionItem,
    ExceptionReason,
    Ledger,
    LedgerEntry,
    Match,
    MatchRule,
    Money,
    ReconciliationReport,
    Transaction,
)
from eod_close.domain.business_days import add_business_days, business_days_between
from eod_close.domain.processor import BRAMBLE, OTTERLINE

FRIDAY = date(2026, 11, 13)
MONDAY = date(2026, 11, 16)


def bank(entry_id: str = "B1", amount: str = "10.00") -> BankEntry:
    return BankEntry(
        entry_id=entry_id, reference="INV-1", amount=Money.of(amount), value_date=FRIDAY
    )


def ledger(entry_id: str = "L1", amount: str = "10.00") -> LedgerEntry:
    return LedgerEntry(
        entry_id=entry_id,
        reference="INV-1",
        amount=Money.of(amount),
        value_date=FRIDAY,
        processor_code="DIRECT",
    )


def test_transaction_base_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        Transaction(  # type: ignore[abstract]
            entry_id="X", reference="R", amount=Money.of(1), value_date=FRIDAY
        )


def test_zero_amount_entries_are_rejected() -> None:
    with pytest.raises(ValueError, match="zero"):
        bank(amount="0")


def test_refund_must_be_negative_and_reference_its_original() -> None:
    with pytest.raises(ValueError, match="negative"):
        LedgerEntry(
            entry_id="L1",
            reference="RFD-1",
            amount=Money.of("5.00"),
            value_date=FRIDAY,
            processor_code="OTTERLINE",
            kind=EntryKind.REFUND,
            original_reference="ORD-1",
        )
    with pytest.raises(ValueError, match="original reference"):
        LedgerEntry(
            entry_id="L1",
            reference="RFD-1",
            amount=Money.of("-5.00"),
            value_date=FRIDAY,
            processor_code="OTTERLINE",
            kind=EntryKind.REFUND,
        )


def test_original_amount_must_be_foreign_currency() -> None:
    with pytest.raises(ValueError, match="foreign"):
        LedgerEntry(
            entry_id="L1",
            reference="ORD-1",
            amount=Money.of("5.00"),
            value_date=FRIDAY,
            processor_code="FERNHOLLOW",
            original_amount=Money.of("5.00"),
        )


def test_collection_rejects_duplicate_ids() -> None:
    with pytest.raises(DuplicateEntryIdError):
        Ledger([ledger("L1"), ledger("L1")], entity_name="Test Ltd")


def test_collection_rejects_entries_in_another_currency() -> None:
    eur = BankEntry(entry_id="B1", reference="R", amount=Money.of("1.00", "EUR"), value_date=FRIDAY)
    with pytest.raises(ValueError, match="EUR entry"):
        BankStatement([eur], bank_name="b", account_name="a", statement_date=FRIDAY)


def test_collection_total_and_lookup() -> None:
    led = Ledger([ledger("L1", "10.00"), ledger("L2", "2.50")], entity_name="Test Ltd")
    assert led.total() == Money.of("12.50")
    assert led.get("L2").amount == Money.of("2.50")
    assert "L1" in led
    assert len(led) == 2


def test_friday_plus_one_business_day_is_monday() -> None:
    assert add_business_days(FRIDAY, 1) == MONDAY
    assert add_business_days(MONDAY, -1) == FRIDAY
    assert business_days_between(FRIDAY, MONDAY) == 1
    assert business_days_between(MONDAY, FRIDAY) == -1


def test_net_settling_processor_deducts_its_fee() -> None:
    """1.4% of £100.00 is £1.40, plus the fixed £0.20."""
    assert BRAMBLE.fee_for(Money.of("100.00")) == Money.of("1.60")
    assert BRAMBLE.settlement_amount(Money.of("100.00")) == Money.of("98.40")
    assert BRAMBLE.fee_rule.describe() == "1.4% + £0.20"


def test_processors_do_not_charge_fees_on_refunds() -> None:
    assert BRAMBLE.fee_for(Money.of("-50.00")) == Money.zero()


def test_gross_settling_processor_pays_the_full_amount() -> None:
    assert OTTERLINE.settlement_amount(Money.of("100.00")) == Money.of("100.00")
    assert OTTERLINE.expected_settlement_date(FRIDAY) == date(2026, 11, 17)


def make_report(matches: list[Match], exceptions: list[ExceptionItem]) -> ReconciliationReport:
    statement = BankStatement(
        [bank("B1"), bank("B2")], bank_name="b", account_name="a", statement_date=FRIDAY
    )
    led = Ledger([ledger("L1")], entity_name="Test Ltd")
    return ReconciliationReport(FRIDAY, statement, led, matches, exceptions)


def test_report_is_balanced_when_every_entry_is_accounted_for() -> None:
    report = make_report(
        [Match((bank("B1"),), (ledger("L1"),), MatchRule.EXACT, 1.0)],
        [ExceptionItem(bank("B2"), ExceptionReason.DUPLICATE, related_entry_ids=("B1",))],
    )
    assert report.is_balanced
    assert report.exceptions_by_reason() == {ExceptionReason.DUPLICATE: 1}


def test_report_is_not_balanced_with_unaccounted_entries() -> None:
    report = make_report([Match((bank("B1"),), (ledger("L1"),), MatchRule.EXACT, 1.0)], [])
    assert not report.is_balanced
    assert report.unaccounted_bank_ids == {"B2"}


def test_report_rejects_an_entry_used_twice() -> None:
    """The core invariant: one payment can never explain two others."""
    with pytest.raises(ValueError, match="more than once"):
        make_report(
            [
                Match((bank("B1"),), (ledger("L1"),), MatchRule.EXACT, 1.0),
                Match((bank("B2"),), (ledger("L1"),), MatchRule.EXACT, 1.0),
            ],
            [],
        )


def test_match_requires_both_sides_and_valid_confidence() -> None:
    with pytest.raises(ValueError, match="each side"):
        Match((), (ledger(),), MatchRule.EXACT, 1.0)
    with pytest.raises(ValueError, match="Confidence"):
        Match((bank(),), (ledger(),), MatchRule.EXACT, 1.5)


def test_match_difference_is_bank_minus_ledger() -> None:
    match = Match((bank(amount="98.40"),), (ledger(amount="100.00"),), MatchRule.FEE_ADJUSTED, 0.9)
    assert match.difference == Money.of("-1.60")
