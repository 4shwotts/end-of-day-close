"""Times the naive pairwise reconciler against the engine at growing sizes.

The benchmark data is synthetic and simple on purpose: exact matches, fee
deductions and settlement delays only, which both reconcilers can solve. That
keeps the comparison about algorithmic cost rather than about which one knows
more rules.

At large sizes the naive approach is far too slow to run in full: 100,000
entries means about five billion comparisons. Above a threshold it is timed on
a sample of bank entries and scaled up, and the result is marked as estimated.
The scaling is linear because each naive scan costs about the same however
many entries are already claimed (see ``NaiveReconciler.reconcile``).
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import date
from typing import Final

from eod_close.domain.business_days import add_business_days
from eod_close.domain.entries import BankEntry, LedgerEntry
from eod_close.domain.money import Money
from eod_close.domain.processor import BRAMBLE, DIRECT, FICTIONAL_PROCESSORS, OTTERLINE
from eod_close.domain.statements import BankStatement, Ledger
from eod_close.engine.engine import ReconciliationEngine
from eod_close.evaluation.naive import NaiveReconciler

BENCHMARK_DATE: Final = date(2026, 11, 13)

# Above this many entries, the naive run is sampled rather than run in full.
NAIVE_FULL_RUN_LIMIT: Final = 2_000
NAIVE_SAMPLE_SIZE: Final = 200


def synthetic_day(size: int, seed: int = 0) -> tuple[BankStatement, Ledger]:
    """Build a day of ``size`` bank and ``size`` ledger entries that all reconcile.

    Args:
        size: Entries per side.
        seed: Seed for amounts, rule mix and shuffling.

    Returns:
        The bank statement and ledger, each shuffled independently.
    """
    rng = random.Random(seed)
    bank: list[BankEntry] = []
    ledger: list[LedgerEntry] = []
    for n in range(size):
        reference = f"ORD-{n:07d}"
        gross = Money.from_minor_units(rng.randint(500, 100_000))
        roll = rng.random()
        if roll < 0.6:
            processor, delay, received = DIRECT, 0, gross
        elif roll < 0.8:
            processor, delay, received = BRAMBLE, 1, BRAMBLE.settlement_amount(gross)
        else:
            processor, delay, received = OTTERLINE, rng.randint(1, 3), gross
        ledger.append(
            LedgerEntry(
                entry_id=f"L{n:07d}",
                reference=reference,
                amount=gross,
                value_date=add_business_days(BENCHMARK_DATE, -delay),
                processor_code=processor.code,
            )
        )
        bank.append(
            BankEntry(
                entry_id=f"B{n:07d}",
                reference=reference,
                amount=received,
                value_date=BENCHMARK_DATE,
                counterparty=processor.code,
            )
        )
    rng.shuffle(bank)
    rng.shuffle(ledger)
    statement = BankStatement(
        bank, bank_name="Benchmark", account_name="Benchmark", statement_date=BENCHMARK_DATE
    )
    return statement, Ledger(ledger, entity_name="Benchmark")


@dataclass(frozen=True)
class BenchmarkRow:
    """Timings at one size.

    Attributes:
        size: Entries per side.
        engine_seconds: Wall-clock time for the engine.
        naive_seconds: Wall-clock time for the naive reconciler.
        naive_estimated: True if the naive time was extrapolated from a sample.
    """

    size: int
    engine_seconds: float
    naive_seconds: float
    naive_estimated: bool

    @property
    def speedup(self) -> float:
        """How many times faster the engine was."""
        return self.naive_seconds / self.engine_seconds if self.engine_seconds else float("inf")


def run_benchmark(size: int, seed: int = 0) -> BenchmarkRow:
    """Time both reconcilers on one synthetic day.

    Args:
        size: Entries per side.
        seed: Seed for the synthetic data.

    Returns:
        The timings.
    """
    statement, ledger = synthetic_day(size, seed)

    started = time.perf_counter()
    ReconciliationEngine().reconcile(statement, ledger)
    engine_seconds = time.perf_counter() - started

    naive = NaiveReconciler({p.code: p for p in FICTIONAL_PROCESSORS})
    bank_rows = list(statement)
    estimated = size > NAIVE_FULL_RUN_LIMIT
    sample = bank_rows[:NAIVE_SAMPLE_SIZE] if estimated else bank_rows
    started = time.perf_counter()
    naive.reconcile(sample, list(ledger))
    naive_seconds = (time.perf_counter() - started) * (len(bank_rows) / max(len(sample), 1))
    return BenchmarkRow(size, engine_seconds, naive_seconds, estimated)
