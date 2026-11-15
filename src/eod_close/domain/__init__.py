"""The reconciliation domain model.

Money, entries, the two input files, processors and the reconciliation
results. Everything else in the project builds on these types.
"""

from eod_close.domain.entries import BankEntry, EntryKind, LedgerEntry, Side, Transaction
from eod_close.domain.money import CurrencyMismatchError, Money
from eod_close.domain.processor import FICTIONAL_PROCESSORS, FeeRule, PaymentProcessor
from eod_close.domain.results import (
    ExceptionItem,
    ExceptionReason,
    Match,
    MatchRule,
    ReconciliationReport,
)
from eod_close.domain.statements import BankStatement, DuplicateEntryIdError, Ledger

__all__ = [
    "FICTIONAL_PROCESSORS",
    "BankEntry",
    "BankStatement",
    "CurrencyMismatchError",
    "DuplicateEntryIdError",
    "EntryKind",
    "ExceptionItem",
    "ExceptionReason",
    "FeeRule",
    "Ledger",
    "LedgerEntry",
    "Match",
    "MatchRule",
    "Money",
    "PaymentProcessor",
    "ReconciliationReport",
    "Side",
    "Transaction",
]
