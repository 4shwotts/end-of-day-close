"""The home page's workspace: one day's bank statement and ledger, side by side.

Before reconciling, the workspace is just the two lists. After, every row
carries its outcome: a short tag for the kind of match and the IDs of its
partners, or a flag that it needs review. The problems are collected with
their effect on the gap between the two sides, and those effects always add
up to the summary's unexplained difference, so the figure in the status bar
can be checked line by line.

The sample day is a fixed, hand-written pair of files in ``sample_day/``:
small enough to take in at a glance, with every kind of match and exactly
three problems (a payment missing from the ledger, a duplicate and a likely
typo). The engine reconciles it live; nothing about the outcome is stored.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
from functools import cache
from pathlib import Path
from typing import Final, Literal

from eod_close.domain.entries import Transaction
from eod_close.domain.money import Money
from eod_close.domain.results import ExceptionReason, MatchRule, ReconciliationReport
from eod_close.domain.statements import BankStatement, Ledger
from eod_close.web.report_view import ReportView, ReviewItem, Summary, build_report_view
from eod_close.web.services import reconcile_uploads

SAMPLE_DAY_DIR: Final = Path(__file__).parent / "sample_day"

MATCH_TAGS: Final[Mapping[MatchRule, tuple[str, str]]] = {
    MatchRule.EXACT: ("exact", "Exact"),
    MatchRule.FEE_ADJUSTED: ("fee", "Fee"),
    MatchRule.DATE_WINDOW: ("delayed", "Delayed"),
    MatchRule.CURRENCY_TOLERANCE: ("currency", "Currency"),
    MatchRule.BATCH: ("batch", "Batch"),
    MatchRule.REFUND_LINK: ("refund", "Refund"),
    MatchRule.CHARGEBACK_LINK: ("chargeback", "Chargeback"),
}
"""Each match type's filter key and the short label shown on its rows."""

REVIEW_TAGS: Final[Mapping[ExceptionReason, str]] = {
    ExceptionReason.LIKELY_TRANSPOSITION: "Typo?",
    ExceptionReason.DUPLICATE: "Duplicate",
    ExceptionReason.MISSING_FROM_LEDGER: "Missing",
    ExceptionReason.MISSING_FROM_BANK: "Missing",
}
"""The short label on a row that needs review."""

type Side = Literal["bank", "ledger"]
type Status = Literal["pending", "matched", "review"]


@dataclass(frozen=True)
class Row:
    """One line of the bank statement or the ledger, with its outcome.

    Attributes:
        side: Which list the line belongs to.
        entry_id: The line's ID, unique within its list.
        value_date: When the money moved (bank) or the sale was booked (ledger).
        reference: The payment or order reference.
        description: Free text from the file.
        amount: The amount, positive for money in.
        status: Not yet reconciled, matched, or needing review.
        tag_key: The match type's filter key, such as ``"fee"``; None unless matched.
        tag: The short label shown on the row, such as ``"Fee"`` or ``"Duplicate"``.
        group: Identifies the match or problem the row belongs to, shared by
            every row in it, so selecting one row can highlight the others.
        partners: The IDs of the rows on the other side of the same match or problem.
        reason: Why the rows match, or what is wrong, in one sentence.
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
    partners: tuple[str, ...] = ()
    reason: str | None = None

    @property
    def partner_side(self) -> Side:
        """The other list, where this row's partners are."""
        return "ledger" if self.side == "bank" else "bank"


@dataclass(frozen=True)
class Problem:
    """A problem in the needs-review panel, with what it does to the gap.

    Attributes:
        item: The problem as the results page describes it.
        group: The ID its rows share in the tables.
        effect: Its share of the unexplained difference: the bank amount
            minus the ledger amount, counting a missing side as zero.
    """

    item: ReviewItem
    group: str
    effect: Money


@dataclass(frozen=True)
class Workspace:
    """Everything the home page's workspace shows.

    Attributes:
        bank_rows: The bank statement, in file order.
        ledger_rows: The ledger, in file order.
        summary: The headline figures; None until the day is reconciled.
        problems: What needs review, in the results page's order.
        view: The full report view, for the CSV download; None until reconciled.
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
        """The sum of every problem's effect; equal to the unexplained difference."""
        return Money.total(problem.effect for problem in self.problems)

    @property
    def tags_present(self) -> list[tuple[str, str]]:
        """The match types on this day, as (key, label), in ``MATCH_TAGS`` order."""
        keys = {row.tag_key for row in self.bank_rows + self.ledger_rows if row.tag_key}
        return [tag for tag in MATCH_TAGS.values() if tag[0] in keys]


def build_workspace(
    statement: BankStatement, ledger: Ledger, report: ReconciliationReport | None = None
) -> Workspace:
    """Lay out a day's two lists, with the engine's outcome if there is one.

    Args:
        statement: The bank statement.
        ledger: The ledger.
        report: The engine's report, or None to show the day unreconciled.

    Returns:
        The workspace. Every row of both lists appears exactly once.
    """
    if report is None:
        plain = _plain_rows(statement, ledger)
        return Workspace(
            bank_rows=tuple(row for row in plain if row.side == "bank"),
            ledger_rows=tuple(row for row in plain if row.side == "ledger"),
        )

    view = build_report_view(report)
    rows = {(row.side, row.entry_id): row for row in _plain_rows(statement, ledger)}
    for number, match in enumerate(view.matches, start=1):
        key, label = MATCH_TAGS[match.rule]
        _mark(
            rows,
            f"m{number}",
            "matched",
            key,
            label,
            match.reason,
            match.bank_ids,
            match.ledger_ids,
        )
    problems = []
    for number, item in enumerate(view.review, start=1):
        group = f"r{number}"
        _mark(
            rows,
            group,
            "review",
            None,
            REVIEW_TAGS[item.reason_code],
            item.reason,
            item.bank_ids,
            item.ledger_ids,
        )
        effect = (item.bank_amount or Money.zero()) - (item.ledger_amount or Money.zero())
        problems.append(Problem(item, group, effect))
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
        The bank statement, the ledger and the engine's report.
    """
    return reconcile_uploads(
        (SAMPLE_DAY_DIR / "bank.csv").read_bytes(), (SAMPLE_DAY_DIR / "ledger.csv").read_bytes()
    )


def sample_day_csv(name: Literal["bank", "ledger"]) -> str:
    """Return one of the sample day's files as CSV text, for download."""
    return (SAMPLE_DAY_DIR / f"{name}.csv").read_text(encoding="utf-8")


def _plain_rows(statement: BankStatement, ledger: Ledger) -> list[Row]:
    """Both lists as rows with no outcome yet, bank first, each in file order."""
    return [_row("bank", entry) for entry in statement] + [
        _row("ledger", entry) for entry in ledger
    ]


def _mark(
    rows: dict[tuple[Side, str], Row],
    group: str,
    status: Status,
    tag_key: str | None,
    tag: str,
    reason: str,
    bank_ids: tuple[str, ...],
    ledger_ids: tuple[str, ...],
) -> None:
    """Put one match's or problem's outcome on each of its rows, in place."""
    sides: tuple[tuple[Side, tuple[str, ...], tuple[str, ...]], ...] = (
        ("bank", bank_ids, ledger_ids),
        ("ledger", ledger_ids, bank_ids),
    )
    for side, ids, partners in sides:
        for entry_id in ids:
            rows[side, entry_id] = replace(
                rows[side, entry_id],
                status=status,
                tag_key=tag_key,
                tag=tag,
                group=group,
                partners=partners,
                reason=reason,
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
