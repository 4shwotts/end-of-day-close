"""Browser tests of the home page's workspace: reconcile the sample day and explore it."""

import re
from decimal import Decimal

import pytest
from playwright.sync_api import Browser, Page, expect

pytestmark = pytest.mark.e2e


def money(text: str) -> Decimal:
    """Read an amount as shown on the page, such as ``-£42.50`` or ``£1,250.00``."""
    return Decimal(re.sub(r"[^0-9.\-]", "", text))


def reconcile(page: Page, live_server: str) -> None:
    page.goto(live_server)
    page.get_by_role("button", name="Match them").click()
    expect(page.locator("#status-matched")).to_be_visible()


def test_reconcile_the_sample_day_and_explore_the_result(page: Page, live_server: str) -> None:
    """Load, reconcile, select a row, read the review panel, and check the figures agree."""
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(live_server)
    status = page.locator("#status-line")
    expect(status).to_contain_text("Not matched yet")
    expect(status).to_contain_text("24 bank payments")
    expect(status).to_contain_text("26 in your records")
    expect(page.locator("[data-select]")).to_have_count(0)

    page.get_by_role("button", name="Match them").click()
    expect(status).to_contain_text("22 matched")
    expect(status).to_contain_text("3 for a person to check")
    expect(status).to_contain_text("seconds")
    expect(status).to_be_focused()

    # Selecting a row highlights its partner on the other side and explains the match.
    page.locator("#row-bank-B02 [data-select]").click()
    expect(page.locator("#row-bank-B02")).to_have_class(re.compile(r"\bis-selected\b"))
    expect(page.locator("#row-ledger-L02")).to_have_class(re.compile(r"\bis-selected\b"))
    expect(page.locator("#row-bank-B02 [data-select]")).to_have_attribute("aria-pressed", "true")
    selection = page.locator("#selection")
    expect(selection).to_contain_text("B02 ↔ L02 · Card fee:")
    expect(selection).to_contain_text("£5.25 less")

    # The needs-review panel lists each exception with a next step...
    problems = page.locator(".problem")
    expect(problems).to_have_count(3)
    for label in ("Typo", "Duplicate", "Not in your records"):
        expect(page.locator(".review-panel")).to_contain_text(label)
    expect(problems.locator(".next-step")).to_have_count(3)

    # ...and their effects add up to the unexplained figure in the status bar.
    effects = [
        Decimal(value)
        for value in problems.locator("[data-effect]").evaluate_all(
            "els => els.map(el => el.dataset.effect)"
        )
    ]
    unexplained = money(page.locator("#status-unexplained").inner_text())
    assert sum(effects) == unexplained == money(page.locator("#problems-total").inner_text())
    assert unexplained == Decimal("-42.50")


def test_a_problem_can_be_found_in_the_lists(page: Page, live_server: str) -> None:
    reconcile(page, live_server)
    duplicate = page.locator(".problem", has_text="Duplicate")
    duplicate.get_by_role("button", name="Show in the lists").click()
    expect(page.locator("#row-ledger-L26")).to_have_class(re.compile(r"\bis-selected\b"))
    expect(duplicate).to_have_class(re.compile(r"\bis-selected\b"))
    expect(page.locator("#row-ledger-L26 [data-select]")).to_be_focused()


@pytest.mark.parametrize(
    ("pill", "bank_rows", "ledger_rows"),
    [("All", 24, 26), ("To check", 2, 2), ("Matched", 22, 24), ("Bundled", 1, 3)],
)
def test_the_filter_shows_only_the_chosen_rows(
    page: Page, live_server: str, pill: str, bank_rows: int, ledger_rows: int
) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    reconcile(page, live_server)
    page.locator(".filter").get_by_text(pill, exact=True).click()
    expect(page.locator(".list-bank .row:visible")).to_have_count(bank_rows)
    expect(page.locator(".list-ledger .row:visible")).to_have_count(ledger_rows)


def test_the_filter_works_from_the_keyboard(page: Page, live_server: str) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    reconcile(page, live_server)
    page.locator("#show-all").focus()
    page.keyboard.press("ArrowRight")
    expect(page.locator("#show-matched")).to_be_checked()
    page.keyboard.press("ArrowRight")
    expect(page.locator("#show-review")).to_be_checked()
    expect(page.locator(".list-bank .row:visible")).to_have_count(2)


def test_on_a_phone_one_list_shows_and_partner_links_switch_it(
    page: Page, live_server: str
) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    reconcile(page, live_server)
    expect(page.locator(".list-bank")).to_be_visible()
    expect(page.locator(".list-ledger")).to_be_hidden()
    page.locator("#row-bank-B02 .partner").click()
    expect(page.locator(".list-ledger")).to_be_visible()
    expect(page.locator("#row-ledger-L02")).to_have_class(re.compile(r"\bis-selected\b"))
    page.locator(".side-switch").get_by_text("Bank statement").click()
    expect(page.locator(".list-bank")).to_be_visible()


def test_reconcile_works_without_javascript(browser: Browser, live_server: str) -> None:
    """The form posts as a plain page, and partner links still jump to the partner."""
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    page.goto(live_server)
    page.get_by_role("button", name="Match them").click()
    expect(page.locator("#status-line")).to_contain_text("22 matched")
    page.locator("#row-bank-B02 .partner").click()
    expect(page).to_have_url(re.compile(r"#row-ledger-L02$"))
    context.close()


def test_reconcile_from_the_keyboard(page: Page, live_server: str) -> None:
    page.goto(live_server)
    page.get_by_role("button", name="Match them").focus()
    page.keyboard.press("Enter")
    expect(page.locator("#status-line")).to_be_focused()
    page.locator("#row-bank-B05 [data-select]").focus()
    page.keyboard.press("Enter")
    expect(page.locator("#selection")).to_contain_text("B05 ↔ L05 · Euro:")


def test_reduced_motion_turns_the_reveal_off(browser: Browser, live_server: str) -> None:
    context = browser.new_context(reduced_motion="reduce")
    page = context.new_page()
    reconcile(page, live_server)
    names = page.locator(".is-reconciled .outcome .tag").first.evaluate(
        "el => getComputedStyle(el).animationName"
    )
    assert names == "none"
    context.close()


def test_tooltips_explain_terms_on_focus(page: Page, live_server: str) -> None:
    page.goto(live_server)
    button = page.get_by_role("button", name="What is bank statement?")
    button.focus()
    tooltip = page.locator("#tip-bank")
    expect(tooltip).to_be_visible()
    expect(tooltip).to_contain_text("actually came into or left the account")
    page.keyboard.press("Escape")
    expect(tooltip).to_be_hidden()


def test_the_report_downloads_from_the_home_page(page: Page, live_server: str) -> None:
    page.goto(live_server)
    with page.expect_download() as download_info:
        page.get_by_role("link", name="Download report (CSV)").click()
    assert download_info.value.suggested_filename == "sample-day-report.csv"
