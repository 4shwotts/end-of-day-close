"""Measuring the engine: accuracy against answer keys, and speed against a naive baseline."""

from eod_close.evaluation.accuracy import (
    EXPECTED_RESOLUTION,
    AccuracyResult,
    KindScore,
    evaluate,
    evaluate_seeds,
)
from eod_close.evaluation.benchmark import BenchmarkRow, run_benchmark, synthetic_day
from eod_close.evaluation.naive import NaiveReconciler

__all__ = [
    "EXPECTED_RESOLUTION",
    "AccuracyResult",
    "BenchmarkRow",
    "KindScore",
    "NaiveReconciler",
    "evaluate",
    "evaluate_seeds",
    "run_benchmark",
    "synthetic_day",
]
