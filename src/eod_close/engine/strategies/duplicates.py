"""Strategy 7: flag entries that duplicate another entry on the same side."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from datetime import date

from eod_close.domain.entries import Transaction
from eod_close.domain.money import Money
from eod_close.domain.results import ExceptionReason
from eod_close.engine.base import Candidate, MatchContext, MatchStrategy


class DuplicateDetection(MatchStrategy):
    """Flag unclaimed entries identical to another entry in the same file.

    Runs after the matching strategies. By then one copy of a duplicated
    payment has normally been matched, and the leftover copy is identical
    to it in reference, amount and date. If no copy was matched, the copy
    with the lowest entry ID is kept as the genuine entry and the rest are
    flagged.

    Algorithm: group every entry on each side by (reference, amount, date)
    in a hash map, then walk the groups with more than one member.

    Complexity: O(n + m) time and space.

    Why this approach: comparing each leftover with every other entry is
    O(n²). Grouping by key finds every set of identical entries in one pass.
    """

    @property
    def name(self) -> str:
        """``"duplicates"``."""
        return "duplicates"

    def propose(self, context: MatchContext) -> Iterator[Candidate]:
        """Propose duplicate exceptions. See the class docstring."""
        yield from self._side(context.bank_statement.entries, context.is_bank_claimed, bank=True)
        yield from self._side(context.ledger.entries, context.is_ledger_claimed, bank=False)

    @staticmethod
    def _side(
        entries: Sequence[Transaction], is_claimed: Callable[[str], bool], *, bank: bool
    ) -> Iterator[Candidate]:
        groups: defaultdict[tuple[str, Money, date], list[Transaction]] = defaultdict(list)
        for entry in entries:
            groups[(entry.reference, entry.amount, entry.value_date)].append(entry)
        for group in groups.values():
            if len(group) < 2:
                continue
            claimed = [e for e in group if is_claimed(e.entry_id)]
            # Chosen by ID, not file position, so reordering the rows of a
            # file cannot change which copy is flagged.
            genuine = min(claimed or group, key=lambda e: e.entry_id)
            for entry in group:
                if entry is genuine or is_claimed(entry.entry_id):
                    continue
                ids = (entry.entry_id,)
                yield Candidate(
                    ids if bank else (),
                    () if bank else ids,
                    ExceptionReason.DUPLICATE,
                    0.9,
                    f"Duplicate of {genuine.entry_id}: same reference, amount and date.",
                    related_ids=(genuine.entry_id,),
                )
