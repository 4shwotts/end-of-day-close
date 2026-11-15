"""The headline figures shown on the home page, taken from the real evaluators.

Accuracy is cheap to measure (well under a second for every level), so it is
computed from the answer keys the first time it is asked for and then kept.

The benchmark is not: timing 100,000 entries takes several seconds and the
result depends on the machine. It is measured once with
``python -m eod_close.evaluation benchmark --save`` and stored in
``benchmark_snapshot.json`` beside this module, together with the date it was
taken, so the page can say where the figure came from. If the snapshot is
missing the page leaves those figures out rather than inventing them.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Final

from eod_close.evaluation.accuracy import AccuracyResult, evaluate_seeds
from eod_close.evaluation.benchmark import BenchmarkRow, run_benchmark
from eod_close.generator.difficulty import Difficulty

ACCURACY_SEEDS: Final = range(20)
"""The same seeds the README's accuracy table and the evaluator's default use."""

SNAPSHOT_PATH: Final = Path(__file__).with_name("benchmark_snapshot.json")


@dataclass(frozen=True)
class AccuracyFigures:
    """Accuracy over every level, and on the hardest level alone.

    Both are recall: the share of planted decisions the engine got exactly
    right (same entries, same outcome).

    Attributes:
        overall: Recall over every level combined.
        hardest: Recall at the hardest level.
        decisions: How many planted decisions ``overall`` covers.
        days: How many generated days they come from.
    """

    overall: float
    hardest: float
    decisions: int
    days: int


@dataclass(frozen=True)
class BenchmarkSnapshot:
    """A saved benchmark run.

    Attributes:
        measured_on: The date of the run.
        rows: The timings, smallest size first.
    """

    measured_on: date
    rows: tuple[BenchmarkRow, ...]

    @property
    def largest(self) -> BenchmarkRow:
        """The run with the most entries."""
        return max(self.rows, key=lambda row: row.size)

    @property
    def entries_per_second(self) -> int:
        """Bank and ledger entries reconciled per second on the largest run."""
        row = self.largest
        return round(2 * row.size / row.engine_seconds)


@lru_cache(maxsize=1)
def accuracy_figures() -> AccuracyFigures:
    """Measure the engine against the answer keys at every level.

    Cached, so only the first caller pays the cost (well under a second).

    Returns:
        Overall and hardest-level accuracy.
    """
    results = {level: evaluate_seeds(level, ACCURACY_SEEDS) for level in Difficulty}
    combined = AccuracyResult()
    for result in results.values():
        combined.add(result)
    hardest = results[list(Difficulty)[-1]].overall
    return AccuracyFigures(
        overall=combined.overall.recall or 0.0,
        hardest=hardest.recall or 0.0,
        decisions=combined.overall.planted,
        days=len(ACCURACY_SEEDS) * len(results),
    )


def save_benchmark_snapshot(
    sizes: Sequence[int], path: Path = SNAPSHOT_PATH, *, today: date | None = None
) -> BenchmarkSnapshot:
    """Run the benchmark and save the result for the home page.

    Args:
        sizes: Entries per side for each run.
        path: Where to write the snapshot.
        today: The date to record; defaults to today.

    Returns:
        The snapshot that was written.
    """
    snapshot = BenchmarkSnapshot(
        measured_on=today or date.today(),
        rows=tuple(run_benchmark(size) for size in sorted(sizes)),
    )
    payload = {
        "measured_on": snapshot.measured_on.isoformat(),
        "rows": [asdict(row) for row in snapshot.rows],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return snapshot


def load_benchmark_snapshot(path: Path = SNAPSHOT_PATH) -> BenchmarkSnapshot | None:
    """Read the saved benchmark, if there is one.

    Args:
        path: The snapshot file.

    Returns:
        The snapshot, or None if the file does not exist.
    """
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return BenchmarkSnapshot(
        measured_on=date.fromisoformat(payload["measured_on"]),
        rows=tuple(BenchmarkRow(**row) for row in payload["rows"]),
    )
