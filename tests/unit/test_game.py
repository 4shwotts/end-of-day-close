"""Game scoring tests: how a player's answers are validated, scored and compared with the engine."""

import pytest

from eod_close.domain.results import ExceptionReason, ReconciliationReport
from eod_close.engine import ReconciliationEngine
from eod_close.game import InvalidAnswerError, PlayerAnswers, score_answers
from eod_close.generator import Case, CaseGenerator, Difficulty
from eod_close.generator.answer_key import DiscrepancyKind
from eod_close.web.services import answers_from_form

_RESOLUTION = {
    DiscrepancyKind.DUPLICATE: ExceptionReason.DUPLICATE,
    DiscrepancyKind.MISSING_FROM_BANK: ExceptionReason.MISSING_FROM_BANK,
    DiscrepancyKind.MISSING_FROM_LEDGER: ExceptionReason.MISSING_FROM_LEDGER,
    DiscrepancyKind.TRANSPOSITION: ExceptionReason.LIKELY_TRANSPOSITION,
}


def setup(difficulty: Difficulty = Difficulty.HEAD_OF_OPS) -> tuple[Case, ReconciliationReport]:
    case = CaseGenerator().generate(3, difficulty)
    return case, ReconciliationEngine().reconcile(case.bank_statement, case.ledger)


def perfect_answers(case: Case) -> PlayerAnswers:
    """Answers built straight from the answer key, as a flawless player would give them."""
    groups = []
    exceptions = {}
    for item in case.answer_key.items:
        if item.kind.expects_match or item.kind is DiscrepancyKind.TRANSPOSITION:
            groups.append((frozenset(item.bank_ids), frozenset(item.ledger_ids)))
        if not item.kind.expects_match:
            for entry_id in item.bank_ids + item.ledger_ids:
                exceptions[entry_id] = _RESOLUTION[item.kind]
    return PlayerAnswers(tuple(groups), exceptions, elapsed_seconds=90)


def test_perfect_answers_score_full_marks_and_balance() -> None:
    case, report = setup()
    score = score_answers(case, perfect_answers(case), report)
    assert score.accuracy == 1.0
    assert score.balanced
    assert all(item.player_correct for item in score.items)


def test_empty_answers_score_zero_and_do_not_balance() -> None:
    case, report = setup(Difficulty.INTERN)
    score = score_answers(case, PlayerAnswers(), report)
    assert score.accuracy == 0.0
    assert not score.balanced
    assert len(score.unassigned_ids) == len(case.bank_statement) + len(case.ledger)
    assert score.verdict == "The engine wins this one."


def test_a_one_sided_group_prevents_balancing() -> None:
    case, report = setup(Difficulty.INTERN)
    answers = perfect_answers(case)
    bank_ids, _ = answers.groups[0]
    broken = PlayerAnswers(((bank_ids, frozenset()), *answers.groups[1:]), answers.exceptions)
    score = score_answers(case, broken, report)
    assert score.one_sided_groups == 1
    assert not score.balanced


def test_extra_guesses_cannot_raise_accuracy() -> None:
    """Accuracy divides by the larger of planted and claimed decisions."""
    case, report = setup(Difficulty.INTERN)
    scattered = PlayerAnswers(
        exceptions={
            **dict.fromkeys(case.bank_statement.ids, ExceptionReason.MISSING_FROM_LEDGER),
            **dict.fromkeys(case.ledger.ids, ExceptionReason.MISSING_FROM_BANK),
        }
    )
    assert score_answers(case, scattered, report).accuracy == 0.0


def test_unknown_entries_are_rejected() -> None:
    case, report = setup(Difficulty.INTERN)
    with pytest.raises(InvalidAnswerError, match="Unknown"):
        score_answers(case, PlayerAnswers(((frozenset({"B9999"}), frozenset()),)), report)


def test_an_entry_in_two_groups_is_rejected() -> None:
    case, report = setup(Difficulty.INTERN)
    bank_id = sorted(case.bank_statement.ids)[0]
    ledger_ids = sorted(case.ledger.ids)[:2]
    groups = tuple((frozenset({bank_id}), frozenset({lid})) for lid in ledger_ids)
    with pytest.raises(InvalidAnswerError, match="more than one group"):
        score_answers(case, PlayerAnswers(groups), report)


def test_form_fields_parse_into_groups_and_exceptions() -> None:
    case, report = setup()
    answers = perfect_answers(case)
    form: dict[str, str] = {}
    for n, (bank_ids, ledger_ids) in enumerate(answers.groups, start=1):
        for entry_id in bank_ids | ledger_ids:
            form[f"group-{entry_id}"] = str(n)
    for entry_id, reason in answers.exceptions.items():
        form[f"reason-{entry_id}"] = reason.value
    form["reason-B0001-unknown"] = "nonsense"
    score = score_answers(case, answers_from_form(case, form, 30), report)
    assert score.accuracy == 1.0
    assert score.elapsed_seconds == 30
