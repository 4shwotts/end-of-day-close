"""Strategy 2: matches where the processor deducted its fee before paying out."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator

from eod_close.domain.entries import EntryKind, LedgerEntry
from eod_close.domain.money import Money
from eod_close.domain.processor import PaymentProcessor
from eod_close.domain.results import MatchRule
from eod_close.engine.base import Candidate, MatchContext, MatchStrategy


class FeeAdjustedMatch(MatchStrategy):
    """Match net bank receipts to gross ledger sales using each processor's fee rule.

    Algorithm: for every unclaimed ledger payment taken through a
    net-settling processor, compute the amount the bank should receive and
    index the entry by (reference, expected net amount). Each bank entry is
    then a hash lookup, filtered to the date window.

    Complexity: O(n + m) time and O(m) space.

    Why this approach: the alternative is to compute the difference for
    every bank and ledger pair and test whether it looks like a fee, which
    is O(n·m) and guesses at the fee. The processor's published rule gives
    the exact expected amount, so one lookup suffices and nothing is
    guessed.
    """

    @property
    def name(self) -> str:
        """``"fee-adjusted"``."""
        return "fee-adjusted"

    def propose(self, context: MatchContext) -> Iterator[Candidate]:
        """Propose fee-adjusted matches. See the class docstring."""
        index: defaultdict[tuple[str, Money], list[tuple[LedgerEntry, PaymentProcessor]]] = (
            defaultdict(list)
        )
        for entry in context.unmatched_ledger():
            processor = context.processor(entry.processor_code)
            # Batching processors are left to the batch strategy: their single
            # sales never appear on the bank statement on their own.
            if (
                entry.kind is not EntryKind.PAYMENT
                or processor is None
                or not processor.settles_net
                or processor.batches_payouts
            ):
                continue
            index[(entry.reference, processor.settlement_amount(entry.amount))].append(
                (entry, processor)
            )
        for bank in context.unmatched_bank():
            for ledger, processor in index.get((bank.reference, bank.amount), ()):
                gap = context.settlement_gap(ledger.value_date, bank.value_date)
                if gap is None:
                    continue
                fee = processor.fee_for(ledger.amount)
                # Settling on the processor's nominal day is the likeliest
                # story; each day early or late costs a little confidence.
                confidence = 0.95 - 0.01 * abs(gap - processor.settlement_delay_days)
                yield Candidate(
                    (bank.entry_id,),
                    (ledger.entry_id,),
                    MatchRule.FEE_ADJUSTED,
                    confidence,
                    f"{processor.name} deducted a fee of {fee} ({processor.fee_rule.describe()}).",
                )
