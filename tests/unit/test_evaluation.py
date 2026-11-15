"""Evaluation tests: precision and recall scoring, the benchmark and the command-line tools."""

from dataclasses import replace

import pytest

from eod_close.domain.processor import FICTIONAL_PROCESSORS
from eod_close.domain.results import ReconciliationReport
from eod_close.engine import ReconciliationEngine
from eod_close.engine.__main__ import main as engine_main
from eod_close.evaluation import NaiveReconciler, evaluate, run_benchmark, synthetic_day
from eod_close.evaluation.__main__ import main as evaluation_main
from eod_close.generator import CaseGenerator, Difficulty, DiscrepancyKind


def test_a_perfect_report_scores_full_precision_and_recall() -> None:
    case = CaseGenerator().generate(0, Difficulty.ANALYST)
    result = evaluate(case, ReconciliationEngine().reconcile(case.bank_statement, case.ledger))
    assert result.overall.precision == 1.0
    assert result.overall.recall == 1.0


def test_dropping_a_match_lowers_recall_for_its_kind_only() -> None:
    case = CaseGenerator().generate(0, Difficulty.ANALYST)
    report = ReconciliationEngine().reconcile(case.bank_statement, case.ledger)
    fee_match = next(m for m in report.matches if m.rule.value == "fee-adjusted")
    # Turn the fee match into two missing exceptions, as a weaker engine would.
    from eod_close.domain.results import ExceptionItem, ExceptionReason

    weaker = ReconciliationReport(
        report.business_date,
        report.bank_statement,
        report.ledger,
        tuple(m for m in report.matches if m is not fee_match),
        (
            *report.exceptions,
            ExceptionItem(fee_match.bank_entries[0], ExceptionReason.MISSING_FROM_LEDGER),
            ExceptionItem(fee_match.ledger_entries[0], ExceptionReason.MISSING_FROM_BANK),
        ),
    )
    result = evaluate(case, weaker)
    fee = result.by_kind[DiscrepancyKind.PROCESSOR_FEE]
    assert fee.recall is not None
    assert fee.recall < 1.0
    assert fee.precision == 1.0
    assert result.by_kind[DiscrepancyKind.MISSING_FROM_BANK].precision != 1.0
    assert result.by_kind[DiscrepancyKind.CLEAN].recall == 1.0


def test_either_copy_of_a_duplicate_may_be_the_one_flagged() -> None:
    """Swapping which copy was matched must not change the score."""
    case = CaseGenerator().generate(1, Difficulty.SENIOR_ANALYST)
    report = ReconciliationEngine().reconcile(case.bank_statement, case.ledger)
    duplicate = next(x for x in report.exceptions if x.reason.value == "duplicate")
    original_id = duplicate.related_entry_ids[0]
    match = next(
        m
        for m in report.matches
        if original_id in {e.entry_id for e in (*m.bank_entries, *m.ledger_entries)}
    )
    on_bank = duplicate.entry.entry_id in report.bank_statement
    swapped_match = (
        replace(match, bank_entries=(duplicate.entry,))  # type: ignore[arg-type]
        if on_bank
        else replace(match, ledger_entries=(duplicate.entry,))  # type: ignore[arg-type]
    )
    original = (match.bank_entries if on_bank else match.ledger_entries)[0]
    swapped = ReconciliationReport(
        report.business_date,
        report.bank_statement,
        report.ledger,
        tuple(swapped_match if m is match else m for m in report.matches),
        tuple(
            replace(x, entry=original, related_entry_ids=(duplicate.entry.entry_id,))
            if x is duplicate
            else x
            for x in report.exceptions
        ),
    )
    assert evaluate(case, swapped).overall == evaluate(case, report).overall


def test_naive_and_engine_agree_on_the_benchmark_data() -> None:
    statement, ledger = synthetic_day(300, seed=4)
    naive = NaiveReconciler({p.code: p for p in FICTIONAL_PROCESSORS})
    naive_pairs = set(naive.reconcile(list(statement), list(ledger)))
    report = ReconciliationEngine().reconcile(statement, ledger)
    engine_pairs = {
        (m.bank_entries[0].entry_id, m.ledger_entries[0].entry_id) for m in report.matches
    }
    assert naive_pairs == engine_pairs
    assert len(engine_pairs) == 300


def test_benchmark_estimates_large_naive_runs() -> None:
    assert not run_benchmark(100).naive_estimated
    assert run_benchmark(2_500).naive_estimated


def test_engine_cli_prints_a_summary(capsys: pytest.CaptureFixture[str]) -> None:
    assert engine_main(["--seed", "1", "--difficulty", "intern"]) == 0
    assert "balanced: True" in capsys.readouterr().out


def test_evaluation_cli_prints_markdown_tables(capsys: pytest.CaptureFixture[str]) -> None:
    assert evaluation_main(["accuracy", "--seeds", "2"]) == 0
    assert evaluation_main(["benchmark", "--sizes", "50"]) == 0
    out = capsys.readouterr().out
    assert "| Problem type |" in out
    assert "| Entries per side |" in out
