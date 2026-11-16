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
    tags = page.locator(".list-bank .row-matched .mark").all_inner_texts()
    assert {tag.strip() for tag in tags} == {
        "Exact",
        "Fee",
        "Delayed",
        "Currency",
        "Batch",
        "Refund",
    }
    expect(page.get_by_text("Why?")).to_have_count(0)


def test_every_row_explains_itself_in_a_popover(page: Page, live_server: str) -> None:
    """Open, close and keyboard behaviour of the popover, on a match and on a problem."""
    page.set_viewport_size({"width": 1440, "height": 900})
    match(page, live_server)
    why = page.locator("#row-bank-B02").get_by_role("button", name="Why was ORD-51207 matched?")
    reason = page.locator("#why-bank-B02")
    expect(reason).to_be_hidden()
    why.click()
    expect(reason).to_be_visible()
    expect(reason).to_contain_text("£5.25 less")

    # Opening another closes the first: only one is open at a time.
    wrong = page.locator("#row-bank-B09").get_by_role(
        "button", name="What is wrong with INV-40125?"
    )
    wrong.click()
    expect(reason).to_be_hidden()
    problem = page.locator("#why-bank-B09")
    expect(problem).to_contain_text("two digits look swapped")
    expect(problem.locator(".next-step")).to_contain_text("Check the invoice")

    # Escape closes it and leaves focus on its button.
    page.keyboard.press("Escape")
    expect(problem).to_be_hidden()
    expect(wrong).to_be_focused()

    # Enter and Space open it from the keyboard; a click outside closes it.
    page.keyboard.press("Enter")
    expect(problem).to_be_visible()
    page.keyboard.press("Escape")
    page.keyboard.press("Space")
    expect(problem).to_be_visible()
    page.mouse.click(5, 5)
    expect(problem).to_be_hidden()
    expect(page.locator(".why-popover:popover-open")).to_have_count(0)


@pytest.mark.parametrize(
    ("pill", "bank_rows", "ledger_rows"),
    [("All", 12, 13), ("Matched", 10, 11), ("Problems", 2, 2)],
)
def test_the_filter_shows_only_the_chosen_rows(
    page: Page, live_server: str, pill: str, bank_rows: int, ledger_rows: int
) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    match(page, live_server)
    expect(page.get_by_text("Show all matches")).to_have_count(0)
    page.locator(".filter").get_by_text(pill, exact=True).click()
    expect(page.locator(".list-bank .row:visible")).to_have_count(bank_rows)
    expect(page.locator(".list-ledger .row:visible")).to_have_count(ledger_rows)


def test_the_filter_works_from_the_keyboard(page: Page, live_server: str) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    match(page, live_server)
    page.locator("#show-all").focus()
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")
    expect(page.locator("#show-problems")).to_be_checked()
    expect(page.locator(".list-bank .row:visible")).to_have_count(2)


def test_selecting_a_card_shows_its_rows_even_when_filtered_out(
    page: Page, live_server: str
) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    match(page, live_server)
    page.locator(".filter").get_by_text("Matched", exact=True).click()
    expect(page.locator("#row-bank-B09")).to_be_hidden()
    page.locator(".problem", has_text="Typo").click()
    expect(page.locator("#show-all")).to_be_checked()
    expect(page.locator("#row-bank-B09")).to_be_visible()


def test_the_description_shows_on_hover_and_on_focus(page: Page, live_server: str) -> None:
    page.goto(live_server)
    reference = page.locator("#row-ledger-L02 .ref-button")
    description = page.locator("#desc-ledger-L02")
    expect(description).to_be_hidden()
    reference.hover()
    expect(description).to_contain_text("ORD-51207")
    expect(description).to_contain_text("Web order: oak serving board set")
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


def test_the_stamp_gives_the_real_result(page: Page, live_server: str) -> None:
    """After Match the stamp lands: red ink and the real figure when the day does not balance."""
    match(page, live_server)
    stamp = page.locator("#result-stamp")
    expect(stamp).to_have_text("Doesn't balance: £91.00")
    expect(stamp).to_have_class(re.compile("(^| )stamp-bad( |$)"))
    assert stamp.evaluate("el => getComputedStyle(el).animationName") == "stamp-in"


def test_reduced_motion_skips_the_stamp_animation(browser: Browser, live_server: str) -> None:
    context = browser.new_context(reduced_motion="reduce")
    page = context.new_page()
    match(page, live_server)
    stamp = page.locator("#result-stamp")
    expect(stamp).to_be_visible()
    assert stamp.evaluate("el => getComputedStyle(el).animationName") == "none"
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
