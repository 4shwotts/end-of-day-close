"""Busy-day tests: a realistic problem rate, reproducible days, honest marks and live work."""

import csv
import io

import pytest

from eod_close.generator import CaseGenerator
from eod_close.generator.difficulty import BUSY_DAY_PROFILE
from eod_close.web import busy_day
from eod_close.web.busy_day import (
    PAGE_SIZE,
    PROBLEM_KINDS,
    BusyDay,
    answer_key_csv,
    list_page,
    run_busy_day,
)


def outcome(day: BusyDay) -> tuple[object, ...]:
    """Everything a run reports except the measured times, which always vary."""
    return (
        tuple(day.statement),
        tuple(day.ledger),
        day.answer_key,
        day.workspace.headline,
        tuple((p.item.label, p.what, p.effect) for p in day.workspace.problems),
        day.planted,
        day.found,
        day.false_alarms,
        day.naive_unexplained,
    )


def test_the_same_seed_gives_identical_results() -> None:
    assert outcome(run_busy_day(48213)) == outcome(run_busy_day(48213))


def test_different_seeds_give_different_results() -> None:
    first, second = run_busy_day(48213), run_busy_day(48214)
    assert outcome(first) != outcome(second)
    assert tuple(first.statement) != tuple(second.statement)


def test_every_run_does_the_work_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing is cached: two runs of one seed generate and match the day twice."""
    calls = []
    real = CaseGenerator.generate_profile

    def counting(self: CaseGenerator, *args: object, **kwargs: object) -> object:
        calls.append(args)
        return real(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(CaseGenerator, "generate_profile", counting)
    first, second = busy_day.run_busy_day(7), busy_day.run_busy_day(7)
    assert len(calls) == 2
    assert first.report is not second.report


def test_a_busy_day_has_a_real_days_volume_and_problem_rate() -> None:
    rates = []
    for seed in range(20):
        day = run_busy_day(seed)
        assert 250 <= len(day.statement) <= 350
        rates.append(day.planted / len(day.statement))
    average = sum(rates) / len(rates)
    assert 0.02 <= average <= 0.05, f"average problem rate {average:.1%}"


def test_the_profile_keeps_every_kind_of_problem() -> None:
    assert set(BUSY_DAY_PROFILE.kinds) >= PROBLEM_KINDS


def test_marks_add_up() -> None:
    day = run_busy_day(48213)
    assert 0 <= day.found <= day.planted
    assert day.missed == day.planted - day.found
    assert day.found + day.false_alarms == len(day.workspace.problems)


def test_the_engine_explains_every_line_and_the_simple_approach_does_not() -> None:
    day = run_busy_day(48213)
    assert day.engine_unexplained == 0
    assert day.naive_unexplained > len(day.workspace.problems)
    assert 0 < day.seconds < 5
    assert 0 < day.naive_seconds < 5


def test_the_lists_page_together_and_clamp_to_the_last_page() -> None:
    workspace = run_busy_day(48213).workspace
    first = list_page(workspace, 1, "all")
    assert len(first.bank_rows) == PAGE_SIZE
    assert first.pages == -(-max(len(workspace.bank_rows), len(workspace.ledger_rows)) // PAGE_SIZE)
    beyond = list_page(workspace, 999, "all")
    assert beyond.page == beyond.pages
    assert beyond.bank_rows or beyond.ledger_rows


def test_the_problems_filter_keeps_only_problem_rows() -> None:
    workspace = run_busy_day(48213).workspace
    problems = list_page(workspace, 1, "problems")
    assert problems.bank_count + problems.ledger_count > 0
    assert all(row.status == "review" for row in problems.bank_rows + problems.ledger_rows)


def test_the_answer_key_downloads_as_one_row_per_planted_item() -> None:
    key = run_busy_day(48213).answer_key
    rows = list(csv.DictReader(io.StringIO(answer_key_csv(key))))
    assert len(rows) == len(key.items)
    assert {row["problem"] for row in rows} == {"yes", "no"}
    duplicate = next(row for row in rows if row["kind"] == "duplicate")
    assert duplicate["problem"] == "yes"


def test_the_first_cards_show_the_range_of_problems() -> None:
    day = run_busy_day(48213)
    kinds = {p.item.label for p in day.workspace.problems}
    first = {p.item.label for p in day.problems[: len(kinds)]}
    assert first == kinds
    assert sorted(map(id, day.problems)) == sorted(map(id, day.workspace.problems))
