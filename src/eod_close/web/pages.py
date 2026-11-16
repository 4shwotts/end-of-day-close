"""The server-rendered pages: reconcile, training mode and how it works.

The Reconcile page (the home page) is a small day's bank statement and
records side by side, with a Match button that puts the engine's outcome on
every row and lists the problems it found. The sample day is fixed; "Try
another day" generates one of the same shape. Upload runs the same engine on
your own files. Training mode is the game: work a day by hand and compare
with the engine. How it works explains the approach, runs a busy day and
races the engine against the obvious approach.

Pages are Jinja templates. HTMX swaps in partial responses for the matched
workspace, the progress check, the busy day, the race results and the
upload results; every form also works as a plain form post without
JavaScript.
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
from eod_close.generator import Case, Difficulty
from eod_close.generator.answer_key import DiscrepancyKind
from eod_close.web.busy_day import (
    Show,
    answer_key_csv,
    list_page,
    run_busy_day,
)
from eod_close.web.chart import build_timing_chart
from eod_close.web.report_view import build_report_view, report_csv
from eod_close.web.schemas import MAX_SEED
from eod_close.web.services import (
    MAX_UPLOAD_BYTES,
    UploadError,
    answers_from_form,
    engine_seconds,
    load_case,
    parse_uploads,
    timed_reconcile,
)
from eod_close.web.wording import KIND_NAMES, MATCH_NAMES, PROBLEM_NAMES, REASON_CHOICES
from eod_close.web.workspace import (
    build_workspace,
    load_sample_day,
    load_small_day,
    sample_day_csv,
)

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
    match_names=MATCH_NAMES,
    problem_names=PROBLEM_NAMES,
    kind_names=KIND_NAMES,
    reason_choices=REASON_CHOICES,
    difficulties=list(Difficulty),
    reasons=list(ExceptionReason),
    kinds=list(DiscrepancyKind),
)


def _percent(value: float) -> str:
    return f"{value:.1%}"


def _duration(seconds: int | None) -> str:
    if seconds is None:
        return "not timed"
    minutes, rest = divmod(seconds, 60)
    return f"{minutes}:{rest:02d}"


def _seconds(value: float) -> str:
    return f"{value:,.2f} s" if value >= 1 else f"{value * 1000:,.1f} ms"


def _in_seconds(value: float) -> str:
    # Everyday wording for the engine's time ("0.031 seconds"), where the
    # race table uses the compact "31.0 ms" for engineers.
    if value < 0.001:
        return "under 0.001 seconds"
    return f"{value:,.1f} seconds" if value >= 1 else f"{value:.3f} seconds"


templates.env.filters.update(
    percent=_percent, duration=_duration, seconds=_seconds, in_seconds=_in_seconds
)

router = APIRouter(include_in_schema=False)

# A fixed menu of sizes rather than free input, so nobody can ask a shared
# instance to time a naive run on a million rows.
# Below about 500 a side the engine's index-building cost outweighs its
# gain, which the race explains in a sentence rather than charts.
RACE_SIZES: Final = (500, 1_000, 2_500, 5_000, 10_000)

# The seed and difficulty of the example files offered beside the upload form.
SAMPLE_SEED: Final = 1
SAMPLE_DIFFICULTY: Final = Difficulty.ANALYST

# Read-only on purpose: TemplateResponse adds the current request to the
# context it is given, so passing this mapping itself would keep the first
# visitor's request and reuse it for every later page (BUG-005). Pages pass
# a fresh copy instead, and a mutation attempt fails loudly.
_FORMAT_CONTEXT: Final = MappingProxyType(
    {"bank_columns": BANK_COLUMNS, "ledger_columns": LEDGER_COLUMNS}
)


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _results_context(
    report: ReconciliationReport, csv_name: str, seconds: float
) -> dict[str, object]:
    """Build the template values shared by every results page.

    The CSV report travels inside the page as a ``data:`` link. Uploaded
    files are never stored, so there is nothing a later download request
    could fetch; embedding the report also keeps the upload form a single
    form that works with or without JavaScript.

    Args:
        report: The engine's report.
        csv_name: The file name the browser should save the report as.
        seconds: How long the engine took.

    Returns:
        The report view, the engine's time, the report's download link and
        its file name.
    """
    view = build_report_view(report)
    encoded = base64.b64encode(report_csv(view).encode("utf-8")).decode("ascii")
    return {
        "view": view,
        "seconds": seconds,
        "csv_href": f"data:text/csv;charset=utf-8;base64,{encoded}",
        "csv_name": csv_name,
    }


def _workspace_context(seed: int | None, *, reconciled: bool) -> dict[str, object]:
    """Build the Reconcile page's workspace for the sample day or a small day.

    Args:
        seed: The generated small day's seed, or None for the fixed sample day.
        reconciled: Whether to show the engine's outcome on the rows.

    Returns:
        The workspace, and the addresses of the day itself, its Match button
        and its report.
    """
    if seed is None:
        statement, ledger, report = load_sample_day()
        base = ""
        report_url = "/sample-day/report.csv"
    else:
        _check_seed(seed)
        statement, ledger, report = load_small_day(seed)
        base = f"/day/{seed}"
        report_url = f"{base}/report.csv"
    return {
        "workspace": build_workspace(statement, ledger, report if reconciled else None),
        "day_url": base or "/",
        "match_url": f"{base}/reconcile",
        "report_url": report_url,
    }


def _workspace_page(request: Request, seed: int | None, *, reconciled: bool) -> HTMLResponse:
    # HTMX swaps in just the workspace; without JavaScript the Match form
    # posts as a plain form and gets the whole page back, already matched.
    template = "_workspace.html" if reconciled and _is_htmx(request) else "home.html"
    return templates.TemplateResponse(
        request, template, _workspace_context(seed, reconciled=reconciled)
    )


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    """The Reconcile page: the sample day's two lists, ready to match."""
    return _workspace_page(request, None, reconciled=False)


@router.post("/reconcile", response_class=HTMLResponse)
def reconcile_sample_day(request: Request) -> HTMLResponse:
    """Match the sample day and show what is wrong."""
    return _workspace_page(request, None, reconciled=True)


@router.get("/sample-day/{name}.csv")
def sample_day_download(name: str) -> Response:
    """Download the sample day: its bank file, records file or report."""
    if name in ("bank", "ledger"):
        text = sample_day_csv("bank" if name == "bank" else "ledger")
    elif name == "report":
        text = report_csv(build_report_view(load_sample_day()[2]))
    else:
        raise HTTPException(status_code=404)
    return _csv_response(text, f"sample-day-{name}.csv")


@router.get("/day")
def another_day() -> RedirectResponse:
    """Pick a random small day and redirect to its own, shareable address."""
    return RedirectResponse(f"/day/{secrets.randbelow(MAX_SEED + 1)}", status_code=303)


@router.get("/day/{seed}", response_class=HTMLResponse)
def small_day(request: Request, seed: int) -> HTMLResponse:
    """A generated small day, the same shape as the sample day, ready to match."""
    return _workspace_page(request, seed, reconciled=False)


@router.post("/day/{seed}/reconcile", response_class=HTMLResponse)
def reconcile_small_day(request: Request, seed: int) -> HTMLResponse:
    """Match a generated small day and show what is wrong."""
    return _workspace_page(request, seed, reconciled=True)


@router.get("/day/{seed}/report.csv")
def small_day_report(seed: int) -> Response:
    """Download the engine's report on a generated small day."""
    _check_seed(seed)
    text = report_csv(build_report_view(load_small_day(seed)[2]))
    return _csv_response(text, f"day-{seed}-report.csv")


@router.get("/sample")
@router.get("/sample/{seed}")
def old_harder_day() -> RedirectResponse:
    """The large generated day moved to How it works as the busy day."""
    return RedirectResponse("/how-it-works#busy-day", status_code=301)


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


# Fields a training form sends besides the two per line: the start time,
# with room to spare for anything a browser adds.
_EXTRA_FORM_FIELDS: Final = 16


async def _answers_form(request: Request, case: Case) -> dict[str, str]:
    """Read a training day's answers, allowing for the size of the day.

    Every line sends two fields, its pair number and its "if no partner"
    choice, so a Head of Ops day sends over 1,200. The framework's default
    limit of 1,000 fields would reject it (BUG-008), so the limit is set
    from the day itself: large enough for this day, and no larger.

    Args:
        request: The form post.
        case: The day being played.

    Returns:
        The form's fields as text.
    """
    limit = 2 * (len(case.bank_statement) + len(case.ledger)) + _EXTRA_FORM_FIELDS
    async with request.form(max_fields=limit) as form:
        return {key: str(value) for key, value in form.items()}


@router.post("/play/{difficulty}/{seed}/check", response_class=HTMLResponse)
async def check_progress(request: Request, difficulty: Difficulty, seed: int) -> HTMLResponse:
    """HTMX partial: how much is still unassigned, without revealing answers."""
    _check_seed(seed)
    case, report = load_case(difficulty, seed)
    form = await _answers_form(request, case)
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
    form = await _answers_form(request, case)
    elapsed: int | None = None
    with contextlib.suppress(ValueError):
        elapsed = int(time.time()) - int(form.get("started_at", ""))
    # The start time comes from the page, so it is trusted only for display;
    # a negative or day-long value is dropped rather than shown.
    if elapsed is not None and not 0 <= elapsed <= 86_400:
        elapsed = None
    score = score_answers(case, answers_from_form(case, form, elapsed), report)
    return templates.TemplateResponse(
        request,
        "close.html",
        {
            "case": case,
            "score": score,
            "report": report,
            "engine_seconds": engine_seconds(difficulty, seed),
        },
    )


@router.get("/play/{difficulty}/{seed}/report.csv")
def training_day_report(difficulty: Difficulty, seed: int) -> Response:
    """Download the engine's full answer to a training day, for the close screen."""
    _check_seed(seed)
    _, report = load_case(difficulty, seed)
    return _csv_response(
        report_csv(build_report_view(report)), f"{difficulty.value}-day-{seed}-engine-report.csv"
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
    """How it works: the steps, the figures, the busy day and the race."""
    return templates.TemplateResponse(request, "how_it_works.html", _how_it_works_context())


@router.get("/arena")
def old_arena_page() -> RedirectResponse:
    """The page was called the algorithm arena; old links still work."""
    return RedirectResponse("/how-it-works", status_code=301)


def _busy_day_context(
    seed: int, page: int = 1, show: Show = "all", focus: str | None = None
) -> dict[str, object]:
    """Generate, match and mark a busy day, and cut its lists to one page.

    Args:
        seed: The day's number.
        page: The page of the two lists to show.
        show: Every row, or only the problem rows.
        focus: A problem whose rows to bring into view and highlight.

    Returns:
        The How it works values, plus the day and its lists.
    """
    _check_seed(seed)
    busy = run_busy_day(seed)
    context = _how_it_works_context()
    context.update(busy=busy, lists=list_page(busy.workspace, page, show, focus))
    return context


@router.post("/how-it-works/busy-day", response_model=None)
def new_busy_day(request: Request) -> HTMLResponse | RedirectResponse:
    """Run a busy day with a new number.

    HTMX swaps in the result and puts the day's own address in the address
    bar, so reloading or sharing it runs the same day again. Without
    JavaScript the form redirects to that address.
    """
    seed = secrets.randbelow(MAX_SEED + 1)
    if not _is_htmx(request):
        return RedirectResponse(f"/how-it-works/busy-day/{seed}#busy-day", status_code=303)
    response = templates.TemplateResponse(request, "_busy_day.html", _busy_day_context(seed))
    response.headers["HX-Push-Url"] = f"/how-it-works/busy-day/{seed}"
    return response


@router.get("/how-it-works/busy-day/{seed}", response_class=HTMLResponse)
def busy_day(request: Request, seed: int) -> HTMLResponse:
    """How it works, with one numbered busy day run live."""
    return templates.TemplateResponse(request, "how_it_works.html", _busy_day_context(seed))


@router.get("/how-it-works/busy-day/{seed}/lists", response_class=HTMLResponse)
def busy_day_lists(
    request: Request,
    seed: int,
    page: Annotated[int, Query(ge=1)] = 1,
    show: Show = "all",
    focus: Annotated[str | None, Query(pattern=r"^r\d{1,4}$")] = None,
) -> HTMLResponse:
    """One page of a busy day's two lists; the whole page without JavaScript.

    With ``focus``, the page holding one problem's rows, with them
    highlighted, for "Show its rows in the lists".
    """
    context = _busy_day_context(seed, page, show, focus)
    if _is_htmx(request):
        return templates.TemplateResponse(request, "_busy_lists.html", context)
    context["lists_open"] = True
    return templates.TemplateResponse(request, "how_it_works.html", context)


@router.get("/how-it-works/busy-day/{seed}/{name}.csv")
def busy_day_file(seed: int, name: str) -> Response:
    """Download a busy day's bank file, records file, answer key or the engine's report."""
    if name not in ("bank", "ledger", "answer-key", "report"):
        raise HTTPException(status_code=404)
    _check_seed(seed)
    busy = run_busy_day(seed)
    buffer = io.StringIO()
    if name == "bank":
        write_bank_csv(busy.statement, buffer)
    elif name == "ledger":
        write_ledger_csv(busy.ledger, buffer)
    elif name == "answer-key":
        buffer.write(answer_key_csv(busy.answer_key))
    else:
        buffer.write(report_csv(build_report_view(busy.report)))
    return _csv_response(buffer.getvalue(), f"busy-day-{seed}-{name}.csv")


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
        statement, ledger = parse_uploads(
            await bank_file.read(MAX_UPLOAD_BYTES + 1), await ledger_file.read(MAX_UPLOAD_BYTES + 1)
        )
        report, seconds = timed_reconcile(statement, ledger)
        context.update(_results_context(report, "reconciliation-report.csv", seconds))
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
