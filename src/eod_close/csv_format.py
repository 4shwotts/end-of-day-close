"""Reading and writing bank statements and ledgers as CSV.

This is the documented file format for the generator's output and for
files uploaded to the web interface. Amounts are written and read
as decimal strings, never through float, and every row is validated so that a
malformed upload fails with the line number rather than part-way through
matching.

Bank columns:
    entry_id, value_date, reference, counterparty, description, amount, currency

Ledger columns:
    entry_id, value_date, reference, processor, kind, description, amount,
    currency, original_amount, original_currency, original_reference

Dates are ISO 8601 (``2026-11-13``). Amounts are signed decimals with no
thousands separators or currency symbols (``-15.20``).
"""

from __future__ import annotations

import csv
from collections.abc import Callable
from datetime import date
from typing import Final, TextIO

from eod_close.domain.entries import BankEntry, EntryKind, LedgerEntry
from eod_close.domain.money import Money
from eod_close.domain.statements import BankStatement, Ledger

BANK_COLUMNS: Final = (
    "entry_id",
    "value_date",
    "reference",
    "counterparty",
    "description",
    "amount",
    "currency",
)
LEDGER_COLUMNS: Final = (
    "entry_id",
    "value_date",
    "reference",
    "processor",
    "kind",
    "description",
    "amount",
    "currency",
    "original_amount",
    "original_currency",
    "original_reference",
)


class CsvFormatError(ValueError):
    """Raised when a CSV file does not follow the documented format."""


def write_bank_csv(statement: BankStatement, handle: TextIO) -> None:
    """Write a bank statement in the documented format.

    Args:
        statement: The statement to write.
        handle: A text stream opened with ``newline=""``.
    """
    writer = csv.writer(handle)
    writer.writerow(BANK_COLUMNS)
    for e in statement:
        writer.writerow(
            (
                e.entry_id,
                e.value_date.isoformat(),
                e.reference,
                e.counterparty,
                e.description,
                str(e.amount.amount),
                e.amount.currency,
            )
        )


def write_ledger_csv(ledger: Ledger, handle: TextIO) -> None:
    """Write a ledger in the documented format.

    Args:
        ledger: The ledger to write.
        handle: A text stream opened with ``newline=""``.
    """
    writer = csv.writer(handle)
    writer.writerow(LEDGER_COLUMNS)
    for e in ledger:
        original = e.original_amount
        writer.writerow(
            (
                e.entry_id,
                e.value_date.isoformat(),
                e.reference,
                e.processor_code,
                e.kind.value,
                e.description,
                str(e.amount.amount),
                e.amount.currency,
                str(original.amount) if original else "",
                original.currency if original else "",
                e.original_reference or "",
            )
        )


def read_bank_csv(
    handle: TextIO,
    *,
    bank_name: str = "Uploaded bank statement",
    account_name: str = "Uploaded account",
) -> BankStatement:
    """Read a bank statement in the documented format.

    Args:
        handle: A text stream of CSV.
        bank_name: Name to give the statement, since the file does not hold one.
        account_name: Account name to give the statement.

    Returns:
        The statement, dated by its latest entry.

    Raises:
        CsvFormatError: If the header or any row is invalid, or the file has
            no entries.
    """

    def parse(row: dict[str, str]) -> BankEntry:
        """Build one bank entry from a validated row."""
        return BankEntry(
            entry_id=row["entry_id"],
            reference=row["reference"],
            amount=Money.of(row["amount"], row["currency"]),
            value_date=date.fromisoformat(row["value_date"]),
            counterparty=row["counterparty"],
            description=row["description"],
        )

    entries = _read_rows(handle, BANK_COLUMNS, parse)
    try:
        return BankStatement(
            entries,
            bank_name=bank_name,
            account_name=account_name,
            statement_date=max(e.value_date for e in entries),
        )
    except ValueError as exc:
        raise CsvFormatError(str(exc)) from exc


def read_ledger_csv(handle: TextIO, *, entity_name: str = "Uploaded ledger") -> Ledger:
    """Read a ledger in the documented format.

    Args:
        handle: A text stream of CSV.
        entity_name: Company name to give the ledger.

    Returns:
        The ledger.

    Raises:
        CsvFormatError: If the header or any row is invalid, or the file has
            no entries.
    """

    def parse(row: dict[str, str]) -> LedgerEntry:
        """Build one ledger entry from a validated row."""
        original = (
            Money.of(row["original_amount"], row["original_currency"])
            if row["original_amount"]
            else None
        )
        return LedgerEntry(
            entry_id=row["entry_id"],
            reference=row["reference"],
            amount=Money.of(row["amount"], row["currency"]),
            value_date=date.fromisoformat(row["value_date"]),
            processor_code=row["processor"],
            kind=EntryKind(row["kind"]),
            original_amount=original,
            original_reference=row["original_reference"] or None,
            description=row["description"],
        )

    entries = _read_rows(handle, LEDGER_COLUMNS, parse)
    try:
        return Ledger(entries, entity_name=entity_name)
    except ValueError as exc:
        raise CsvFormatError(str(exc)) from exc


def _read_rows[T](
    handle: TextIO, columns: tuple[str, ...], parse: Callable[[dict[str, str]], T]
) -> list[T]:
    reader = csv.DictReader(handle)
    if tuple(reader.fieldnames or ()) != columns:
        raise CsvFormatError(f"Expected columns {', '.join(columns)}")
    rows = []
    for row in reader:
        # Fields are stripped because spreadsheet exports often pad cells.
        cleaned = {key: (value or "").strip() for key, value in row.items() if key is not None}
        try:
            rows.append(parse(cleaned))
        except (ValueError, TypeError) as exc:
            # reader.line_num counts physical lines, so it matches what the
            # user sees in an editor even when a field contains a newline.
            raise CsvFormatError(f"Line {reader.line_num}: {exc}") from exc
    if not rows:
        raise CsvFormatError("The file has no entries")
    return rows
