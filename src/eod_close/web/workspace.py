"""The Reconcile page's workspace: one small day's two lists, side by side.

The page tells one story: the bank and your records don't agree, the engine
matches them, and here is what is wrong. Before matching, the workspace is
just the two lists. After, every row carries its outcome (a short tag for
the kind of match, or the kind of problem) and the problems are collected
as cards, each saying in one sentence what is wrong and what to do. Each
card's amount is its effect on the gap between the two sides, and those
effects always add up to the summary's unexplained difference, so the
figure under the headline can be checked card by card.

There are two kinds of day. The sample day is a fixed, hand-written pair of
files in ``sample_day/``: twelve bank lines, every common kind of match and
exactly three problems. "Try another day" generates a day of the same shape
from a seed (see ``SMALL_DAY_MIX``). Either way the engine reconciles the
day live; nothing about the outcome is stored.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
from functools import cache, lru_cache
from pathlib import Path
from typing import Final, Literal, Protocol

from eod_close.domain.entries import Transaction
from eod_close.domain.money import Money
from eod_close.domain.results import ExceptionReason, MatchRule, ReconciliationReport
from eod_close.domain.statements import BankStatement, Ledger
from eod_close.engine.engine import ReconciliationEngine
from eod_close.generator.answer_key import DiscrepancyKind
from eod_close.generator.case_generator import CaseGenerator
from eod_close.web.report_view import ReportView, ReviewItem, Summary, build_report_view
from eod_close.web.services import parse_uploads

SAMPLE_DAY_DIR: Final = Path(__file__).parent / "sample_day"

SMALL_DAY_MIX: Final[tuple[DiscrepancyKind, ...]] = (
    *([DiscrepancyKind.CLEAN] * 4),
    DiscrepancyKind.PROCESSOR_FEE,
    DiscrepancyKind.SETTLEMENT_DELAY,
    DiscrepancyKind.FX_ROUNDING,
    DiscrepancyKind.BATCH_PAYOUT,
    DiscrepancyKind.REFUND,
    DiscrepancyKind.MISSING_FROM_LEDGER,
    DiscrepancyKind.TRANSPOSITION,
    DiscrepancyKind.DUPLICATE,
)
"""What every generated small day contains, the same shape as the sample day.

A duplicate also plants the clean pair it copies, so this gives five exact
matches, one of each other kind of match, and three problems: about twelve
lines a side.
"""

MATCH_TAGS: Final[Mapping[MatchRule, tuple[str, str]]] = {
    MatchRule.EXACT: ("exact", "Exact"),
    MatchRule.FEE_ADJUSTED: ("fee", "Fee"),
    MatchRule.DATE_WINDOW: ("delayed", "Delayed"),
    MatchRule.CURRENCY_TOLERANCE: ("currency", "Currency"),
    MatchRule.BATCH: ("batch", "Batch"),
    MatchRule.REFUND_LINK: ("refund", "Refund"),
    MatchRule.CHARGEBACK_LINK: ("chargeback", "Disputed"),
}
"""Each match type's style key and the short tag shown on its rows."""

REVIEW_TAGS: Final[Mapping[ExceptionReason, str]] = {
    ExceptionReason.LIKELY_TRANSPOSITION: "Typo",
    ExceptionReason.DUPLICATE: "Duplicate",
    ExceptionReason.MISSING_FROM_LEDGER: "Missing",
    ExceptionReason.MISSING_FROM_BANK: "Missing",
}
"""The short tag on a row with a problem."""

# Cards are ordered by how a person would tackle them: money nobody has
# accounted for first, then the corrections.
_CARD_ORDER: Final = (
    ExceptionReason.MISSING_FROM_LEDGER,
    ExceptionReason.MISSING_FROM_BANK,
    ExceptionReason.LIKELY_TRANSPOSITION,
    ExceptionReason.DUPLICATE,
)

type Side = Literal["bank", "ledger"]
type Status = Literal["pending", "matched", "review"]


@dataclass(frozen=True)
class Row:
    """One line of the bank statement or your records, with its outcome.

    Attributes:
        side: Which list the line belongs to.
        entry_id: The line's ID, unique within its list. Not shown on the
            page, but it names the row for links and tests.
        value_date: When the money moved (bank) or the sale was booked (records).
        reference: The payment or order reference.
        description: Free text from the file, shown on hover or focus.
        amount: The amount, positive for money in.
        status: Not yet matched, matched, or a problem.
        tag_key: The match type's style key, such as ``"fee"``; None unless matched.
        tag: The short tag shown on the row, such as ``"Fee"`` or ``"Duplicate"``.
        group: Identifies the match or problem the row belongs to, shared by
            every row in it, so selecting a problem can highlight its rows.
        reason: Why the rows match, or what is wrong, in one sentence.
        next_step: What to do about a problem row; None for a matched row.
    """

    side: Side
    entry_id: str
    value_date: date
    reference: str
    description: str
    amount: Money
    status: Status = "pending"
    tag_key: str | None = None
    tag: str | None = None
    group: str | None = None
    reason: str | None = None
    next_step: str | None = None


@dataclass(frozen=True)
class Problem:
    """One problem card, with what it does to the gap.

    Attributes:
        item: The problem as the results page describes it.
        group: The ID its rows share in the tables.
        what: What is wrong, in one plain sentence.
        next_step: What to do about it.
        effect: Its share of the unexplained difference: the bank amount
            minus the records amount, counting a missing side as zero.
    """

    item: ReviewItem
    group: str
    what: str
    next_step: str
    effect: Money


@dataclass(frozen=True)
class Workspace:
    """Everything the Reconcile page's workspace shows.

    Attributes:
        bank_rows: The bank statement, in file order.
        ledger_rows: Your records, in file order.
        summary: The headline figures; None until the day is matched.
        problems: The problem cards, most pressing first.
        view: The full report view; None until matched.
    """

    bank_rows: tuple[Row, ...]
    ledger_rows: tuple[Row, ...]
    summary: Summary | None = None
    problems: tuple[Problem, ...] = ()
    view: ReportView | None = None

    @property
    def reconciled(self) -> bool:
        """True once the engine's outcome is on the rows."""
        return self.summary is not None

    @property
    def problems_total(self) -> Money:
        """The sum of every card's effect; equal to the unexplained difference."""
        return Money.total(problem.effect for problem in self.problems)

    @property
    def headline(self) -> str:
        """The result in one sentence, such as "Matched 10 payments and found 3 problems"."""
        if self.summary is None:
            return ""
        matched = _count(self.summary.matched, "payment")
        found = _count(len(self.problems), "problem") if self.problems else "no problems"
        return f"Matched {matched} and found {found}"


def build_workspace(
    statement: BankStatement, ledger: Ledger, report: ReconciliationReport | None = None
) -> Workspace:
    """Lay out a day's two lists, with the engine's outcome if there is one.

    Args:
        statement: The bank statement.
        ledger: Your records.
        report: The engine's report, or None to show the day not yet matched.

    Returns:
        The workspace. Every row of both lists appears exactly once.
    """
    plain = _plain_rows(statement, ledger)
    if report is None:
        return Workspace(
            bank_rows=tuple(row for row in plain if row.side == "bank"),
            ledger_rows=tuple(row for row in plain if row.side == "ledger"),
        )

    view = build_report_view(report)
    rows = {(row.side, row.entry_id): row for row in plain}
    for number, match in enumerate(view.matches, start=1):
        key, label = MATCH_TAGS[match.rule]
        _mark(rows, f"m{number}", "matched", key, label, match.reason, match)
    problems = []
    items = sorted(view.review, key=lambda item: _CARD_ORDER.index(item.reason_code))
    for number, item in enumerate(items, start=1):
        group = f"r{number}"
        side: Side = "bank" if item.bank_ids else "ledger"
        first = rows[side, (item.bank_ids or item.ledger_ids)[0]]
        what, next_step = _describe(item, first.reference or "no reference")
        # A problem row's popover says what the card says, in the same words.
        _mark(rows, group, "review", None, REVIEW_TAGS[item.reason_code], what, item, next_step)
        effect = (item.bank_amount or Money.zero()) - (item.ledger_amount or Money.zero())
        problems.append(Problem(item, group, what, next_step, effect))
    return Workspace(
        bank_rows=tuple(row for row in rows.values() if row.side == "bank"),
        ledger_rows=tuple(row for row in rows.values() if row.side == "ledger"),
        summary=view.summary,
        problems=tuple(problems),
        view=view,
    )


@cache
def load_sample_day() -> tuple[BankStatement, Ledger, ReconciliationReport]:
    """Read and reconcile the fixed sample day.

    Cached: the files ship with the app and never change while it runs.

    Returns:
        The bank statement, your records and the engine's report.
    """
    statement, ledger = parse_uploads(
        (SAMPLE_DAY_DIR / "bank.csv").read_bytes(), (SAMPLE_DAY_DIR / "ledger.csv").read_bytes()
    )
    return statement, ledger, ReconciliationEngine().reconcile(statement, ledger)


# Bounded, unlike the sample day: any visitor can ask for any seed.
@lru_cache(maxsize=64)
def load_small_day(seed: int) -> tuple[BankStatement, Ledger, ReconciliationReport]:
    """Generate and reconcile one small day of the sample day's shape.

    Args:
        seed: Chooses the day. The same seed always gives the same day.

    Returns:
        The bank statement, your records and the engine's report.
    """
    statement, ledger, _ = CaseGenerator().generate_mix(seed, SMALL_DAY_MIX, "small")
    return statement, ledger, ReconciliationEngine().reconcile(statement, ledger)


def sample_day_csv(name: Literal["bank", "ledger"]) -> str:
    """Return one of the sample day's files as CSV text, for download."""
    return (SAMPLE_DAY_DIR / f"{name}.csv").read_text(encoding="utf-8")


def _describe(item: ReviewItem, reference: str) -> tuple[str, str]:
    """Say what is wrong and what to do, without the IDs the page hides.

    The results page's own wording names entries by ID, which suits a long
    report. The workspace shows only dates, references and amounts, so its
    cards speak in those terms.

    Args:
        item: The problem.
        reference: The reference on the problem's first row.

    Returns:
        One sentence saying what is wrong, and one saying what to do.
    """
    code = item.reason_code
    bank, ledger = item.bank_amount, item.ledger_amount
    if code is ExceptionReason.MISSING_FROM_LEDGER and bank is not None:
        if bank.is_negative():
            return (
                f"{abs(bank)} left the bank ({reference}) but isn't in your records.",
                "Check whether a refund or charge wasn't recorded.",
            )
        return (
            f"{bank} arrived in the bank ({reference}) but isn't in your records.",
            "Check whether a sale wasn't recorded.",
        )
    if code is ExceptionReason.MISSING_FROM_BANK and ledger is not None:
        return (
            f"Your records show {ledger} for {reference}, but it hasn't reached the bank.",
            "Check with the customer or card company that it was paid.",
        )
    if code is ExceptionReason.LIKELY_TRANSPOSITION and bank is not None and ledger is not None:
        return (
            f"The bank received {bank} for {reference}, but your records say {ledger}: "
            "two digits look swapped.",
            f"Check the invoice. If the bank is right, correct your records to {bank}.",
        )
    if item.ledger_ids:
        return (
            f"{ledger} for {reference} is in your records twice but arrived once.",
            "Delete the extra entry in your records.",
        )
    return (
        f"The bank lists {bank} for {reference} twice.",
        "Ask the bank whether the payment really came in twice.",
    )


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _plain_rows(statement: BankStatement, ledger: Ledger) -> list[Row]:
    """Both lists as rows with no outcome yet, bank first, each in file order."""
    return [_row("bank", entry) for entry in statement] + [
        _row("ledger", entry) for entry in ledger
    ]


class _HasIds(Protocol):
    """Anything naming the entries on each side: a match or a problem."""

    @property
    def bank_ids(self) -> tuple[str, ...]: ...

    @property
    def ledger_ids(self) -> tuple[str, ...]: ...


def _mark(
    rows: dict[tuple[Side, str], Row],
    group: str,
    status: Status,
    tag_key: str | None,
    tag: str,
    reason: str,
    ids: _HasIds,
    next_step: str | None = None,
) -> None:
    """Put one match's or problem's outcome on each of its rows, in place."""
    sides: tuple[tuple[Side, tuple[str, ...]], ...] = (
        ("bank", ids.bank_ids),
        ("ledger", ids.ledger_ids),
    )
    for side, entry_ids in sides:
        for entry_id in entry_ids:
            rows[side, entry_id] = replace(
                rows[side, entry_id],
                status=status,
                tag_key=tag_key,
                tag=tag,
                group=group,
                reason=reason,
                next_step=next_step,
            )


def _row(side: Side, entry: Transaction) -> Row:
    return Row(
        side=side,
        entry_id=entry.entry_id,
        value_date=entry.value_date,
        reference=entry.reference,
        description=entry.description,
        amount=entry.amount,
    )
