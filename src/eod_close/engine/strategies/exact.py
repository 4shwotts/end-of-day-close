"""Strategy 1: exact matches on reference, amount and date."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from datetime import date

from eod_close.domain.entries import LedgerEntry
from eod_close.domain.money import Money
from eod_close.domain.results import MatchRule
from eod_close.engine.base import Candidate, MatchContext, MatchStrategy


class ExactMatch(MatchStrategy):
    """Pair entries with the same reference, amount and value date.

    Algorithm: build a hash map from (reference, amount, date) to the
    unclaimed ledger entries with that key, then look up each bank entry.

    Complexity: O(n + m) time and O(m) space.

    Why this approach: the naive method compares every bank entry with every
    ledger entry, which is O(n·m). Most entries on a normal day match
    exactly, so clearing them in linear time first shrinks the input for the
    costlier strategies that follow.
    """

    @property
    def name(self) -> str:
        """``"exact"``."""
        return "exact"

    def propose(self, context: MatchContext) -> Iterator[Candidate]:
        """Propose exact matches. See the class docstring for the algorithm."""
        index: defaultdict[tuple[str, Money, date], list[LedgerEntry]] = defaultdict(list)
        for entry in context.unmatched_ledger():
            # A blank reference carries no evidence, so it cannot make a
            # match exact; the date-window strategy handles those.
            if entry.reference:
                index[(entry.reference, entry.amount, entry.value_date)].append(entry)
        for bank in context.unmatched_bank():
            for ledger in index.get((bank.reference, bank.amount, bank.value_date), ()):
                yield Candidate(
                    (bank.entry_id,),
                    (ledger.entry_id,),
                    MatchRule.EXACT,
                    1.0,
                    "Same reference, amount and date.",
                )
