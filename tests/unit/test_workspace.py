"""Workspace tests: the small days' outcome, the problem cards and the side-by-side rows."""

from collections import Counter

import pytest

from eod_close.domain import Money
from eod_close.domain.results import ExceptionReason
from eod_close.web.report_view import ReviewItem
from eod_close.web.workspace import (
    MATCH_TAGS,
    Workspace,
    build_workspace,
    load_sample_day,
    load_small_day,
)
from eod_close.web.workspace import _describe as describe

THREE_PROBLEMS = Counter(
    {
        ExceptionReason.MISSING_FROM_LEDGER: 1,
        ExceptionReason.DUPLICATE: 1,
        ExceptionReason.LIKELY_TRANSPOSITION: 1,
    }
)


def reconciled() -> Workspace:
    return build_workspace(*load_sample_day())


def test_the_sample_day_is_about_a_dozen_lines_a_side() -> None:
    workspace = reconciled()
    assert len(workspace.bank_rows) == 12
    assert len(workspace.ledger_rows) == 13
    assert workspace.summary is not None
    assert workspace.summary.matched == 10


def test_the_sample_day_shows_each_common_kind_of_match() -> None:
    keys = {row.tag_key for row in reconciled().bank_rows if row.tag_key}
    expected = {key for key, _ in MATCH_TAGS.values()} - {"chargeback"}
    assert keys == expected


def test_the_sample_day_has_exactly_three_clear_problems() -> None:
    assert Counter(p.item.reason_code for p in reconciled().problems) == THREE_PROBLEMS


def test_the_headline_counts_matches_and_problems() -> None:
    assert reconciled().headline == "Matched 10 payments and found 3 problems"


def test_the_problems_add_up_to_the_unexplained_difference() -> None:
    workspace = reconciled()
    assert workspace.summary is not None
    assert workspace.problems_total == workspace.summary.unexplained_difference
    assert workspace.problems_total == Money.of("-91.00")


def test_problem_cards_say_what_is_wrong_and_what_to_do_in_plain_words() -> None:
    cards = {p.item.reason_code: p for p in reconciled().problems}
    missing = cards[ExceptionReason.MISSING_FROM_LEDGER]
    assert missing.what == "£25.00 arrived in the bank (INV-40139) but isn't in your records."
    assert missing.next_step == "Check whether a sale wasn't recorded."
    assert missing.effect == Money.of("25.00")
    typo = cards[ExceptionReason.LIKELY_TRANSPOSITION]
    assert "£14.99 for INV-40125" in typo.what
    assert "£41.99" in typo.what
    assert typo.effect == Money.of("-27.00")
    duplicate = cards[ExceptionReason.DUPLICATE]
    assert duplicate.what == "£89.00 for INV-40135 is in your records twice but arrived once."
    for card in cards.values():
        assert not any(f"{side}{n:02d}" in card.what for side in "BL" for n in range(1, 30)), (
            "cards never name the IDs the page hides"
        )


def test_money_nobody_recorded_comes_first() -> None:
    first = reconciled().problems[0]
    assert first.item.reason_code is ExceptionReason.MISSING_FROM_LEDGER
    assert first.group == "r1"


def test_every_line_appears_once_in_file_order() -> None:
    statement, ledger, report = load_sample_day()
    workspace = build_workspace(statement, ledger, report)
    assert [row.entry_id for row in workspace.bank_rows] == [e.entry_id for e in statement]
    assert [row.entry_id for row in workspace.ledger_rows] == [e.entry_id for e in ledger]


def test_every_row_has_an_outcome_and_matched_rows_a_reason() -> None:
    for row in reconciled().bank_rows + reconciled().ledger_rows:
        assert row.status in ("matched", "review"), f"{row.entry_id} has no outcome"
        assert row.group
        assert row.reason


def test_a_typo_card_covers_a_row_on_each_side() -> None:
    workspace = reconciled()
    typo = next(p for p in workspace.problems if p.item.label == "Typo")
    rows = [r for r in workspace.bank_rows + workspace.ledger_rows if r.group == typo.group]
    assert sorted((r.side, r.entry_id) for r in rows) == [("bank", "B09"), ("ledger", "L10")]


def test_show_all_matches_lists_each_match_once_with_both_sides() -> None:
    matches = reconciled().matches
    assert len(matches) == 10
    batch = next(m for m in matches if m.tag_key == "batch")
    assert len(batch.bank_rows) == 1
    assert len(batch.ledger_rows) == 2
    assert all(m.bank_rows and m.ledger_rows and m.reason for m in matches)


def test_before_matching_no_row_has_an_outcome() -> None:
    statement, ledger, _ = load_sample_day()
    workspace = build_workspace(statement, ledger)
    assert not workspace.reconciled
    assert workspace.headline == ""
    assert all(row.status == "pending" and row.tag is None for row in workspace.bank_rows)
    assert workspace.problems == ()


@pytest.mark.parametrize("seed", range(0, 200, 7))
def test_another_day_has_the_sample_day_shape(seed: int) -> None:
    workspace = build_workspace(*load_small_day(seed))
    assert 12 <= len(workspace.bank_rows) <= 13
    assert 12 <= len(workspace.ledger_rows) <= 13
    assert workspace.headline == "Matched 10 payments and found 3 problems"
    assert Counter(p.item.reason_code for p in workspace.problems) == THREE_PROBLEMS
    assert workspace.summary is not None
    assert workspace.problems_total == workspace.summary.unexplained_difference


def test_another_day_is_the_same_for_the_same_seed() -> None:
    first = build_workspace(*load_small_day(5))
    load_small_day.cache_clear()
    again = build_workspace(*load_small_day(5))
    assert first.bank_rows == again.bank_rows
    assert first.ledger_rows == again.ledger_rows


def problem(
    reason: ExceptionReason, bank: str | None = None, ledger: str | None = None
) -> ReviewItem:
    return ReviewItem(
        reason_code=reason,
        bank_ids=("B1",) if bank else (),
        ledger_ids=("L1",) if ledger else (),
        bank_amount=Money.of(bank) if bank else None,
        ledger_amount=Money.of(ledger) if ledger else None,
        reason="",
        next_step="",
    )


@pytest.mark.parametrize(
    ("item", "what", "next_step"),
    [
        (
            problem(ExceptionReason.MISSING_FROM_LEDGER, bank="-12.00"),
            "£12.00 left the bank (REF-1) but isn't in your records.",
            "Check whether a refund or charge wasn't recorded.",
        ),
        (
            problem(ExceptionReason.MISSING_FROM_BANK, ledger="30.00"),
            "Your records show £30.00 for REF-1, but it hasn't reached the bank.",
            "Check with the customer or card company that it was paid.",
        ),
        (
            problem(ExceptionReason.DUPLICATE, bank="18.00"),
            "The bank lists £18.00 for REF-1 twice.",
            "Ask the bank whether the payment really came in twice.",
        ),
    ],
)
def test_cards_word_the_rarer_problems_plainly(item: ReviewItem, what: str, next_step: str) -> None:
    assert describe(item, "REF-1") == (what, next_step)
