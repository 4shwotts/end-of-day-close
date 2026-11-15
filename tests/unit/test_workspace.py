"""Workspace tests: the sample day's outcome and the side-by-side rows."""

from collections import Counter

from eod_close.domain import Money
from eod_close.domain.results import ExceptionReason
from eod_close.web.workspace import MATCH_TAGS, Workspace, build_workspace, load_sample_day


def reconciled() -> Workspace:
    statement, ledger, report = load_sample_day()
    return build_workspace(statement, ledger, report)


def test_the_sample_day_is_small_and_mostly_matches() -> None:
    workspace = reconciled()
    assert 20 <= len(workspace.bank_rows) <= 30
    assert 20 <= len(workspace.ledger_rows) <= 30
    assert workspace.summary is not None
    assert workspace.summary.matched == 22
    assert {row.tag_key for row in workspace.bank_rows if row.tag_key} == {
        key for key, _ in MATCH_TAGS.values()
    }, "the demo shows every kind of match"


def test_the_sample_day_has_exactly_three_clear_problems() -> None:
    reasons = Counter(problem.item.reason_code for problem in reconciled().problems)
    assert reasons == Counter(
        {
            ExceptionReason.MISSING_FROM_LEDGER: 1,
            ExceptionReason.DUPLICATE: 1,
            ExceptionReason.LIKELY_TRANSPOSITION: 1,
        }
    )


def test_the_problems_add_up_to_the_unexplained_difference() -> None:
    workspace = reconciled()
    assert workspace.summary is not None
    assert workspace.problems_total == workspace.summary.unexplained_difference
    assert workspace.problems_total == Money.of("-42.50")


def test_every_line_appears_once_in_file_order() -> None:
    statement, ledger, report = load_sample_day()
    workspace = build_workspace(statement, ledger, report)
    assert [row.entry_id for row in workspace.bank_rows] == [e.entry_id for e in statement]
    assert [row.entry_id for row in workspace.ledger_rows] == [e.entry_id for e in ledger]


def test_partners_point_at_each_other_and_share_a_group() -> None:
    workspace = reconciled()
    by_id = {(row.side, row.entry_id): row for row in workspace.bank_rows + workspace.ledger_rows}
    for row in by_id.values():
        assert row.status in ("matched", "review"), f"{row.entry_id} has no outcome"
        for partner_id in row.partners:
            partner = by_id[row.partner_side, partner_id]
            assert row.entry_id in partner.partners
            assert partner.group == row.group


def test_a_batch_payout_links_one_bank_line_to_several_sales() -> None:
    batch = next(row for row in reconciled().bank_rows if row.tag_key == "batch")
    assert len(batch.partners) == 3


def test_before_reconciling_no_row_has_an_outcome() -> None:
    statement, ledger, _ = load_sample_day()
    workspace = build_workspace(statement, ledger)
    assert not workspace.reconciled
    assert all(row.status == "pending" and row.tag is None for row in workspace.bank_rows)
    assert workspace.problems == ()
