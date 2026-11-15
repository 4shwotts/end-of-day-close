"""Turns the engine's report into what the reconcile page shows.

The engine's own notes are short and technical ("Settled T+1"). This module
rewrites each result for an operations analyst: a plain-English reason for
every match, a reason and a suggested next step for everything that needs
review, and a summary of how far apart the bank and the ledger are. It also
writes the same content as a CSV report.

Nothing here changes a decision. It only reads the report, so the engine,
the accuracy figures and the tests of the engine are unaffected.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final

from eod_close.domain.business_days import business_days_between
from eod_close.domain.entries import BankEntry, LedgerEntry
from eod_close.domain.money import Money
from eod_close.domain.processor import FICTIONAL_PROCESSORS, PaymentProcessor
from eod_close.domain.results import (
    ExceptionItem,
    ExceptionReason,
    Match,
    MatchRule,
    ReconciliationReport,
)

MATCH_LABELS: Final = {
    MatchRule.EXACT: "Exact",
    MatchRule.FEE_ADJUSTED: "Fee-adjusted",
    MatchRule.DATE_WINDOW: "Delayed settlement",
    MatchRule.CURRENCY_TOLERANCE: "Currency rounding",
    MatchRule.BATCH: "Batch payout",
    MatchRule.REFUND_LINK: "Refund",
    MatchRule.CHARGEBACK_LINK: "Chargeback",
}
"""Plain names for each kind of match, in the order the page lists them."""

REVIEW_LABELS: Final = {
    ExceptionReason.LIKELY_TRANSPOSITION: "Possible typo",
    ExceptionReason.DUPLICATE: "Duplicate",
    ExceptionReason.MISSING_FROM_LEDGER: "Not in the ledger",
    ExceptionReason.MISSING_FROM_BANK: "Not on the bank statement",
}
"""Plain names for each kind of problem, in the order the page lists them."""

REPORT_COLUMNS: Final = (
    "status",
    "type",
    "bank_entries",
    "ledger_entries",
    "bank_amount",
    "ledger_amount",
    "difference",
    "reason",
    "next_step",
)
"""The columns of the downloadable CSV report."""


@dataclass(frozen=True)
class MatchRow:
    """One match, ready to show.

    Attributes:
        rule: The engine's rule, which decides the label and the grouping.
        bank_ids: The bank entries, by ID.
        ledger_ids: The ledger entries, by ID.
        bank_amount: The bank side's total.
        ledger_amount: The ledger side's total.
        reason: Why the entries belong together, in one sentence.
    """

    rule: MatchRule
    bank_ids: tuple[str, ...]
    ledger_ids: tuple[str, ...]
    bank_amount: Money
    ledger_amount: Money
    reason: str

    @property
    def label(self) -> str:
        """The plain name of the match type, for example ``"Delayed settlement"``."""
        return MATCH_LABELS[self.rule]

    @property
    def difference(self) -> Money:
        """Bank minus ledger, such as a deducted fee."""
        return self.bank_amount - self.ledger_amount


@dataclass(frozen=True)
class ReviewItem:
    """One problem that a person needs to look at.

    A likely typo involves a bank entry and a ledger entry; every other
    problem involves a single entry, so one side's amount is None.

    Attributes:
        reason_code: The engine's reason, which decides the label and grouping.
        bank_ids: The bank entries involved, by ID.
        ledger_ids: The ledger entries involved, by ID.
        bank_amount: The bank amount, if a bank entry is involved.
        ledger_amount: The ledger amount, if a ledger entry is involved.
        reason: What is wrong, in one sentence.
        next_step: What an analyst would normally do about it.
    """

    reason_code: ExceptionReason
    bank_ids: tuple[str, ...]
    ledger_ids: tuple[str, ...]
    bank_amount: Money | None
    ledger_amount: Money | None
    reason: str
    next_step: str

    @property
    def label(self) -> str:
        """The plain name of the problem, for example ``"Possible typo"``."""
        return REVIEW_LABELS[self.reason_code]


@dataclass(frozen=True)
class Summary:
    """The headline figures for one reconciliation.

    Attributes:
        matched: How many matches the engine made.
        needs_review: How many problems need a person.
        bank_total: The sum of every bank entry.
        ledger_total: The sum of every ledger entry.
        explained_difference: The part of the gap the matches account for,
            such as processor fees and currency rounding.
    """

    matched: int
    needs_review: int
    bank_total: Money
    ledger_total: Money
    explained_difference: Money

    @property
    def total_difference(self) -> Money:
        """Bank total minus ledger total."""
        return self.bank_total - self.ledger_total

    @property
    def unexplained_difference(self) -> Money:
        """The part of the gap that only the items needing review can explain."""
        return self.total_difference - self.explained_difference

    @property
    def balances(self) -> bool:
        """True when nothing needs review, so every penny of the gap is explained."""
        return self.needs_review == 0

    @property
    def headline(self) -> str:
        """One line saying whether the books balance, and by how much if not."""
        if self.balances:
            return "Books balance"
        if self.unexplained_difference.is_zero():
            # Problems can cancel out in total (a duplicate and a missing
            # payment of the same amount), so the gap alone is not the test.
            noun = "item needs" if self.needs_review == 1 else "items need"
            return f"Books don't balance yet: {self.needs_review} {noun} review"
        return f"Books don't balance by {abs(self.unexplained_difference)}"


@dataclass(frozen=True)
class ReportView:
    """Everything the reconcile results show.

    Attributes:
        summary: The headline figures.
        matches: Every match, grouped by type in ``MATCH_LABELS`` order.
        review: Every problem, grouped by type in ``REVIEW_LABELS`` order and,
            within a type, largest amount first, since those matter most.
    """

    summary: Summary
    matches: tuple[MatchRow, ...]
    review: tuple[ReviewItem, ...]

    def matches_by_label(self) -> list[tuple[str, list[MatchRow]]]:
        """Return the matches grouped by type, leaving out empty types."""
        return _grouped(self.matches, MATCH_LABELS.values())

    def review_by_label(self) -> list[tuple[str, list[ReviewItem]]]:
        """Return the problems grouped by type, leaving out empty types."""
        return _grouped(self.review, REVIEW_LABELS.values())

    def examples(self, rules: Iterable[MatchRule]) -> list[MatchRow]:
        """Pick one example match of each rule, for a preview.

        The shortest explanation of each type is chosen, since a preview has
        little room; types the day does not contain are skipped.

        Args:
            rules: The match types wanted, in display order.

        Returns:
            At most one row per rule, in the order given.
        """
        picked = []
        for rule in rules:
            rows = [row for row in self.matches if row.rule is rule]
            if rows:
                picked.append(min(rows, key=lambda row: (len(row.reason), row.bank_ids)))
        return picked


def build_report_view(
    report: ReconciliationReport,
    processors: Sequence[PaymentProcessor] = FICTIONAL_PROCESSORS,
) -> ReportView:
    """Describe a reconciliation report for people.

    Args:
        report: The engine's report.
        processors: The processors the engine used, for names and fee rules.

    Returns:
        The summary, the matches and the problems, each in display order.
    """
    by_code = {p.code: p for p in processors}
    match_order = list(MATCH_LABELS)
    review_order = list(REVIEW_LABELS)
    matches = sorted(
        (_match_row(m, by_code) for m in report.matches),
        key=lambda row: (match_order.index(row.rule), row.bank_ids),
    )
    review = sorted(
        _review_items(report.exceptions, by_code),
        key=lambda item: (
            review_order.index(item.reason_code),
            -_size(item),
            item.bank_ids + item.ledger_ids,
        ),
    )
    summary = Summary(
        matched=len(matches),
        needs_review=len(review),
        bank_total=Money.total(e.amount for e in report.bank_statement),
        ledger_total=Money.total(e.amount for e in report.ledger),
        explained_difference=Money.total(row.difference for row in matches),
    )
    return ReportView(summary, tuple(matches), tuple(review))


def report_csv(view: ReportView) -> str:
    """Write the view as a CSV report, one row per match or problem.

    Amounts are plain decimal strings, as in the input format, so the file
    opens cleanly in a spreadsheet and can be read back without rounding.

    Args:
        view: The report view.

    Returns:
        The CSV text, with a header row.
    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(REPORT_COLUMNS)
    for row in view.matches:
        writer.writerow(
            [
                "matched",
                row.label,
                " ".join(row.bank_ids),
                " ".join(row.ledger_ids),
                _plain(row.bank_amount),
                _plain(row.ledger_amount),
                _plain(row.difference),
                row.reason,
                "",
            ]
        )
    for item in view.review:
        difference = (
            item.bank_amount - item.ledger_amount
            if item.bank_amount is not None and item.ledger_amount is not None
            else None
        )
        writer.writerow(
            [
                "needs review",
                item.label,
                " ".join(item.bank_ids),
                " ".join(item.ledger_ids),
                _plain(item.bank_amount),
                _plain(item.ledger_amount),
                _plain(difference),
                item.reason,
                item.next_step,
            ]
        )
    return buffer.getvalue()


# ---- Matches ------------------------------------------------------------


def _match_row(match: Match, processors: Mapping[str, PaymentProcessor]) -> MatchRow:
    return MatchRow(
        rule=match.rule,
        bank_ids=tuple(e.entry_id for e in match.bank_entries),
        ledger_ids=tuple(e.entry_id for e in match.ledger_entries),
        bank_amount=match.bank_total,
        ledger_amount=match.ledger_total,
        reason=_match_reason(match, processors),
    )


def _match_reason(found: Match, processors: Mapping[str, PaymentProcessor]) -> str:
    bank = found.bank_entries[0]
    ledger = found.ledger_entries[0]
    processor = processors.get(ledger.processor_code)
    name = processor.name if processor else ledger.processor_code
    gap = -found.difference
    delay = business_days_between(ledger.value_date, bank.value_date)

    match found.rule:
        case MatchRule.EXACT:
            return "Same reference, amount and date on both sides."
        case MatchRule.FEE_ADJUSTED:
            rule = processor.fee_rule.describe() if processor else "its fee"
            if delay > 0:
                return (
                    f"Arrived {_days(delay)} after booking and {gap} lower, matching the fee "
                    f"{name} charges ({rule})."
                )
            return f"{gap} lower than booked, matching the fee {name} charges ({rule})."
        case MatchRule.DATE_WINDOW:
            if not bank.reference:
                return (
                    f"Same amount from {name}, {_days(delay)} after booking. The bank line "
                    "had no reference, so it was matched on amount, sender and date."
                )
            return (
                f"Same reference and amount; arrived {_days(delay)} after it was booked "
                f"({_short(ledger.value_date)} to {_short(bank.value_date)})."
            )
        case MatchRule.CURRENCY_TOLERANCE:
            original = f"A {ledger.original_amount} sale" if ledger.original_amount else "A sale"
            return (
                f"{original} converted to pounds; the two sides differ by {abs(found.difference)} "
                "because of rounding in the conversion."
            )
        case MatchRule.BATCH:
            return (
                f"One {name} payout covering {len(found.ledger_entries)} sales booked "
                f"{_date_span(e.value_date for e in found.ledger_entries)}, less {gap} in fees."
            )
        case MatchRule.REFUND_LINK:
            return f"Refund of order {ledger.original_reference}, paid back to the customer."
        case MatchRule.CHARGEBACK_LINK:
            return (
                f"The customer disputed order {ledger.original_reference} (a chargeback), so "
                f"{name} took the payment back, plus a {gap} chargeback fee."
            )
    raise AssertionError(f"Unhandled match rule: {found.rule}")  # pragma: no cover


# ---- Items needing review -----------------------------------------------


def _review_items(
    exceptions: Iterable[ExceptionItem], processors: Mapping[str, PaymentProcessor]
) -> list[ReviewItem]:
    items = list(exceptions)
    by_id = {x.entry.entry_id: x for x in items}
    done: set[str] = set()
    result: list[ReviewItem] = []
    for x in items:
        entry_id = x.entry.entry_id
        if entry_id in done:
            continue
        done.add(entry_id)
        if x.reason is ExceptionReason.LIKELY_TRANSPOSITION:
            # The engine flags both halves of a likely typo; they are one
            # problem to fix, so they are shown and counted once.
            partner = next(
                (by_id[i] for i in x.related_entry_ids if i in by_id and i not in done), None
            )
            if partner is not None:
                done.add(partner.entry.entry_id)
                result.append(_typo(x, partner))
                continue
        result.append(_single(x, processors))
    return result


def _typo(first: ExceptionItem, second: ExceptionItem) -> ReviewItem:
    bank_item, ledger_item = (
        (first, second) if isinstance(first.entry, BankEntry) else (second, first)
    )
    bank, ledger = bank_item.entry, ledger_item.entry
    gap = abs(bank.amount - ledger.amount)
    return ReviewItem(
        reason_code=ExceptionReason.LIKELY_TRANSPOSITION,
        bank_ids=(bank.entry_id,),
        ledger_ids=(ledger.entry_id,),
        bank_amount=bank.amount,
        ledger_amount=ledger.amount,
        reason=(
            f"Same reference, but the ledger shows {ledger.amount} and the bank shows "
            f"{bank.amount}. The difference of {gap} is a multiple of 9, which is what "
            "swapping two digits produces."
        ),
        next_step=(
            f"Check the original invoice. If the bank is right, correct the ledger from "
            f"{ledger.amount} to {bank.amount}; the two will then match exactly."
        ),
    )


def _single(item: ExceptionItem, processors: Mapping[str, PaymentProcessor]) -> ReviewItem:
    entry = item.entry
    is_bank = isinstance(entry, BankEntry)
    reason, next_step = _explain(item, processors)
    return ReviewItem(
        reason_code=item.reason,
        bank_ids=(entry.entry_id,) if is_bank else (),
        ledger_ids=() if is_bank else (entry.entry_id,),
        bank_amount=entry.amount if is_bank else None,
        ledger_amount=None if is_bank else entry.amount,
        reason=reason,
        next_step=next_step,
    )


def _explain(item: ExceptionItem, processors: Mapping[str, PaymentProcessor]) -> tuple[str, str]:
    entry = item.entry
    reference = f"reference {entry.reference}" if entry.reference else "no reference"
    related = ", ".join(item.related_entry_ids) or "another entry"

    if isinstance(entry, BankEntry):
        if item.reason is ExceptionReason.DUPLICATE:
            return (
                f"Appears twice on the bank statement: same reference, amount and date as "
                f"{related}.",
                "Ask the bank whether the payment really went through twice. If it did, "
                "ask for the second one to be reversed.",
            )
        outgoing = entry.amount.is_negative()
        other_party = _other_party(entry.counterparty, processors, outgoing=outgoing)
        if outgoing:
            return (
                f"{abs(entry.amount)} left the account {other_party} ({reference}), but "
                "nothing in the ledger explains it.",
                "Find out what the payment was for and book it in the ledger. If nobody "
                "authorised it, dispute it with the bank.",
            )
        return (
            f"{entry.amount} arrived {other_party} ({reference}), but nothing in the ledger "
            "explains it.",
            "Find out what the money is for and book it in the ledger. If it is not yours, "
            "return it.",
        )

    ledger: LedgerEntry = entry
    if item.reason is ExceptionReason.DUPLICATE:
        return (
            f"Booked twice in the ledger: same reference, amount and date as {related}.",
            f"Reverse the duplicate ledger entry {ledger.entry_id}.",
        )
    processor = processors.get(ledger.processor_code)
    who = processor.name if processor and processor.code != "DIRECT" else "the customer"
    return (
        f"{ledger.amount} booked on {_short(ledger.value_date)} ({reference}) is not on the "
        "bank statement.",
        f"Check with {who} whether it has been paid. If it is still on its way it will "
        "match on a later day; if not, chase it.",
    )


# ---- Formatting helpers -------------------------------------------------


def _grouped[T: (MatchRow, ReviewItem)](
    rows: Iterable[T], labels: Iterable[str]
) -> list[tuple[str, list[T]]]:
    groups: dict[str, list[T]] = {label: [] for label in labels}
    for row in rows:
        groups[row.label].append(row)
    return [(label, members) for label, members in groups.items() if members]


def _other_party(code: str, processors: Mapping[str, PaymentProcessor], *, outgoing: bool) -> str:
    # "from DIRECT" reads as a code, not English; a direct transfer has no
    # processor in between, so it is described by how the money moved.
    if code == "DIRECT":
        return "by direct bank transfer"
    processor = processors.get(code)
    return f"{'to' if outgoing else 'from'} {processor.name if processor else code}"


def _days(count: int) -> str:
    return f"{count} working day{'' if count == 1 else 's'}"


def _short(day: date) -> str:
    return day.strftime("%d %b")


def _date_span(days: Iterable[date]) -> str:
    unique = sorted(set(days))
    if len(unique) == 1:
        return f"on {_short(unique[0])}"
    return f"between {_short(unique[0])} and {_short(unique[-1])}"


def _size(item: ReviewItem) -> int:
    amount = item.bank_amount if item.bank_amount is not None else item.ledger_amount
    return abs(amount.minor_units) if amount is not None else 0


def _plain(amount: Money | None) -> str:
    return "" if amount is None else str(amount.amount)
