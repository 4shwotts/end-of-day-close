"""Glue between the web layer and the engine: cases, uploads and form parsing.

The server keeps no game state. A case is identified by its difficulty and
seed and regenerated on demand, which is cheap and deterministic, so any
instance can serve any request. That is what lets the deployed service scale
to zero and back without losing anyone's game.
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from functools import lru_cache
from typing import Final

from eod_close.csv_format import CsvFormatError, read_bank_csv, read_ledger_csv
from eod_close.domain.results import ExceptionReason, ReconciliationReport
from eod_close.domain.statements import BankStatement, Ledger
from eod_close.engine import ReconciliationEngine
from eod_close.game import PlayerAnswers
from eod_close.generator import Case, CaseGenerator, Difficulty

# Upload limits protect a small, shared instance. 20,000 rows reconcile in
# well under a second; 2 MB comfortably holds that many rows of this format.
MAX_UPLOAD_BYTES: Final = 2 * 1024 * 1024
MAX_UPLOAD_ROWS: Final = 20_000

_ENGINE: Final = ReconciliationEngine()


class UploadError(ValueError):
    """Raised when an uploaded file is too large or not in the documented format."""


@lru_cache(maxsize=64)
def load_case(difficulty: Difficulty, seed: int) -> tuple[Case, ReconciliationReport]:
    """Generate a case and the engine's report for it.

    Cached because a game touches the same case several times (work, check,
    close). Safe to cache: both values are immutable and fully determined by
    the arguments.

    Args:
        difficulty: The level.
        seed: The seed.

    Returns:
        The case and the engine's report on it.
    """
    case = CaseGenerator().generate(seed, difficulty)
    return case, _ENGINE.reconcile(case.bank_statement, case.ledger)


def reconcile_uploads(
    bank_csv: bytes, ledger_csv: bytes
) -> tuple[BankStatement, Ledger, ReconciliationReport]:
    """Parse two uploaded CSV files and reconcile them.

    Args:
        bank_csv: The raw bank file.
        ledger_csv: The raw ledger file.

    Returns:
        The parsed statement, the parsed ledger and the engine's report.

    Raises:
        UploadError: If a file is too large, not UTF-8 or not in the
            documented format, with a message fit to show the user.
    """
    try:
        statement = read_bank_csv(_text(bank_csv, "bank"), bank_name="Uploaded bank statement")
        ledger = read_ledger_csv(_text(ledger_csv, "ledger"), entity_name="Uploaded ledger")
    except CsvFormatError as exc:
        raise UploadError(str(exc)) from exc
    for name, count in (("bank", len(statement)), ("ledger", len(ledger))):
        if count > MAX_UPLOAD_ROWS:
            raise UploadError(
                f"The {name} file has {count:,} rows; the limit is {MAX_UPLOAD_ROWS:,}."
            )
    return statement, ledger, _ENGINE.reconcile(statement, ledger)


def _text(raw: bytes, name: str) -> io.StringIO:
    if not raw:
        raise UploadError(f"The {name} file is empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise UploadError(f"The {name} file is larger than {MAX_UPLOAD_BYTES // 1024 // 1024} MB.")
    try:
        # utf-8-sig drops the byte-order mark that Excel adds when saving CSV.
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UploadError(f"The {name} file is not UTF-8 text.") from exc
    return io.StringIO(text, newline="")


def answers_from_form(
    case: Case, form: Mapping[str, str], elapsed_seconds: int | None
) -> PlayerAnswers:
    """Turn the work-the-case form into ``PlayerAnswers``.

    Each entry has a ``group-<id>`` text field and a ``reason-<id>`` select.
    Entries sharing a group label are linked. An entry with a reason is an
    exception; two entries marked as a transposition that share a label are
    one finding.

    Args:
        case: The case being worked.
        form: The submitted form fields.
        elapsed_seconds: The player's time, if known.

    Returns:
        The parsed answers. Unknown or empty fields are ignored.
    """
    groups: dict[str, tuple[set[str], set[str]]] = {}
    exceptions: dict[str, ExceptionReason] = {}
    sides = [(case.bank_statement.ids, 0), (case.ledger.ids, 1)]
    for ids, side in sides:
        for entry_id in sorted(ids):
            label = form.get(f"group-{entry_id}", "").strip()
            if label:
                groups.setdefault(label.upper(), (set(), set()))[side].add(entry_id)
            reason = form.get(f"reason-{entry_id}", "")
            if reason in ExceptionReason._value2member_map_:
                exceptions[entry_id] = ExceptionReason(reason)
    return PlayerAnswers(
        groups=tuple(
            (frozenset(bank), frozenset(ledger)) for _, (bank, ledger) in sorted(groups.items())
        ),
        exceptions=exceptions,
        elapsed_seconds=elapsed_seconds,
    )
