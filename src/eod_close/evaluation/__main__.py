"""Command-line entry point for the accuracy table and the benchmark.

Usage::

    python -m eod_close.evaluation accuracy --seeds 20
    python -m eod_close.evaluation benchmark --sizes 1000 10000 100000
    python -m eod_close.evaluation benchmark --save

Both print Markdown tables, so the output can be pasted into the README.
``--save`` instead stores the timings for the home page (see ``headline``).
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from eod_close.evaluation.accuracy import AccuracyResult, evaluate_seeds
from eod_close.evaluation.benchmark import run_benchmark
from eod_close.evaluation.headline import SNAPSHOT_PATH, save_benchmark_snapshot
from eod_close.generator import Difficulty, DiscrepancyKind


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value:.1%}"


def print_accuracy(seeds: int) -> None:
    """Print precision and recall per difficulty and per problem type.

    Args:
        seeds: How many seeds (0 to seeds - 1) to score at each level.
    """
    results = {d: evaluate_seeds(d, range(seeds)) for d in Difficulty}
    print(f"Accuracy over seeds 0-{seeds - 1} at each level\n")
    print("| Level | Decisions | Precision | Recall |")
    print("|---|---:|---:|---:|")
    for difficulty, result in results.items():
        overall = result.overall
        print(
            f"| {difficulty.label} | {overall.planted} | {_percent(overall.precision)} "
            f"| {_percent(overall.recall)} |"
        )
    combined = AccuracyResult()
    for result in results.values():
        combined.add(result)
    print("\n| Problem type | Planted | Precision | Recall |")
    print("|---|---:|---:|---:|")
    for kind in DiscrepancyKind:
        score = combined.by_kind[kind]
        print(
            f"| {kind.label} | {score.planted} | {_percent(score.precision)} "
            f"| {_percent(score.recall)} |"
        )


def print_benchmark(sizes: Sequence[int]) -> None:
    """Print engine and naive timings for each size.

    Args:
        sizes: Entries per side for each run.
    """
    print("| Entries per side | Engine | Naive pairwise | Speed-up |")
    print("|---:|---:|---:|---:|")
    for size in sizes:
        row = run_benchmark(size)
        naive = f"{row.naive_seconds:,.2f} s" + (" (est.)" if row.naive_estimated else "")
        print(f"| {size:,} | {row.engine_seconds:,.3f} s | {naive} | {row.speedup:,.0f}x |")


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and run the chosen report.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser(description="Measure the reconciliation engine.")
    commands = parser.add_subparsers(dest="command", required=True)
    accuracy = commands.add_parser("accuracy", help="precision and recall against answer keys")
    accuracy.add_argument("--seeds", type=int, default=20)
    benchmark = commands.add_parser("benchmark", help="engine against naive pairwise")
    benchmark.add_argument("--sizes", type=int, nargs="+", default=[1_000, 10_000, 100_000])
    benchmark.add_argument(
        "--save", action="store_true", help="also save the timings for the home page"
    )
    args = parser.parse_args(argv)

    if args.command == "accuracy":
        print_accuracy(args.seeds)
    elif args.save:
        save_benchmark_snapshot(args.sizes)
        print(f"Saved the benchmark to {SNAPSHOT_PATH}")
    else:
        print_benchmark(args.sizes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
