"""Scores the engine's decisions against the generator's answer key.

Both sides are reduced to the same shape, a *decision*: a resolution (a match
rule or an exception reason) plus the set of bank and ledger entries it
covers. A decision is correct only if the engine produced exactly the
planted decision, with the same entries and the same resolution. Pairing the
right entries under the wrong rule counts as wrong, because the explanation
shown to the player would be wrong.

Per problem type:
    precision = correct decisions / decisions the engine made of that type
    recall    = correct decisions / decisions planted of that type
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Final

from eod_close.domain.entries import Side, Transaction
from eod_close.domain.results import ExceptionReason, MatchRule, ReconciliationReport
from eod_close.engine.engine import ReconciliationEngine
from eod_close.generator.answer_key import AnswerKey, DiscrepancyKind
from eod_close.generator.case_generator import Case, CaseGenerator
from eod_close.generator.difficulty import Difficulty

type Resolution = MatchRule | ExceptionReason

EXPECTED_RESOLUTION: Final[Mapping[DiscrepancyKind, Resolution]] = {
    DiscrepancyKind.CLEAN: MatchRule.EXACT,
    DiscrepancyKind.PROCESSOR_FEE: MatchRule.FEE_ADJUSTED,
    DiscrepancyKind.SETTLEMENT_DELAY: MatchRule.DATE_WINDOW,
    DiscrepancyKind.FX_ROUNDING: MatchRule.CURRENCY_TOLERANCE,
    DiscrepancyKind.BATCH_PAYOUT: MatchRule.BATCH,
    DiscrepancyKind.REFUND: MatchRule.REFUND_LINK,
    DiscrepancyKind.CHARGEBACK: MatchRule.CHARGEBACK_LINK,
    DiscrepancyKind.DUPLICATE: ExceptionReason.DUPLICATE,
    DiscrepancyKind.MISSING_FROM_BANK: ExceptionReason.MISSING_FROM_BANK,
    DiscrepancyKind.MISSING_FROM_LEDGER: ExceptionReason.MISSING_FROM_LEDGER,
    DiscrepancyKind.TRANSPOSITION: ExceptionReason.LIKELY_TRANSPOSITION,
}
"""The resolution the engine should give each kind of planted item."""

_KIND_FOR: Final = {resolution: kind for kind, resolution in EXPECTED_RESOLUTION.items()}


@dataclass(frozen=True)
class Decision:
    """One resolution and the entries it covers.

    Attributes:
        resolution: The match rule or exception reason.
        bank_ids: Canonical bank entry IDs covered.
        ledger_ids: Canonical ledger entry IDs covered.
    """

    resolution: Resolution
    bank_ids: frozenset[str]
    ledger_ids: frozenset[str]

    @property
    def kind(self) -> DiscrepancyKind:
        """The problem type this resolution corresponds to."""
        return _KIND_FOR[self.resolution]


@dataclass
class KindScore:
    """Counts for one problem type, from which precision and recall follow.

    Attributes:
        planted: Decisions of this type in the answer key.
        claimed: Decisions of this type the engine made.
        correct: Decisions in both.
    """

    planted: int = 0
    claimed: int = 0
    correct: int = 0

    @property
    def precision(self) -> float | None:
        """Share of the engine's decisions that were right; None if it made none."""
        return self.correct / self.claimed if self.claimed else None

    @property
    def recall(self) -> float | None:
        """Share of planted decisions the engine found; None if none were planted."""
        return self.correct / self.planted if self.planted else None

    def add(self, other: KindScore) -> None:
        """Accumulate another score's counts into this one."""
        self.planted += other.planted
        self.claimed += other.claimed
        self.correct += other.correct


@dataclass
class AccuracyResult:
    """Scores per problem type, for one case or summed over many.

    Attributes:
        by_kind: The score for each problem type.
        cases: How many cases were scored.
    """

    by_kind: dict[DiscrepancyKind, KindScore] = field(
        default_factory=lambda: {kind: KindScore() for kind in DiscrepancyKind}
    )
    cases: int = 0

    @property
    def overall(self) -> KindScore:
        """Counts summed over every problem type (micro-averaged)."""
        total = KindScore()
        for score in self.by_kind.values():
            total.add(score)
        return total

    def add(self, other: AccuracyResult) -> None:
        """Accumulate another result into this one."""
        for kind, score in other.by_kind.items():
            self.by_kind[kind].add(score)
        self.cases += other.cases


def canonical_ids(entries: Iterable[Transaction]) -> dict[str, str]:
    """Map each entry ID to the lowest ID of the entries identical to it.

    Two copies of a duplicated payment are indistinguishable, so it does
    not matter which copy is matched and which is flagged. Mapping both to
    one canonical ID lets either choice score as correct.
    """
    groups: dict[tuple[object, ...], list[str]] = {}
    for e in entries:
        groups.setdefault((e.reference, e.amount, e.value_date), []).append(e.entry_id)
    return {entry_id: min(ids) for ids in groups.values() for entry_id in ids}


def planted_decisions(answer_key: AnswerKey, canon: Mapping[str, str]) -> Counter[Decision]:
    """Convert an answer key into decisions.

    Args:
        answer_key: The generator's key.
        canon: Canonical ID for every entry ID.

    Returns:
        A multiset of decisions; duplicates can make two decisions identical.
    """
    return Counter(
        Decision(
            EXPECTED_RESOLUTION[item.kind],
            frozenset(canon[i] for i in item.bank_ids),
            frozenset(canon[i] for i in item.ledger_ids),
        )
        for item in answer_key.items
    )


def engine_decisions(report: ReconciliationReport, canon: Mapping[str, str]) -> Counter[Decision]:
    """Convert an engine report into decisions.

    A transposition flags two entries but is a single finding, so its
    exception items are merged into one decision. Every other exception
    covers just its own entry; a duplicate's original is related to it but
    belongs to the original's own match.

    Args:
        report: The engine's report.
        canon: Canonical ID for every entry ID.

    Returns:
        A multiset of decisions.
    """
    decisions: Counter[Decision] = Counter(
        Decision(
            m.rule,
            frozenset(canon[e.entry_id] for e in m.bank_entries),
            frozenset(canon[e.entry_id] for e in m.ledger_entries),
        )
        for m in report.matches
    )
    transpositions: set[Decision] = set()
    for item in report.exceptions:
        if item.reason is ExceptionReason.LIKELY_TRANSPOSITION:
            ids = {item.entry.entry_id, *item.related_entry_ids}
            transpositions.add(
                Decision(
                    item.reason,
                    frozenset(canon[i] for i in ids if i in report.bank_statement),
                    frozenset(canon[i] for i in ids if i in report.ledger),
                )
            )
            continue
        own = frozenset({canon[item.entry.entry_id]})
        is_bank = item.side is Side.BANK
        decisions[
            Decision(item.reason, own if is_bank else frozenset(), frozenset() if is_bank else own)
        ] += 1
    decisions.update(transpositions)
    return decisions


def evaluate(case: Case, report: ReconciliationReport) -> AccuracyResult:
    """Score one report against its case's answer key.

    Args:
        case: The generated case, including its answer key.
        report: The engine's report for that case.

    Returns:
        Precision and recall counts per problem type.
    """
    canon = {
        **canonical_ids(case.bank_statement),
        **canonical_ids(case.ledger),
    }
    planted = planted_decisions(case.answer_key, canon)
    claimed = engine_decisions(report, canon)
    correct = planted & claimed
    result = AccuracyResult(cases=1)
    for counter, attribute in ((planted, "planted"), (claimed, "claimed"), (correct, "correct")):
        for decision, count in counter.items():
            score = result.by_kind[decision.kind]
            setattr(score, attribute, getattr(score, attribute) + count)
    return result


def evaluate_seeds(
    difficulty: Difficulty, seeds: Iterable[int], engine: ReconciliationEngine | None = None
) -> AccuracyResult:
    """Generate, reconcile and score a set of seeded cases.

    Args:
        difficulty: The level to generate.
        seeds: The seeds to use.
        engine: The engine to score; defaults to the default configuration.

    Returns:
        The summed result over every seed.
    """
    reconciler = engine or ReconciliationEngine()
    generator = CaseGenerator()
    total = AccuracyResult()
    for seed in seeds:
        case = generator.generate(seed, difficulty)
        total.add(evaluate(case, reconciler.reconcile(case.bank_statement, case.ledger)))
    return total
