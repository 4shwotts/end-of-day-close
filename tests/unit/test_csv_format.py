"""CSV format tests: round trips and the validation errors a bad upload produces."""

import io

import pytest

from eod_close.csv_format import (
    BANK_COLUMNS,
    CsvFormatError,
    read_bank_csv,
    read_ledger_csv,
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
