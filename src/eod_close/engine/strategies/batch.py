"""Strategy 5: batch payouts, where one bank credit covers several sales."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator, Sequence
from datetime import date

from eod_close.domain.entries import BankEntry, EntryKind, LedgerEntry
from eod_close.domain.processor import PaymentProcessor
from eod_close.domain.results import MatchRule
from eod_close.engine.base import Candidate, MatchContext, MatchStrategy


class SubsetSearchLimitError(RuntimeError):
    """Raised when a subset-sum search would exceed its state limit."""


def bounded_subset_sum(
    values: Sequence[int], target: int, max_states: int
) -> tuple[int, ...] | None:
    """Find a subset of ``values`` that sums exactly to ``target``.

    Algorithm: dynamic programming over reachable sums. A dictionary maps
    each sum reached so far to the (previous sum, item index) that first
    reached it. Each item extends a snapshot of the existing sums, so it is
    used at most once (0/1 subset-sum). Sums above ``target`` are discarded,
    since the values are positive. The chosen indices are recovered by
    walking the back-pointers from ``target`` to zero.

    Complexity: O(k·S) time and O(S) space for k values, where S is the
    number of distinct reachable sums, at most min(target, 2^k, max_states).

    Why this approach: trying every subset is O(2^k), so 30 sales would
    mean a billion subsets. DP on sums is pseudo-polynomial, bounded by the
    target in pence, and the explicit state limit guarantees it cannot blow
    up on a large payout.

    Args:
        values: Positive amounts in minor units. Non-positive values are
            ignored.
        target: The amount to reach, in minor units.
        max_states: The most distinct sums to hold before giving up.

    Returns:
        Indices into ``values`` of one subset summing to ``target``, in
        ascending order, or None if no subset does.

    Raises:
        SubsetSearchLimitError: If the search would hold more than
            ``max_states`` sums.
    """
    if target <= 0:
        return None
    parent: dict[int, tuple[int, int]] = {0: (-1, -1)}
    for index, value in enumerate(values):
        if value <= 0 or value > target:
            continue
        for reached in list(parent):
            new_sum = reached + value
            if new_sum > target or new_sum in parent:
                continue
            parent[new_sum] = (reached, index)
            if new_sum == target:
                chosen = []
                cursor = target
                while cursor:
                    cursor, item = parent[cursor]
                    chosen.append(item)
                return tuple(sorted(chosen))
            if len(parent) > max_states:
                raise SubsetSearchLimitError(f"More than {max_states} partial sums")
    return None


class BatchMatch(MatchStrategy):
    """Match a processor's single payout to the set of sales it covers.

    Only bank credits from processors that batch their payouts are
    considered, and only that processor's unclaimed sales inside the date
    window can belong to them. The sales are converted to the net amounts
    the processor pays, and ``bounded_subset_sum`` looks for a set that adds
    up to the payout.

    Sales from the same booking day are tried first, one day at a time,
    because processors pay each day's sales together: a same-day subset is
    far more likely to be the true one than a coincidental mix of days. A
    payout that equals a whole day's sales ranks highest. Only if no single
    day works is the whole window searched, with lower confidence.

    Complexity: per payout, O(k·S) time and O(S) space as for
    ``bounded_subset_sum``, with k capped at ``max_batch_candidates`` and S
    at ``max_subset_states``. Pools larger than the cap are skipped, and
    their entries fall through to the exceptions queue for a person to
    resolve.

    Why this approach: see ``bounded_subset_sum``. Partitioning by day
    first keeps k small in the common case, so the search is usually
    trivial.

    Limitation: when two different subsets both sum to the payout, the
    strategy returns one of them deterministically (by entry ID), which may
    not be the true one. Only a processor's payout report can settle that.
    """

    @property
    def name(self) -> str:
        """``"batch"``."""
        return "batch"

    def propose(self, context: MatchContext) -> Iterator[Candidate]:
        """Propose batch matches. See the class docstring."""
        pools: defaultdict[str, list[LedgerEntry]] = defaultdict(list)
        for entry in context.unmatched_ledger():
            processor = context.processor(entry.processor_code)
            if entry.kind is EntryKind.PAYMENT and processor and processor.batches_payouts:
                pools[processor.code].append(entry)

        for bank in context.unmatched_bank():
            processor = context.processor(bank.counterparty)
            if processor is None or not processor.batches_payouts or not bank.is_credit:
                continue
            window = [
                e
                for e in pools.get(processor.code, ())
                if context.settlement_gap(e.value_date, bank.value_date) is not None
            ]
            yield from self._propose_for(context, bank, processor, window)

    def _propose_for(
        self,
        context: MatchContext,
        bank: BankEntry,
        processor: PaymentProcessor,
        window: list[LedgerEntry],
    ) -> Iterator[Candidate]:
        by_day: defaultdict[date, list[LedgerEntry]] = defaultdict(list)
        for entry in window:
            by_day[entry.value_date].append(entry)
        found_same_day = False
        for day in sorted(by_day, reverse=True):
            group = by_day[day]
            subset = self._search(context, group, bank, processor)
            if subset is not None:
                found_same_day = True
                whole_day = len(subset) == len(group)
                yield self._candidate(bank, processor, subset, 0.9 if whole_day else 0.8)
        if not found_same_day and len(by_day) > 1:
            subset = self._search(context, window, bank, processor)
            if subset is not None:
                yield self._candidate(bank, processor, subset, 0.6)

    @staticmethod
    def _search(
        context: MatchContext,
        pool: list[LedgerEntry],
        bank: BankEntry,
        processor: PaymentProcessor,
    ) -> list[LedgerEntry] | None:
        if not pool or len(pool) > context.config.max_batch_candidates:
            return None
        # The search returns the first subset it reaches, so the pool is put
        # in ID order; otherwise row order would decide between two subsets
        # that both fit.
        pool = sorted(pool, key=lambda e: e.entry_id)
        nets = [processor.settlement_amount(e.amount).minor_units for e in pool]
        try:
            indices = bounded_subset_sum(
                nets, bank.amount.minor_units, context.config.max_subset_states
            )
        except SubsetSearchLimitError:
            return None
        return None if indices is None else [pool[i] for i in indices]

    @staticmethod
    def _candidate(
        bank: BankEntry, processor: PaymentProcessor, subset: list[LedgerEntry], confidence: float
    ) -> Candidate:
        return Candidate(
            (bank.entry_id,),
            tuple(sorted(e.entry_id for e in subset)),
            MatchRule.BATCH,
            confidence,
            f"{processor.name} payout covering {len(subset)} sales, net of "
            f"its {processor.fee_rule.describe()} fee.",
        )
