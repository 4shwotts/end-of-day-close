"""The JSON API: generate cases, reconcile uploaded files and score answers.

Every endpoint's docstring becomes its description in the generated API docs
at ``/docs``, so they are written for API users rather than for maintainers.
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Path, UploadFile

from eod_close.game import InvalidAnswerError, PlayerAnswers, score_answers
from eod_close.generator import Difficulty
from eod_close.web.schemas import (
    MAX_SEED,
    CaseOut,
    CaseRequest,
    ReconcileOut,
    ScoreOut,
    ScoreRequest,
)
from eod_close.web.services import MAX_UPLOAD_BYTES, UploadError, load_case, reconcile_uploads

router = APIRouter(prefix="/api", tags=["API"])

SeedPath = Annotated[int, Path(ge=0, le=MAX_SEED, description="The case's seed.")]


@router.get("/health", tags=["Operations"])
def health() -> dict[str, str]:
    """Report that the service is running. Used by the container and Render health checks."""
    return {"status": "ok"}


@router.post("/cases")
def generate_case(request: CaseRequest) -> CaseOut:
    """Generate a fictional business day to reconcile.

    Returns the bank statement and the ledger, but never the answer key. The
    same difficulty and seed always produce the same day; leave the seed out
    for a random one, and read the seed used from the response.
    """
    seed = request.seed if request.seed is not None else secrets.randbelow(MAX_SEED + 1)
    case, _ = load_case(request.difficulty, seed)
    return CaseOut.of(case)


@router.get("/cases/{difficulty}/{seed}")
def get_case(difficulty: Difficulty, seed: SeedPath) -> CaseOut:
    """Fetch a previously generated day by its difficulty and seed."""
    case, _ = load_case(difficulty, seed)
    return CaseOut.of(case)


@router.post("/cases/{difficulty}/{seed}/score")
def score_case(difficulty: Difficulty, seed: SeedPath, answers: ScoreRequest) -> ScoreOut:
    """Score a player's answers for one day, and compare them with the engine.

    Send each group of linked bank and ledger entries, and each entry marked
    as an exception with its reason. A likely transposition is marked on
    both entries and the two are also sent as one group.

    The response gives accuracy (correct decisions over the larger of planted
    and claimed), whether the day balances, the engine's accuracy on the same
    day, and a review of every planted discrepancy with its explanation.
    """
    case, report = load_case(difficulty, seed)
    player = PlayerAnswers(
        groups=tuple((frozenset(g.bank_ids), frozenset(g.ledger_ids)) for g in answers.groups),
        exceptions={x.entry_id: x.reason for x in answers.exceptions},
        elapsed_seconds=answers.elapsed_seconds,
    )
    try:
        return ScoreOut.of(score_answers(case, player, report))
    except InvalidAnswerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/reconcile")
async def reconcile(
    bank_file: Annotated[
        UploadFile, File(description="Bank statement CSV in the documented format.")
    ],
    ledger_file: Annotated[UploadFile, File(description="Ledger CSV in the documented format.")],
) -> ReconcileOut:
    """Reconcile any two CSV files in the documented format.

    Each file may be up to 2 MB and 20,000 rows. Amounts are exact decimals
    without currency symbols or thousands separators. The column layouts are
    described on the Upload page and in the README.
    """
    # Read one byte past the limit so an oversized file is detected without
    # reading all of it into memory.
    bank = await bank_file.read(MAX_UPLOAD_BYTES + 1)
    ledger = await ledger_file.read(MAX_UPLOAD_BYTES + 1)
    try:
        _, _, report = reconcile_uploads(bank, ledger)
    except UploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ReconcileOut.of(report)
