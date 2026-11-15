"""Edge cases: empty and single-entry inputs, exact boundaries, ties and extreme values."""

from datetime import date

import pytest

from eod_close.domain import (
    BankEntry,
    BankStatement,
    EntryKind,
    ExceptionReason,
    Ledger,
    LedgerEntry,
    MatchRule,
    Money,
)
from eod_close.engine import EngineConfig, ReconciliationEngine
from eod_close.engine.strategies import SubsetSearchLimitError, bounded_subset_sum

TUESDAY = date(2026, 11, 17)
MONDAY = date(2026, 11, 16)
FRIDAY = date(2026, 11, 13)


def bank(entry_id: str, reference: str, amount: str, **extra: object) -> BankEntry:
    fields: dict[str, object] = {"value_date": TUESDAY, "counterparty": "DIRECT", **extra}
    return BankEntry(entry_id=entry_id, reference=reference, amount=Money.of(amount), **fields)  # type: ignore[arg-type]


def ledger(entry_id: str, reference: str, amount: str, **extra: object) -> LedgerEntry:
    fields: dict[str, object] = {"value_date": TUESDAY, "processor_code": "DIRECT", **extra}
    return LedgerEntry(entry_id=entry_id, reference=reference, amount=Money.of(amount), **fields)  # type: ignore[arg-type]


def reconcile(
    banks: list[BankEntry], ledgers: list[LedgerEntry], config: EngineConfig | None = None
) -> tuple[dict[MatchRule, int], dict[ExceptionReason, int]]:
    statement = BankStatement(banks, bank_name="b", account_name="a", statement_date=TUESDAY)
    report = ReconciliationEngine(config=config).reconcile(
        statement, Ledger(ledgers, entity_name="Test Ltd")
    )
    assert report.is_balanced
    rules: dict[MatchRule, int] = {}
    for m in report.matches:
        rules[m.rule] = rules.get(m.rule, 0) + 1
    return rules, report.exceptions_by_reason()


def test_empty_files_reconcile_to_an_empty_balanced_report() -> None:
    assert reconcile([], []) == ({}, {})


def test_single_bank_entry_with_empty_ledger() -> None:
    assert reconcile([bank("B1", "INV-1", "10.00")], []) == (
        {},
        {ExceptionReason.MISSING_FROM_LEDGER: 1},
    )


def test_single_ledger_entry_with_empty_bank() -> None:
    assert reconcile([], [ledger("L1", "INV-1", "10.00")]) == (
        {},
        {ExceptionReason.MISSING_FROM_BANK: 1},
    )


def test_single_matching_pair() -> None:
    assert reconcile([bank("B1", "INV-1", "10.00")], [ledger("L1", "INV-1", "10.00")]) == (
        {MatchRule.EXACT: 1},
        {},
    )


@pytest.mark.parametrize(
    ("booked", "expected"),
    [
        (TUESDAY, {MatchRule.EXACT: 1}),  # T+0 is an exact match, not a delay
        (date(2026, 11, 12), {MatchRule.DATE_WINDOW: 1}),  # Thursday: T+3, the limit
        (date(2026, 11, 11), {}),  # Wednesday: T+4, just outside
        (date(2026, 11, 18), {}),  # booked after the money arrived
    ],
)
def test_date_window_boundaries(booked: date, expected: dict[MatchRule, int]) -> None:
    rules, _ = reconcile(
        [bank("B1", "ORD-1", "50.00", counterparty="OTTERLINE")],
        [ledger("L1", "ORD-1", "50.00", value_date=booked, processor_code="OTTERLINE")],
    )
    assert rules == expected


def test_weekend_does_not_count_towards_the_date_window() -> None:
    """Friday to Tuesday is four calendar days but only T+2."""
    rules, _ = reconcile(
        [bank("B1", "ORD-1", "50.00")],
        [ledger("L1", "ORD-1", "50.00", value_date=FRIDAY, processor_code="OTTERLINE")],
    )
    assert rules == {MatchRule.DATE_WINDOW: 1}


def test_a_zero_day_window_still_allows_same_day_matches() -> None:
    rules, _ = reconcile(
        [bank("B1", "ORD-1", "50.00"), bank("B2", "ORD-2", "50.00")],
        [ledger("L1", "ORD-1", "50.00"), ledger("L2", "ORD-2", "50.00", value_date=MONDAY)],
        EngineConfig(date_window_days=0),
    )
    assert rules == {MatchRule.EXACT: 1}


def test_fx_tolerance_of_zero_disables_the_strategy_for_any_difference() -> None:
    eur = Money.of("120.00", "EUR")
    rules, _ = reconcile(
        [bank("B1", "ORD-1", "100.01")],
        [ledger("L1", "ORD-1", "100.00", processor_code="FERNHOLLOW", original_amount=eur)],
        EngineConfig(fx_tolerance=Money.zero()),
    )
    assert rules == {}


def test_tie_between_identical_candidates_goes_to_the_lowest_ids() -> None:
    statement = BankStatement(
        [bank("B2", "INV-1", "10.00"), bank("B1", "INV-1", "10.00")],
        bank_name="b",
        account_name="a",
        statement_date=TUESDAY,
    )
    report = ReconciliationEngine().reconcile(
        statement,
        Ledger([ledger("L2", "INV-1", "10.00"), ledger("L1", "INV-1", "10.00")], entity_name="T"),
    )
    pairs = {(m.bank_entries[0].entry_id, m.ledger_entries[0].entry_id) for m in report.matches}
    assert pairs == {("B1", "L1"), ("B2", "L2")}


def test_very_large_amounts_match_exactly() -> None:
    huge = "123456789012345678901234567.89"
    assert reconcile([bank("B1", "INV-1", huge)], [ledger("L1", "INV-1", huge)]) == (
        {MatchRule.EXACT: 1},
        {},
    )


def test_very_large_payout_does_not_blow_up_the_batch_search() -> None:
    """A £10m payout against 30 sales must give up within its state limit, not hang."""
    sales = [
        ledger(f"L{i:02d}", f"ORD-{i}", f"{1000 + i * 37}.00", value_date=MONDAY,
               processor_code="QUILLFEATHER")
        for i in range(30)
    ]  # fmt: skip
    rules, exceptions = reconcile(
        [bank("B1", "QFW-PAYOUT", "10000000.00", counterparty="QUILLFEATHER")],
        sales,
        EngineConfig(max_subset_states=1_000),
    )
    assert rules == {}
    assert exceptions[ExceptionReason.MISSING_FROM_BANK] == 30


def test_batch_pool_above_the_candidate_limit_is_skipped() -> None:
    sales = [
        ledger(f"L{i}", f"ORD-{i}", "10.00", value_date=MONDAY, processor_code="QUILLFEATHER")
        for i in range(4)
    ]
    rules, _ = reconcile(
        [bank("B1", "QFW-PAYOUT", "19.62", counterparty="QUILLFEATHER")],
        sales,
        EngineConfig(max_batch_candidates=3),
    )
    assert rules == {}


def test_subset_sum_with_no_values_or_non_positive_target() -> None:
    assert bounded_subset_sum([], 100, max_states=10) is None
    assert bounded_subset_sum([100], 0, max_states=10) is None
    assert bounded_subset_sum([100], -5, max_states=10) is None


def test_subset_sum_state_limit_is_exclusive() -> None:
    """Holding exactly max_states sums is allowed; one more raises."""
    assert bounded_subset_sum([1, 2], 3, max_states=3) == (0, 1)
    with pytest.raises(SubsetSearchLimitError):
        bounded_subset_sum([1, 2, 4], 7, max_states=4)


def test_chargeback_with_the_wrong_fee_is_not_linked() -> None:
    rules, _ = reconcile(
        [bank("B1", "CB-ORD-1", "-50.00", counterparty="BRAMBLE")],
        [
            ledger(
                "L1", "CHB-1", "-40.00", processor_code="BRAMBLE",
                kind=EntryKind.CHARGEBACK, original_reference="ORD-1",
            )
        ],
    )  # fmt: skip
    assert rules == {}


def test_partial_refund_amount_mismatch_is_not_linked() -> None:
    rules, _ = reconcile(
        [bank("B1", "ORD-1", "-20.00", counterparty="OTTERLINE")],
        [
            ledger(
                "L1", "RFD-1", "-25.00", processor_code="OTTERLINE",
                kind=EntryKind.REFUND, original_reference="ORD-1",
            )
        ],
    )  # fmt: skip
    assert rules == {}


def test_transposition_in_a_negative_amount_is_detected() -> None:
    _, exceptions = reconcile([bank("B1", "INV-1", "-12.34")], [ledger("L1", "INV-1", "-21.34",
        kind=EntryKind.REFUND, original_reference="INV-0")])  # fmt: skip
    assert exceptions == {ExceptionReason.LIKELY_TRANSPOSITION: 2}


def test_difference_not_divisible_by_nine_is_not_a_transposition() -> None:
    _, exceptions = reconcile([bank("B1", "INV-1", "12.34")], [ledger("L1", "INV-1", "12.35")])
    assert ExceptionReason.LIKELY_TRANSPOSITION not in exceptions


def test_every_copy_of_a_triplicate_after_the_first_is_flagged() -> None:
    _, exceptions = reconcile(
        [bank("B1", "INV-1", "10.00")],
        [ledger(f"L{i}", "INV-1", "10.00") for i in range(3)],
    )
    assert exceptions == {ExceptionReason.DUPLICATE: 2}


def test_engine_config_rejects_invalid_limits() -> None:
    with pytest.raises(ValueError, match="negative"):
        EngineConfig(date_window_days=-1)
    with pytest.raises(ValueError, match="positive"):
        EngineConfig(max_subset_states=0)


def test_blank_reference_settlement_only_matches_the_same_processor() -> None:
    """BUG-001: a reference-less Otterline credit must not claim another processor's sale."""
    rules, exceptions = reconcile(
        [bank("B1", "", "24.99", counterparty="OTTERLINE")],
        [ledger("L1", "ORD-1", "24.99", value_date=MONDAY, processor_code="QUILLFEATHER")],
    )
    assert rules == {}
    assert exceptions == {
        ExceptionReason.MISSING_FROM_LEDGER: 1,
        ExceptionReason.MISSING_FROM_BANK: 1,
    }


def test_batch_choice_does_not_depend_on_ledger_order() -> None:
    """BUG-003: a payout of £29.43 fits {£10, £20} or {£30}; row order must not decide."""
    sales = [
        ledger(i, f"ORD-{i}", amount, value_date=MONDAY, processor_code="QUILLFEATHER")
        for i, amount in (("L1", "10.00"), ("L2", "20.00"), ("L3", "30.00"))
    ]
    payout = [bank("B1", "QFW-PAYOUT", "29.43", counterparty="QUILLFEATHER")]

    def chosen(order: list[LedgerEntry]) -> list[list[str]]:
        statement = BankStatement(payout, bank_name="b", account_name="a", statement_date=TUESDAY)
        report = ReconciliationEngine().reconcile(statement, Ledger(order, entity_name="T"))
        return [[e.entry_id for e in m.ledger_entries] for m in report.matches]

    assert chosen(sales) == chosen(sales[::-1])
