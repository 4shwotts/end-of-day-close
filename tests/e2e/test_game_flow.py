"""Browser tests of the reconcile tool and training mode, using Playwright and Chromium."""

import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from eod_close.generator import CaseGenerator, Difficulty

pytestmark = pytest.mark.e2e

SEED = 4


def test_play_a_perfect_day_and_extend_the_streak(page: Page, live_server: str) -> None:
    """Start a day from the menu, link every planted pair, close it, and see the streak go up."""
    case = CaseGenerator().generate(SEED, Difficulty.INTERN)
    page.goto(f"{live_server}/training")
    page.get_by_label("Intern", exact=True).check()
    page.get_by_label("Day number (optional)").fill(str(SEED))
    page.get_by_role("button", name="Start the day").click()
    expect(page).to_have_url(re.compile(rf"/play/intern/{SEED}$"))

    for item in case.answer_key.items:
        for entry_id in item.bank_ids + item.ledger_ids:
            page.get_by_label(f"Select {entry_id}", exact=True).check()
        page.get_by_role("button", name="Link selected").click()
        expect(page.locator("#link-status")).to_contain_text("Linked")

    page.get_by_role("button", name="Check progress").click()
    expect(page.locator("#progress")).to_contain_text("Everything is linked or explained")

    page.get_by_role("button", name="Close the day").first.click()
    expect(page.get_by_role("heading", level=1)).to_contain_text("day closed")
    expect(page.locator("#close-summary")).to_contain_text("100.0%")
    expect(page.locator("#streak-count")).to_have_text("1")


def test_an_unbalanced_day_resets_the_streak(page: Page, live_server: str) -> None:
    page.goto(f"{live_server}/play/intern/{SEED + 1}")
    page.evaluate("window.localStorage.setItem('eod-close.streak', '3')")
    page.get_by_role("button", name="Close the day").first.click()
    expect(page.get_by_role("heading", level=1)).to_contain_text("not balanced")
    expect(page.locator("#streak-count")).to_have_text("0")


def test_start_page_works_from_the_keyboard(page: Page, live_server: str) -> None:
    """Tab to the first level, choose another with the arrow keys and start with Enter."""
    page.goto(f"{live_server}/training")
    page.keyboard.press("Tab")
    expect(page.get_by_role("link", name="Skip to content")).to_be_focused()
    page.get_by_label("Intern", exact=True).focus()
    page.keyboard.press("ArrowDown")
    expect(page.get_by_label("Analyst", exact=True)).to_be_checked()
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(r"/play/analyst/\d+$"))


def test_arena_draws_the_chart_with_htmx(page: Page, live_server: str) -> None:
    page.goto(f"{live_server}/arena")
    for size in ("1,000", "2,500", "5,000"):
        page.get_by_label(size, exact=True).uncheck()
    page.get_by_role("button", name="Run the race").click()
    expect(page.locator("#arena-results svg")).to_be_visible()
    expect(page.locator("#arena-results table")).to_contain_text("500")
    expect(page).to_have_url(f"{live_server}/arena")


@pytest.mark.parametrize("path", ["/", "/sample/1", "/training", "/play/senior-analyst/1"])
def test_pages_fit_a_phone_screen(page: Page, live_server: str, path: str) -> None:
    """At phone width no page may scroll sideways; wide tables scroll in their own box."""
    page.set_viewport_size({"width": 375, "height": 800})
    page.goto(f"{live_server}{path}")
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 0


def test_sample_day_from_the_home_page(page: Page, live_server: str) -> None:
    """Open a sample day, read the summary, open a match type and download the report."""
    page.goto(live_server)
    page.get_by_role("link", name="Try a sample day").click()
    expect(page).to_have_url(re.compile(r"/sample/\d+$"))
    expect(page.locator("#summary")).to_contain_text("Needs review")
    expect(page.locator("#summary")).to_contain_text("Books balance")

    page.locator("details.group summary", has_text="Exact").click()
    expect(page.get_by_role("columnheader", name="Why they match").first).to_be_visible()

    with page.expect_download() as download_info:
        page.get_by_role("link", name="Download the report (CSV)").click()
    download = download_info.value
    assert download.suggested_filename.endswith("-report.csv")
    first_line = Path(download.path()).read_text(encoding="utf-8").splitlines()[0]
    assert first_line.startswith("status,type,bank_entries")


def test_upload_own_files_shows_results_in_place(
    page: Page, live_server: str, tmp_path: Path
) -> None:
    """Upload the example files and see the results appear without leaving the page."""
    for name in ("bank", "ledger"):
        content = page.request.get(f"{live_server}/samples/{name}.csv").body()
        (tmp_path / f"{name}.csv").write_bytes(content)
    page.goto(live_server)
    page.get_by_role("link", name="Upload your own files").click()
    page.get_by_label("Bank statement (CSV)").set_input_files(tmp_path / "bank.csv")
    page.get_by_label("Ledger (CSV)").set_input_files(tmp_path / "ledger.csv")
    page.get_by_role("button", name="Reconcile").click()
    expect(page.locator("#results #summary")).to_contain_text("Matched")
    expect(page).to_have_url(re.compile(rf"^{re.escape(live_server)}/(#upload)?$"))
