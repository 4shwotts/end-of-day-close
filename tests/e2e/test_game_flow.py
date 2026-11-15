"""Browser tests of upload, the harder day, training mode and How it works, using Playwright."""

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


def test_how_it_works_draws_the_race_chart_with_htmx(page: Page, live_server: str) -> None:
    page.goto(live_server)
    page.get_by_role("navigation", name="Main").get_by_role("link", name="How it works").click()
    expect(page.get_by_role("heading", level=1)).to_have_text("How it works")
    for size in ("1,000", "2,500", "5,000"):
        page.get_by_label(size, exact=True).uncheck()
    page.get_by_role("button", name="Run the race").click()
    expect(page.locator("#race-results svg")).to_be_visible()
    expect(page.locator("#race-results table")).to_contain_text("500")
    expect(page).to_have_url(f"{live_server}/how-it-works")


def test_old_arena_link_lands_on_how_it_works(page: Page, live_server: str) -> None:
    page.goto(f"{live_server}/arena")
    expect(page).to_have_url(f"{live_server}/how-it-works")


def test_streak_is_shown_in_training_mode_only(page: Page, live_server: str) -> None:
    page.goto(live_server)
    expect(page.locator("#streak-count")).to_have_count(0)
    page.goto(f"{live_server}/training")
    expect(page.locator("#streak-count")).to_be_visible()


def test_drop_zone_shows_the_chosen_file(page: Page, live_server: str, tmp_path: Path) -> None:
    """Choosing a file the ordinary way names it in the zone, for every user."""
    csv_file = tmp_path / "my-bank.csv"
    csv_file.write_bytes(page.request.get(f"{live_server}/samples/bank.csv").body())
    page.goto(f"{live_server}/upload")
    page.get_by_label("Bank statement (CSV)").set_input_files(csv_file)
    expect(page.locator("#bank_file-chosen")).to_have_text("Chosen: my-bank.csv")
    expect(page.locator("#ledger_file-chosen")).to_have_text("No file chosen")


def test_drop_zone_accepts_a_dropped_file(page: Page, live_server: str) -> None:
    """Simulate dragging a file over the ledger zone and dropping it."""
    page.goto(f"{live_server}/upload")
    zone = page.locator(".dropzone", has=page.locator("#ledger_file"))
    transfer = page.evaluate_handle(
        """() => {
            const transfer = new DataTransfer();
            transfer.items.add(new File(["entry_id"],"dropped-ledger.csv", {type: "text/csv"}));
            return transfer;
        }"""
    )
    zone.dispatch_event("dragenter", {"dataTransfer": transfer})
    expect(zone).to_have_class(re.compile(r"\bis-dragover\b"))
    zone.dispatch_event("drop", {"dataTransfer": transfer})
    expect(zone).not_to_have_class(re.compile(r"\bis-dragover\b"))
    expect(page.locator("#ledger_file-chosen")).to_have_text("Chosen: dropped-ledger.csv")
    assert page.evaluate("document.getElementById('ledger_file').files[0].name") == (
        "dropped-ledger.csv"
    )


@pytest.mark.parametrize("width", [375, 820, 1440])
@pytest.mark.parametrize(
    "path",
    ["/", "/upload", "/sample/1", "/training", "/how-it-works", "/play/senior-analyst/1"],
)
def test_pages_fit_phone_tablet_and_desktop(
    page: Page, live_server: str, path: str, width: int
) -> None:
    """No page may scroll sideways at any width; wide tables scroll in their own box."""
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(f"{live_server}{path}")
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 0


def test_harder_day_from_the_home_page(page: Page, live_server: str) -> None:
    """Open a harder day, read the summary, open a match type and download the report."""
    page.goto(live_server)
    page.get_by_role("link", name="Try a harder day").click()
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
    expect(page).to_have_url(f"{live_server}/upload")
    page.get_by_label("Bank statement (CSV)").set_input_files(tmp_path / "bank.csv")
    page.get_by_label("Ledger (CSV)").set_input_files(tmp_path / "ledger.csv")
    page.get_by_role("button", name="Reconcile").click()
    expect(page.locator("#results #summary")).to_contain_text("Matched")
    expect(page).to_have_url(f"{live_server}/upload")
