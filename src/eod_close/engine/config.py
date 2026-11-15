"""Tunable limits for the matching engine.

Every threshold that trades recall for precision lives here, so it can be
changed in one place and tested at its exact boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

from eod_close.domain.money import Money


@dataclass(frozen=True)
class EngineConfig:
    """Thresholds used by the matching strategies.

    Attributes:
        date_window_days: How many business days a bank entry may trail its
            ledger entry. Three covers the slowest nominal settlement (T+2)
            plus a day for payments taken after the processor's cut-off. A
            wider window would let unrelated payments of the same amount pair
            up.
        fx_tolerance: The largest absolute difference accepted for
            foreign-currency entries. Five pence absorbs per-step rounding
            by the processor without hiding a genuine pricing error.
        max_batch_candidates: The most ledger entries the batch strategy will
            search over for one payout.
        max_subset_states: The most partial sums the subset-sum search may
            hold before giving up.

    Raises:
        ValueError: If any limit is negative, or a search limit is zero.
    """

    date_window_days: int = 3
    fx_tolerance: Money = Money.of("0.05")
    max_batch_candidates: int = 30
    max_subset_states: int = 250_000

    def __post_init__(self) -> None:
        if self.date_window_days < 0 or self.fx_tolerance.is_negative():
            raise ValueError("Windows and tolerances cannot be negative")
        if self.max_batch_candidates < 1 or self.max_subset_states < 1:
            raise ValueError("Search limits must be positive")
