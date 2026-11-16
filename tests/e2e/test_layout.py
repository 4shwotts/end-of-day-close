"""Layout checks in Chromium and WebKit: nothing crosses a border, nothing wraps that shouldn't.

These guard the faults that make a page look unfinished: a table wider than
its card, a reference or amount broken over two lines, and a popover that
pushes the table about when it opens. Each runs at phone, tablet and
desktop widths on the Reconcile results and the busy day.
"""

from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, Page, Playwright, expect

pytestmark = pytest.mark.e2e

WIDTHS = {"phone": 390, "tablet": 820, "desktop": 1440}

# Every box on the page, every table cell, and every reference and amount.
# A box may not be wider inside than out; the tables' own scroll box is the
# one place horizontal scrolling is allowed, so it is not checked as a box.
# A line of text that wraps has more than one distinct line top.
LAYOUT_FAULTS = """() => {
  const faults = [];
  const shown = el => el.getClientRects().length > 0;
  const name = el => el.id || (el.closest('tr') && el.closest('tr').id) || el.className;
  for (const box of document.querySelectorAll(
      '.card, .list, .busy-section, .problem, .problem-line, .lists-toolbar')) {
    if (shown(box) && box.scrollWidth > box.clientWidth + 1) {
      faults.push(`box overflows: ${name(box)} (${box.scrollWidth} > ${box.clientWidth})`);
    }
  }
  for (const cell of document.querySelectorAll('.list-table td, .list-table th')) {
    if (shown(cell) && cell.scrollWidth > cell.clientWidth + 1) {
      faults.push(`cell overflows: ${name(cell)} ${cell.className}`);
    }
  }
  for (const text of document.querySelectorAll('.list-table td.amount, .list-table .ref-button')) {
    if (!shown(text)) continue;
    const range = document.createRange();
    range.selectNodeContents(text);
    const lines = new Set([...range.getClientRects()].map(r => Math.round(r.top)));
    if (lines.size > 1) faults.push(`wraps: ${text.textContent.trim()}`);
  }
  return faults;
}"""


@pytest.fixture(scope="module", params=["chromium", "webkit"])
def any_browser(playwright: Playwright, request: pytest.FixtureRequest) -> Iterator[Browser]:
    browser = playwright[request.param].launch()
    yield browser
    browser.close()


def open_page(browser: Browser, width: int) -> Page:
    context = browser.new_context(viewport={"width": width, "height": 900}, reduced_motion="reduce")
    return context.new_page()


def matched_reconcile_page(page: Page, live_server: str) -> None:
    page.goto(live_server)
    page.get_by_role("button", name="Match", exact=True).click()
    page.locator(".result-headline").wait_for()
    # Match is finished only once app.js has moved focus to the headline,
    # which it does after HTMX's settle step. Acting sooner can race it: on
    # a slow machine a test's own focus would be taken away a moment later.
    expect(page.locator(".result-headline")).to_be_focused()


def busy_day_with_lists(page: Page, live_server: str) -> None:
    page.goto(f"{live_server}/how-it-works/busy-day/48213")
    page.get_by_text("View the two lists").click()
    page.locator("#busy-lists .list-bank tbody tr").first.wait_for()


@pytest.mark.parametrize("width", WIDTHS.values(), ids=WIDTHS.keys())
def test_reconcile_results_fit_their_cards(
    any_browser: Browser, live_server: str, width: int
) -> None:
    page = open_page(any_browser, width)
    matched_reconcile_page(page, live_server)
    assert page.evaluate(LAYOUT_FAULTS) == []
    if width < 1000:
        page.locator(".side-switch").get_by_text("Your records").click()
        assert page.evaluate(LAYOUT_FAULTS) == []
    page.context.close()


@pytest.mark.parametrize("width", WIDTHS.values(), ids=WIDTHS.keys())
def test_busy_day_results_fit_their_cards(
    any_browser: Browser, live_server: str, width: int
) -> None:
    page = open_page(any_browser, width)
    busy_day_with_lists(page, live_server)
    assert page.evaluate(LAYOUT_FAULTS) == []
    line = page.locator("#busy-result .problem-line").first
    line.locator("summary").click()
    line.get_by_role("link", name="Show its rows in the lists").click()
    # Focus moves to the first row only once HTMX has settled the swap, so the
    # check below never measures the lists half-way through it.
    expect(page.locator("#busy-lists .row.is-flash").first).to_be_focused()
    # A tooltip shown under a resting pointer floats over the layout without
    # moving it, but would still count towards its cell's width.
    page.mouse.move(0, 0)
    assert page.evaluate(LAYOUT_FAULTS) == []
    page.context.close()


def test_both_lists_use_the_same_column_widths(any_browser: Browser, live_server: str) -> None:
    page = open_page(any_browser, 1440)
    matched_reconcile_page(page, live_server)
    widths = page.evaluate(
        "() => [...document.querySelectorAll('.list-table thead tr')].map(row =>"
        " [...row.cells].map(cell => Math.round(cell.getBoundingClientRect().width)))"
    )
    assert len(widths) == 2
    assert widths[0] == widths[1]
    page.context.close()


@pytest.mark.parametrize("width", WIDTHS.values(), ids=WIDTHS.keys())
def test_opening_a_popover_never_changes_the_table(
    any_browser: Browser, live_server: str, width: int
) -> None:
    page = open_page(any_browser, width)
    matched_reconcile_page(page, live_server)
    table = page.locator(".list-bank table")
    before = table.bounding_box()
    for row in ("#row-bank-B02", "#row-bank-B09"):
        page.locator(f"{row} .why-button").click()
        popover = page.locator(f"#why-{row.removeprefix('#row-')}")
        expect(popover).to_be_visible()
        after = table.bounding_box()
        assert before is not None
        assert after is not None
        # Within a pixel: WebKit can round a row's height differently by a
        # fraction once a popover is in the top layer, which no one can see.
        assert abs(after["width"] - before["width"]) < 1
        assert abs(after["height"] - before["height"]) < 1
        box = popover.bounding_box()
        assert box is not None
        assert box["x"] >= 0
        assert box["x"] + box["width"] <= width, "the popover stays inside the page"
    page.context.close()


@pytest.mark.parametrize("width", WIDTHS.values(), ids=WIDTHS.keys())
def test_info_icons_line_up_down_each_list(
    any_browser: Browser, live_server: str, width: int
) -> None:
    """Every row's info button sits at the same x, whatever the type beside it."""
    page = open_page(any_browser, width)
    matched_reconcile_page(page, live_server)
    lefts = page.evaluate(
        "() => [...document.querySelectorAll('.list-bank .why-button')]"
        ".map(b => Math.round(b.getBoundingClientRect().left))"
    )
    assert len(lefts) == 12
    assert len(set(lefts)) == 1, lefts
    page.context.close()


@pytest.mark.parametrize("width", [1600, 1920])
def test_training_tables_fit_side_by_side_with_no_scrollbars(
    any_browser: Browser, live_server: str, width: int
) -> None:
    page = open_page(any_browser, width)
    page.goto(f"{live_server}/play/head-of-ops/3/work")
    boxes = page.evaluate(
        "() => [...document.querySelectorAll('.panel-scroll')].map(b => ["
        "b.scrollWidth - b.clientWidth, b.scrollHeight - b.clientHeight,"
        " Math.round(b.getBoundingClientRect().top)])"
    )
    assert len(boxes) == 2
    for wider, taller, _ in boxes:
        assert wider <= 0, "no sideways scrolling inside a table"
        assert taller <= 0, "no scrolling up and down inside a table"
    assert boxes[0][2] == boxes[1][2], "the two tables sit side by side"
    page.context.close()


@pytest.mark.parametrize("path", ["/", "/training", "/play/analyst/1/work", "/how-it-works"])
@pytest.mark.parametrize("width", WIDTHS.values(), ids=WIDTHS.keys())
def test_the_fictional_data_notice_is_never_covered(
    any_browser: Browser, live_server: str, path: str, width: int
) -> None:
    page = open_page(any_browser, width)
    page.goto(f"{live_server}{path}")
    notice = page.locator(".notice")
    box = notice.bounding_box()
    assert box is not None
    assert box["height"] >= 14
    # Whatever is drawn at the notice's centre must be the notice itself.
    on_top = page.evaluate(
        "([x, y]) => document.elementFromPoint(x, y).closest('.notice') !== null",
        [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2],
    )
    assert on_top
    assert notice.evaluate("el => el.scrollHeight <= el.clientHeight + 1")
    page.context.close()


@pytest.mark.parametrize("width", [1280, 1440])
def test_training_tables_stack_with_no_scrollbars_below_1560(
    any_browser: Browser, live_server: str, width: int
) -> None:
    """Too narrow for both at a readable size, so they stack, each at full width."""
    page = open_page(any_browser, width)
    page.goto(f"{live_server}/play/head-of-ops/3/work")
    boxes = page.evaluate(
        "() => [...document.querySelectorAll('.panel-scroll')].map(b => ["
        "b.scrollWidth - b.clientWidth, b.scrollHeight - b.clientHeight,"
        " Math.round(b.getBoundingClientRect().top)])"
    )
    for wider, taller, _ in boxes:
        assert wider <= 0
        assert taller <= 0
    assert boxes[1][2] > boxes[0][2], "the records table sits below the bank's"
    page.context.close()


@pytest.mark.parametrize("width", WIDTHS.values(), ids=WIDTHS.keys())
def test_changing_the_filter_never_moves_the_page(
    any_browser: Browser, live_server: str, width: int
) -> None:
    """The lists keep their height, so the links below them never jump up."""
    page = open_page(any_browser, width)
    matched_reconcile_page(page, live_server)
    page.evaluate("document.fonts.ready")

    def positions() -> tuple[float, float]:
        # Measured from the top of the page, not the window: clicking scrolls
        # the filter into view, which moves everything relative to the window.
        found: list[float] = page.evaluate(
            "() => [document.querySelector('.lists').getBoundingClientRect().height,"
            " document.querySelector('.quick-links').getBoundingClientRect().top + scrollY]"
        )
        return round(found[0]), round(found[1])

    before = positions()
    for choice in ("Problems", "Matched", "All"):
        page.locator(".filter").get_by_text(choice, exact=True).click()
        assert positions() == before, choice
    page.context.close()
