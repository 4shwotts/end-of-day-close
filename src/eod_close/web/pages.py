"""The server-rendered pages: reconcile, training mode and how it works.

The home page is a small sample day's bank statement and ledger side by
side, with a Reconcile button that puts the engine's outcome on every row.
Upload runs the same engine on your own files; a generated harder day shows
the full results view. Training mode is the game: work a day by hand and
compare with the engine. How it works explains the approach and races the
engine against the obvious one.

Pages are Jinja templates. HTMX swaps in partial responses for the
reconciled workspace, the progress check, the race results and the upload
results; every form also works as a plain form post without JavaScript.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import secrets
import time
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Final

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from eod_close.csv_format import BANK_COLUMNS, LEDGER_COLUMNS, write_bank_csv, write_ledger_csv
from eod_close.domain.results import ExceptionReason, ReconciliationReport
from eod_close.evaluation.benchmark import NAIVE_FULL_RUN_LIMIT, run_benchmark
from eod_close.evaluation.headline import accuracy_figures, load_benchmark_snapshot
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
from eod_close.web.workspace import build_workspace, load_sample_day, sample_day_csv

STATIC_DIR: Final = Path(__file__).parent / "static"


@cache
def static_url(path: str) -> str:
    """Return a static file's address, versioned by a hash of its content.

    Without a version, a browser may keep using a stylesheet it fetched
    before the site changed, and render new pages with old styles (BUG-006).
    The address changes whenever the file does, so a new version is always
    fetched, and an unchanged file can be cached for good. Cached per path
    because the files cannot change while the server runs.

    Args:
        path: The file's path inside the static folder, such as ``app.css``.

    Returns:
        The address to link to, such as ``/static/app.css?v=1a2b3c4d5e6f``.
    """
    digest = hashlib.sha256((STATIC_DIR / path).read_bytes()).hexdigest()[:12]
    return f"/static/{path}?v={digest}"


templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.globals.update(
    static_url=static_url,
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
RACE_SIZES: Final = (100, 250, 500, 1_000, 2_500, 5_000)

# The seed and difficulty of the example files offered beside the upload form.
SAMPLE_SEED: Final = 1
SAMPLE_DIFFICULTY: Final = Difficulty.ANALYST

# "Try a harder day" uses the hardest level, because it is the only one that
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


def _workspace_context(*, reconciled: bool) -> dict[str, object]:
    """Build the home page's workspace for the fixed sample day.

    Args:
        reconciled: Whether to show the engine's outcome on the rows.

    Returns:
        The workspace.
    """
    statement, ledger, report = load_sample_day()
    return {"workspace": build_workspace(statement, ledger, report if reconciled else None)}


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    """The home page: the sample day's two lists, ready to reconcile."""
    return templates.TemplateResponse(request, "home.html", _workspace_context(reconciled=False))


@router.post("/reconcile", response_class=HTMLResponse)
def reconcile_sample_day(request: Request) -> HTMLResponse:
    """Reconcile the sample day.

    HTMX swaps in just the workspace; without JavaScript the same form posts
    here and gets the whole page back, already reconciled.
    """
    template = "_workspace.html" if _is_htmx(request) else "home.html"
    return templates.TemplateResponse(request, template, _workspace_context(reconciled=True))


@router.get("/sample-day/{name}.csv")
def sample_day_download(name: str) -> Response:
    """Download the home page's sample day: its bank file, ledger file or report."""
    if name in ("bank", "ledger"):
        text = sample_day_csv("bank" if name == "bank" else "ledger")
    elif name == "report":
        text = report_csv(build_report_view(load_sample_day()[2]))
    else:
        raise HTTPException(status_code=404)
    return _csv_response(text, f"sample-day-{name}.csv")


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
def briefing(request: Request, difficulty: Difficulty, seed: int) -> HTMLResponse:
    """The briefing: how to work the day, read before the clock starts."""
    _check_seed(seed)
    case, _ = load_case(difficulty, seed)
    return templates.TemplateResponse(request, "briefing.html", {"case": case})


@router.get("/play/{difficulty}/{seed}/work", response_class=HTMLResponse)
def work_case(request: Request, difficulty: Difficulty, seed: int) -> HTMLResponse:
    """The work-the-case page: bank and ledger side by side, with the clock running.

    The start time is taken here, when the player leaves the briefing, so
    time spent reading the instructions is not counted.
    """
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


def _how_it_works_context() -> dict[str, object]:
    """Build the values the How it works page always shows.

    Returns:
        The race's size menu, and the headline accuracy and benchmark figures
        (the benchmark is None if no snapshot has been saved).
    """
    return {
        "sizes": RACE_SIZES,
        "naive_limit": NAIVE_FULL_RUN_LIMIT,
        "accuracy": accuracy_figures(),
        "benchmark": load_benchmark_snapshot(),
    }


@router.get("/how-it-works", response_class=HTMLResponse)
def how_it_works(request: Request) -> HTMLResponse:
    """How it works: the steps, the problem types, the figures and the race."""
    return templates.TemplateResponse(request, "how_it_works.html", _how_it_works_context())


@router.get("/arena")
def old_arena_page() -> RedirectResponse:
    """The page was called the algorithm arena; old links still work."""
    return RedirectResponse("/how-it-works", status_code=301)


@router.post("/how-it-works/race", response_class=HTMLResponse)
async def run_race(request: Request) -> HTMLResponse:
    """Time both approaches on the chosen sizes and return the chart."""
    form = await request.form()
    requested = {int(str(v)) for v in form.getlist("size") if str(v).isdigit()}
    chosen = sorted(requested & set(RACE_SIZES))
    context = _how_it_works_context()
    if len(chosen) < 2:
        context["error"] = "Choose at least two sizes to compare."
    else:
        rows = [run_benchmark(size) for size in chosen]
        context.update(rows=rows, chart=build_timing_chart(rows))
    template = "_race_results.html" if _is_htmx(request) else "how_it_works.html"
    return templates.TemplateResponse(request, template, context)


@router.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request) -> HTMLResponse:
    """Upload your own bank statement and ledger."""
    return templates.TemplateResponse(request, "upload.html", dict(_FORMAT_CONTEXT))


@router.post("/upload", response_class=HTMLResponse)
async def upload(
    request: Request,
    bank_file: Annotated[UploadFile, File()],
    ledger_file: Annotated[UploadFile, File()],
) -> HTMLResponse:
    """Reconcile two uploaded files and show the results."""
    context: dict[str, object] = {} if _is_htmx(request) else dict(_FORMAT_CONTEXT)
    try:
        _, _, report = reconcile_uploads(
            await bank_file.read(MAX_UPLOAD_BYTES + 1), await ledger_file.read(MAX_UPLOAD_BYTES + 1)
        )
        context.update(_results_context(report, "reconciliation-report.csv"))
        status = 200
    except UploadError as exc:
        context["error"] = str(exc)
        status = 422
    template = "_results.html" if _is_htmx(request) else "upload.html"
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
