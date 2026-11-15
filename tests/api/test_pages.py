"""HTML page tests: rendering, security headers, form handling and accessibility markup."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from eod_close.generator import CaseGenerator, Difficulty
from eod_close.web.app import create_app


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.mark.parametrize("path", ["/", "/arena", "/upload", "/play/intern/1"])
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
    for path in ["/", "/arena", "/upload", "/play/head-of-ops/1"]:
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


def test_upload_page_round_trip_with_the_sample_files(client: TestClient) -> None:
    bank = client.get("/samples/bank.csv")
    ledger = client.get("/samples/ledger.csv")
    assert bank.headers["content-type"].startswith("text/csv")
    response = client.post(
        "/upload",
        files={"bank_file": ("b.csv", bank.content), "ledger_file": ("l.csv", ledger.content)},
        headers={"HX-Request": "true"},
    )
    assert "Every entry is accounted for" in response.text


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
