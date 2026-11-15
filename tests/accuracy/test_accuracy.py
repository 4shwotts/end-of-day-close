"""Accuracy regression test: fails if the engine drops below the set thresholds.

The seeds are a held-out set (1000-1019), separate from the seeds used while
developing and fixing the engine, so the thresholds are not tuned to the
cases they are checked on. Generation is deterministic, so these results
only move when the engine or the generator changes.

Thresholds:
    Intern and Analyst have no noise, so anything below 100% is a regression.
    Senior Analyst and Head of Ops include deliberate ambiguity (blank
    references, shared price points, held-back batch sales) that no
    rule-based engine can always resolve.
"""

from functools import cache
from typing import Final

import pytest

from eod_close.evaluation import AccuracyResult, evaluate_seeds
from eod_close.generator import Difficulty, DiscrepancyKind

pytestmark = pytest.mark.accuracy

SEEDS: Final = range(1000, 1020)

LEVEL_THRESHOLDS: Final = {
    Difficulty.INTERN: 1.0,
    Difficulty.ANALYST: 1.0,
    Difficulty.SENIOR_ANALYST: 0.995,
    Difficulty.HEAD_OF_OPS: 0.985,
}

DEFAULT_KIND_THRESHOLD: Final = 0.95
KIND_THRESHOLDS: Final = {
    # About 100 batches in the sample, so one miss is a whole percentage point.
    DiscrepancyKind.BATCH_PAYOUT: 0.90,
    # Absorbs the other side of every ambiguous blank-reference settlement.
    DiscrepancyKind.MISSING_FROM_BANK: 0.93,
}


@cache
def results() -> dict[Difficulty, AccuracyResult]:
    return {difficulty: evaluate_seeds(difficulty, SEEDS) for difficulty in Difficulty}


@pytest.mark.parametrize("difficulty", list(Difficulty))
def test_overall_accuracy_per_level(difficulty: Difficulty) -> None:
    overall = results()[difficulty].overall
    threshold = LEVEL_THRESHOLDS[difficulty]
    assert overall.precision is not None
    assert overall.recall is not None
    assert overall.precision >= threshold, f"precision {overall.precision:.4f}"
    assert overall.recall >= threshold, f"recall {overall.recall:.4f}"


@pytest.mark.parametrize("kind", list(DiscrepancyKind))
def test_accuracy_per_problem_type(kind: DiscrepancyKind) -> None:
    """Scored across all levels combined, so every type has a useful sample."""
    combined = AccuracyResult()
    for result in results().values():
        combined.add(result)
    score = combined.by_kind[kind]
    threshold = KIND_THRESHOLDS.get(kind, DEFAULT_KIND_THRESHOLD)
    assert score.planted > 0, "every problem type should appear in the sample"
    assert score.precision is not None
    assert score.recall is not None
    assert score.precision >= threshold, f"precision {score.precision:.4f}"
    assert score.recall >= threshold, f"recall {score.recall:.4f}"
