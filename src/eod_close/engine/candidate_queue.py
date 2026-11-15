"""A priority queue that ranks candidate matches by confidence.

Strategies often propose overlapping candidates: two ledger entries for the
same amount inside one bank entry's date window, or a sale that fits two
batch payouts. The queue hands them back best first, so the engine can accept
the strongest candidate and skip any later one that reuses a claimed entry.
"""

from __future__ import annotations

import heapq
from collections.abc import Iterable, Iterator
from itertools import count

from eod_close.engine.base import Candidate

type _HeapItem = tuple[float, tuple[str, ...], tuple[str, ...], int, Candidate]


class CandidateQueue:
    """A max-priority queue of candidates, built on ``heapq``.

    Ordering is by confidence, highest first. Ties are broken by the
    candidate's bank IDs, then ledger IDs, in ascending order. Breaking ties
    on stable IDs rather than on the order strategies happened to propose
    them makes the result deterministic: the same two files always reconcile
    the same way, which tests, replays and the player's comparison rely on.

    Complexity: O(log k) per push and pop for k queued candidates; O(k) space.

    Args:
        candidates: Initial contents. Building from an iterable is O(k).
    """

    def __init__(self, candidates: Iterable[Candidate] = ()) -> None:
        # A unique sequence number sits before the candidate in each tuple, so
        # heapq never falls through to comparing Candidate objects themselves.
        self._sequence = count()
        self._heap: list[_HeapItem] = [self._item(c) for c in candidates]
        heapq.heapify(self._heap)

    def _item(self, candidate: Candidate) -> _HeapItem:
        # heapq is a min-heap, so confidence is negated to pop the highest first.
        return (
            -candidate.confidence,
            candidate.bank_ids,
            candidate.ledger_ids,
            next(self._sequence),
            candidate,
        )

    def push(self, candidate: Candidate) -> None:
        """Add a candidate."""
        heapq.heappush(self._heap, self._item(candidate))

    def pop(self) -> Candidate:
        """Remove and return the best candidate.

        Raises:
            IndexError: If the queue is empty.
        """
        return heapq.heappop(self._heap)[-1]

    def drain(self) -> Iterator[Candidate]:
        """Yield every candidate, best first, emptying the queue."""
        while self._heap:
            yield self.pop()

    def __len__(self) -> int:
        return len(self._heap)
