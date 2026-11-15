"""Strategy 8: flag pairs whose amounts differ by transposed digits."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator

from eod_close.domain.entries import BankEntry
from eod_close.domain.results import ExceptionReason
from eod_close.engine.base import Candidate, MatchContext, MatchStrategy


class TranspositionDetection(MatchStrategy):
    """Flag same-reference pairs whose difference suggests swapped digits.

    Swapping adjacent digits a and b at place values 10^(k+1) and 10^k
    changes a number by (a - b)(10^(k+1) - 10^k) = 9(a - b)10^k, which is
    always a multiple of 9. This is the bookkeeper's "divide by nine" test.
    One in nine random differences also passes it, so the strategy requires
    the same reference too, and ranks a pair higher when the two amounts use
    exactly the same digits.

    The pair is flagged as an exception rather than matched, because the
    ledger amount is wrong and needs correcting, not just pairing.

    Algorithm: index unclaimed bank entries by reference, then test each
    unclaimed ledger entry against the bank entries with its reference.

    Complexity: O(n + m) time for distinct references (O(n·m) in the
    degenerate case where every entry shares one reference); O(n) space.

    Why this approach: comparing all pairs is O(n·m), and nearly every pair
    has unrelated references. The reference index narrows the search to the
    pairs that could plausibly be the same payment.
    """

    @property
    def name(self) -> str:
        """``"transposition"``."""
        return "transposition"

    def propose(self, context: MatchContext) -> Iterator[Candidate]:
        """Propose transposition exceptions. See the class docstring."""
        by_reference: defaultdict[str, list[BankEntry]] = defaultdict(list)
        for bank in context.unmatched_bank():
            if bank.reference:
                by_reference[bank.reference].append(bank)
        for ledger in context.unmatched_ledger():
            for bank in by_reference.get(ledger.reference, ()):
                if bank.amount.is_negative() != ledger.amount.is_negative():
                    continue
                difference = bank.amount.minor_units - ledger.amount.minor_units
                if difference == 0 or difference % 9:
                    continue
                same_digits = sorted(str(abs(bank.amount.minor_units))) == sorted(
                    str(abs(ledger.amount.minor_units))
                )
                yield Candidate(
                    (bank.entry_id,),
                    (ledger.entry_id,),
                    ExceptionReason.LIKELY_TRANSPOSITION,
                    0.8 if same_digits else 0.5,
                    f"Ledger shows {ledger.amount}, bank shows {bank.amount}: the "
                    f"difference of {abs(bank.amount - ledger.amount)} is divisible by 9, "
                    "which suggests transposed digits.",
                )
