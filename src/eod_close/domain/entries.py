"""Bank and ledger entries.

A reconciliation compares two independent records of the same money: what the
bank says arrived or left (``BankEntry``) and what the business booked
(``LedgerEntry``). Both share the ``Transaction`` base, so matching strategies
can treat them uniformly where the rules allow.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from eod_close.domain.money import Money


class Side(StrEnum):
    """Which of the two records an entry belongs to."""

    BANK = "bank"
    LEDGER = "ledger"


class EntryKind(StrEnum):
    """What a ledger entry represents.

    Refunds and chargebacks are both money going back to a customer, but they
    are handled differently: a refund is the merchant's choice, while a
    chargeback is forced by the card issuer and carries an extra fee.
    """

    PAYMENT = "payment"
    REFUND = "refund"
    CHARGEBACK = "chargeback"


@dataclass(frozen=True, kw_only=True)
class Transaction(ABC):
    """Fields common to every bank and ledger entry.

    Attributes:
        entry_id: Identifier unique within its file, for example ``"B0042"``.
        reference: The payment reference, such as an order or invoice number.
        amount: Signed amount; credits are positive and debits negative.
        value_date: For bank entries, the date the money moved. For ledger
            entries, the booking date.
        description: Free text, as it appears in the source file.

    Raises:
        ValueError: If ``entry_id`` is blank or ``amount`` is zero.
    """

    entry_id: str
    reference: str
    amount: Money
    value_date: date
    description: str = ""

    def __post_init__(self) -> None:
        if not self.entry_id.strip():
            raise ValueError("entry_id must not be blank")
        # A zero line moves no money, so it can never break a reconciliation;
        # accepting one would only add noise to the matching.
        if self.amount.is_zero():
            raise ValueError(f"{self.entry_id}: amount must not be zero")

    @property
    @abstractmethod
    def side(self) -> Side:
        """Which record this entry belongs to."""


@dataclass(frozen=True, kw_only=True)
class BankEntry(Transaction):
    """One line on the bank statement.

    Attributes:
        counterparty: Who the money came from or went to, as the bank reports
            it. For processor settlements this is the processor's code.
    """

    counterparty: str = ""

    @property
    def side(self) -> Side:
        """Always ``Side.BANK``."""
        return Side.BANK

    @property
    def is_credit(self) -> bool:
        """True if money arrived in the account."""
        return self.amount.is_positive()


@dataclass(frozen=True, kw_only=True)
class LedgerEntry(Transaction):
    """One line in the internal ledger.

    Attributes:
        processor_code: The processor the payment was taken through.
        kind: Payment, refund or chargeback.
        original_amount: For foreign-currency sales, the amount the customer
            paid in their own currency. ``amount`` holds the sterling value
            booked at the daily reference rate.
        original_reference: For refunds and chargebacks, the reference of the
            payment being reversed.

    Raises:
        ValueError: If the sign of ``amount`` does not suit ``kind``, if a
            reversal lacks ``original_reference``, or if ``original_amount``
            is in the same currency as ``amount`` or has the opposite sign.
    """

    processor_code: str
    kind: EntryKind = EntryKind.PAYMENT
    original_amount: Money | None = None
    original_reference: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.processor_code.strip():
            raise ValueError(f"{self.entry_id}: processor_code must not be blank")
        if self.kind is EntryKind.PAYMENT:
            if not self.amount.is_positive():
                raise ValueError(f"{self.entry_id}: a payment must be positive")
            if self.original_reference is not None:
                raise ValueError(f"{self.entry_id}: a payment has no original reference")
        else:
            if not self.amount.is_negative():
                raise ValueError(f"{self.entry_id}: a {self.kind} must be negative")
            if not self.original_reference:
                raise ValueError(f"{self.entry_id}: a {self.kind} needs an original reference")
        if self.original_amount is not None:
            if self.original_amount.currency == self.amount.currency:
                raise ValueError(f"{self.entry_id}: original amount must be a foreign currency")
            if self.original_amount.is_negative() != self.amount.is_negative():
                raise ValueError(f"{self.entry_id}: original amount has the wrong sign")

    @property
    def side(self) -> Side:
        """Always ``Side.LEDGER``."""
        return Side.LEDGER
