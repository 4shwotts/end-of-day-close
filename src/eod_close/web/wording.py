"""The plain words the web pages use for the engine's terms.

The domain, the API, the command line and the README keep the precise
accounting terms (fee-adjusted, settlement delay, transposition). The pages
are for anyone, so they say the same things in everyday words: "Card fee",
"Arrived late", "Typo". Keeping every page's names in this one module means
they cannot drift apart from page to page.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from eod_close.domain.results import ExceptionReason, MatchRule
from eod_close.generator.answer_key import DiscrepancyKind

MATCH_NAMES: Final[Mapping[MatchRule, str]] = {
    MatchRule.EXACT: "Exact",
    MatchRule.FEE_ADJUSTED: "Card fee",
    MatchRule.DATE_WINDOW: "Arrived late",
    MatchRule.CURRENCY_TOLERANCE: "Euro rounding",
    MatchRule.BATCH: "Bundled payment",
    MatchRule.REFUND_LINK: "Refund",
    MatchRule.CHARGEBACK_LINK: "Disputed payment",
}
"""Each kind of match, in the order the pages list them."""

PROBLEM_NAMES: Final[Mapping[ExceptionReason, str]] = {
    ExceptionReason.LIKELY_TRANSPOSITION: "Typo",
    ExceptionReason.DUPLICATE: "Duplicate",
    ExceptionReason.MISSING_FROM_LEDGER: "Not in your records",
    ExceptionReason.MISSING_FROM_BANK: "Not on the bank statement",
}
"""Each kind of problem left for a person, in the order the pages list them."""

KIND_NAMES: Final[Mapping[DiscrepancyKind, str]] = {
    DiscrepancyKind.CLEAN: "Exact match",
    DiscrepancyKind.PROCESSOR_FEE: "Card fee",
    DiscrepancyKind.SETTLEMENT_DELAY: "Arrived late",
    DiscrepancyKind.FX_ROUNDING: "Euro rounding",
    DiscrepancyKind.BATCH_PAYOUT: "Bundled payment",
    DiscrepancyKind.REFUND: "Refund",
    DiscrepancyKind.CHARGEBACK: "Disputed payment",
    DiscrepancyKind.DUPLICATE: "Duplicate",
    DiscrepancyKind.MISSING_FROM_BANK: "Not on the bank statement",
    DiscrepancyKind.MISSING_FROM_LEDGER: "Not in your records",
    DiscrepancyKind.TRANSPOSITION: "Typo",
}
"""The training game's planted items, named as the player meets them."""

REASON_CHOICES: Final[Mapping[ExceptionReason, str]] = {
    ExceptionReason.MISSING_FROM_LEDGER: "Not in my records",
    ExceptionReason.MISSING_FROM_BANK: "Not on statement",
    ExceptionReason.DUPLICATE: "Duplicate",
    ExceptionReason.LIKELY_TRANSPOSITION: "Typo",
}
"""The options a player chooses from for a line with no partner."""
