"""HTML page tests: rendering, security headers, form handling and accessibility markup."""

import base64
import csv
import hashlib
import io
import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from eod_close.evaluation.headline import accuracy_figures, load_benchmark_snapshot
from eod_close.generator import CaseGenerator, Difficulty
from eod_close.web import pages
from eod_close.web.app import create_app
from eod_close.web.report_view import REPORT_COLUMNS

PAGES = [
    "/",
    "/upload",
    "/training",
    "/how-it-works",
    "/sample/1",
    "/play/intern/1",
    "/play/intern/1/work",
]


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.mark.parametrize("path", PAGES)
def test_pages_render_with_the_fictional_notice(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200
    assert "not real financial software" in response.text
    assert 'lang="en-GB"' in response.text


def test_pages_send_a_strict_content_security_policy(client: TestClient) -> None:
    policy = client.get("/").headers["Content-Security-Policy"]
    assert "script-src 'self'" in policy
    assert "unsafe-inline" not in policy


def test_no_page_loads_anything_from_another_site(client: TestClient) -> None:
    for path in [*PAGES, "/play/head-of-ops/1"]:
        html = client.get(path).text
        assert "https://" not in html


def test_new_day_redirects_to_a_seeded_url(client: TestClient) -> None:
    response = client.get("/play?difficulty=analyst&seed=7", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/play/analyst/7"
    random_day = client.get("/play?difficulty=intern", follow_redirects=False)
    assert random_day.headers["location"].startswith("/play/intern/")


def test_out_of_range_seed_is_not_found(client: TestClient) -> None:
    assert client.get("/play/intern/2000000000").status_code == 404


def test_every_form_control_on_the_work_page_has_a_label(client: TestClient) -> None:
    html = client.get("/play/senior-analyst/1/work").text
    controls = html.count('class="group-input"') + html.count("<select ")
    labelled = html.count('aria-label="Pair number for') + html.count("has no partner, why")
    assert controls == labelled


def test_a_day_opens_on_a_briefing_with_no_clock_running(client: TestClient) -> None:
    """The instructions come first; the lists and the start time only on the work page."""
    html = client.get("/play/intern/3").text
    assert "How to play" in html
    assert "Start the timer" in html
    assert 'href="/play/intern/3/work"' in html
    assert "started_at" not in html
    assert "link-select" not in html


def test_the_work_page_starts_the_clock_and_has_one_close_button(client: TestClient) -> None:
    html = client.get("/play/intern/3/work").text
    assert re.search(r'name="started_at" value="\d+"', html)
    assert html.count(">Close the day</button>") == 1
    assert '<details class="help">' in html, "the instructions start folded away"


def test_progress_check_counts_unassigned_entries(client: TestClient) -> None:
    case = CaseGenerator().generate(1, Difficulty.INTERN)
    total = len(case.bank_statement) + len(case.ledger)
    response = client.post("/play/intern/1/check", data={}, headers={"HX-Request": "true"})
    assert f"{total} lines still to pair or explain" in response.text


def test_closing_a_perfect_day_shows_it_balanced(client: TestClient) -> None:
    case = CaseGenerator().generate(4, Difficulty.INTERN)
    form = {"started_at": "0"}
    for n, item in enumerate(case.answer_key.items, start=1):
        for entry_id in item.bank_ids + item.ledger_ids:
            form[f"group-{entry_id}"] = str(n)
    html = client.post("/play/intern/4/close", data=form).text
    assert 'data-balanced="true"' in html
    assert "100.0%" in html
    assert "not timed" in html  # a start time of 0 is implausible, so no time is shown
    assert "You vs the engine" in html
    assert "See every problem explained" in html


def test_closing_an_empty_day_is_not_balanced(client: TestClient) -> None:
    html = client.post("/play/intern/4/close", data={"started_at": "x"}).text
    assert 'data-balanced="false"' in html


def test_race_returns_a_chart_and_a_table(client: TestClient) -> None:
    response = client.post(
        "/how-it-works/race", data={"size": ["100", "250"]}, headers={"HX-Request": "true"}
    )
    assert "<svg" in response.text
    assert "<table>" in response.text
    assert "<html" not in response.text


def test_race_without_htmx_returns_the_full_page(client: TestClient) -> None:
    response = client.post("/how-it-works/race", data={"size": ["100", "250"]})
    assert "<html" in response.text
    assert "<svg" in response.text


def test_race_ignores_sizes_outside_the_menu(client: TestClient) -> None:
    response = client.post(
        "/how-it-works/race", data={"size": ["100", "1000000"]}, headers={"HX-Request": "true"}
    )
    assert "Choose at least two sizes" in response.text


def report_from_page(html: str) -> list[dict[str, str]]:
    """Decode the CSV report embedded in a results page's download link."""
    found = re.search(r'href="data:text/csv;charset=utf-8;base64,([A-Za-z0-9+/=]+)"', html)
    assert found, "the results must offer the report as a download"
    text = base64.b64decode(found.group(1)).decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))


def test_home_page_shows_the_sample_day_ready_to_reconcile(client: TestClient) -> None:
    html = client.get("/").text
    assert "Match a bank statement against your records and see what doesn't add up." in html
    assert "Not matched yet" in html
    assert "24 bank payments" in html
    assert "26 in your records" in html
    assert 'hx-post="/reconcile"' in html
    assert "data-select" not in html, "no outcomes before Reconcile is pressed"
    for link in ('href="/upload"', 'href="/sample-day/report.csv"', 'href="/sample"'):
        assert link in html


def test_reconcile_returns_the_workspace_with_every_outcome(client: TestClient) -> None:
    html = client.post("/reconcile", headers={"HX-Request": "true"}).text
    assert "<html" not in html
    assert "22 matched" in html
    assert "3 for a person to check" in html
    assert re.search(r'id="status-unexplained">-£42.50<', html)
    assert re.search(r'id="problems-total">-£42.50<', html)
    assert html.count('class="problem"') == 3
    assert 'id="row-bank-B02"' in html
    assert 'href="#row-ledger-L02"' in html


def test_reconcile_without_htmx_returns_the_whole_page_reconciled(client: TestClient) -> None:
    html = client.post("/reconcile").text
    assert "<html" in html
    assert "22 matched" in html


@pytest.mark.parametrize("name", ["bank", "ledger", "report"])
def test_home_sample_day_files_download(client: TestClient, name: str) -> None:
    response = client.get(f"/sample-day/{name}.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert f"sample-day-{name}.csv" in response.headers["content-disposition"]
    if name == "report":
        rows = list(csv.DictReader(io.StringIO(response.text)))
        assert [r["status"] for r in rows].count("needs review") == 3


def test_unknown_home_sample_day_file_is_not_found(client: TestClient) -> None:
    assert client.get("/sample-day/answers.csv").status_code == 404


def test_how_it_works_figures_come_from_the_evaluators(client: TestClient) -> None:
    html = client.get("/how-it-works").text
    accuracy = accuracy_figures()
    assert f"{accuracy.overall:.1%}" in html
    assert f"{accuracy.hardest:.1%}" in html
    snapshot = load_benchmark_snapshot()
    assert snapshot is not None, "the benchmark snapshot ships with the app"
    assert f"{snapshot.entries_per_second:,}" in html


def test_upload_page_has_a_drop_zone_for_each_file(client: TestClient) -> None:
    html = client.get("/upload").text
    for field in ("bank_file", "ledger_file"):
        assert f'<label class="dropzone-title" for="{field}">' in html
        assert f'aria-describedby="{field}-hint {field}-chosen"' in html
    assert html.count("data-dropzone ") + html.count("data-dropzone>") == 2
    assert "The columns each file needs" in html


def test_old_arena_address_redirects_to_how_it_works(client: TestClient) -> None:
    response = client.get("/arena", follow_redirects=False)
    assert response.status_code == 301
    assert response.headers["location"] == "/how-it-works"


@pytest.mark.parametrize(
    ("path", "has_streak"),
    [("/", False), ("/upload", False), ("/how-it-works", False), ("/training", True)],
)
def test_streak_appears_in_training_mode_only(
    client: TestClient, path: str, has_streak: bool
) -> None:
    assert ('id="streak-count"' in client.get(path).text) is has_streak


def test_home_page_does_not_keep_an_earlier_request(client: TestClient) -> None:
    """BUG-005: the shared format columns must not pick up a request and reuse it."""
    client.get("/")
    client.get("/")
    assert "request" not in pages._FORMAT_CONTEXT


def test_upload_shows_the_summary_and_offers_the_report(client: TestClient) -> None:
    bank = client.get("/samples/bank.csv")
    ledger = client.get("/samples/ledger.csv")
    assert bank.headers["content-type"].startswith("text/csv")
    response = client.post(
        "/upload",
        files={"bank_file": ("b.csv", bank.content), "ledger_file": ("l.csv", ledger.content)},
        headers={"HX-Request": "true"},
    )
    assert 'id="summary"' in response.text
    assert "For a person to check" in response.text
    assert "<html" not in response.text
    rows = report_from_page(response.text)
    assert rows
    assert tuple(rows[0]) == REPORT_COLUMNS


def test_upload_without_htmx_returns_the_upload_page_with_results(client: TestClient) -> None:
    bank = client.get("/samples/bank.csv").content
    ledger = client.get("/samples/ledger.csv").content
    html = client.post(
        "/upload", files={"bank_file": ("b.csv", bank), "ledger_file": ("l.csv", ledger)}
    ).text
    assert "<html" in html
    assert 'id="summary"' in html


def test_sample_day_redirects_to_its_own_address(client: TestClient) -> None:
    response = client.get("/sample", follow_redirects=False)
    assert response.status_code == 303
    assert re.fullmatch(r"/sample/\d+", response.headers["location"])


def test_sample_day_shows_every_kind_of_result(client: TestClient) -> None:
    html = client.get("/sample/1").text
    for label in ("Typo", "Duplicate", "Card fee", "Bundled payment", "Next:"):
        assert label in html
    assert len(report_from_page(html)) > 100


@pytest.mark.parametrize("name", ["bank", "ledger", "report"])
def test_sample_day_files_download_as_csv(client: TestClient, name: str) -> None:
    response = client.get(f"/sample/1/{name}.csv")
    assert response.headers["content-type"].startswith("text/csv")
    assert f"sample-day-1-{name}.csv" in response.headers["content-disposition"]


def test_unknown_sample_day_files_are_not_found(client: TestClient) -> None:
    assert client.get("/sample/1/secrets.csv").status_code == 404
    assert client.get("/sample/2000000000").status_code == 404


def test_upload_error_is_shown_not_raised(client: TestClient) -> None:
    files = {"bank_file": ("b.csv", b""), "ledger_file": ("l.csv", b"")}
    partial = client.post("/upload", files=files, headers={"HX-Request": "true"})
    assert partial.status_code == 200
    assert "empty" in partial.text
    assert client.post("/upload", files=files).status_code == 422


def test_unknown_sample_is_not_found(client: TestClient) -> None:
    assert client.get("/samples/secrets.csv").status_code == 404


def test_blank_day_number_starts_a_random_day(client: TestClient) -> None:
    """BUG-004: a blank optional field arrives as "seed=", not as a missing parameter."""
    response = client.get("/play?difficulty=analyst&seed=", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/play/analyst/")


@pytest.mark.parametrize("seed", ["abc", "-1", "2000000000"])
def test_invalid_day_number_is_rejected(client: TestClient, seed: str) -> None:
    assert client.get(f"/play?seed={seed}", follow_redirects=False).status_code == 422


def test_pages_link_static_files_by_content_version(client: TestClient) -> None:
    """BUG-006: a stylesheet's address must change when its content does."""
    html = client.get("/").text
    for name in ("app.css", "app.js", "game.js", "vendor/htmx-2.0.4.min.js"):
        digest = hashlib.sha256((pages.STATIC_DIR / name).read_bytes()).hexdigest()[:12]
        assert f'"/static/{name}?v={digest}"' in html
    assert 'href="/static/app.css"' not in html


def test_the_linked_stylesheet_loads_and_may_be_cached_for_good(client: TestClient) -> None:
    href = re.search(r'<link rel="stylesheet" href="([^"]+)"', client.get("/").text)
    assert href
    response = client.get(href.group(1))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert ".summary-bar" in response.text
    assert "immutable" in response.headers["cache-control"]


def test_unversioned_static_files_are_checked_every_time(client: TestClient) -> None:
    """The font is fetched by the stylesheet without a version, so it must be revalidated."""
    response = client.get("/static/fonts/inter-latin-wght.woff2")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
