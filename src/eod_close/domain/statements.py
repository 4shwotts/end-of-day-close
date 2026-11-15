"""Collections of entries: the bank statement and the internal ledger.

These are the two inputs to a reconciliation. Both are immutable and index
their entries by ID, so the engine can look an entry up in constant time and
can rely on IDs being unique within each file.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import date

from eod_close.domain.entries import BankEntry, LedgerEntry, Transaction
from eod_close.domain.money import Money


class DuplicateEntryIdError(ValueError):
    """Raised when two entries in the same file share an ID."""


class EntryCollection[E: Transaction]:
    """An immutable, ordered collection of entries indexed by entry ID.

    Args:
        entries: The entries, in file order.
        currency: The account currency; every entry must use it.

    Raises:
        DuplicateEntryIdError: If two entries share an ID.
        ValueError: If an entry is in a currency other than ``currency``.
    """

    def __init__(self, entries: Iterable[E], *, currency: str = "GBP") -> None:
        self._currency = currency
        self._by_id: dict[str, E] = {}
        for entry in entries:
            if entry.entry_id in self._by_id:
                raise DuplicateEntryIdError(f"Entry ID {entry.entry_id!r} appears more than once")
            if entry.amount.currency != currency:
                raise ValueError(
                    f"{entry.entry_id}: {entry.amount.currency} entry in a {currency} file"
                )
            self._by_id[entry.entry_id] = entry
        # Kept separately from the dict so the order is explicit, not an
        # implementation detail of dict insertion order.
        self._entries: tuple[E, ...] = tuple(self._by_id.values())

    @property
    def currency(self) -> str:
        """The account currency."""
        return self._currency

    @property
    def entries(self) -> tuple[E, ...]:
        """All entries, in file order."""
        return self._entries

    @property
    def ids(self) -> frozenset[str]:
        """The set of entry IDs."""
        return frozenset(self._by_id)

    def get(self, entry_id: str) -> E:
        """Look up an entry by ID.

        Args:
            entry_id: The ID to find.

        Returns:
            The entry.

        Raises:
            KeyError: If no entry has that ID.
        """
        return self._by_id[entry_id]

    def total(self) -> Money:
        """Return the signed sum of every entry."""
        return Money.total((entry.amount for entry in self._entries), self._currency)

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[E]:
        return iter(self._entries)

    def __contains__(self, entry_id: object) -> bool:
        return entry_id in self._by_id


class Ledger(EntryCollection[LedgerEntry]):
    """The business's own record of payments, refunds and chargebacks.

    Args:
        entries: The ledger entries.
        entity_name: The company whose ledger this is.
        currency: The functional currency the ledger is kept in.
    """

    def __init__(
        self, entries: Iterable[LedgerEntry], *, entity_name: str, currency: str = "GBP"
    ) -> None:
        super().__init__(entries, currency=currency)
        self.entity_name = entity_name


class BankStatement(EntryCollection[BankEntry]):
    """The bank's record of money in and out of one account.

    Args:
        entries: The statement lines.
        bank_name: The bank that issued the statement.
        account_name: The account the statement covers.
        statement_date: The business day the statement is for.
        currency: The account currency.
    """

    def __init__(
        self,
        entries: Iterable[BankEntry],
        *,
        bank_name: str,
        account_name: str,
        statement_date: date,
        currency: str = "GBP",
    ) -> None:
        super().__init__(entries, currency=currency)
        self.bank_name = bank_name
        self.account_name = account_name
        self.statement_date = statement_date
