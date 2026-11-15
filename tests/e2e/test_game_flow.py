"""Browser tests of the main game flow, using Playwright and Chromium."""

import re

import pytest
from playwright.sync_api import Page, expect

from eod_close.generator import CaseGenerator, Difficulty

pytestmark = pytest.mark.e2e

SEED = 4


def test_play_a_perfect_day_and_extend_the_streak(page: Page, live_server: str) -> None:
    """Start a day from the menu, link every planted pair, close it, and see the streak go up."""
    case = CaseGenerator().generate(SEED, Difficulty.INTERN)
    page.goto(live_server)
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
    page.goto(live_server)
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


def test_work_page_fits_a_phone_screen(page: Page, live_server: str) -> None:
    """At phone width the page must not scroll sideways; wide tables scroll in their own box."""
    page.set_viewport_size({"width": 375, "height": 800})
    page.goto(f"{live_server}/play/senior-analyst/1")
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 0
