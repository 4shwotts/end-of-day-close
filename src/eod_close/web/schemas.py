"""Request and response models for the JSON API.

Amounts are always strings holding an exact decimal, such as ``"98.40"``.
JSON numbers are parsed as floats by most clients, which would undo the
project's rule that money is never a float, so they are never used for money.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from eod_close.domain.entries import BankEntry, LedgerEntry
from eod_close.domain.results import ExceptionReason, MatchRule, ReconciliationReport
from eod_close.game import GameScore
from eod_close.generator import Case, Difficulty, DiscrepancyKind

MAX_SEED = 1_000_000_000


class BankEntryOut(BaseModel):
    """One bank statement line."""

    entry_id: str
    value_date: date
    reference: str
    counterparty: str
    description: str
    amount: str = Field(description="Signed exact decimal, for example '-15.20'.")
    currency: str

    @classmethod
    def of(cls, entry: BankEntry) -> BankEntryOut:
        """Convert a domain entry."""
        return cls(
            entry_id=entry.entry_id,
            value_date=entry.value_date,
            reference=entry.reference,
            counterparty=entry.counterparty,
            description=entry.description,
            amount=str(entry.amount.amount),
            currency=entry.amount.currency,
        )


class LedgerEntryOut(BaseModel):
    """One ledger line."""

    entry_id: str
    value_date: date
    reference: str
    processor: str
    kind: str
    description: str
    amount: str = Field(description="Signed exact decimal, for example '100.00'.")
    currency: str
    original_amount: str | None = None
    original_currency: str | None = None
    original_reference: str | None = None

    @classmethod
    def of(cls, entry: LedgerEntry) -> LedgerEntryOut:
        """Convert a domain entry."""
        original = entry.original_amount
        return cls(
            entry_id=entry.entry_id,
            value_date=entry.value_date,
            reference=entry.reference,
            processor=entry.processor_code,
            kind=entry.kind.value,
            description=entry.description,
            amount=str(entry.amount.amount),
            currency=entry.amount.currency,
            original_amount=str(original.amount) if original else None,
            original_currency=original.currency if original else None,
            original_reference=entry.original_reference,
        )


class CaseRequest(BaseModel):
    """Parameters for generating a case."""

    difficulty: Difficulty = Difficulty.ANALYST
    seed: int | None = Field(
        default=None,
        ge=0,
        le=MAX_SEED,
        description="Leave empty for a random day; the response includes the seed used.",
    )


class CaseOut(BaseModel):
    """A generated business day. The answer key is never included."""

    case_id: str
    difficulty: Difficulty
    seed: int
    business_date: date
    bank: list[BankEntryOut]
    ledger: list[LedgerEntryOut]

    @classmethod
    def of(cls, case: Case) -> CaseOut:
        """Convert a generated case, leaving out its answer key."""
        return cls(
            case_id=case.case_id,
            difficulty=case.difficulty,
            seed=case.seed,
            business_date=case.business_date,
            bank=[BankEntryOut.of(e) for e in case.bank_statement],
            ledger=[LedgerEntryOut.of(e) for e in case.ledger],
        )


class MatchOut(BaseModel):
    """One match in a reconciliation."""

    rule: MatchRule
    confidence: float = Field(description="Ranking score from 0 to 1; not an amount.")
    bank_ids: list[str]
    ledger_ids: list[str]
    difference: str = Field(description="Bank total minus ledger total, as an exact decimal.")
    note: str


class ExceptionOut(BaseModel):
    """One unmatched entry and the reason."""

    entry_id: str
    side: Literal["bank", "ledger"]
    reason: ExceptionReason
    note: str
    related_entry_ids: list[str]


class ReconcileOut(BaseModel):
    """The engine's report."""

    business_date: date
    balanced: bool
    matches: list[MatchOut]
    exceptions: list[ExceptionOut]

    @classmethod
    def of(cls, report: ReconciliationReport) -> ReconcileOut:
        """Convert an engine report."""
        return cls(
            business_date=report.business_date,
            balanced=report.is_balanced,
            matches=[
                MatchOut(
                    rule=m.rule,
                    confidence=round(m.confidence, 3),
                    bank_ids=[e.entry_id for e in m.bank_entries],
                    ledger_ids=[e.entry_id for e in m.ledger_entries],
                    difference=str(m.difference.amount),
                    note=m.note,
                )
                for m in report.matches
            ],
            exceptions=[
                ExceptionOut(
                    entry_id=x.entry.entry_id,
                    side=x.side.value,
                    reason=x.reason,
                    note=x.note,
                    related_entry_ids=list(x.related_entry_ids),
                )
                for x in report.exceptions
            ],
        )


class GroupIn(BaseModel):
    """Entries the player has linked together."""

    bank_ids: list[str] = Field(default_factory=list)
    ledger_ids: list[str] = Field(default_factory=list)


class ExceptionIn(BaseModel):
    """An entry the player has marked as an exception."""

    entry_id: str
    reason: ExceptionReason


class ScoreRequest(BaseModel):
    """A player's answers for one case."""

    groups: list[GroupIn] = Field(default_factory=list)
    exceptions: list[ExceptionIn] = Field(default_factory=list)
    elapsed_seconds: int | None = Field(default=None, ge=0)


class ItemReviewOut(BaseModel):
    """How one planted discrepancy was handled."""

    kind: DiscrepancyKind
    explanation: str
    bank_ids: list[str]
    ledger_ids: list[str]
    player_correct: bool
    engine_correct: bool


class ScoreOut(BaseModel):
    """The player's score, compared with the engine's."""

    accuracy: float
    engine_accuracy: float
    balanced: bool
    verdict: str
    planted: int
    correct: int
    claimed: int
    unassigned_ids: list[str]
    elapsed_seconds: int | None
    items: list[ItemReviewOut]

    @classmethod
    def of(cls, score: GameScore) -> ScoreOut:
        """Convert a game score."""
        return cls(
            accuracy=round(score.accuracy, 4),
            engine_accuracy=round(score.engine_accuracy, 4),
            balanced=score.balanced,
            verdict=score.verdict,
            planted=score.planted,
            correct=score.player_correct,
            claimed=score.player_claimed,
            unassigned_ids=list(score.unassigned_ids),
            elapsed_seconds=score.elapsed_seconds,
            items=[
                ItemReviewOut(
                    kind=i.kind,
                    explanation=i.explanation,
                    bank_ids=list(i.bank_ids),
                    ledger_ids=list(i.ledger_ids),
                    player_correct=i.player_correct,
                    engine_correct=i.engine_correct,
                )
                for i in score.items
            ],
        )
