"""The busy day on How it works: a real day's volume, matched live, with its evidence.

A visitor should not have to take the result on trust, so every claim the
section makes can be checked:

- The day is seeded. Its number is shown, and running the same number again
  gives the same day and the same answers.
- The generator records every problem it plants (the answer key), so the
  engine's answers are marked against it: found, false alarms, missed.
- The obvious approach, checking every pair, runs on the same day and is
  timed the same way, so the comparison is like for like.
- The bank file, the records file, the answer key and the engine's report
  can all be downloaded and checked in a spreadsheet.

Nothing here is cached or written in advance. Each request generates the day
from its seed, reconciles it and marks it again; generating a day is
deterministic, so the files and answers are identical every time, and only
the measured times vary.
"""

from __future__ import annotations

import csv
import io
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal

from eod_close.domain.processor import FICTIONAL_PROCESSORS
from eod_close.domain.results import ReconciliationReport
from eod_close.domain.statements import BankStatement, Ledger
from eod_close.engine import ReconciliationEngine
from eod_close.evaluation.accuracy import evaluate_key
from eod_close.evaluation.naive import NaiveReconciler
from eod_close.generator import AnswerKey, CaseGenerator, DiscrepancyKind
from eod_close.generator.difficulty import BUSY_DAY_PROFILE
from eod_close.web.workspace import Problem, Row, Workspace, build_workspace

PROBLEM_KINDS: Final = frozenset(
    {
        DiscrepancyKind.DUPLICATE,
        DiscrepancyKind.MISSING_FROM_BANK,
        DiscrepancyKind.MISSING_FROM_LEDGER,
        DiscrepancyKind.TRANSPOSITION,
    }
)
"""The planted kinds that are problems for a person, rather than matches."""

PAGE_SIZE: Final = 20
"""Rows per page of each list under "View the two lists"."""

# Each approach is timed several times and the best run kept, so a stray
# pause on a shared server does not decide the comparison.
_TIMING_RUNS: Final = 3

type Show = Literal["all", "problems"]


@dataclass(frozen=True)
class BusyDay:
    """One busy day, matched and marked.

    Attributes:
        seed: The day's number; the same number always gives the same day.
        statement: The bank statement.
        ledger: Your records.
        answer_key: What the generator planted.
        report: The engine's report.
        workspace: The rows and problem cards, as the Reconcile page shows them.
        seconds: The engine's best time.
        planted: Problems the generator planted.
        found: Planted problems the engine reported exactly.
        false_alarms: Problems the engine reported that were not planted.
        naive_seconds: Checking every pair's best time on the same day.
        naive_unexplained: Lines checking every pair left with no partner
            and no reason. The engine leaves none: every line is either
            matched or reported as a problem.
    """

    seed: int
    statement: BankStatement
    ledger: Ledger
    answer_key: AnswerKey
    report: ReconciliationReport
    workspace: Workspace
    seconds: float
    planted: int
    found: int
    false_alarms: int
    naive_seconds: float
    naive_unexplained: int

    @property
    def missed(self) -> int:
        """Planted problems the engine did not report exactly."""
        return self.planted - self.found

    @property
    def lines(self) -> int:
        """Bank and records lines together."""
        return len(self.statement) + len(self.ledger)

    @property
    def problems(self) -> tuple[Problem, ...]:
        """The problem cards, taking one of each kind in turn.

        The Reconcile page lists every missing payment before any typo; on a
        busy day that would fill the first cards with one kind, so here the
        kinds take turns and the first few show the range.
        """
        by_kind: dict[str, list[Problem]] = {}
        for problem in self.workspace.problems:
            by_kind.setdefault(problem.item.label, []).append(problem)
        queues = list(by_kind.values())
        ordered: list[Problem] = []
        while any(queues):
            ordered.extend(queue.pop(0) for queue in queues if queue)
        return tuple(ordered)

    @property
    def matched(self) -> int:
        """How many matches the engine made."""
        summary = self.workspace.summary
        return summary.matched if summary else 0

    @property
    def engine_unexplained(self) -> int:
        """Lines the engine left with no match and no reason; zero on a balanced day."""
        return len(self.report.unaccounted_bank_ids) + len(self.report.unaccounted_ledger_ids)


@dataclass(frozen=True)
class ListPage:
    """One page of the two lists under "View the two lists".

    Attributes:
        show: Every row, or only the rows with a problem.
        focus: The problem whose rows are highlighted on this page, if any.
        page: This page's number, from 1.
        pages: How many pages there are.
        bank_rows: This page's bank rows.
        ledger_rows: This page's records rows.
        bank_count: Bank rows across every page.
        ledger_count: Records rows across every page.
    """

    show: Show
    focus: str | None
    page: int
    pages: int
    bank_rows: tuple[Row, ...]
    ledger_rows: tuple[Row, ...]
    bank_count: int
    ledger_count: int


def run_busy_day(seed: int) -> BusyDay:
    """Generate, match and mark one busy day, timing both approaches.

    Deliberately not cached: the point is to show the work being done.

    Args:
        seed: The day's number.

    Returns:
        The day, the engine's answers, their marks and the comparison.
    """
    statement, ledger, answer_key = CaseGenerator().generate_profile(seed, BUSY_DAY_PROFILE, "busy")
    engine = ReconciliationEngine()
    report, seconds = _best_time(lambda: engine.reconcile(statement, ledger))
    naive = NaiveReconciler({p.code: p for p in FICTIONAL_PROCESSORS})
    pairs, naive_seconds = _best_time(lambda: naive.reconcile(list(statement), list(ledger)))

    scores = evaluate_key(statement, ledger, answer_key, report).by_kind
    planted = sum(scores[kind].planted for kind in PROBLEM_KINDS)
    claimed = sum(scores[kind].claimed for kind in PROBLEM_KINDS)
    found = sum(scores[kind].correct for kind in PROBLEM_KINDS)
    return BusyDay(
        seed=seed,
        statement=statement,
        ledger=ledger,
        answer_key=answer_key,
        report=report,
        workspace=build_workspace(statement, ledger, report),
        seconds=seconds,
        planted=planted,
        found=found,
        false_alarms=claimed - found,
        naive_seconds=naive_seconds,
        naive_unexplained=len(statement) + len(ledger) - 2 * len(pairs),
    )


def list_page(workspace: Workspace, page: int, show: Show, focus: str | None = None) -> ListPage:
    """Cut the two lists into pages, optionally keeping only the problem rows.

    Both lists turn the same page together, so page 3 shows the third block
    of each. A page number past the end shows the last page.

    Args:
        workspace: The matched day.
        page: The page wanted, from 1.
        show: ``"all"`` or ``"problems"``.
        focus: A problem to bring into view, such as ``"r3"``. The lists
            switch to the Problems filter and to the page holding that
            problem's first row, whatever ``show`` and ``page`` say. An
            unknown problem changes nothing.

    Returns:
        The page.
    """
    if focus is not None and any(
        r.group == focus for r in workspace.bank_rows + workspace.ledger_rows
    ):
        show = "problems"
        page = _page_holding(workspace, focus)
    else:
        focus = None

    bank, ledger = _filtered(workspace.bank_rows, show), _filtered(workspace.ledger_rows, show)
    pages = max(1, math.ceil(max(len(bank), len(ledger)) / PAGE_SIZE))
    page = min(max(page, 1), pages)
    window = slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)
    return ListPage(show, focus, page, pages, bank[window], ledger[window], len(bank), len(ledger))


def _filtered(rows: tuple[Row, ...], show: Show) -> tuple[Row, ...]:
    return rows if show == "all" else tuple(row for row in rows if row.status == "review")


def _page_holding(workspace: Workspace, group: str) -> int:
    """The page of the Problems view on which a problem's first row appears.

    A typo has a row on each side, which can sit at different positions in
    the two lists; the earlier one decides, so at least one row is on the
    page, and in practice both are, since problems are few.
    """
    positions = [
        index
        for rows in (workspace.bank_rows, workspace.ledger_rows)
        for index, row in enumerate(_filtered(rows, "problems"))
        if row.group == group
    ]
    return min(positions) // PAGE_SIZE + 1


def answer_key_csv(answer_key: AnswerKey) -> str:
    """The answer key as CSV, one planted item a row, for checking in a spreadsheet.

    Args:
        answer_key: The generator's key.

    Returns:
        CSV text with a header row.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["kind", "problem", "bank_entries", "ledger_entries", "explanation"])
    for item in answer_key.items:
        writer.writerow(
            [
                item.kind.value,
                "yes" if item.kind in PROBLEM_KINDS else "no",
                " ".join(item.bank_ids),
                " ".join(item.ledger_ids),
                item.explanation,
            ]
        )
    return buffer.getvalue()


def _best_time[T](run: Callable[[], T]) -> tuple[T, float]:
    """Run something a few times and return its result and the fastest time."""
    best = math.inf
    result: T | None = None
    for _ in range(_TIMING_RUNS):
        started = time.perf_counter()
        result = run()
        best = min(best, time.perf_counter() - started)
    assert result is not None  # _TIMING_RUNS is at least one
    return result, best
