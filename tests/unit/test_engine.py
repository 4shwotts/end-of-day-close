"""Matching engine tests: each strategy on small hand-built days, then the engine as a whole."""

from collections.abc import Sequence
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
from eod_close.domain.processor import FICTIONAL_PROCESSORS
from eod_close.engine import (
    Candidate,
    CandidateQueue,
    EngineConfig,
    MatchContext,
    MatchStrategy,
    ReconciliationEngine,
)
from eod_close.engine.strategies import (
    BatchMatch,
    CurrencyToleranceMatch,
    DateWindowMatch,
    DuplicateDetection,
    ExactMatch,
    FeeAdjustedMatch,
    ReversalLink,
    SubsetSearchLimitError,
    TranspositionDetection,
    bounded_subset_sum,
)
from eod_close.engine.strategies.reversal import reference_suffixes
from eod_close.generator import CaseGenerator, Difficulty

TUESDAY = date(2026, 11, 17)
MONDAY = date(2026, 11, 16)
FRIDAY = date(2026, 11, 13)
PROCESSORS = {p.code: p for p in FICTIONAL_PROCESSORS}


def bank(
    entry_id: str, reference: str, amount: str, day: date = TUESDAY, counterparty: str = "DIRECT"
) -> BankEntry:
    return BankEntry(
        entry_id=entry_id,
        reference=reference,
        amount=Money.of(amount),
        value_date=day,
        counterparty=counterparty,
    )


def ledger(
    entry_id: str,
    reference: str,
    amount: str,
    day: date = TUESDAY,
    processor: str = "DIRECT",
    **extra: object,
) -> LedgerEntry:
    return LedgerEntry(
        entry_id=entry_id,
        reference=reference,
        amount=Money.of(amount),
        value_date=day,
        processor_code=processor,
        **extra,  # type: ignore[arg-type]
    )


def context(banks: Sequence[BankEntry], ledgers: Sequence[LedgerEntry]) -> MatchContext:
    statement = BankStatement(banks, bank_name="b", account_name="a", statement_date=TUESDAY)
    return MatchContext(
        statement, Ledger(ledgers, entity_name="Test Ltd"), PROCESSORS, EngineConfig()
    )


def pairs(
    strategy: MatchStrategy, ctx: MatchContext
) -> set[tuple[tuple[str, ...], tuple[str, ...]]]:
    return {(c.bank_ids, c.ledger_ids) for c in strategy.propose(ctx)}


def test_exact_match_pairs_same_reference_amount_and_date() -> None:
    ctx = context(
        [bank("B1", "INV-1", "10.00"), bank("B2", "INV-2", "10.00")],
        [ledger("L1", "INV-1", "10.00"), ledger("L2", "INV-2", "10.01")],
    )
    assert pairs(ExactMatch(), ctx) == {(("B1",), ("L1",))}


def test_fee_adjusted_match_uses_the_processor_fee_rule() -> None:
    """Bramblecard charges 1.4% + 20p, so £100.00 gross settles as £98.40."""
    ctx = context(
        [bank("B1", "ORD-1", "98.40", counterparty="BRAMBLE")],
        [ledger("L1", "ORD-1", "100.00", MONDAY, "BRAMBLE")],
    )
    (candidate,) = FeeAdjustedMatch().propose(ctx)
    assert candidate.resolution is MatchRule.FEE_ADJUSTED
    assert "£1.60" in candidate.note


def test_date_window_accepts_t_plus_three_but_not_t_plus_four() -> None:
    ctx = context(
        [bank("B1", "ORD-1", "50.00")],
        [
            ledger("L3", "ORD-1", "50.00", date(2026, 11, 12), "OTTERLINE"),  # Thursday: T+3
            ledger("L4", "ORD-1", "50.00", date(2026, 11, 11), "OTTERLINE"),  # Wednesday: T+4
        ],
    )
    assert pairs(DateWindowMatch(), ctx) == {(("B1",), ("L3",))}


def test_date_window_rejects_a_different_reference() -> None:
    ctx = context([bank("B1", "ORD-1", "50.00")], [ledger("L1", "ORD-2", "50.00", MONDAY)])
    assert pairs(DateWindowMatch(), ctx) == set()


def test_date_window_prefers_the_closer_date() -> None:
    ctx = context(
        [bank("B1", "", "50.00")],
        [ledger("L1", "ORD-1", "50.00", FRIDAY), ledger("L2", "ORD-2", "50.00", MONDAY)],
    )
    best = CandidateQueue(DateWindowMatch().propose(ctx)).pop()
    assert best.ledger_ids == ("L2",)


def test_currency_tolerance_applies_only_to_foreign_currency_sales() -> None:
    eur = Money.of("120.00", "EUR")
    ctx = context(
        [bank("B1", "ORD-1", "100.03"), bank("B2", "ORD-2", "100.03")],
        [
            ledger("L1", "ORD-1", "100.00", MONDAY, "FERNHOLLOW", original_amount=eur),
            ledger("L2", "ORD-2", "100.00", MONDAY, "DIRECT"),
        ],
    )
    assert pairs(CurrencyToleranceMatch(), ctx) == {(("B1",), ("L1",))}


def test_currency_tolerance_is_inclusive_at_the_limit() -> None:
    eur = Money.of("120.00", "EUR")
    ctx = context(
        [bank("B1", "ORD-1", "100.05"), bank("B2", "ORD-2", "100.06")],
        [
            ledger("L1", "ORD-1", "100.00", MONDAY, "FERNHOLLOW", original_amount=eur),
            ledger("L2", "ORD-2", "100.00", MONDAY, "FERNHOLLOW", original_amount=eur),
        ],
    )
    assert pairs(CurrencyToleranceMatch(), ctx) == {(("B1",), ("L1",))}


def test_bounded_subset_sum_finds_an_exact_subset() -> None:
    values = [500, 1200, 300, 700]
    chosen = bounded_subset_sum(values, 1500, max_states=1000)
    assert chosen is not None
    assert sum(values[i] for i in chosen) == 1500


def test_bounded_subset_sum_returns_none_when_no_subset_fits() -> None:
    assert bounded_subset_sum([500, 1200], 1000, max_states=1000) is None


def test_bounded_subset_sum_stops_at_its_state_limit() -> None:
    with pytest.raises(SubsetSearchLimitError):
        bounded_subset_sum(list(range(1, 40)), 10_000, max_states=50)


def test_batch_match_finds_the_sales_in_a_payout() -> None:
    """Quillfeather charges 1.9%: £10.00 and £20.00 settle as £9.81 and £19.62."""
    ctx = context(
        [bank("B1", "QFW-PAYOUT", "29.43", counterparty="QUILLFEATHER")],
        [
            ledger("L1", "ORD-1", "10.00", MONDAY, "QUILLFEATHER"),
            ledger("L2", "ORD-2", "20.00", MONDAY, "QUILLFEATHER"),
            ledger("L3", "ORD-3", "35.00", MONDAY, "QUILLFEATHER"),
        ],
    )
    assert pairs(BatchMatch(), ctx) == {(("B1",), ("L1", "L2"))}


def test_reference_suffixes() -> None:
    assert reference_suffixes("CB-ORD-1234") == ["CB-ORD-1234", "ORD-1234", "1234"]


def test_reversal_links_refunds_and_chargebacks() -> None:
    ctx = context(
        [
            bank("B1", "ORD-1", "-25.00", counterparty="OTTERLINE"),
            bank("B2", "CB-ORD-2", "-55.00", counterparty="BRAMBLE"),
        ],
        [
            ledger(
                "L1", "RFD-1", "-25.00", FRIDAY, "OTTERLINE",
                kind=EntryKind.REFUND, original_reference="ORD-1",
            ),
            ledger(
                "L2", "CHB-1", "-40.00", TUESDAY, "BRAMBLE",
                kind=EntryKind.CHARGEBACK, original_reference="ORD-2",
            ),
        ],
    )  # fmt: skip
    rules = {c.ledger_ids: c.resolution for c in ReversalLink().propose(ctx)}
    assert rules == {("L1",): MatchRule.REFUND_LINK, ("L2",): MatchRule.CHARGEBACK_LINK}


def test_duplicate_detection_flags_the_unmatched_copy() -> None:
    ctx = context(
        [bank("B1", "INV-1", "10.00")],
        [ledger("L1", "INV-1", "10.00"), ledger("L2", "INV-1", "10.00")],
    )
    ctx.claim(Candidate(("B1",), ("L1",), MatchRule.EXACT, 1.0))
    (candidate,) = DuplicateDetection().propose(ctx)
    assert candidate.ledger_ids == ("L2",)
    assert candidate.related_ids == ("L1",)


def test_transposition_detection_uses_the_divisible_by_nine_rule() -> None:
    """£1,234.50 keyed as £1,243.50 differs by £9.00."""
    ctx = context(
        [bank("B1", "INV-1", "1234.50"), bank("B2", "INV-2", "100.00")],
        [ledger("L1", "INV-1", "1243.50"), ledger("L2", "INV-2", "100.05")],
    )
    (candidate,) = TranspositionDetection().propose(ctx)
    assert candidate.resolution is ExceptionReason.LIKELY_TRANSPOSITION
    assert (candidate.bank_ids, candidate.ledger_ids) == (("B1",), ("L1",))
    assert candidate.confidence == 0.8


def test_queue_pops_highest_confidence_then_lowest_ids() -> None:
    queue = CandidateQueue(
        [
            Candidate(("B2",), ("L1",), MatchRule.EXACT, 0.9),
            Candidate(("B1",), ("L2",), MatchRule.EXACT, 0.9),
            Candidate(("B3",), ("L3",), MatchRule.EXACT, 0.95),
        ]
    )
    assert [c.bank_ids for c in queue.drain()] == [("B3",), ("B1",), ("B2",)]


def test_engine_never_matches_an_entry_twice() -> None:
    """Two bank lines compete for one ledger entry; one becomes an exception."""
    statement = BankStatement(
        [bank("B1", "INV-1", "10.00"), bank("B2", "INV-1", "10.00")],
        bank_name="b",
        account_name="a",
        statement_date=TUESDAY,
    )
    report = ReconciliationEngine().reconcile(
        statement, Ledger([ledger("L1", "INV-1", "10.00")], entity_name="Test Ltd")
    )
    assert [m.bank_entries[0].entry_id for m in report.matches] == ["B1"]
    assert [e.reason for e in report.exceptions] == [ExceptionReason.DUPLICATE]
    assert report.is_balanced


def test_leftovers_become_missing_exceptions() -> None:
    statement = BankStatement(
        [bank("B1", "INV-9", "10.00")], bank_name="b", account_name="a", statement_date=TUESDAY
    )
    report = ReconciliationEngine().reconcile(
        statement, Ledger([ledger("L1", "INV-1", "20.00")], entity_name="Test Ltd")
    )
    assert report.exceptions_by_reason() == {
        ExceptionReason.MISSING_FROM_LEDGER: 1,
        ExceptionReason.MISSING_FROM_BANK: 1,
    }


@pytest.mark.parametrize("difficulty", list(Difficulty))
def test_engine_balances_every_generated_case(difficulty: Difficulty) -> None:
    engine = ReconciliationEngine()
    for seed in range(5):
        case = CaseGenerator().generate(seed, difficulty)
        assert engine.reconcile(case.bank_statement, case.ledger).is_balanced
