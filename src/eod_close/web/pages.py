"""The server-rendered pages: reconcile, training mode and the algorithm arena.

Reconcile is the home page: upload two files, or open a generated sample
day, and see every match explained and everything that needs review.
Training mode is the game: work a day by hand and compare with the engine.

Pages are Jinja templates. HTMX swaps in partial responses for the progress
check, the arena results and the upload results; every form also works as a
plain form post without JavaScript.
"""

from __future__ import annotations

import base64
import contextlib
import io
import secrets
import time
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Final

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from eod_close.csv_format import BANK_COLUMNS, LEDGER_COLUMNS, write_bank_csv, write_ledger_csv
from eod_close.domain.results import ExceptionReason, ReconciliationReport
from eod_close.evaluation.benchmark import NAIVE_FULL_RUN_LIMIT, run_benchmark
from eod_close.game import score_answers
from eod_close.generator import Difficulty
from eod_close.generator.answer_key import DiscrepancyKind
from eod_close.web.chart import build_timing_chart
from eod_close.web.report_view import build_report_view, report_csv
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

# The seed and difficulty of the example files offered beside the upload form.
SAMPLE_SEED: Final = 1
SAMPLE_DIFFICULTY: Final = Difficulty.ANALYST

# "Try a sample day" uses the hardest level, because it is the only one that
# contains every kind of problem, including possible typos.
SAMPLE_DAY_DIFFICULTY: Final = Difficulty.HEAD_OF_OPS

# Read-only on purpose: TemplateResponse adds the current request to the
# context it is given, so passing this mapping itself would keep the first
# visitor's request and reuse it for every later page (BUG-005). Pages pass
# a fresh copy instead, and a mutation attempt fails loudly.
_FORMAT_CONTEXT: Final = MappingProxyType(
    {"bank_columns": BANK_COLUMNS, "ledger_columns": LEDGER_COLUMNS}
)


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _results_context(report: ReconciliationReport, csv_name: str) -> dict[str, object]:
    """Build the template values shared by every results page.

    The CSV report travels inside the page as a ``data:`` link. Uploaded
    files are never stored, so there is nothing a later download request
    could fetch; embedding the report also keeps the upload form a single
    form that works with or without JavaScript.

    Args:
        report: The engine's report.
        csv_name: The file name the browser should save the report as.

    Returns:
        The report view, the report's download link and its file name.
    """
    view = build_report_view(report)
    encoded = base64.b64encode(report_csv(view).encode("utf-8")).decode("ascii")
    return {
        "view": view,
        "csv_href": f"data:text/csv;charset=utf-8;base64,{encoded}",
        "csv_name": csv_name,
    }


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    """Reconcile, the home page: upload two files or try a sample day."""
    return templates.TemplateResponse(request, "home.html", dict(_FORMAT_CONTEXT))


@router.get("/sample")
def new_sample_day() -> RedirectResponse:
    """Pick a random sample day and redirect to its own, shareable address."""
    return RedirectResponse(f"/sample/{secrets.randbelow(MAX_SEED + 1)}", status_code=303)


@router.get("/sample/{seed}", response_class=HTMLResponse)
def sample_day(request: Request, seed: int) -> HTMLResponse:
    """Reconcile a generated day and show the results."""
    _check_seed(seed)
    case, report = load_case(SAMPLE_DAY_DIFFICULTY, seed)
    context = _results_context(report, f"sample-day-{seed}-report.csv")
    context.update(case=case)
    return templates.TemplateResponse(request, "sample.html", context)


@router.get("/sample/{seed}/{name}.csv")
def sample_day_file(seed: int, name: str) -> Response:
    """Download a sample day's bank file, ledger file or report."""
    _check_seed(seed)
    case, report = load_case(SAMPLE_DAY_DIFFICULTY, seed)
    buffer = io.StringIO()
    if name == "bank":
        write_bank_csv(case.bank_statement, buffer)
    elif name == "ledger":
        write_ledger_csv(case.ledger, buffer)
    elif name == "report":
        buffer.write(report_csv(build_report_view(report)))
    else:
        raise HTTPException(status_code=404)
    return _csv_response(buffer.getvalue(), f"sample-day-{seed}-{name}.csv")


@router.get("/training", response_class=HTMLResponse)
def training(request: Request) -> HTMLResponse:
    """Training mode: choose a level and start a day."""
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


@router.get("/upload")
def upload_page() -> RedirectResponse:
    """The upload form now lives on the home page; old links still work."""
    return RedirectResponse("/#upload", status_code=301)


@router.post("/upload", response_class=HTMLResponse)
async def upload(
    request: Request,
    bank_file: Annotated[UploadFile, File()],
    ledger_file: Annotated[UploadFile, File()],
) -> HTMLResponse:
    """Reconcile two uploaded files and show the results."""
    context: dict[str, object] = dict(_FORMAT_CONTEXT)
    try:
        _, _, report = reconcile_uploads(
            await bank_file.read(MAX_UPLOAD_BYTES + 1), await ledger_file.read(MAX_UPLOAD_BYTES + 1)
        )
        context.update(_results_context(report, "reconciliation-report.csv"))
        status = 200
    except UploadError as exc:
        context["error"] = str(exc)
        status = 422
    template = "_results.html" if _is_htmx(request) else "home.html"
    # HTMX does not swap error responses by default, so its partial is sent
    # with 200 and the error is shown in the page instead.
    return templates.TemplateResponse(
        request, template, context, status_code=200 if _is_htmx(request) else status
    )


@router.get("/samples/{name}.csv")
def sample_file(name: str) -> Response:
    """Download an example bank or ledger file in the upload format."""
    case, _ = load_case(SAMPLE_DIFFICULTY, SAMPLE_SEED)
    buffer = io.StringIO()
    if name == "bank":
        write_bank_csv(case.bank_statement, buffer)
    elif name == "ledger":
        write_ledger_csv(case.ledger, buffer)
    else:
        raise HTTPException(status_code=404)
    return _csv_response(buffer.getvalue(), f"sample-{name}.csv")


def _csv_response(text: str, filename: str) -> Response:
    return Response(
        text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _check_seed(seed: int) -> None:
    if not 0 <= seed <= MAX_SEED:
        raise HTTPException(status_code=404, detail="No such day")
