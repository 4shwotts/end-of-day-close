"""The reconciliation engine: runs the strategies and builds the report.

The engine owns the one rule no strategy may break: an entry is used at most
once. It runs the strategies in order, ranks each strategy's proposals in a
``CandidateQueue`` and accepts them best first, skipping any that reuses a
claimed entry. Whatever is left at the end becomes an exception.
"""

from __future__ import annotations

from collections.abc import Sequence

from eod_close.domain.entries import BankEntry, LedgerEntry
from eod_close.domain.processor import FICTIONAL_PROCESSORS, PaymentProcessor
from eod_close.domain.results import (
    ExceptionItem,
    ExceptionReason,
    Match,
    MatchRule,
    ReconciliationReport,
)
from eod_close.domain.statements import BankStatement, Ledger
from eod_close.engine.base import Candidate, MatchContext, MatchStrategy
from eod_close.engine.candidate_queue import CandidateQueue
from eod_close.engine.config import EngineConfig
from eod_close.engine.strategies import default_strategies


class ReconciliationEngine:
    """Reconciles a bank statement against a ledger.

    Strategies run one after another, strongest evidence first. Within one
    strategy, the candidate queue settles conflicts by confidence. Across
    strategies, order decides: an exact match is never displaced by a fuzzy
    one, however confident.

    Complexity: the sum of the strategies' costs plus O(k log k) to rank k
    candidates. With the default strategies this is O((n + m) log(n + m))
    for typical inputs, plus the bounded batch search.

    Args:
        processors: The processors the engine knows about.
        config: Thresholds for the strategies.
        strategies: The strategies to run, in order. Defaults to
            ``default_strategies()``.
    """

    def __init__(
        self,
        processors: Sequence[PaymentProcessor] = FICTIONAL_PROCESSORS,
        config: EngineConfig | None = None,
        strategies: Sequence[MatchStrategy] | None = None,
    ) -> None:
        self._processors = {p.code: p for p in processors}
        self._config = config or EngineConfig()
        self._strategies = list(strategies) if strategies is not None else default_strategies()

    @property
    def config(self) -> EngineConfig:
        """The thresholds in use."""
        return self._config

    def reconcile(self, bank_statement: BankStatement, ledger: Ledger) -> ReconciliationReport:
        """Reconcile one day.

        Args:
            bank_statement: The bank side.
            ledger: The ledger side.

        Returns:
            A balanced report: every entry is in exactly one match or
            exception.
        """
        context = MatchContext(bank_statement, ledger, self._processors, self._config)
        accepted: list[Candidate] = []
        for strategy in self._strategies:
            queue = CandidateQueue(strategy.propose(context))
            accepted.extend(c for c in queue.drain() if context.claim(c))

        matches = [self._to_match(c, bank_statement, ledger) for c in accepted if c.is_match]
        exceptions = [
            item
            for c in accepted
            if not c.is_match
            for item in self._to_exceptions(c, bank_statement, ledger)
        ]
        exceptions.extend(
            ExceptionItem(
                e,
                ExceptionReason.MISSING_FROM_LEDGER,
                "No ledger entry explains this bank entry.",
            )
            for e in context.unmatched_bank()
        )
        exceptions.extend(
            ExceptionItem(
                e,
                ExceptionReason.MISSING_FROM_BANK,
                "No bank entry explains this ledger entry.",
            )
            for e in context.unmatched_ledger()
        )
        return ReconciliationReport(
            business_date=bank_statement.statement_date,
            bank_statement=bank_statement,
            ledger=ledger,
            matches=tuple(matches),
            exceptions=tuple(exceptions),
        )

    @staticmethod
    def _to_match(candidate: Candidate, bank: BankStatement, ledger: Ledger) -> Match:
        assert isinstance(candidate.resolution, MatchRule)
        return Match(
            bank_entries=tuple(bank.get(i) for i in candidate.bank_ids),
            ledger_entries=tuple(ledger.get(i) for i in candidate.ledger_ids),
            rule=candidate.resolution,
            confidence=candidate.confidence,
            note=candidate.note,
        )

    @staticmethod
    def _to_exceptions(
        candidate: Candidate, bank: BankStatement, ledger: Ledger
    ) -> list[ExceptionItem]:
        assert isinstance(candidate.resolution, ExceptionReason)
        entries: list[BankEntry | LedgerEntry] = [bank.get(i) for i in candidate.bank_ids]
        entries.extend(ledger.get(i) for i in candidate.ledger_ids)
        all_ids = {e.entry_id for e in entries}
        # Each flagged entry points at the others in the same finding, so a
        # transposition's bank and ledger lines can be shown side by side.
        return [
            ExceptionItem(
                entry,
                candidate.resolution,
                candidate.note,
                related_entry_ids=tuple(sorted(all_ids - {entry.entry_id})) + candidate.related_ids,
            )
            for entry in entries
        ]
