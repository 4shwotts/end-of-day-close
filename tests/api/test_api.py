"""JSON API tests: case generation, scoring and reconciling uploaded files."""

import io
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from eod_close.csv_format import BANK_COLUMNS, write_bank_csv, write_ledger_csv
from eod_close.generator import CaseGenerator, Difficulty
from eod_close.web.app import create_app
from eod_close.web.services import MAX_UPLOAD_BYTES


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


def csv_files(seed: int = 2) -> dict[str, tuple[str, bytes, str]]:
    case = CaseGenerator().generate(seed, Difficulty.ANALYST)
    bank, ledger = io.StringIO(), io.StringIO()
    write_bank_csv(case.bank_statement, bank)
    write_ledger_csv(case.ledger, ledger)
    return {
        "bank_file": ("bank.csv", bank.getvalue().encode(), "text/csv"),
        "ledger_file": ("ledger.csv", ledger.getvalue().encode(), "text/csv"),
    }


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_generate_case_is_reproducible_and_hides_the_answer_key(client: TestClient) -> None:
    first = client.post("/api/cases", json={"difficulty": "analyst", "seed": 42}).json()
    again = client.get("/api/cases/analyst/42").json()
    assert first == again
    assert "answer_key" not in first
    assert first["case_id"] == "EOD-analyst-42"


def test_amounts_are_decimal_strings_not_numbers(client: TestClient) -> None:
    """JSON numbers become floats in most clients, so money must travel as strings."""
    case = client.post("/api/cases", json={"difficulty": "intern", "seed": 1}).json()
    assert all(isinstance(e["amount"], str) for e in case["bank"] + case["ledger"])


def test_generate_case_without_seed_returns_the_seed_used(client: TestClient) -> None:
    case = client.post("/api/cases", json={"difficulty": "intern"}).json()
    assert client.get(f"/api/cases/intern/{case['seed']}").json() == case


def test_invalid_difficulty_and_seed_are_rejected(client: TestClient) -> None:
    assert client.post("/api/cases", json={"difficulty": "ceo"}).status_code == 422
    assert client.post("/api/cases", json={"seed": -1}).status_code == 422
    assert client.get("/api/cases/intern/99999999999").status_code == 422


def test_score_reports_accuracy_and_explanations(client: TestClient) -> None:
    case = CaseGenerator().generate(5, Difficulty.INTERN)
    groups = [
        {"bank_ids": list(item.bank_ids), "ledger_ids": list(item.ledger_ids)}
        for item in case.answer_key.items
    ]
    response = client.post(
        "/api/cases/intern/5/score", json={"groups": groups, "elapsed_seconds": 45}
    )
    body = response.json()
    assert response.status_code == 200
    assert body["accuracy"] == 1.0
    assert body["balanced"] is True
    assert body["elapsed_seconds"] == 45
    assert all(item["explanation"] for item in body["items"])


def test_score_rejects_unknown_entries(client: TestClient) -> None:
    response = client.post(
        "/api/cases/intern/5/score", json={"groups": [{"bank_ids": ["B9999"], "ledger_ids": []}]}
    )
    assert response.status_code == 422
    assert "Unknown" in response.json()["detail"]


def test_reconcile_uploaded_files(client: TestClient) -> None:
    response = client.post("/api/reconcile", files=csv_files())
    body = response.json()
    assert response.status_code == 200
    assert body["balanced"] is True
    assert body["matches"]
    assert all(isinstance(m["difference"], str) for m in body["matches"])


def test_reconcile_rejects_a_malformed_file_with_its_line(client: TestClient) -> None:
    files = csv_files()
    header = ",".join(BANK_COLUMNS)
    files["bank_file"] = (
        "bank.csv",
        f"{header}\nB1,13/11/2026,X,DIRECT,,1.00,GBP\n".encode(),
        "text/csv",
    )
    response = client.post("/api/reconcile", files=files)
    assert response.status_code == 422
    assert "Line 2" in response.json()["detail"]


def test_reconcile_rejects_an_oversized_file(client: TestClient) -> None:
    files = csv_files()
    files["bank_file"] = ("bank.csv", b"x" * (MAX_UPLOAD_BYTES + 1), "text/csv")
    response = client.post("/api/reconcile", files=files)
    assert response.status_code == 422
    assert "larger than" in response.json()["detail"]


def test_reconcile_accepts_an_excel_byte_order_mark(client: TestClient) -> None:
    files = csv_files()
    name, content, kind = files["bank_file"]
    files["bank_file"] = (name, b"\xef\xbb\xbf" + content, kind)
    assert client.post("/api/reconcile", files=files).status_code == 200


def test_reconcile_rejects_non_utf8(client: TestClient) -> None:
    files = csv_files()
    files["ledger_file"] = ("ledger.csv", "entry_id\n£".encode("latin-1"), "text/csv")
    assert client.post("/api/reconcile", files=files).status_code == 422


def test_every_endpoint_is_documented(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            assert operation.get("description"), f"{method.upper()} {path} has no description"
