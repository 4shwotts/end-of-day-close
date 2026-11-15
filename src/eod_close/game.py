"""Scoring a player's attempt to close the day.

The player links bank and ledger entries into groups and marks the rest as
exceptions. They do not name a match rule: a person closing the day decides
*what goes with what*, and the rule is the engine's explanation. So both the
player and the engine are scored on the same reduced decisions: "these
entries match", or "this entry is an exception for this reason".

This module is independent of the web layer, so the API, the HTML pages and
the tests share one scoring implementation.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Final

from eod_close.domain.entries import Side
from eod_close.domain.results import ExceptionReason, ReconciliationReport
from eod_close.evaluation.accuracy import EXPECTED_RESOLUTION, canonical_ids
from eod_close.generator.answer_key import DiscrepancyKind
from eod_close.generator.case_generator import Case

MATCH: Final = "match"
"""The resolution label for any group of linked entries."""

type Label = str


class InvalidAnswerError(ValueError):
    """Raised when an answer refers to unknown entries or uses one twice."""


@dataclass(frozen=True)
class PlayerAnswers:
    """What the player submitted.

    Attributes:
        groups: Linked entries, each as (bank IDs, ledger IDs).
        exceptions: Entries marked as exceptions, by entry ID. Entries marked
            as a likely transposition are listed here and also share a group
            with their counterpart, so the pair can be scored as one finding.
        elapsed_seconds: How long the player took, if known.
    """

    groups: tuple[tuple[frozenset[str], frozenset[str]], ...] = ()
    exceptions: Mapping[str, ExceptionReason] = field(default_factory=dict)
    elapsed_seconds: int | None = None


@dataclass(frozen=True)
class _Decision:
    label: Label
    bank_ids: frozenset[str]
    ledger_ids: frozenset[str]


@dataclass(frozen=True)
class ItemReview:
    """How the player and the engine handled one planted item.

    Attributes:
        kind: What was planted.
        explanation: Why the entries disagreed.
        bank_ids: The bank entries involved.
        ledger_ids: The ledger entries involved.
        player_correct: True if the player resolved it exactly.
        engine_correct: True if the engine resolved it exactly.
    """

    kind: DiscrepancyKind
    explanation: str
    bank_ids: tuple[str, ...]
    ledger_ids: tuple[str, ...]
    player_correct: bool
    engine_correct: bool


@dataclass(frozen=True)
class GameScore:
    """The result shown on the close-the-day screen.

    Attributes:
        planted: Decisions in the answer key.
        player_claimed: Decisions the player made.
        player_correct: Player decisions that match the key.
        engine_claimed: Decisions the engine made.
        engine_correct: Engine decisions that match the key.
        unassigned_ids: Entries the player neither linked nor marked.
        one_sided_groups: Groups with entries on only one side, which cannot
            be a match.
        elapsed_seconds: The player's time, if known.
        items: A review of every planted item.
    """

    planted: int
    player_claimed: int
    player_correct: int
    engine_claimed: int
    engine_correct: int
    unassigned_ids: tuple[str, ...]
    one_sided_groups: int
    elapsed_seconds: int | None
    items: tuple[ItemReview, ...]

    @property
    def balanced(self) -> bool:
        """True if every entry is linked or explained and every group has both sides."""
        return not self.unassigned_ids and not self.one_sided_groups

    @property
    def accuracy(self) -> float:
        """Correct decisions as a share of the larger of planted and claimed.

        Dividing by the larger count penalises both missing a discrepancy
        and inventing extra ones, so splitting entries into many tiny
        guesses cannot raise the score.
        """
        denominator = max(self.planted, self.player_claimed)
        return self.player_correct / denominator if denominator else 1.0

    @property
    def engine_accuracy(self) -> float:
        """The engine's accuracy on the same measure."""
        denominator = max(self.planted, self.engine_claimed)
        return self.engine_correct / denominator if denominator else 1.0

    @property
    def verdict(self) -> str:
        """A one-line comparison with the engine, for the close screen."""
        if self.accuracy > self.engine_accuracy:
            return "You beat the engine."
        if self.accuracy == self.engine_accuracy:
            return "You matched the engine."
        return "The engine wins this one."


def _validate(case: Case, answers: PlayerAnswers) -> None:
    seen: Counter[str] = Counter()
    for bank_ids, ledger_ids in answers.groups:
        unknown = (bank_ids - case.bank_statement.ids) | (ledger_ids - case.ledger.ids)
        if unknown:
            raise InvalidAnswerError(f"Unknown entries: {sorted(unknown)}")
        seen.update(bank_ids | ledger_ids)
    unknown_exceptions = set(answers.exceptions) - case.bank_statement.ids - case.ledger.ids
    if unknown_exceptions:
        raise InvalidAnswerError(f"Unknown entries: {sorted(unknown_exceptions)}")
    reused = sorted(i for i, n in seen.items() if n > 1)
    if reused:
        raise InvalidAnswerError(f"Entries in more than one group: {reused}")


def _player_decisions(
    case: Case, answers: PlayerAnswers, canon: Mapping[str, str]
) -> tuple[Counter[_Decision], int]:
    decisions: Counter[_Decision] = Counter()
    one_sided = 0
    grouped: set[str] = set()
    for bank_ids, ledger_ids in answers.groups:
        members = bank_ids | ledger_ids
        grouped |= members
        reasons = {answers.exceptions.get(i) for i in members}
        if reasons == {ExceptionReason.LIKELY_TRANSPOSITION}:
            # A transposition is one finding across both entries.
            decisions[
                _label_decision(ExceptionReason.LIKELY_TRANSPOSITION, bank_ids, ledger_ids, canon)
            ] += 1
            continue
        matched_bank = frozenset(i for i in bank_ids if i not in answers.exceptions)
        matched_ledger = frozenset(i for i in ledger_ids if i not in answers.exceptions)
        if matched_bank or matched_ledger:
            if not (matched_bank and matched_ledger):
                one_sided += 1
            decisions[_label_decision(MATCH, matched_bank, matched_ledger, canon)] += 1
    for entry_id, reason in answers.exceptions.items():
        if entry_id in grouped and reason is ExceptionReason.LIKELY_TRANSPOSITION:
            continue
        on_bank = entry_id in case.bank_statement
        own = frozenset({entry_id})
        decisions[
            _label_decision(
                reason, own if on_bank else frozenset(), frozenset() if on_bank else own, canon
            )
        ] += 1
    return decisions, one_sided


def _label_decision(
    label: Label | ExceptionReason,
    bank_ids: Iterable[str],
    ledger_ids: Iterable[str],
    canon: Mapping[str, str],
) -> _Decision:
    return _Decision(
        str(label), frozenset(canon[i] for i in bank_ids), frozenset(canon[i] for i in ledger_ids)
    )


def _engine_decisions(report: ReconciliationReport, canon: Mapping[str, str]) -> Counter[_Decision]:
    decisions: Counter[_Decision] = Counter(
        _label_decision(
            MATCH,
            (e.entry_id for e in m.bank_entries),
            (e.entry_id for e in m.ledger_entries),
            canon,
        )
        for m in report.matches
    )
    transpositions: set[_Decision] = set()
    for item in report.exceptions:
        if item.reason is ExceptionReason.LIKELY_TRANSPOSITION:
            ids = {item.entry.entry_id, *item.related_entry_ids}
            transpositions.add(
                _label_decision(
                    item.reason,
                    (i for i in ids if i in report.bank_statement),
                    (i for i in ids if i in report.ledger),
                    canon,
                )
            )
            continue
        own = (item.entry.entry_id,)
        is_bank = item.side is Side.BANK
        decisions[
            _label_decision(item.reason, own if is_bank else (), () if is_bank else own, canon)
        ] += 1
    decisions.update(transpositions)
    return decisions


def _planted_decision(
    kind: DiscrepancyKind,
    bank_ids: Iterable[str],
    ledger_ids: Iterable[str],
    canon: Mapping[str, str],
) -> _Decision:
    label: Label | ExceptionReason = MATCH if kind.expects_match else EXPECTED_RESOLUTION[kind]
    return _label_decision(label, bank_ids, ledger_ids, canon)


def score_answers(
    case: Case, answers: PlayerAnswers, engine_report: ReconciliationReport
) -> GameScore:
    """Score a player's answers and the engine's report against the answer key.

    Args:
        case: The case the player worked.
        answers: What the player submitted.
        engine_report: The engine's report for the same case.

    Returns:
        The score, with a review of every planted item.

    Raises:
        InvalidAnswerError: If the answers refer to unknown entries or put
            an entry in two groups.
    """
    _validate(case, answers)
    canon = {**canonical_ids(case.bank_statement), **canonical_ids(case.ledger)}
    planted_by_item = [
        (item, _planted_decision(item.kind, item.bank_ids, item.ledger_ids, canon))
        for item in case.answer_key.items
    ]
    planted = Counter(decision for _, decision in planted_by_item)
    player, one_sided = _player_decisions(case, answers, canon)
    engine = _engine_decisions(engine_report, canon)
    player_hits, engine_hits = planted & player, planted & engine

    # Hits are consumed as items are reviewed, so two identical planted
    # decisions (possible with duplicates) need two hits to both be correct.
    player_left, engine_left = Counter(player_hits), Counter(engine_hits)
    reviews = []
    for item, decision in planted_by_item:
        player_ok = player_left[decision] > 0
        engine_ok = engine_left[decision] > 0
        player_left[decision] -= player_ok
        engine_left[decision] -= engine_ok
        reviews.append(
            ItemReview(
                kind=item.kind,
                explanation=item.explanation,
                bank_ids=item.bank_ids,
                ledger_ids=item.ledger_ids,
                player_correct=player_ok,
                engine_correct=engine_ok,
            )
        )

    assigned = {i for b, led in answers.groups for i in b | led} | set(answers.exceptions)
    unassigned = sorted((case.bank_statement.ids | case.ledger.ids) - assigned)
    return GameScore(
        planted=sum(planted.values()),
        player_claimed=sum(player.values()),
        player_correct=sum(player_hits.values()),
        engine_claimed=sum(engine.values()),
        engine_correct=sum(engine_hits.values()),
        unassigned_ids=tuple(unassigned),
        one_sided_groups=one_sided,
        elapsed_seconds=answers.elapsed_seconds,
        items=tuple(reviews),
    )
