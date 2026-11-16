"""Engine timing: measured on the run that produced the report, and shown in plain words."""

import pytest

from eod_close.generator import Difficulty
from eod_close.web import pages
from eod_close.web.services import (
    engine_seconds,
    load_case,
    parse_uploads,
    timed_reconcile,
)
from eod_close.web.workspace import SAMPLE_DAY_DIR


def test_a_case_keeps_the_time_of_the_run_that_solved_it() -> None:
    first = engine_seconds(Difficulty.INTERN, 7)
    assert 0 < first < 5
    load_case(Difficulty.INTERN, 7)
    assert engine_seconds(Difficulty.INTERN, 7) == first, "cached with the report, not re-run"


def test_timing_covers_only_the_engine() -> None:
    statement, ledger = parse_uploads(
        (SAMPLE_DAY_DIR / "bank.csv").read_bytes(), (SAMPLE_DAY_DIR / "ledger.csv").read_bytes()
    )
    report, seconds = timed_reconcile(statement, ledger)
    assert len(report.matches) == 10
    assert seconds > 0


@pytest.mark.parametrize(
    ("value", "words"),
    [
        (0.0004, "under 0.001 seconds"),
        (0.0312, "0.031 seconds"),
        (4.28, "4.3 seconds"),
    ],
)
def test_times_are_shown_in_plain_seconds(value: float, words: str) -> None:
    assert pages._in_seconds(value) == words
