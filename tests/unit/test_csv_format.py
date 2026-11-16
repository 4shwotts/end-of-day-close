"""CSV format tests: round trips and the validation errors a bad upload produces."""

import io

import pytest

from eod_close.csv_format import (
    BANK_COLUMNS,
    CsvFormatError,
    read_bank_csv,
    read_ledger_csv,
    spreadsheet_safe,
    write_bank_csv,
    write_ledger_csv,
)
from eod_close.generator import CaseGenerator, Difficulty


def test_bank_and_ledger_round_trip_through_csv() -> None:
    case = CaseGenerator().generate(11, Difficulty.HEAD_OF_OPS)
    bank_out, ledger_out = io.StringIO(), io.StringIO()
    write_bank_csv(case.bank_statement, bank_out)
    write_ledger_csv(case.ledger, ledger_out)

    bank_back = read_bank_csv(io.StringIO(bank_out.getvalue()))
    ledger_back = read_ledger_csv(io.StringIO(ledger_out.getvalue()))

    assert bank_back.entries == case.bank_statement.entries
    assert ledger_back.entries == case.ledger.entries


def test_wrong_header_is_rejected() -> None:
    with pytest.raises(CsvFormatError, match="Expected columns"):
        read_bank_csv(io.StringIO("id,amount\nB1,1.00\n"))


def test_bad_row_reports_its_line_number() -> None:
    header = ",".join(BANK_COLUMNS)
    good = "B1,2026-11-13,INV-1,DIRECT,,10.00,GBP"
    bad = "B2,2026-11-13,INV-2,DIRECT,,1.005,GBP"
    text = f"{header}\n{good}\n{bad}\n"
    with pytest.raises(CsvFormatError, match="Line 3"):
        read_bank_csv(io.StringIO(text))


def test_duplicate_ids_in_a_file_are_rejected() -> None:
    header = ",".join(BANK_COLUMNS)
    row = "B1,2026-11-13,INV-1,DIRECT,,10.00,GBP"
    with pytest.raises(CsvFormatError, match="more than once"):
        read_bank_csv(io.StringIO(f"{header}\n{row}\n{row}\n"))


def test_empty_file_is_rejected() -> None:
    with pytest.raises(CsvFormatError, match="no entries"):
        read_bank_csv(io.StringIO(",".join(BANK_COLUMNS) + "\n"))


@pytest.mark.parametrize(
    "cell",
    ["=SUM(A1:A9)", "+44 20 7946 0000", "-1+1", "@cmd", "\tlead", "\rlead", '=HYPERLINK("x")'],
)
def test_cells_that_look_like_formulas_are_neutralised(cell: str) -> None:
    """OWASP's CSV injection advice: a leading apostrophe makes the cell plain text."""
    assert spreadsheet_safe(cell) == "'" + cell


@pytest.mark.parametrize("cell", ["-34.99", "+12", "1250.00", "INV-40112", "", "Exact"])
def test_numbers_and_ordinary_text_are_left_alone(cell: str) -> None:
    assert spreadsheet_safe(cell) == cell


def test_values_that_are_not_text_pass_through() -> None:
    assert spreadsheet_safe(None) is None
    assert spreadsheet_safe(7) == 7
