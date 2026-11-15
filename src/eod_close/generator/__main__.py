"""Command-line entry point: write one generated case to disk.

Usage::

    python -m eod_close.generator --seed 42 --difficulty analyst --out cases/demo

Writes ``bank.csv``, ``ledger.csv`` and ``answer_key.json`` to the output
folder and prints a summary of what was planted.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from eod_close.csv_format import write_bank_csv, write_ledger_csv
from eod_close.generator.case_generator import CaseGenerator
from eod_close.generator.difficulty import Difficulty


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments, generate the case and write its files.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser(description="Generate a fictional reconciliation case.")
    parser.add_argument("--seed", type=int, required=True, help="any integer")
    parser.add_argument(
        "--difficulty",
        choices=[d.value for d in Difficulty],
        default=Difficulty.ANALYST.value,
    )
    parser.add_argument("--out", type=Path, required=True, help="output folder")
    args = parser.parse_args(argv)

    case = CaseGenerator().generate(args.seed, Difficulty(args.difficulty))
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    # newline="" stops the csv module's \r\n endings being doubled on Windows.
    with (out / "bank.csv").open("w", encoding="utf-8", newline="") as handle:
        write_bank_csv(case.bank_statement, handle)
    with (out / "ledger.csv").open("w", encoding="utf-8", newline="") as handle:
        write_ledger_csv(case.ledger, handle)
    (out / "answer_key.json").write_text(case.answer_key.to_json(), encoding="utf-8")

    print(f"{case.case_id}  business day {case.business_date:%A %d %B %Y}")
    print(f"{len(case.bank_statement)} bank entries, {len(case.ledger)} ledger entries")
    for kind, count in sorted(Counter(i.kind for i in case.answer_key.items).items()):
        print(f"  {kind.label:<22}{count:>4}")
    print(f"Written to {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
