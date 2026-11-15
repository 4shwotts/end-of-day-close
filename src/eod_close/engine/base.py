"""The strategy interface and the shared state that strategies read.

Each matching strategy implements ``MatchStrategy``: given a ``MatchContext``
holding the entries that are still unclaimed, it proposes ``Candidate``
pairings. Strategies never claim entries themselves. The engine ranks their
proposals and decides which to accept, which keeps every strategy a pure
function of the context and easy to test on its own.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

from eod_close.domain.business_days import business_days_between
from eod_close.domain.entries import BankEntry, LedgerEntry
from eod_close.domain.processor import PaymentProcessor
from eod_close.domain.results import ExceptionReason, MatchRule
from eod_close.domain.statements import BankStatement, Ledger
from eod_close.engine.config import EngineConfig


@dataclass(frozen=True)
class Candidate:
    """A proposed resolution for one or more entries.

    A candidate either matches entries (``resolution`` is a ``MatchRule``) or
    flags them as exceptions (``resolution`` is an ``ExceptionReason``).

    Attributes:
        bank_ids: Bank entries the candidate would claim.
        ledger_ids: Ledger entries the candidate would claim.
        resolution: The match rule or exception reason.
        confidence: Ranking score from 0 to 1; higher wins.
        note: Explanation for people.
        related_ids: Entries referred to but not claimed, such as the
            original of a duplicate.

    Raises:
        ValueError: If the candidate claims nothing, a match lacks a side,
            or ``confidence`` is out of range.
    """

    bank_ids: tuple[str, ...]
    ledger_ids: tuple[str, ...]
    resolution: MatchRule | ExceptionReason
    confidence: float
    note: str = ""
    related_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.bank_ids and not self.ledger_ids:
            raise ValueError("A candidate must claim at least one entry")
        if isinstance(self.resolution, MatchRule) and not (self.bank_ids and self.ledger_ids):
            raise ValueError("A match candidate needs entries on both sides")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"Confidence must be between 0 and 1, not {self.confidence}")

    @property
    def is_match(self) -> bool:
        """True if accepting the candidate creates a match."""
        return isinstance(self.resolution, MatchRule)


class MatchContext:
    """The inputs to a reconciliation and which entries are already claimed.

    Args:
        bank_statement: The bank side.
        ledger: The ledger side.
        processors: Known processors by code. Entries naming an unknown
            processor are left to the strategies that do not need one.
        config: Engine thresholds.
    """

    def __init__(
        self,
        bank_statement: BankStatement,
        ledger: Ledger,
        processors: Mapping[str, PaymentProcessor],
        config: EngineConfig,
    ) -> None:
        self.bank_statement = bank_statement
        self.ledger = ledger
        self.config = config
        self._processors = processors
        self._claimed_bank: set[str] = set()
        self._claimed_ledger: set[str] = set()

    def processor(self, code: str) -> PaymentProcessor | None:
        """Return the processor with this code, or None if it is unknown."""
        return self._processors.get(code)

    def unmatched_bank(self) -> list[BankEntry]:
        """Unclaimed bank entries, in file order."""
        return [e for e in self.bank_statement if e.entry_id not in self._claimed_bank]

    def unmatched_ledger(self) -> list[LedgerEntry]:
        """Unclaimed ledger entries, in file order."""
        return [e for e in self.ledger if e.entry_id not in self._claimed_ledger]

    def is_bank_claimed(self, entry_id: str) -> bool:
        """True if the bank entry is already matched or flagged."""
        return entry_id in self._claimed_bank

    def is_ledger_claimed(self, entry_id: str) -> bool:
        """True if the ledger entry is already matched or flagged."""
        return entry_id in self._claimed_ledger

    def claim(self, candidate: Candidate) -> bool:
        """Claim every entry in the candidate, or none of them.

        All-or-nothing matters for batches: claiming three of a batch's four
        sales would leave the fourth stranded and the payout mismatched.

        Args:
            candidate: The candidate to accept.

        Returns:
            True if the entries were claimed; False if any was already taken.
        """
        if any(i in self._claimed_bank for i in candidate.bank_ids) or any(
            i in self._claimed_ledger for i in candidate.ledger_ids
        ):
            return False
        self._claimed_bank.update(candidate.bank_ids)
        self._claimed_ledger.update(candidate.ledger_ids)
        return True

    def settlement_gap(self, booked: date, received: date) -> int | None:
        """Business days from booking to receipt, if inside the date window.

        Only lags in one direction are accepted: money cannot settle before
        the sale it pays for was taken.

        Args:
            booked: The ledger entry's booking date.
            received: The bank entry's value date.

        Returns:
            The gap in business days, or None if outside the window.
        """
        gap = business_days_between(booked, received)
        return gap if 0 <= gap <= self.config.date_window_days else None


class MatchStrategy(ABC):
    """One way of explaining why bank and ledger entries belong together.

    Subclasses document their algorithm and its time and space complexity.
    In the complexities, n is the number of unclaimed bank entries and m
    the number of unclaimed ledger entries.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """A short name for logs and the How it works race."""

    @abstractmethod
    def propose(self, context: MatchContext) -> Iterable[Candidate]:
        """Propose candidates among the unclaimed entries.

        Args:
            context: The inputs and the current claims. Strategies must not
                modify it.

        Returns:
            Candidates in any order; they may overlap, and the engine
            resolves conflicts by confidence.
        """
