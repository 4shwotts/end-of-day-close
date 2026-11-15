"""Headline figure tests: live accuracy and the saved benchmark snapshot."""

from datetime import date
from pathlib import Path

from eod_close.evaluation.accuracy import evaluate_seeds
from eod_close.evaluation.headline import (
    ACCURACY_SEEDS,
    SNAPSHOT_PATH,
    accuracy_figures,
    load_benchmark_snapshot,
    save_benchmark_snapshot,
)
from eod_close.generator import Difficulty


def test_accuracy_figures_match_a_fresh_evaluation() -> None:
    figures = accuracy_figures()
    hardest = evaluate_seeds(Difficulty.HEAD_OF_OPS, ACCURACY_SEEDS).overall
    assert figures.hardest == hardest.recall
    assert figures.days == len(ACCURACY_SEEDS) * len(Difficulty)
    assert figures.decisions > hardest.planted
    assert figures.hardest <= figures.overall <= 1.0


def test_a_saved_benchmark_reads_back_the_same(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.json"
    saved = save_benchmark_snapshot([250, 100], path, today=date(2026, 11, 15))
    loaded = load_benchmark_snapshot(path)
    assert loaded == saved
    assert [row.size for row in loaded.rows] == [100, 250]
    assert loaded.largest.size == 250
    assert loaded.entries_per_second == round(500 / loaded.largest.engine_seconds)


def test_a_missing_snapshot_gives_no_figures(tmp_path: Path) -> None:
    assert load_benchmark_snapshot(tmp_path / "absent.json") is None


def test_the_shipped_snapshot_covers_100000_entries() -> None:
    """The home page quotes the time for 100,000 entries, so the snapshot must have it."""
    snapshot = load_benchmark_snapshot(SNAPSHOT_PATH)
    assert snapshot is not None
    assert snapshot.largest.size == 100_000
    assert snapshot.largest.naive_estimated
    assert snapshot.largest.speedup > 1
