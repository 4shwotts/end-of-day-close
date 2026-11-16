"""Error handling, rate limits, request size and security headers, end to end."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from eod_close.web.app import create_app
from eod_close.web.limits import LIMITS, MAX_BODY_BYTES

HTMX = {"HX-Request": "true"}
RACE_FORM = {"size": ["500", "1000"]}


def _failing_app() -> FastAPI:
    """The real app, plus one page and one API route that always fail."""
    app = create_app()

    @app.get("/broken")
    def broken_page() -> None:
        raise RuntimeError("secret detail: database password is hunter2")

    @app.get("/api/broken")
    def broken_api() -> None:
        raise RuntimeError("secret detail: database password is hunter2")

    return app


@pytest.fixture(scope="module")
def failing() -> Iterator[TestClient]:
    # raise_server_exceptions=False hands the 500 response to the test, as
    # a real server would send it, rather than re-raising the error.
    with TestClient(_failing_app(), raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def limited() -> Iterator[TestClient]:
    """A fresh app with the deployed rate limits, so each test starts at zero."""
    with TestClient(create_app(rate_limits=True)) as client:
        yield client


# ---- Unexpected errors -----------------------------------------------------


def test_an_unexpected_page_error_shows_a_plain_page_without_details(
    failing: TestClient,
) -> None:
    response = failing.get("/broken")
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("text/html")
    assert "Something went wrong" in response.text
    for leak in ("hunter2", "Traceback", "RuntimeError"):
        assert leak not in response.text


def test_an_unexpected_api_error_is_clean_json(failing: TestClient) -> None:
    response = failing.get("/api/broken")
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error."}


def test_an_unexpected_error_still_carries_the_security_headers(failing: TestClient) -> None:
    """The catch-all handler runs outside the header middleware, so it adds them itself."""
    for path in ("/broken", "/api/broken"):
        headers = failing.get(path).headers
        assert "default-src 'self'" in headers["Content-Security-Policy"]
        assert headers["X-Content-Type-Options"] == "nosniff"


def test_an_unexpected_error_during_htmx_goes_to_the_error_box(failing: TestClient) -> None:
    response = failing.get("/broken", headers=HTMX)
    assert response.status_code == 500
    assert response.headers["HX-Retarget"] == "#request-error"
    assert "<html" not in response.text
    assert "Please try again" in response.text


def test_the_error_is_logged_in_full(failing: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    """The details go to the server's log, where the owner can read them."""
    failing.get("/broken")
    assert "hunter2" in caplog.text


# ---- Expected errors -------------------------------------------------------


def test_an_unknown_page_is_a_friendly_404(failing: TestClient) -> None:
    response = failing.get("/no-such-page")
    assert response.status_code == 404
    assert "Page not found" in response.text
    assert "not real financial software" in response.text


def test_an_unknown_api_address_is_json(failing: TestClient) -> None:
    response = failing.get("/api/no-such-thing")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_invalid_page_input_does_not_echo_the_validation_details(failing: TestClient) -> None:
    response = failing.get("/how-it-works/busy-day/1/lists?focus=<script>")
    assert response.status_code == 422
    assert "<script>" not in response.text
    assert "not valid" in response.text


def test_invalid_api_input_still_names_the_field(failing: TestClient) -> None:
    """The API's callers are programs, which need to know which field was wrong."""
    response = failing.post("/api/cases", json={"difficulty": "wizard"})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][-1] == "difficulty"


# ---- Rate limits -----------------------------------------------------------


def test_the_race_is_refused_past_its_limit_with_a_retry_after(limited: TestClient) -> None:
    for _ in range(LIMITS["race"].requests):
        assert limited.post("/how-it-works/race", data=RACE_FORM).status_code == 200
    refused = limited.post("/how-it-works/race", data=RACE_FORM)
    assert refused.status_code == 429
    assert 1 <= int(refused.headers["Retry-After"]) <= 60
    assert "Too many requests" in refused.text


def test_a_refused_htmx_request_says_why_in_the_error_box(limited: TestClient) -> None:
    for _ in range(LIMITS["busy-day"].requests):
        limited.get("/how-it-works/busy-day/7/lists", headers=HTMX)
    refused = limited.get("/how-it-works/busy-day/7/lists", headers=HTMX)
    assert refused.status_code == 429
    assert refused.headers["HX-Retarget"] == "#request-error"
    assert "try again in" in refused.text


def test_a_refused_api_call_is_json(limited: TestClient) -> None:
    for _ in range(LIMITS["new-day"].requests):
        limited.get("/api/cases/intern/1")
    refused = limited.get("/api/cases/intern/1")
    assert refused.status_code == 429
    assert "Too many requests" in refused.json()["detail"]


def test_each_visitor_has_their_own_allowance(limited: TestClient) -> None:
    """Behind Render's proxy, the last X-Forwarded-For entry is the visitor."""
    first = {"X-Forwarded-For": "203.0.113.5"}
    for _ in range(LIMITS["race"].requests):
        limited.post("/how-it-works/race", data=RACE_FORM, headers=first)
    assert limited.post("/how-it-works/race", data=RACE_FORM, headers=first).status_code == 429
    # The same visitor claiming another address in front gains nothing; a
    # different visitor (the proxy's entry, last) has a fresh allowance.
    spoofed = {"X-Forwarded-For": "198.51.100.7, 203.0.113.5"}
    assert limited.post("/how-it-works/race", data=RACE_FORM, headers=spoofed).status_code == 429
    second = {"X-Forwarded-For": "203.0.113.5, 198.51.100.7"}
    assert limited.post("/how-it-works/race", data=RACE_FORM, headers=second).status_code == 200


def test_light_pages_are_never_limited(limited: TestClient) -> None:
    for _ in range(LIMITS["new-day"].requests + 5):
        assert limited.get("/how-it-works").status_code == 200


def test_the_test_app_is_not_limited(failing: TestClient) -> None:
    """The suites make far more requests than a visitor; only the deployed app limits."""
    for _ in range(LIMITS["race"].requests + 1):
        assert failing.post("/how-it-works/race", data=RACE_FORM).status_code == 200


def test_the_deployed_app_is_limited() -> None:
    from eod_close.web.app import app

    assert set(app.state.limiters) == set(LIMITS)


# ---- Request size ----------------------------------------------------------


def test_a_body_over_the_limit_is_refused_before_it_is_stored(failing: TestClient) -> None:
    big = b"x" * (MAX_BODY_BYTES + 1)
    files = {"bank_file": ("b.csv", big), "ledger_file": ("l.csv", b"y")}
    page = failing.post("/upload", files=files)
    assert page.status_code == 413
    assert "too large" in page.text
    api = failing.post("/api/reconcile", files=files)
    assert api.status_code == 413
    assert api.json() == {"detail": "The request is too large."}


def test_a_streamed_body_is_cut_off_once_it_passes_the_limit(failing: TestClient) -> None:
    """Without a Content-Length, the bytes are counted as they arrive."""

    def chunks() -> Iterator[bytes]:
        yield b"--x\r\nContent-Disposition: form-data; name=bank_file; filename=b.csv\r\n\r\n"
        for _ in range(MAX_BODY_BYTES // 65_536 + 2):
            yield b"x" * 65_536

    response = failing.post(
        "/api/reconcile",
        content=chunks(),
        headers={"Content-Type": "multipart/form-data; boundary=x"},
    )
    assert response.status_code == 413


# ---- Uploads must be CSV ---------------------------------------------------


@pytest.mark.parametrize("name", ["statement.xlsx", "photo.png", "notes"])
def test_a_file_not_named_csv_is_refused_plainly(failing: TestClient, name: str) -> None:
    files = {"bank_file": (name, b"a,b\n"), "ledger_file": ("l.csv", b"a,b\n")}
    page = failing.post("/upload", files=files)
    assert page.status_code == 422
    assert "must be a CSV file" in page.text
    api = failing.post("/api/reconcile", files=files)
    assert api.status_code == 422
    assert "must be a CSV file" in api.json()["detail"]


def test_the_csv_check_ignores_letter_case(failing: TestClient) -> None:
    files = {"bank_file": ("B.CSV", b""), "ledger_file": ("l.Csv", b"")}
    response = failing.post("/api/reconcile", files=files)
    assert "must be a CSV file" not in response.json()["detail"]


# ---- Headers ---------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/api/health", "/no-such-page"])
def test_every_response_limits_browser_features_and_insists_on_https(
    failing: TestClient, path: str
) -> None:
    headers = failing.get(path).headers
    assert "camera=()" in headers["Permissions-Policy"]
    assert "geolocation=()" in headers["Permissions-Policy"]
    assert headers["Strict-Transport-Security"].startswith("max-age=31536000")
