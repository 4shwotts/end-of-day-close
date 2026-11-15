"""The matching strategies, and the order the engine runs them in."""

from eod_close.engine.base import MatchStrategy
from eod_close.engine.strategies.batch import BatchMatch, SubsetSearchLimitError, bounded_subset_sum
from eod_close.engine.strategies.currency_tolerance import CurrencyToleranceMatch
from eod_close.engine.strategies.date_window import DateWindowMatch
from eod_close.engine.strategies.duplicates import DuplicateDetection
from eod_close.engine.strategies.exact import ExactMatch
from eod_close.engine.strategies.fee_adjusted import FeeAdjustedMatch
from eod_close.engine.strategies.reversal import ReversalLink
from eod_close.engine.strategies.transposition import TranspositionDetection


def default_strategies() -> list[MatchStrategy]:
    """Return the strategies in the order the engine runs them.

    The order runs from strongest evidence to weakest, so a fuzzy rule only
    sees entries no stricter rule could explain. Batches come after the
    single-entry rules, because subset-sum is the costliest search and works
    best on a small pool. Duplicate and transposition detection come last,
    because they look for what the matching left behind.
    """
    return [
        ExactMatch(),
        FeeAdjustedMatch(),
        DateWindowMatch(),
        CurrencyToleranceMatch(),
        BatchMatch(),
        ReversalLink(),
        DuplicateDetection(),
        TranspositionDetection(),
    ]


__all__ = [
    "BatchMatch",
    "CurrencyToleranceMatch",
    "DateWindowMatch",
    "DuplicateDetection",
    "ExactMatch",
    "FeeAdjustedMatch",
    "ReversalLink",
    "SubsetSearchLimitError",
    "TranspositionDetection",
    "bounded_subset_sum",
    "default_strategies",
]
