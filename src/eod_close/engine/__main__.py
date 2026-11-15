"""Command-line entry point: reconcile one generated case and summarise it.

Usage::

    python -m eod_close.engine --seed 42 --difficulty head-of-ops

Prints what the generator planted next to what the engine found. This is a
quick visual check; the scored comparison is the accuracy evaluator.
"""

from __future__ import annotations

import argparse
import time
from collections import Counter
from collections.abc import Sequence
from typing import Final

from eod_close.domain.results import ExceptionReason, MatchRule
from eod_close.engine.engine import ReconciliationEngine
from eod_close.evaluation.accuracy import EXPECTED_RESOLUTION
from eod_close.generator import CaseGenerator, Difficulty

# A duplicate's original pair is planted as CLEAN, so the exact-match count
# includes those pairs.
EXPECTED: Final = EXPECTED_RESOLUTION


def main(argv: Sequence[str] | None = None) -> int:
    """Generate, reconcile and print a planted-versus-found table.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser(description="Reconcile a generated case.")
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument(
        "--difficulty", choices=[d.value for d in Difficulty], default=Difficulty.ANALYST.value
    )
    args = parser.parse_args(argv)

    case = CaseGenerator().generate(args.seed, Difficulty(args.difficulty))
    started = time.perf_counter()
    report = ReconciliationEngine().reconcile(case.bank_statement, case.ledger)
    elapsed_ms = (time.perf_counter() - started) * 1000

    planted = Counter(EXPECTED[item.kind] for item in case.answer_key.items)
    found: Counter[MatchRule | ExceptionReason] = Counter(m.rule for m in report.matches)
    # A transposition flags two entries but is one finding, so exceptions are
    # counted per finding rather than per entry.
    findings = {
        (item.reason, tuple(sorted((item.entry.entry_id, *item.related_entry_ids))))
        for item in report.exceptions
    }
    found.update(reason for reason, _ in findings)

    print(f"{case.case_id}: reconciled in {elapsed_ms:.1f} ms, balanced: {report.is_balanced}")
    print(f"  {'Resolution':<24}{'Planted':>8}{'Found':>8}")
    for resolution in EXPECTED.values():
        print(f"  {resolution.value:<24}{planted[resolution]:>8}{found[resolution]:>8}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
