"""Difficulty levels and the case profile each one produces.

A profile sets how many items a case contains and how likely each kind of
discrepancy is. Harder levels add problem types gradually, in roughly the
order a new analyst would learn them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from eod_close.generator.answer_key import DiscrepancyKind as K

MAX_BATCHES: Final = 3


class Difficulty(StrEnum):
    """The four difficulty levels, from easiest to hardest."""

    INTERN = "intern"
    ANALYST = "analyst"
    SENIOR_ANALYST = "senior-analyst"
    HEAD_OF_OPS = "head-of-ops"

    @property
    def label(self) -> str:
        """The display name, for example ``"Head of Ops"``."""
        return _LABELS[self]


_LABELS: Final = {
    Difficulty.INTERN: "Intern",
    Difficulty.ANALYST: "Analyst",
    Difficulty.SENIOR_ANALYST: "Senior Analyst",
    Difficulty.HEAD_OF_OPS: "Head of Ops",
}


@dataclass(frozen=True)
class DifficultyProfile:
    """How a case at one difficulty level is built.

    Attributes:
        difficulty: The level this profile is for.
        item_count: How many planted items the case contains. A batch is one
            item but several ledger entries, so entry counts are higher.
        weights: Relative likelihood of each kind. Kinds not listed never
            appear.
        max_batch_size: The most ledger entries in one batch payout.
        max_batches: The most batch payouts in one case.
        blank_reference_rate: Chance that a settlement-delay bank line
            arrives without its reference.
        price_point_rate: Chance that an amount is a common retail price
            such as £24.99, so unrelated payments share amounts.
        held_back_rate: Chance that a batch payout leaves out one of the
            day's sales.

    The three rates are probabilities, not amounts, so they are floats.
    They add the ambiguity that makes real reconciliations hard, and they
    are zero at the two easier levels.

    Raises:
        ValueError: If ``item_count`` is smaller than the number of kinds
            (every listed kind is guaranteed to appear at least once), if
            ``max_batches`` exceeds ``MAX_BATCHES``, or if a rate is outside
            0 to 1.
    """

    difficulty: Difficulty
    item_count: int
    weights: tuple[tuple[K, int], ...]
    max_batch_size: int = 0
    max_batches: int = 0
    blank_reference_rate: float = 0.0
    price_point_rate: float = 0.0
    held_back_rate: float = 0.0

    def __post_init__(self) -> None:
        if self.item_count < len(self.weights):
            raise ValueError("item_count must allow every listed kind to appear once")
        # Each batch covers its own booking day, one to three business days
        # back, so a fourth batch would fall outside the settlement window.
        if self.max_batches > MAX_BATCHES:
            raise ValueError(f"max_batches cannot exceed {MAX_BATCHES}")
        rates = (self.blank_reference_rate, self.price_point_rate, self.held_back_rate)
        if not all(0.0 <= rate <= 1.0 for rate in rates):
            raise ValueError("Rates must be between 0 and 1")

    @property
    def kinds(self) -> tuple[K, ...]:
        """The kinds this profile can produce."""
        return tuple(kind for kind, _ in self.weights)


_PROFILES: Final = {
    Difficulty.INTERN: DifficultyProfile(
        Difficulty.INTERN,
        item_count=15,
        weights=((K.CLEAN, 60), (K.SETTLEMENT_DELAY, 20), (K.PROCESSOR_FEE, 20)),
    ),
    Difficulty.ANALYST: DifficultyProfile(
        Difficulty.ANALYST,
        item_count=40,
        weights=(
            (K.CLEAN, 45),
            (K.SETTLEMENT_DELAY, 15),
            (K.PROCESSOR_FEE, 15),
            (K.FX_ROUNDING, 10),
            (K.REFUND, 5),
            (K.MISSING_FROM_BANK, 5),
            (K.MISSING_FROM_LEDGER, 5),
        ),
    ),
    Difficulty.SENIOR_ANALYST: DifficultyProfile(
        Difficulty.SENIOR_ANALYST,
        item_count=100,
        weights=(
            (K.CLEAN, 40),
            (K.SETTLEMENT_DELAY, 12),
            (K.PROCESSOR_FEE, 12),
            (K.FX_ROUNDING, 8),
            (K.BATCH_PAYOUT, 4),
            (K.REFUND, 5),
            (K.CHARGEBACK, 4),
            (K.DUPLICATE, 5),
            (K.MISSING_FROM_BANK, 5),
            (K.MISSING_FROM_LEDGER, 5),
        ),
        max_batch_size=5,
        max_batches=2,
        blank_reference_rate=0.25,
        price_point_rate=0.2,
    ),
    Difficulty.HEAD_OF_OPS: DifficultyProfile(
        Difficulty.HEAD_OF_OPS,
        item_count=300,
        weights=(
            (K.CLEAN, 35),
            (K.SETTLEMENT_DELAY, 12),
            (K.PROCESSOR_FEE, 12),
            (K.FX_ROUNDING, 8),
            (K.BATCH_PAYOUT, 4),
            (K.REFUND, 5),
            (K.CHARGEBACK, 4),
            (K.DUPLICATE, 5),
            (K.MISSING_FROM_BANK, 5),
            (K.MISSING_FROM_LEDGER, 5),
            (K.TRANSPOSITION, 5),
        ),
        max_batch_size=8,
        max_batches=3,
        blank_reference_rate=0.4,
        price_point_rate=0.35,
        held_back_rate=0.5,
    ),
}


def profile_for(difficulty: Difficulty) -> DifficultyProfile:
    """Return the profile for a difficulty level."""
    return _PROFILES[difficulty]
