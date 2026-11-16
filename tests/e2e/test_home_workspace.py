"""Browser tests of the Reconcile page: one instruction, Match, then what is wrong."""

import re
from decimal import Decimal

import pytest
from playwright.sync_api import Browser, Page, expect

pytestmark = pytest.mark.e2e

SELECTED = re.compile(r"\bis-selected\b")


def money(text: str) -> Decimal:
    """Read an amount as shown on the page, such as ``-£91.00`` or ``£1,250.00``."""
    return Decimal(re.sub(r"[^0-9.\-]", "", text))


def match(page: Page, url: str) -> None:
    page.goto(url)
    page.get_by_role("button", name="Match", exact=True).click()
    expect(page.locator(".result-headline")).to_be_visible()


def test_match_the_sample_day_and_read_what_is_wrong(page: Page, live_server: str) -> None:
    """Instruction, Match, headline, problem cards, highlighted rows and a total that adds up."""
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(live_server)
    expect(page.locator("#status-heading")).to_have_text(
        "Your bank and your records don't quite agree. Press Match to find out why."
    )
    expect(page.locator(".list-bank tbody tr")).to_have_count(12)
    expect(page.locator(".list-ledger tbody tr")).to_have_count(13)
    expect(page.locator(".list-bank thead th")).to_have_text(["Date", "Reference", "Amount"])

    page.get_by_role("button", name="Match", exact=True).click()
    headline = page.locator(".result-headline")
    expect(headline).to_have_text("Matched 10 payments and found 3 problems")
    expect(headline).to_be_focused()

    # The problems come first, as plain cards with a next step.
    cards = page.locator(".problem")
    expect(cards).to_have_count(3)
    expect(page.locator("#problems-heading")).to_have_text(re.compile(r"Problems found \(3\)"))
    expect(cards.nth(0)).to_contain_text(
        "£25.00 arrived in the bank (INV-40139) but isn't in your records."
    )
    expect(cards.nth(0)).to_contain_text("Check whether a sale wasn't recorded.")
    expect(cards.locator(".next-step")).to_have_count(3)

    # Selecting a card highlights its rows in both lists, and nothing else.
    typo = page.locator(".problem", has_text="Typo")
    typo.click()
    expect(typo).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#row-bank-B09")).to_have_class(SELECTED)
    expect(page.locator("#row-ledger-L10")).to_have_class(SELECTED)
    expect(page.locator(".row.is-selected")).to_have_count(2)

    duplicate = page.locator(".problem", has_text="Duplicate")
    duplicate.click()
    expect(page.locator("#row-ledger-L13")).to_have_class(SELECTED)
    expect(page.locator(".row.is-selected")).to_have_count(1)
    expect(typo).to_have_attribute("aria-pressed", "false")

    # The difference under the headline is the total of the cards.
    effects = [
        Decimal(value)
        for value in cards.locator("[data-effect]").evaluate_all(
            "els => els.map(el => el.dataset.effect)"
        )
    ]
    difference = money(page.locator("#status-unexplained").inner_text())
    assert sum(effects) == difference == Decimal("-91.00")


def test_matched_rows_carry_a_type_and_say_why(page: Page, live_server: str) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    match(page, live_server)
    tags = page.locator(".list-bank .row-matched .tag").all_inner_texts()
    assert {tag.strip() for tag in tags} == {
        "Exact",
        "Fee",
        "Delayed",
        "Currency",
        "Batch",
        "Refund",
    }
    fee = page.locator("#row-bank-B02")
    expect(fee.locator(".why-text")).to_be_hidden()
    fee.get_by_text("Why?").click()
    expect(fee.locator(".why-text")).to_contain_text("£5.25 less")
    expect(page.locator(".row-review .why")).to_have_count(0)


def test_every_match_is_listed_once_unfolded(page: Page, live_server: str) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    match(page, live_server)
    matches = page.locator(".match-list li")
    expect(matches.first).to_be_hidden()
    page.get_by_text("Show all matches (10)").click()
    expect(matches).to_have_count(10)
    expect(page.locator(".match-list")).to_contain_text("QFW-PAYOUT-20261112-01 £73.56")


def test_the_description_shows_on_hover_and_on_focus(page: Page, live_server: str) -> None:
    page.goto(live_server)
    reference = page.locator("#row-ledger-L02 .ref-button")
    description = page.locator("#desc-ledger-L02")
    expect(description).to_be_hidden()
    reference.hover()
    expect(description).to_have_text("Web order: oak serving board set")
    page.mouse.move(0, 0)
    expect(description).to_be_hidden()
    reference.focus()
    expect(description).to_be_visible()


def test_on_a_phone_selecting_a_card_switches_to_its_list(page: Page, live_server: str) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    match(page, live_server)
    expect(page.locator(".list-bank")).to_be_visible()
    expect(page.locator(".list-ledger")).to_be_hidden()
    page.locator(".problem", has_text="Duplicate").click()
    expect(page.locator(".list-ledger")).to_be_visible()
    expect(page.locator("#row-ledger-L13")).to_have_class(SELECTED)
    page.locator(".side-switch").get_by_text("Bank statement").click()
    expect(page.locator(".list-bank")).to_be_visible()


def test_match_works_without_javascript(browser: Browser, live_server: str) -> None:
    """The form posts as a plain page and the result is the same."""
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    page.goto(live_server)
    page.get_by_role("button", name="Match", exact=True).click()
    expect(page.locator(".result-headline")).to_have_text(
        "Matched 10 payments and found 3 problems"
    )
    expect(page.locator(".problem")).to_have_count(3)
    context.close()


def test_a_problem_card_works_from_the_keyboard(page: Page, live_server: str) -> None:
    page.goto(live_server)
    page.get_by_role("button", name="Match", exact=True).focus()
    page.keyboard.press("Enter")
    expect(page.locator(".result-headline")).to_be_focused()
    page.locator(".problem", has_text="Typo").focus()
    page.keyboard.press("Enter")
    expect(page.locator("#row-bank-B09")).to_have_class(SELECTED)


def test_try_another_day_gives_a_small_day_of_the_same_shape(page: Page, live_server: str) -> None:
    page.goto(live_server)
    page.get_by_role("link", name="Try another day").click()
    expect(page).to_have_url(re.compile(r"/day/\d+$"))
    bank_rows = page.locator(".list-bank tbody tr").count()
    assert 12 <= bank_rows <= 13
    page.get_by_role("button", name="Match", exact=True).click()
    expect(page.locator(".result-headline")).to_have_text(
        "Matched 10 payments and found 3 problems"
    )
    effects = page.locator(".problem [data-effect]").evaluate_all(
        "els => els.map(el => el.dataset.effect)"
    )
    total = sum(Decimal(value) for value in effects)
    assert total == money(page.locator("#status-unexplained").inner_text())


def test_reduced_motion_turns_the_reveal_off(browser: Browser, live_server: str) -> None:
    context = browser.new_context(reduced_motion="reduce")
    page = context.new_page()
    match(page, live_server)
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


def test_the_report_downloads_after_matching(page: Page, live_server: str) -> None:
    page.goto(live_server)
    expect(page.get_by_role("link", name="Download the report (CSV)")).to_have_count(0)
    page.get_by_role("button", name="Match", exact=True).click()
    with page.expect_download() as download_info:
        page.get_by_role("link", name="Download the report (CSV)").click()
    assert download_info.value.suggested_filename == "sample-day-report.csv"
