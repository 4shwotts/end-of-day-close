"""The naive pairwise reconciler, kept as a baseline for the benchmark.

This is how a reconciliation is often first written: for each bank entry,
scan the whole ledger for something that fits. It applies the same exact,
fee-adjusted and date-window rules as the engine, so the two can be compared
like for like, but it compares pairs one by one instead of indexing.
"""

from __future__ import annotations

from collections.abc import Mapping

from eod_close.domain.business_days import business_days_between
from eod_close.domain.entries import BankEntry, EntryKind, LedgerEntry
from eod_close.domain.processor import PaymentProcessor


class NaiveReconciler:
    """Pairs entries by scanning every ledger entry for each bank entry.

    Complexity: O(n·m) time and O(m) space. Each bank entry scans the
    ledger until it finds a fit, which on average is half of it.

    Args:
        processors: Known processors by code, for the fee rule.
        date_window_days: The same settlement window the engine uses.
    """

    def __init__(
        self, processors: Mapping[str, PaymentProcessor], date_window_days: int = 3
    ) -> None:
        self._processors = processors
        self._window = date_window_days

    def fits(self, bank: BankEntry, ledger: LedgerEntry) -> bool:
        """Return True if the pair satisfies the exact, fee or date-window rule."""
        if bank.reference != ledger.reference:
            return False
        gap = business_days_between(ledger.value_date, bank.value_date)
        if not 0 <= gap <= self._window:
            return False
        if bank.amount == ledger.amount:
            return True
        processor = self._processors.get(ledger.processor_code)
        return (
            processor is not None
            and processor.settles_net
            and ledger.kind is EntryKind.PAYMENT
            and bank.amount == processor.settlement_amount(ledger.amount)
        )

    def reconcile(self, bank: list[BankEntry], ledger: list[LedgerEntry]) -> list[tuple[str, str]]:
        """Pair each bank entry with the first unclaimed ledger entry that fits.

        Args:
            bank: Bank entries, in the order to process them.
            ledger: Ledger entries, in the order to scan them.

        Returns:
            (bank ID, ledger ID) pairs.
        """
        # Claimed entries are skipped rather than removed, so every scan
        # walks the same list and the cost per bank entry stays steady. The
        # benchmark relies on that to extrapolate large runs from a sample.
        claimed: set[str] = set()
        pairs = []
        for b in bank:
            for led in ledger:
                if led.entry_id not in claimed and self.fits(b, led):
                    claimed.add(led.entry_id)
                    pairs.append((b.entry_id, led.entry_id))
                    break
        return pairs
