"""Property-based tests: invariants that must hold for any input, not just chosen examples."""

import random
from datetime import date, timedelta
from itertools import combinations

from hypothesis import given
from hypothesis import strategies as st

from eod_close.domain import (
    BankEntry,
    BankStatement,
    EntryKind,
    Ledger,
    LedgerEntry,
    MatchRule,
    Money,
    ReconciliationReport,
)
from eod_close.domain.processor import FICTIONAL_PROCESSORS
from eod_close.engine import Candidate, CandidateQueue, EngineConfig, ReconciliationEngine
from eod_close.engine.strategies import bounded_subset_sum
from eod_close.generator import CaseGenerator, Difficulty
from eod_close.generator.case_generator import transpose_adjacent_digits

PROCESSORS = {p.code: p for p in FICTIONAL_PROCESSORS}
CONFIG = EngineConfig()
START = date(2026, 11, 9)

pence = st.integers(min_value=-(10**12), max_value=10**12)
money = pence.map(Money.from_minor_units)

# A small alphabet of references and amounts forces collisions, which is
# where matching logic goes wrong; fully random values would almost never
# collide and would test very little.
references = st.sampled_from(["", "INV-1", "INV-2", "ORD-1", "ORD-2", "CB-ORD-1"])
small_amounts = st.sampled_from([-1500, -1000, 999, 1000, 1001, 1500, 2499, 4295]).map(
    Money.from_minor_units
)
days = st.integers(min_value=0, max_value=6).map(lambda n: START + timedelta(days=n))
processors = st.sampled_from(sorted(PROCESSORS))


@st.composite
def bank_entries(draw: st.DrawFn) -> list[BankEntry]:
    count = draw(st.integers(min_value=0, max_value=12))
    return [
        BankEntry(
            entry_id=f"B{n:03d}",
            reference=draw(references),
            amount=draw(small_amounts),
            value_date=draw(days),
            counterparty=draw(processors),
        )
        for n in range(count)
    ]


@st.composite
def ledger_entries(draw: st.DrawFn) -> list[LedgerEntry]:
    count = draw(st.integers(min_value=0, max_value=12))
    entries = []
    for n in range(count):
        amount = draw(small_amounts)
        kind = (
            EntryKind.PAYMENT
            if amount.is_positive()
            else draw(st.sampled_from([EntryKind.REFUND, EntryKind.CHARGEBACK]))
        )
        processor = draw(processors)
        foreign = draw(st.booleans()) and kind is EntryKind.PAYMENT
        entries.append(
            LedgerEntry(
                entry_id=f"L{n:03d}",
                reference=draw(references),
                amount=amount,
                value_date=draw(days),
                processor_code=processor,
                kind=kind,
                original_amount=Money.of(amount.amount, "EUR") if foreign else None,
                original_reference=None
                if kind is EntryKind.PAYMENT
                else draw(references) or "ORD-1",
            )
        )
    return entries


def reconcile(bank: list[BankEntry], ledger: list[LedgerEntry]) -> ReconciliationReport:
    statement = BankStatement(bank, bank_name="b", account_name="a", statement_date=START)
    return ReconciliationEngine().reconcile(statement, Ledger(ledger, entity_name="Test Ltd"))


# Money


@given(money, money)
def test_money_subtraction_undoes_addition(a: Money, b: Money) -> None:
    assert (a + b) - b == a


@given(st.lists(money, max_size=20))
def test_money_total_does_not_depend_on_order(amounts: list[Money]) -> None:
    assert Money.total(amounts) == Money.total(reversed(amounts))


@given(pence)
def test_money_minor_units_round_trip(units: int) -> None:
    assert Money.from_minor_units(units).minor_units == units


@given(money)
def test_money_parses_its_own_decimal_representation(a: Money) -> None:
    assert Money.of(str(a.amount)) == a


@given(st.integers(min_value=10, max_value=10**12), st.data())
def test_transposing_adjacent_digits_changes_by_a_multiple_of_nine(
    units: int, data: st.DataObject
) -> None:
    """The basis of the transposition strategy: any adjacent swap differs by 9(a-b)10^k."""
    position = data.draw(st.integers(min_value=0, max_value=len(str(units)) - 2))
    assert (transpose_adjacent_digits(units, position) - units) % 9 == 0


# Algorithms


@given(st.lists(st.integers(min_value=1, max_value=60), max_size=10), st.integers(1, 300))
def test_subset_sum_is_correct_and_complete(values: list[int], target: int) -> None:
    """Any subset returned sums to the target, and None means no subset exists (by brute force)."""
    chosen = bounded_subset_sum(values, target, max_states=10_000)
    if chosen is None:
        assert all(
            sum(c) != target for r in range(len(values) + 1) for c in combinations(values, r)
        )
    else:
        assert len(set(chosen)) == len(chosen)
        assert sum(values[i] for i in chosen) == target


@given(st.lists(st.floats(min_value=0, max_value=1), max_size=30))
def test_queue_pops_in_non_increasing_confidence(confidences: list[float]) -> None:
    queue = CandidateQueue(
        Candidate((f"B{i}",), (f"L{i}",), MatchRule.EXACT, c) for i, c in enumerate(confidences)
    )
    popped = [c.confidence for c in queue.drain()]
    assert popped == sorted(confidences, reverse=True)


# The engine


@given(st.integers(min_value=0, max_value=10**6), st.sampled_from(list(Difficulty)))
def test_engine_never_crashes_and_always_balances_on_generated_cases(
    seed: int, difficulty: Difficulty
) -> None:
    case = CaseGenerator().generate(seed, difficulty)
    report = ReconciliationEngine().reconcile(case.bank_statement, case.ledger)
    assert report.is_balanced


@given(bank_entries(), ledger_entries())
def test_engine_uses_every_entry_exactly_once(
    bank: list[BankEntry], ledger: list[LedgerEntry]
) -> None:
    """The report constructor rejects reuse; this also checks nothing is dropped."""
    report = reconcile(bank, ledger)
    assert report.is_balanced
    matched = sum(len(m.bank_entries) + len(m.ledger_entries) for m in report.matches)
    assert matched + len(report.exceptions) == len(bank) + len(ledger)


@given(bank_entries(), ledger_entries())
def test_every_match_reconciles_under_its_rule(
    bank: list[BankEntry], ledger: list[LedgerEntry]
) -> None:
    """A match is only valid if its amounts obey the rule it claims."""
    for match in reconcile(bank, ledger).matches:
        b, led = match.bank_entries, match.ledger_entries
        processor = PROCESSORS.get(led[0].processor_code)
        match match.rule:
            case MatchRule.EXACT | MatchRule.DATE_WINDOW | MatchRule.REFUND_LINK:
                assert b[0].amount == led[0].amount
            case MatchRule.FEE_ADJUSTED:
                assert processor is not None
                assert b[0].amount == processor.settlement_amount(led[0].amount)
            case MatchRule.CURRENCY_TOLERANCE:
                assert abs(match.difference) <= CONFIG.fx_tolerance
                assert led[0].original_amount is not None
            case MatchRule.BATCH:
                assert processor is not None
                assert match.bank_total == Money.total(
                    processor.settlement_amount(e.amount) for e in led
                )
            case MatchRule.CHARGEBACK_LINK:
                assert processor is not None
                assert b[0].amount == led[0].amount - processor.chargeback_fee


@given(bank_entries(), ledger_entries(), st.randoms(use_true_random=False))
def test_result_does_not_depend_on_row_order(
    bank: list[BankEntry], ledger: list[LedgerEntry], rng: random.Random
) -> None:
    """Reordering the rows of either file must not change any decision.

    Entry IDs stay the same, so the queue's ID tie-break is unaffected;
    only the order the strategies see the entries in changes.
    """

    def decisions(report: ReconciliationReport) -> set[tuple[object, ...]]:
        matches = {
            (
                m.rule,
                tuple(e.entry_id for e in m.bank_entries),
                tuple(e.entry_id for e in m.ledger_entries),
            )
            for m in report.matches
        }
        exceptions = {(x.reason, x.entry.entry_id, x.related_entry_ids) for x in report.exceptions}
        return matches | exceptions

    shuffled_bank, shuffled_ledger = bank[:], ledger[:]
    rng.shuffle(shuffled_bank)
    rng.shuffle(shuffled_ledger)
    assert decisions(reconcile(bank, ledger)) == decisions(
        reconcile(shuffled_bank, shuffled_ledger)
    )
