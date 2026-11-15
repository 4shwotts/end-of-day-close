"""The server-rendered game: start a day, work it, close it, plus the arena and upload.

Pages are Jinja templates. HTMX swaps in partial responses for the progress
check, the arena results and the upload results; every form also works as a
plain form post without JavaScript.
"""

from __future__ import annotations

import contextlib
import io
import secrets
import time
from pathlib import Path
from typing import Annotated, Final

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from eod_close.csv_format import BANK_COLUMNS, LEDGER_COLUMNS, write_bank_csv, write_ledger_csv
from eod_close.domain.results import ExceptionReason
from eod_close.evaluation.benchmark import NAIVE_FULL_RUN_LIMIT, run_benchmark
from eod_close.game import score_answers
from eod_close.generator import Difficulty
from eod_close.generator.answer_key import DiscrepancyKind
from eod_close.web.chart import build_timing_chart
from eod_close.web.schemas import MAX_SEED
from eod_close.web.services import (
    MAX_UPLOAD_BYTES,
    UploadError,
    answers_from_form,
    load_case,
    reconcile_uploads,
)

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.globals.update(
    difficulties=list(Difficulty),
    reasons=list(ExceptionReason),
    kinds=list(DiscrepancyKind),
)


def _percent(value: float) -> str:
    return f"{value:.1%}"


def _duration(seconds: int | None) -> str:
    if seconds is None:
        return "n/a"
    minutes, rest = divmod(seconds, 60)
    return f"{minutes}:{rest:02d}"


def _seconds(value: float) -> str:
    return f"{value:,.2f} s" if value >= 1 else f"{value * 1000:,.1f} ms"


templates.env.filters.update(percent=_percent, duration=_duration, seconds=_seconds)

router = APIRouter(include_in_schema=False)

# A fixed menu of sizes rather than free input, so nobody can ask a shared
# instance to time a naive run on a million rows.
ARENA_SIZES: Final = (100, 250, 500, 1_000, 2_500, 5_000)

# The seed and difficulty of the sample files offered on the upload page.
SAMPLE_SEED: Final = 1
SAMPLE_DIFFICULTY: Final = Difficulty.ANALYST


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


@router.get("/", response_class=HTMLResponse)
def start(request: Request) -> HTMLResponse:
    """The start page: choose a difficulty."""
    return templates.TemplateResponse(request, "start.html")


@router.get("/play")
def new_day(
    difficulty: Difficulty = Difficulty.INTERN,
    seed: Annotated[str, Query(max_length=10)] = "",
) -> RedirectResponse:
    """Pick a seed if none was given and redirect to the case's own URL."""
    # Taken as text because a form with the day number left blank sends
    # "seed=", which an integer parameter would reject (BUG-004).
    if not seed.strip():
        chosen = secrets.randbelow(MAX_SEED + 1)
    elif seed.strip().isdigit() and int(seed) <= MAX_SEED:
        chosen = int(seed)
    else:
        raise HTTPException(status_code=422, detail="The day number must be a whole number.")
    return RedirectResponse(f"/play/{difficulty.value}/{chosen}", status_code=303)


@router.get("/play/{difficulty}/{seed}", response_class=HTMLResponse)
def work_case(request: Request, difficulty: Difficulty, seed: int) -> HTMLResponse:
    """The work-the-case page: bank and ledger side by side."""
    _check_seed(seed)
    case, _ = load_case(difficulty, seed)
    return templates.TemplateResponse(
        request, "play.html", {"case": case, "started_at": int(time.time())}
    )


@router.post("/play/{difficulty}/{seed}/check", response_class=HTMLResponse)
async def check_progress(request: Request, difficulty: Difficulty, seed: int) -> HTMLResponse:
    """HTMX partial: how much is still unassigned, without revealing answers."""
    _check_seed(seed)
    case, report = load_case(difficulty, seed)
    form = {k: str(v) for k, v in (await request.form()).items()}
    score = score_answers(case, answers_from_form(case, form, None), report)
    return templates.TemplateResponse(
        request,
        "_progress.html",
        {"unassigned": len(score.unassigned_ids), "one_sided": score.one_sided_groups},
    )


@router.post("/play/{difficulty}/{seed}/close", response_class=HTMLResponse)
async def close_day(request: Request, difficulty: Difficulty, seed: int) -> HTMLResponse:
    """Score the day and show the engine's solution and every explanation."""
    _check_seed(seed)
    case, report = load_case(difficulty, seed)
    form = {k: str(v) for k, v in (await request.form()).items()}
    elapsed: int | None = None
    with contextlib.suppress(ValueError):
        elapsed = int(time.time()) - int(form.get("started_at", ""))
    # The start time comes from the page, so it is trusted only for display;
    # a negative or day-long value is dropped rather than shown.
    if elapsed is not None and not 0 <= elapsed <= 86_400:
        elapsed = None
    score = score_answers(case, answers_from_form(case, form, elapsed), report)
    return templates.TemplateResponse(
        request, "close.html", {"case": case, "score": score, "report": report}
    )


@router.get("/arena", response_class=HTMLResponse)
def arena(request: Request) -> HTMLResponse:
    """The algorithm arena page."""
    return templates.TemplateResponse(
        request,
        "arena.html",
        {"sizes": ARENA_SIZES, "naive_limit": NAIVE_FULL_RUN_LIMIT},
    )


@router.post("/arena/run", response_class=HTMLResponse)
async def arena_run(request: Request) -> HTMLResponse:
    """Time both approaches on the chosen sizes and return the chart."""
    form = await request.form()
    requested = {int(str(v)) for v in form.getlist("size") if str(v).isdigit()}
    chosen = sorted(requested & set(ARENA_SIZES))
    context: dict[str, object] = {"sizes": ARENA_SIZES, "naive_limit": NAIVE_FULL_RUN_LIMIT}
    if len(chosen) < 2:
        context["error"] = "Choose at least two sizes to compare."
    else:
        rows = [run_benchmark(size) for size in chosen]
        context.update(rows=rows, chart=build_timing_chart(rows))
    template = "_arena_results.html" if _is_htmx(request) else "arena.html"
    return templates.TemplateResponse(request, template, context)


@router.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request) -> HTMLResponse:
    """The upload page, with the file format documented."""
    return templates.TemplateResponse(
        request,
        "upload.html",
        {"bank_columns": BANK_COLUMNS, "ledger_columns": LEDGER_COLUMNS},
    )


@router.post("/upload", response_class=HTMLResponse)
async def upload(
    request: Request,
    bank_file: Annotated[UploadFile, File()],
    ledger_file: Annotated[UploadFile, File()],
) -> HTMLResponse:
    """Reconcile two uploaded files and show the report."""
    context: dict[str, object] = {"bank_columns": BANK_COLUMNS, "ledger_columns": LEDGER_COLUMNS}
    try:
        statement, ledger, report = reconcile_uploads(
            await bank_file.read(MAX_UPLOAD_BYTES + 1), await ledger_file.read(MAX_UPLOAD_BYTES + 1)
        )
        context.update(report=report, bank_count=len(statement), ledger_count=len(ledger))
        status = 200
    except UploadError as exc:
        context["error"] = str(exc)
        status = 422
    template = "_upload_results.html" if _is_htmx(request) else "upload.html"
    # HTMX does not swap error responses by default, so its partial is sent
    # with 200 and the error is shown in the page instead.
    return templates.TemplateResponse(
        request, template, context, status_code=200 if _is_htmx(request) else status
    )


@router.get("/samples/{name}.csv")
def sample_file(name: str) -> Response:
    """Download a sample bank or ledger file in the upload format."""
    case, _ = load_case(SAMPLE_DIFFICULTY, SAMPLE_SEED)
    buffer = io.StringIO()
    if name == "bank":
        write_bank_csv(case.bank_statement, buffer)
    elif name == "ledger":
        write_ledger_csv(case.ledger, buffer)
    else:
        raise HTTPException(status_code=404)
    return Response(
        buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="sample-{name}.csv"'},
    )


def _check_seed(seed: int) -> None:
    if not 0 <= seed <= MAX_SEED:
        raise HTTPException(status_code=404, detail="No such day")
