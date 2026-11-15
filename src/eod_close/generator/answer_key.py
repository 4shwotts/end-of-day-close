"""The answer key: what the generator planted in a case.

Every entry in a generated case belongs to exactly one planted item. The
accuracy evaluator scores the matching engine against this key, and the game uses the
explanations on the close-the-day screen. The key never feeds back into the
engine itself; the engine has to work from the files alone.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class DiscrepancyKind(StrEnum):
    """The kinds of item the generator can plant.

    ``CLEAN`` is the control group: a pair that should match exactly. Every
    other kind is a reason the bank and the ledger disagree in real payments
    operations; see the generator for why each one happens.
    """

    CLEAN = "clean"
    SETTLEMENT_DELAY = "settlement-delay"
    PROCESSOR_FEE = "processor-fee"
    FX_ROUNDING = "fx-rounding"
    BATCH_PAYOUT = "batch-payout"
    REFUND = "refund"
    CHARGEBACK = "chargeback"
    DUPLICATE = "duplicate"
    MISSING_FROM_BANK = "missing-from-bank"
    MISSING_FROM_LEDGER = "missing-from-ledger"
    TRANSPOSITION = "transposition"

    @property
    def expects_match(self) -> bool:
        """True if the correct resolution is a match rather than an exception."""
        return self not in _EXCEPTION_KINDS

    @property
    def label(self) -> str:
        """A human-readable label, for example ``"Settlement delay"``."""
        return self.value.replace("-", " ").capitalize().replace("Fx", "FX")


_EXCEPTION_KINDS = frozenset(
    {
        DiscrepancyKind.DUPLICATE,
        DiscrepancyKind.MISSING_FROM_BANK,
        DiscrepancyKind.MISSING_FROM_LEDGER,
        DiscrepancyKind.TRANSPOSITION,
    }
)


@dataclass(frozen=True)
class PlantedItem:
    """One planted item and the entries it covers.

    Attributes:
        kind: What was planted.
        bank_ids: The bank entries involved, possibly none.
        ledger_ids: The ledger entries involved, possibly none.
        explanation: Why the entries disagree, written for the player.
        related_ids: Entries this item refers to without owning them, such as
            the original of a duplicate.
    """

    kind: DiscrepancyKind
    bank_ids: tuple[str, ...]
    ledger_ids: tuple[str, ...]
    explanation: str
    related_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation."""
        return {
            "kind": self.kind.value,
            "bank_ids": list(self.bank_ids),
            "ledger_ids": list(self.ledger_ids),
            "related_ids": list(self.related_ids),
            "explanation": self.explanation,
        }


@dataclass(frozen=True)
class AnswerKey:
    """Every planted item in one case.

    Attributes:
        case_id: The case this key belongs to.
        items: The planted items.
    """

    case_id: str
    items: tuple[PlantedItem, ...]

    def by_kind(self, kind: DiscrepancyKind) -> tuple[PlantedItem, ...]:
        """Return the items of one kind."""
        return tuple(item for item in self.items if item.kind is kind)

    def kinds_present(self) -> frozenset[DiscrepancyKind]:
        """Return the kinds that appear at least once."""
        return frozenset(item.kind for item in self.items)

    def item_for(self, entry_id: str) -> PlantedItem:
        """Return the item that owns an entry.

        Args:
            entry_id: A bank or ledger entry ID.

        Returns:
            The planted item covering that entry.

        Raises:
            KeyError: If no item covers the entry.
        """
        for item in self.items:
            if entry_id in item.bank_ids or entry_id in item.ledger_ids:
                return item
        raise KeyError(entry_id)

    def to_json(self) -> str:
        """Serialise the key as indented JSON."""
        payload = {"case_id": self.case_id, "items": [item.to_dict() for item in self.items]}
        return json.dumps(payload, indent=2, ensure_ascii=False)
