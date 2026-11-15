"""Strategy 6: link refunds and chargebacks to the payment they reverse."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator

from eod_close.domain.entries import EntryKind, LedgerEntry
from eod_close.domain.money import Money
from eod_close.domain.results import MatchRule
from eod_close.engine.base import Candidate, MatchContext, MatchStrategy


def reference_suffixes(reference: str) -> list[str]:
    """Return the reference and each hyphen-delimited suffix of it.

    Processors often prefix the original order reference with their own
    code, so ``"CB-ORD-1234"`` should be found under ``"ORD-1234"``.

    Args:
        reference: A bank reference.

    Returns:
        For ``"CB-ORD-1234"``: ``["CB-ORD-1234", "ORD-1234", "1234"]``.
    """
    parts = reference.split("-")
    return ["-".join(parts[i:]) for i in range(len(parts)) if parts[i]]


class ReversalLink(MatchStrategy):
    """Match bank debits to the ledger refunds and chargebacks they pay.

    The two sides never share a reference: the ledger books a reversal
    under its own reference (``RFD-…`` or ``CHB-…``), while the processor
    reports it against the original order. The link is the ledger entry's
    ``original_reference``.

    A refund must match the bank debit exactly. A chargeback's bank debit
    is larger by the processor's chargeback fee, so the expected debit is
    the ledger amount less that fee.

    Algorithm: index unclaimed reversals by original reference. For each
    bank debit, look up the reference and each hyphen-delimited suffix of
    it, then check the amount rule and the date window.

    Complexity: O(n·p + m) time, where p is the number of hyphen-separated
    parts in a reference (a small constant in practice); O(m) space.

    Why this approach: searching every reversal for one whose original
    reference appears somewhere in the bank text would be O(n·m) substring
    checks. Trying the few suffixes of each bank reference keeps every
    check a hash lookup.
    """

    @property
    def name(self) -> str:
        """``"reversal"``."""
        return "reversal"

    def propose(self, context: MatchContext) -> Iterator[Candidate]:
        """Propose refund and chargeback links. See the class docstring."""
        index: defaultdict[str, list[LedgerEntry]] = defaultdict(list)
        for entry in context.unmatched_ledger():
            if entry.kind is not EntryKind.PAYMENT and entry.original_reference:
                index[entry.original_reference].append(entry)
        if not index:
            return
        for bank in context.unmatched_bank():
            if bank.is_credit:
                continue
            for key in reference_suffixes(bank.reference):
                for ledger in index.get(key, ()):
                    if context.settlement_gap(ledger.value_date, bank.value_date) is None:
                        continue
                    candidate = self._check(context, bank.entry_id, bank.amount, ledger)
                    if candidate is not None:
                        yield candidate

    @staticmethod
    def _check(
        context: MatchContext, bank_id: str, bank_amount: Money, ledger: LedgerEntry
    ) -> Candidate | None:
        if ledger.kind is EntryKind.REFUND and bank_amount == ledger.amount:
            return Candidate(
                (bank_id,),
                (ledger.entry_id,),
                MatchRule.REFUND_LINK,
                0.9,
                f"Refund of order {ledger.original_reference}.",
            )
        processor = context.processor(ledger.processor_code)
        if (
            ledger.kind is EntryKind.CHARGEBACK
            and processor is not None
            and bank_amount == ledger.amount - processor.chargeback_fee
        ):
            return Candidate(
                (bank_id,),
                (ledger.entry_id,),
                MatchRule.CHARGEBACK_LINK,
                0.9,
                f"Chargeback on order {ledger.original_reference}, plus a "
                f"{processor.chargeback_fee} chargeback fee.",
            )
        return None
