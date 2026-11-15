"""Strategy 3: same-amount matches that settled within the date window."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Iterator
from datetime import date

from eod_close.domain.business_days import add_business_days, business_days_between
from eod_close.domain.entries import LedgerEntry
from eod_close.domain.money import Money
from eod_close.domain.results import MatchRule
from eod_close.engine.base import Candidate, MatchContext, MatchStrategy


class DateWindowMatch(MatchStrategy):
    """Match entries of equal amount whose dates differ by a settlement delay.

    Algorithm: group unclaimed ledger entries by amount and sort each group
    by booking date. For each bank entry, binary-search (``bisect``) its
    amount group for bookings between ``date_window_days`` business days
    before the bank date and the bank date itself.

    The bank reference must agree with the ledger reference, or be blank:
    banks sometimes truncate or drop references, but two different
    references are two different payments, however close the amounts.
    When the reference is blank, the bank's remitter must be the ledger
    entry's processor, since that is the only evidence left besides amount
    and date. Closer dates and an agreeing reference rank higher.

    Complexity: O(m log m) to sort, then O(log m + k) per bank entry for k
    entries in its window, so O((n + m) log m + K) overall for K
    candidates. O(m) space.

    Why this approach: scanning every ledger entry for each bank entry is
    O(n·m). Grouping by amount discards most pairs immediately, and sorted
    dates let bisect find the window edges without scanning the group.
    """

    @property
    def name(self) -> str:
        """``"date-window"``."""
        return "date-window"

    def propose(self, context: MatchContext) -> Iterator[Candidate]:
        """Propose date-window matches. See the class docstring."""
        groups: defaultdict[Money, list[LedgerEntry]] = defaultdict(list)
        for entry in context.unmatched_ledger():
            groups[entry.amount].append(entry)
        # Parallel lists: bisect searches the dates, and the same index
        # gives the entry. Ties on date keep file order (sort is stable).
        dates: dict[Money, list[date]] = {}
        for amount, entries in groups.items():
            entries.sort(key=lambda e: e.value_date)
            dates[amount] = [e.value_date for e in entries]

        window = context.config.date_window_days
        for bank in context.unmatched_bank():
            same_amount = groups.get(bank.amount)
            if not same_amount:
                continue
            day_list = dates[bank.amount]
            start = bisect_left(day_list, add_business_days(bank.value_date, -window))
            end = bisect_right(day_list, bank.value_date)
            for ledger in same_amount[start:end]:
                if bank.reference:
                    if bank.reference != ledger.reference:
                        continue
                elif bank.counterparty and bank.counterparty != ledger.processor_code:
                    # Common price points mean many sales share an amount;
                    # without this check a reference-less Otterline credit
                    # could claim a wallet sale or a duplicated invoice.
                    continue
                gap = business_days_between(ledger.value_date, bank.value_date)
                base = 0.9 if bank.reference else 0.6
                yield Candidate(
                    (bank.entry_id,),
                    (ledger.entry_id,),
                    MatchRule.DATE_WINDOW,
                    base - 0.02 * gap,
                    f"Settled T+{gap}: booked {ledger.value_date:%d %b}, "
                    f"received {bank.value_date:%d %b}.",
                )
