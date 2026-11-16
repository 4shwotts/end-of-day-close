"""Browser tests of upload, training mode, How it works and the busy day, using Playwright."""

import io
import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from eod_close.csv_format import write_bank_csv, write_ledger_csv
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
    expect(page.locator("#timer")).to_have_count(0)
    page.get_by_role("link", name="Start the timer").click()
    expect(page).to_have_url(re.compile(rf"/play/intern/{SEED}/work$"))

    for item in case.answer_key.items:
        for entry_id in item.bank_ids + item.ledger_ids:
            page.get_by_label(f"Select {entry_id}", exact=True).check()
        page.get_by_role("button", name="Link selected").click()
        expect(page.locator("#link-status")).to_contain_text("Linked")

    page.get_by_role("button", name="Check progress").click()
    expect(page.locator("#progress")).to_contain_text("Everything is paired or explained")

    page.get_by_role("button", name="Close the day").first.click()
    expect(page.get_by_role("heading", level=1)).to_have_text("You vs the engine")
    expect(page.locator("#close-summary")).to_contain_text("day closed")
    expect(page.locator("#close-summary")).to_contain_text("100.0%")
    expect(page.locator("#streak-count")).to_have_text("1")


def test_an_unbalanced_day_resets_the_streak(page: Page, live_server: str) -> None:
    page.goto(f"{live_server}/play/intern/{SEED + 1}/work")
    page.evaluate("window.localStorage.setItem('eod-close.streak', '3')")
    page.get_by_role("button", name="Close the day").first.click()
    expect(page.locator("#close-summary")).to_contain_text("day not balanced")
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
    [
        "/",
        "/upload",
        "/day/1",
        "/training",
        "/how-it-works",
        "/how-it-works/busy-day/48213",
        "/play/senior-analyst/1",
        "/play/senior-analyst/1/work",
    ],
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


def test_busy_day_on_how_it_works(page: Page, live_server: str) -> None:
    """Run a busy day, check every claim is shown, and run the same day again."""
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{live_server}/how-it-works")
    page.get_by_role("button", name="Run a busy day").click()
    headline = page.locator("#busy-headline")
    expect(headline).to_have_text(
        re.compile(r"Matched [\d,]+ and found [\d,]+ problems? in .*seconds\.")
    )
    expect(page).to_have_url(re.compile(r"/how-it-works/busy-day/\d+$"))
    seed = page.locator("#busy-seed").inner_text()
    marks = page.locator("#busy-marks").inner_text()
    assert re.search(r"Found \d+ of \d+ planted problems", marks)
    expect(page.locator("#engine-unexplained")).to_have_text("0")
    expect(page.locator("#naive-unexplained")).to_contain_text(" of ")

    cards = page.locator("#busy-result .problems").first.locator(".problem")
    expect(cards).to_have_count(5)
    page.get_by_text("Show all problems").click()
    expect(page.locator("#busy-result .busy-more .problem").first).to_be_visible()

    with page.expect_download() as download_info:
        page.get_by_role("link", name="Download the answer key").click()
    assert download_info.value.suggested_filename.endswith("-answer-key.csv")

    page.get_by_role("link", name="Run this exact day again").click()
    expect(page.locator("#busy-seed")).to_have_text(seed)
    expect(page.locator("#busy-marks")).to_have_text(marks)


def test_busy_day_lists_page_and_filter_to_problems(page: Page, live_server: str) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{live_server}/how-it-works/busy-day/48213")
    lists = page.locator("#busy-lists")
    expect(lists).to_be_hidden()
    page.get_by_text("View the two lists").click()
    expect(lists.locator(".list-bank tbody tr")).to_have_count(20)
    lists.get_by_role("link", name="Next").click()
    expect(page.locator("#busy-page")).to_contain_text("Page 2 of")
    lists.get_by_role("link", name="Problems").click()
    expect(lists.locator(".row-matched")).to_have_count(0)
    expect(lists.locator(".row-review").first).to_be_visible()
    expect(lists.get_by_role("link", name="Problems")).to_have_attribute("aria-current", "true")


def test_old_harder_day_links_land_on_the_busy_day(page: Page, live_server: str) -> None:
    page.goto(f"{live_server}/sample/12")
    expect(page).to_have_url(f"{live_server}/how-it-works#busy-day")
    expect(page.get_by_role("heading", name="Busy day")).to_be_visible()


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
    page.get_by_label("Your records (CSV)").set_input_files(tmp_path / "ledger.csv")
    page.get_by_role("button", name="Match them").click()
    expect(page.locator("#results #summary")).to_contain_text("Matched")
    expect(page).to_have_url(f"{live_server}/upload")


def test_a_long_group_scrolls_in_its_own_box(page: Page, live_server: str, tmp_path: Path) -> None:
    """A group of a hundred matches stays a short box, and a shared reason is said once."""
    case = CaseGenerator().generate(1, Difficulty.HEAD_OF_OPS)
    bank, ledger = io.StringIO(), io.StringIO()
    write_bank_csv(case.bank_statement, bank)
    write_ledger_csv(case.ledger, ledger)
    (tmp_path / "bank.csv").write_text(bank.getvalue(), encoding="utf-8")
    (tmp_path / "ledger.csv").write_text(ledger.getvalue(), encoding="utf-8")
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{live_server}/upload")
    page.get_by_label("Bank statement (CSV)").set_input_files(tmp_path / "bank.csv")
    page.get_by_label("Your records (CSV)").set_input_files(tmp_path / "ledger.csv")
    page.get_by_role("button", name="Match them").click()
    page.locator("details.fold summary", has_text="Show all").click()
    exact = page.locator("details.group:not(.fold)", has=page.locator("summary", has_text="Exact"))
    exact.locator("> summary").click()
    expect(exact.locator(".group-reason")).to_have_text("Same reference, amount and date.")
    expect(exact.get_by_role("columnheader", name="Why they match")).to_have_count(0)
    box = exact.locator(".long-list")
    sizes = box.evaluate("el => [el.clientHeight, el.scrollHeight]")
    assert sizes[0] <= 26 * 16, "the box stays about ten rows tall"
    assert sizes[1] > sizes[0], "the rest of the group scrolls inside it"
