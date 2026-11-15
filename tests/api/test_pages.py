"""HTML page tests: rendering, security headers, form handling and accessibility markup."""

import base64
import csv
import io
import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from eod_close.generator import CaseGenerator, Difficulty
from eod_close.web import pages
from eod_close.web.app import create_app
from eod_close.web.report_view import REPORT_COLUMNS

PAGES = ["/", "/training", "/arena", "/sample/1", "/play/intern/1"]


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
    html = client.get("/play/senior-analyst/1").text
    controls = html.count('class="group-input"') + html.count("<select ")
    labelled = html.count('aria-label="Group for') + html.count('aria-label="Exception reason for')
    assert controls == labelled


def test_progress_check_counts_unassigned_entries(client: TestClient) -> None:
    case = CaseGenerator().generate(1, Difficulty.INTERN)
    total = len(case.bank_statement) + len(case.ledger)
    response = client.post("/play/intern/1/check", data={}, headers={"HX-Request": "true"})
    assert f"{total} entries still to link or explain" in response.text


def test_closing_a_perfect_day_shows_it_balanced(client: TestClient) -> None:
    case = CaseGenerator().generate(4, Difficulty.INTERN)
    form = {"started_at": "0"}
    for n, item in enumerate(case.answer_key.items, start=1):
        for entry_id in item.bank_ids + item.ledger_ids:
            form[f"group-{entry_id}"] = str(n)
    html = client.post("/play/intern/4/close", data=form).text
    assert 'data-balanced="true"' in html
    assert "100.0%" in html
    assert "n/a" in html  # a start time of 0 is implausible, so no time is shown
    assert "Every discrepancy, explained" in html


def test_closing_an_empty_day_is_not_balanced(client: TestClient) -> None:
    html = client.post("/play/intern/4/close", data={"started_at": "x"}).text
    assert 'data-balanced="false"' in html


def test_arena_returns_a_chart_and_a_table(client: TestClient) -> None:
    response = client.post(
        "/arena/run", data={"size": ["100", "250"]}, headers={"HX-Request": "true"}
    )
    assert "<svg" in response.text
    assert "<table>" in response.text
    assert "<html" not in response.text


def test_arena_without_htmx_returns_the_full_page(client: TestClient) -> None:
    response = client.post("/arena/run", data={"size": ["100", "250"]})
    assert "<html" in response.text
    assert "<svg" in response.text


def test_arena_ignores_sizes_outside_the_menu(client: TestClient) -> None:
    response = client.post(
        "/arena/run", data={"size": ["100", "1000000"]}, headers={"HX-Request": "true"}
    )
    assert "Choose at least two sizes" in response.text


def report_from_page(html: str) -> list[dict[str, str]]:
    """Decode the CSV report embedded in a results page's download link."""
    found = re.search(r'href="data:text/csv;charset=utf-8;base64,([A-Za-z0-9+/=]+)"', html)
    assert found, "the results must offer the report as a download"
    text = base64.b64decode(found.group(1)).decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))


def test_home_page_leads_with_the_tool(client: TestClient) -> None:
    html = client.get("/").text
    assert "Upload a bank statement and your internal ledger" in html
    assert 'href="/sample"' in html
    assert 'id="upload"' in html
    assert 'aria-current="page"' in html


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
    assert "Needs review" in response.text
    assert "<html" not in response.text
    rows = report_from_page(response.text)
    assert rows
    assert tuple(rows[0]) == REPORT_COLUMNS


def test_upload_without_htmx_returns_the_home_page_with_results(client: TestClient) -> None:
    bank = client.get("/samples/bank.csv").content
    ledger = client.get("/samples/ledger.csv").content
    html = client.post(
        "/upload", files={"bank_file": ("b.csv", bank), "ledger_file": ("l.csv", ledger)}
    ).text
    assert "<html" in html
    assert 'id="summary"' in html


def test_old_upload_address_redirects_to_the_form(client: TestClient) -> None:
    response = client.get("/upload", follow_redirects=False)
    assert response.status_code == 301
    assert response.headers["location"] == "/#upload"


def test_sample_day_redirects_to_its_own_address(client: TestClient) -> None:
    response = client.get("/sample", follow_redirects=False)
    assert response.status_code == 303
    assert re.fullmatch(r"/sample/\d+", response.headers["location"])


def test_sample_day_shows_every_kind_of_result(client: TestClient) -> None:
    html = client.get("/sample/1").text
    for label in ("Possible typo", "Duplicate", "Fee-adjusted", "Batch payout", "Next step:"):
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
