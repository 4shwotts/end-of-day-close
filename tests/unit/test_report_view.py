"""Report view tests: plain-English reasons, next steps, the summary figures and the CSV report."""

import csv
import io
from datetime import date
from decimal import Decimal

from eod_close.domain import BankEntry, BankStatement, Ledger, LedgerEntry, Money
from eod_close.domain.results import ExceptionReason, MatchRule, ReconciliationReport
from eod_close.engine import ReconciliationEngine
from eod_close.generator import CaseGenerator, Difficulty
from eod_close.web.report_view import (
    MATCH_LABELS,
    REPORT_COLUMNS,
    REVIEW_LABELS,
    ReportView,
    build_report_view,
    report_csv,
)

TUESDAY = date(2026, 11, 17)
MONDAY = date(2026, 11, 16)
FRIDAY = date(2026, 11, 13)


def bank(entry_id: str, reference: str, amount: str, day: date, who: str) -> BankEntry:
    return BankEntry(
        entry_id=entry_id,
        reference=reference,
        amount=Money.of(amount),
        value_date=day,
        counterparty=who,
    )


def ledger(entry_id: str, reference: str, amount: str, day: date, who: str) -> LedgerEntry:
    return LedgerEntry(
        entry_id=entry_id,
        reference=reference,
        amount=Money.of(amount),
        value_date=day,
        processor_code=who,
    )


def reconcile(banks: list[BankEntry], ledgers: list[LedgerEntry]) -> ReconciliationReport:
    statement = BankStatement(banks, bank_name="b", account_name="a", statement_date=TUESDAY)
    return ReconciliationEngine().reconcile(statement, Ledger(ledgers, entity_name="Test Ltd"))


def mixed_day() -> ReportView:
    """Build a day with one example of most cases.

    A fee, a late payment with no reference, a typo, a payment missing from
    each side and a duplicate ledger entry.
    """
    report = reconcile(
        [
            bank("B1", "ORD-1", "98.40", TUESDAY, "BRAMBLE"),
            bank("B2", "", "49.99", TUESDAY, "OTTERLINE"),
            bank("B3", "INV-9", "14.99", TUESDAY, "DIRECT"),
            bank("B4", "INV-7", "25.00", TUESDAY, "DIRECT"),
            bank("B5", "INV-3", "12.00", TUESDAY, "DIRECT"),
        ],
        [
            ledger("L1", "ORD-1", "100.00", MONDAY, "BRAMBLE"),
            ledger("L2", "ORD-6", "49.99", FRIDAY, "OTTERLINE"),
            ledger("L3", "INV-9", "41.99", TUESDAY, "DIRECT"),
            ledger("L4", "ORD-5", "30.00", TUESDAY, "OTTERLINE"),
            ledger("L5", "INV-3", "12.00", TUESDAY, "DIRECT"),
            ledger("L6", "INV-3", "12.00", TUESDAY, "DIRECT"),
        ],
    )
    return build_report_view(report)


def test_each_match_is_explained_in_plain_english() -> None:
    reasons = {row.rule: row.reason for row in mixed_day().matches}
    assert reasons[MatchRule.FEE_ADJUSTED] == (
        "Arrived 1 working day after booking and £1.60 lower, matching the fee "
        "Bramblecard Merchant Services charges (1.4% + £0.20)."
    )
    assert "had no reference" in reasons[MatchRule.DATE_WINDOW]
    assert "2 working days after booking" in reasons[MatchRule.DATE_WINDOW]
    assert reasons[MatchRule.EXACT] == "Same reference, amount and date on both sides."


def test_a_likely_typo_is_one_problem_with_a_correction() -> None:
    typos = [i for i in mixed_day().review if i.reason_code is ExceptionReason.LIKELY_TRANSPOSITION]
    assert len(typos) == 1
    typo = typos[0]
    assert (typo.bank_ids, typo.ledger_ids) == (("B3",), ("L3",))
    assert typo.label == "Possible typo"
    assert "correct the ledger from £41.99 to £14.99" in typo.next_step


def test_every_problem_has_a_reason_and_a_next_step() -> None:
    review = {item.ledger_ids or item.bank_ids: item for item in mixed_day().review}
    assert "by direct bank transfer" in review[("B4",)].reason
    assert "Otterline Payments" in review[("L4",)].next_step
    duplicate = next(i for i in review.values() if i.reason_code is ExceptionReason.DUPLICATE)
    assert duplicate.next_step.startswith("Reverse the duplicate ledger entry")
    assert all(item.reason and item.next_step for item in review.values())


def test_summary_splits_the_difference_into_explained_and_unexplained() -> None:
    summary = mixed_day().summary
    assert summary.matched == 3
    assert summary.needs_review == 4
    assert summary.explained_difference == Money.of("-1.60")
    # What only the problems explain: the bank's unmatched money minus the ledger's.
    assert summary.unexplained_difference == Money.of("14.99") + Money.of("25.00") - (
        Money.of("41.99") + Money.of("30.00") + Money.of("12.00")
    )
    assert summary.explained_difference + summary.unexplained_difference == (
        summary.total_difference
    )
    assert not summary.balances


def test_a_day_with_nothing_to_review_balances() -> None:
    view = build_report_view(
        reconcile(
            [bank("B1", "INV-1", "10.00", TUESDAY, "DIRECT")],
            [ledger("L1", "INV-1", "10.00", TUESDAY, "DIRECT")],
        )
    )
    assert view.summary.balances
    assert view.summary.unexplained_difference.is_zero()


def test_the_csv_report_lists_every_match_and_problem_as_plain_numbers() -> None:
    view = mixed_day()
    rows = list(csv.DictReader(io.StringIO(report_csv(view))))
    assert tuple(rows[0]) == REPORT_COLUMNS
    assert [r["status"] for r in rows].count("matched") == len(view.matches)
    assert [r["status"] for r in rows].count("needs review") == len(view.review)
    for row in rows:
        for column in ("bank_amount", "ledger_amount", "difference"):
            if row[column]:
                Decimal(row[column])  # parses, so no currency symbol or separator
    typo = next(r for r in rows if r["type"] == "Possible typo")
    assert (typo["bank_amount"], typo["ledger_amount"], typo["difference"]) == (
        "14.99",
        "41.99",
        "-27.00",
    )


def test_a_generated_day_is_fully_described_and_adds_up() -> None:
    case = CaseGenerator().generate(1, Difficulty.HEAD_OF_OPS)
    report = ReconciliationEngine().reconcile(case.bank_statement, case.ledger)
    view = build_report_view(report)

    assert len(view.matches) == len(report.matches)
    typo_halves = sum(
        1 for x in report.exceptions if x.reason is ExceptionReason.LIKELY_TRANSPOSITION
    )
    assert len(view.review) == len(report.exceptions) - typo_halves // 2

    bank_side = Money.total(
        x.entry.amount for x in report.exceptions if isinstance(x.entry, BankEntry)
    )
    ledger_side = Money.total(
        x.entry.amount for x in report.exceptions if not isinstance(x.entry, BankEntry)
    )
    assert view.summary.unexplained_difference == bank_side - ledger_side

    assert [label for label, _ in view.matches_by_label()] == [
        label for label in MATCH_LABELS.values() if any(row.label == label for row in view.matches)
    ]
    assert {label for label, _ in view.review_by_label()} <= set(REVIEW_LABELS.values())
    for _, items in view.review_by_label():
        sizes = [abs((i.bank_amount or i.ledger_amount or Money.zero()).amount) for i in items]
        assert sizes == sorted(sizes, reverse=True), "largest problems come first"
    assert all(row.reason.endswith(".") for row in view.matches)
