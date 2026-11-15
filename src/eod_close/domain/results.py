"""The outcome of a reconciliation: matches, exceptions and the report.

The matching engine produces these objects, and the game compares a
player's answers with them. ``ReconciliationReport`` enforces the central
invariant of reconciliation: every entry is accounted for at most once,
either in one match or in one exception.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from eod_close.domain.entries import BankEntry, LedgerEntry, Side
from eod_close.domain.money import Money
from eod_close.domain.statements import BankStatement, Ledger


class MatchRule(StrEnum):
    """The rule that justified a match."""

    EXACT = "exact"
    FEE_ADJUSTED = "fee-adjusted"
    DATE_WINDOW = "date-window"
    CURRENCY_TOLERANCE = "currency-tolerance"
    BATCH = "batch"
    REFUND_LINK = "refund-link"
    CHARGEBACK_LINK = "chargeback-link"

    @property
    def label(self) -> str:
        """A human-readable label, for example ``"Fee-adjusted"``."""
        return _RULE_LABELS[self]


class ExceptionReason(StrEnum):
    """Why an entry could not be matched."""

    MISSING_FROM_BANK = "missing-from-bank"
    MISSING_FROM_LEDGER = "missing-from-ledger"
    DUPLICATE = "duplicate"
    LIKELY_TRANSPOSITION = "likely-transposition"

    @property
    def label(self) -> str:
        """A human-readable label, for example ``"Missing from bank"``."""
        return _REASON_LABELS[self]

    @property
    def side(self) -> Side | None:
        """The only side this reason can apply to, or None if it applies to both."""
        return {
            ExceptionReason.MISSING_FROM_BANK: Side.LEDGER,
            ExceptionReason.MISSING_FROM_LEDGER: Side.BANK,
        }.get(self)


_RULE_LABELS = {
    MatchRule.EXACT: "Exact",
    MatchRule.FEE_ADJUSTED: "Fee-adjusted",
    MatchRule.DATE_WINDOW: "Settlement delay",
    MatchRule.CURRENCY_TOLERANCE: "FX rounding",
    MatchRule.BATCH: "Batch payout",
    MatchRule.REFUND_LINK: "Refund",
    MatchRule.CHARGEBACK_LINK: "Chargeback",
}
_REASON_LABELS = {
    ExceptionReason.MISSING_FROM_BANK: "Missing from bank",
    ExceptionReason.MISSING_FROM_LEDGER: "Missing from ledger",
    ExceptionReason.DUPLICATE: "Duplicate",
    ExceptionReason.LIKELY_TRANSPOSITION: "Likely transposition",
}


@dataclass(frozen=True)
class Match:
    """One or more bank entries reconciled to one or more ledger entries.

    Attributes:
        bank_entries: The bank side; several entries only for split payments.
        ledger_entries: The ledger side; several entries for batch payouts.
        rule: The rule that justifies the match.
        confidence: A ranking score from 0 to 1. It is a heuristic weight,
            not an amount of money, so a float is appropriate here.
        note: A short explanation for people, such as the fee deducted.

    Raises:
        ValueError: If either side is empty or ``confidence`` is out of range.
    """

    bank_entries: tuple[BankEntry, ...]
    ledger_entries: tuple[LedgerEntry, ...]
    rule: MatchRule
    confidence: float
    note: str = ""

    def __post_init__(self) -> None:
        if not self.bank_entries or not self.ledger_entries:
            raise ValueError("A match needs at least one entry on each side")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"Confidence must be between 0 and 1, not {self.confidence}")

    @property
    def bank_total(self) -> Money:
        """The sum of the bank side."""
        return Money.total(e.amount for e in self.bank_entries)

    @property
    def ledger_total(self) -> Money:
        """The sum of the ledger side."""
        return Money.total(e.amount for e in self.ledger_entries)

    @property
    def difference(self) -> Money:
        """Bank total minus ledger total, such as a deducted fee."""
        return self.bank_total - self.ledger_total


@dataclass(frozen=True)
class ExceptionItem:
    """An entry that could not be matched, with the reason.

    Named ``ExceptionItem`` rather than ``Exception`` to avoid shadowing the
    Python built-in.

    Attributes:
        entry: The unmatched entry.
        reason: Why it was not matched.
        note: A short explanation for people.
        related_entry_ids: Other entries involved, such as the original of a
            duplicate or the counterpart of a likely typo.
    """

    entry: BankEntry | LedgerEntry
    reason: ExceptionReason
    note: str = ""
    related_entry_ids: tuple[str, ...] = ()

    @property
    def side(self) -> Side:
        """Which record the unmatched entry belongs to."""
        return self.entry.side


@dataclass(frozen=True)
class ReconciliationReport:
    """The result of reconciling one business day.

    Attributes:
        business_date: The day being closed.
        bank_statement: The bank side of the reconciliation.
        ledger: The ledger side of the reconciliation.
        matches: The matches found.
        exceptions: The entries left unmatched, each with a reason.

    Raises:
        ValueError: If any entry is used more than once, or a match or
            exception refers to an entry that is not in the inputs.
    """

    business_date: date
    bank_statement: BankStatement
    ledger: Ledger
    matches: Sequence[Match]
    exceptions: Sequence[ExceptionItem]
    _bank_used: frozenset[str] = field(init=False, repr=False)
    _ledger_used: frozenset[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        bank_ids = [e.entry_id for m in self.matches for e in m.bank_entries]
        ledger_ids = [e.entry_id for m in self.matches for e in m.ledger_entries]
        for item in self.exceptions:
            target = bank_ids if item.side is Side.BANK else ledger_ids
            target.append(item.entry.entry_id)
        # Using an entry twice would let one payment explain two others,
        # which is how a reconciliation appears to balance when it does not.
        for side, ids, known in (
            ("bank", bank_ids, self.bank_statement.ids),
            ("ledger", ledger_ids, self.ledger.ids),
        ):
            repeated = sorted(i for i, n in Counter(ids).items() if n > 1)
            if repeated:
                raise ValueError(f"{side} entries used more than once: {repeated}")
            unknown = sorted(set(ids) - known)
            if unknown:
                raise ValueError(f"{side} entries not in the input: {unknown}")
        object.__setattr__(self, "_bank_used", frozenset(bank_ids))
        object.__setattr__(self, "_ledger_used", frozenset(ledger_ids))

    @property
    def unaccounted_bank_ids(self) -> frozenset[str]:
        """Bank entries in neither a match nor an exception."""
        return self.bank_statement.ids - self._bank_used

    @property
    def unaccounted_ledger_ids(self) -> frozenset[str]:
        """Ledger entries in neither a match nor an exception."""
        return self.ledger.ids - self._ledger_used

    @property
    def is_balanced(self) -> bool:
        """True if every entry is matched or has an exception explaining it."""
        return not self.unaccounted_bank_ids and not self.unaccounted_ledger_ids

    @property
    def matched_entry_count(self) -> int:
        """The number of entries, on both sides, that are in a match."""
        return sum(len(m.bank_entries) + len(m.ledger_entries) for m in self.matches)

    def exceptions_by_reason(self) -> dict[ExceptionReason, int]:
        """Count the exceptions for each reason."""
        return dict(Counter(item.reason for item in self.exceptions))
