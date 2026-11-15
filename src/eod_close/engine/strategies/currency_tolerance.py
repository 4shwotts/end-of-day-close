"""Strategy 4: foreign-currency matches within a rounding tolerance."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterator

from eod_close.domain.results import MatchRule
from eod_close.engine.base import Candidate, MatchContext, MatchStrategy


class CurrencyToleranceMatch(MatchStrategy):
    """Match foreign-currency sales whose sterling value differs by rounding only.

    Only ledger entries with an ``original_amount`` in another currency are
    considered. Applying a tolerance to ordinary sterling payments would
    quietly absorb real errors, such as a mis-keyed penny, that someone
    needs to see.

    Algorithm: sort the unclaimed foreign-currency ledger entries by amount
    in pence. For each bank entry, binary-search for amounts within
    ``fx_tolerance`` either side, then keep those with an agreeing (or
    blank) reference inside the date window. Smaller differences rank
    higher.

    Complexity: O(m log m + n log m + K) time for K candidates; O(m) space.

    Why this approach: a hash lookup needs an exact key, and a tolerance
    has none. Sorting turns "within 5p" into a contiguous range, which
    bisect finds in logarithmic time instead of scanning every entry.
    """

    @property
    def name(self) -> str:
        """``"currency-tolerance"``."""
        return "currency-tolerance"

    def propose(self, context: MatchContext) -> Iterator[Candidate]:
        """Propose currency-tolerance matches. See the class docstring."""
        foreign = sorted(
            (e for e in context.unmatched_ledger() if e.original_amount is not None),
            key=lambda e: e.amount.minor_units,
        )
        if not foreign:
            return
        pence = [e.amount.minor_units for e in foreign]
        tolerance = context.config.fx_tolerance.minor_units
        for bank in context.unmatched_bank():
            target = bank.amount.minor_units
            start = bisect_left(pence, target - tolerance)
            end = bisect_right(pence, target + tolerance)
            for ledger in foreign[start:end]:
                if bank.reference and bank.reference != ledger.reference:
                    continue
                if context.settlement_gap(ledger.value_date, bank.value_date) is None:
                    continue
                difference = bank.amount - ledger.amount
                # Scaled so a zero difference scores 0.85 and one at the
                # tolerance limit 0.75, keeping every FX match below the
                # exact and fee rules.
                closeness = abs(difference.minor_units) / tolerance if tolerance else 0.0
                yield Candidate(
                    (bank.entry_id,),
                    (ledger.entry_id,),
                    MatchRule.CURRENCY_TOLERANCE,
                    0.85 - 0.1 * closeness,
                    f"{ledger.original_amount} converted with a rounding difference "
                    f"of {difference}.",
                )
