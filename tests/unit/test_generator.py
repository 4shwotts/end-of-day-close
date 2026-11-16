"""Case generator tests: reproducibility, the answer key, each difficulty profile and the CLI."""

import io
from collections import Counter
from pathlib import Path

import pytest

from eod_close.csv_format import write_bank_csv, write_ledger_csv
from eod_close.domain.business_days import business_days_between
from eod_close.domain.money import Money
from eod_close.domain.processor import BRAMBLE, QUILLFEATHER
from eod_close.generator import Case, CaseGenerator, Difficulty, DiscrepancyKind, profile_for
from eod_close.generator.__main__ import main as generator_main
from eod_close.generator.case_generator import MAX_FX_ROUNDING_PENCE, transpose_adjacent_digits

SEEDS = range(10)


def generate(seed: int, difficulty: Difficulty) -> Case:
    return CaseGenerator().generate(seed, difficulty)


def as_csv(case: Case) -> str:
    buffer = io.StringIO()
    write_bank_csv(case.bank_statement, buffer)
    write_ledger_csv(case.ledger, buffer)
    return buffer.getvalue() + case.answer_key.to_json()


@pytest.mark.parametrize("difficulty", list(Difficulty))
def test_same_seed_reproduces_the_case_exactly(difficulty: Difficulty) -> None:
    assert as_csv(generate(7, difficulty)) == as_csv(generate(7, difficulty))


def test_different_seeds_give_different_cases() -> None:
    assert as_csv(generate(1, Difficulty.ANALYST)) != as_csv(generate(2, Difficulty.ANALYST))


@pytest.mark.parametrize("difficulty", list(Difficulty))
@pytest.mark.parametrize("seed", SEEDS)
def test_every_entry_belongs_to_exactly_one_planted_item(seed: int, difficulty: Difficulty) -> None:
    """Without this, the accuracy evaluator could not score every engine decision."""
    case = generate(seed, difficulty)
    bank_ids = Counter(i for item in case.answer_key.items for i in item.bank_ids)
    ledger_ids = Counter(i for item in case.answer_key.items for i in item.ledger_ids)
    assert set(bank_ids) == case.bank_statement.ids
    assert set(ledger_ids) == case.ledger.ids
    assert max(bank_ids.values()) == 1
    assert max(ledger_ids.values()) == 1


@pytest.mark.parametrize("difficulty", list(Difficulty))
def test_every_kind_in_the_profile_is_planted(difficulty: Difficulty) -> None:
    case = generate(3, difficulty)
    profile = profile_for(difficulty)
    assert set(profile.kinds) <= case.answer_key.kinds_present()
    assert len(case.answer_key.items) >= profile.item_count


def test_harder_levels_produce_larger_cases() -> None:
    sizes = [len(generate(0, d).ledger) for d in Difficulty]
    assert sizes == sorted(sizes)
    assert len(set(sizes)) == len(sizes)


def test_batches_respect_the_profile_limits() -> None:
    profile = profile_for(Difficulty.HEAD_OF_OPS)
    for seed in SEEDS:
        batches = generate(seed, Difficulty.HEAD_OF_OPS).answer_key.by_kind(
            DiscrepancyKind.BATCH_PAYOUT
        )
        assert 1 <= len(batches) <= profile.max_batches
        assert all(2 <= len(item.ledger_ids) <= profile.max_batch_size for item in batches)


@pytest.mark.parametrize("seed", SEEDS)
def test_planted_discrepancies_follow_their_rules(seed: int) -> None:
    """Each planted item must be explainable by the rule its kind claims."""
    case = generate(seed, Difficulty.HEAD_OF_OPS)
    bank, ledger = case.bank_statement, case.ledger
    for item in case.answer_key.items:
        b = [bank.get(i) for i in item.bank_ids]
        led = [ledger.get(i) for i in item.ledger_ids]
        match item.kind:
            case DiscrepancyKind.CLEAN:
                assert b[0].amount == led[0].amount
                assert b[0].reference == led[0].reference
                assert b[0].value_date == led[0].value_date
            case DiscrepancyKind.SETTLEMENT_DELAY:
                assert b[0].amount == led[0].amount
                assert 1 <= business_days_between(led[0].value_date, b[0].value_date) <= 3
            case DiscrepancyKind.PROCESSOR_FEE:
                assert b[0].amount == BRAMBLE.settlement_amount(led[0].amount)
                assert b[0].amount < led[0].amount
            case DiscrepancyKind.FX_ROUNDING:
                gap = abs(b[0].amount - led[0].amount)
                assert Money.zero() < gap <= Money.from_minor_units(MAX_FX_ROUNDING_PENCE)
                assert led[0].original_amount is not None
            case DiscrepancyKind.BATCH_PAYOUT:
                expected = Money.total(QUILLFEATHER.settlement_amount(e.amount) for e in led)
                assert b[0].amount == expected
            case DiscrepancyKind.REFUND:
                assert b[0].amount == led[0].amount
                assert b[0].reference == led[0].original_reference
            case DiscrepancyKind.CHARGEBACK:
                assert b[0].amount == led[0].amount - BRAMBLE.chargeback_fee
            case DiscrepancyKind.DUPLICATE:
                (copy,) = b or led
                (original_id,) = item.related_ids
                original = bank.get(original_id) if b else ledger.get(original_id)
                assert (copy.reference, copy.amount) == (original.reference, original.amount)
            case DiscrepancyKind.TRANSPOSITION:
                true_units, keyed_units = b[0].amount.minor_units, led[0].amount.minor_units
                assert true_units != keyed_units
                assert (true_units - keyed_units) % 9 == 0
                assert sorted(str(true_units)) == sorted(str(keyed_units))
            case DiscrepancyKind.MISSING_FROM_BANK:
                assert not b
                assert len(led) == 1
            case DiscrepancyKind.MISSING_FROM_LEDGER:
                assert not led
                assert len(b) == 1


def test_a_fixed_mix_plants_exactly_the_kinds_given() -> None:
    kinds = [DiscrepancyKind.CLEAN, DiscrepancyKind.BATCH_PAYOUT, DiscrepancyKind.TRANSPOSITION]
    statement, ledger, key = CaseGenerator().generate_mix(3, kinds, "test")
    assert Counter(item.kind for item in key.items) == Counter(kinds)
    batch = next(i for i in key.items if i.kind is DiscrepancyKind.BATCH_PAYOUT)
    assert len(batch.ledger_ids) == 2, "a fixed mix keeps batches to two sales"
    assert key.case_id == "EOD-test-3"
    again = CaseGenerator().generate_mix(3, kinds, "test")
    assert (list(statement), list(ledger)) == (list(again[0]), list(again[1]))


def test_bank_entries_are_dated_on_the_business_day() -> None:
    case = generate(5, Difficulty.SENIOR_ANALYST)
    assert {e.value_date for e in case.bank_statement} == {case.business_date}
    assert all(e.value_date <= case.business_date for e in case.ledger)


def test_transpose_adjacent_digits() -> None:
    assert transpose_adjacent_digits(123450, 2) == 124350
    with pytest.raises(ValueError, match="Cannot transpose"):
        transpose_adjacent_digits(12, 1)


def test_generator_cli_writes_three_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert generator_main(["--seed", "1", "--difficulty", "intern", "--out", str(tmp_path)]) == 0
    assert {p.name for p in tmp_path.iterdir()} == {"bank.csv", "ledger.csv", "answer_key.json"}
    assert "EOD-intern-1" in capsys.readouterr().out
