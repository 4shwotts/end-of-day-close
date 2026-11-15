"""The matching engine: strategies, candidate ranking and the engine itself."""

from eod_close.engine.base import Candidate, MatchContext, MatchStrategy
from eod_close.engine.candidate_queue import CandidateQueue
from eod_close.engine.config import EngineConfig
from eod_close.engine.engine import ReconciliationEngine
from eod_close.engine.strategies import default_strategies

__all__ = [
    "Candidate",
    "CandidateQueue",
    "EngineConfig",
    "MatchContext",
    "MatchStrategy",
    "ReconciliationEngine",
    "default_strategies",
]
