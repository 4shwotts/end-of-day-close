"""Automated accessibility scan of every page with axe-core, at three widths and both themes.

axe-core is stored in ``vendor/`` beside this file (MPL-2.0), so the scan
needs no network. The pages forbid inline scripts in their Content Security
Policy, so the scan runs in a browser context that bypasses the policy. The
context also asks for reduced motion, so the page is scanned as it settles
rather than part-way through an animation.

axe finds roughly a third to a half of accessibility problems. The rest
(reading order, sensible labels, keyboard flow) is covered by the other
browser tests and by hand.
"""

import io
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, BrowserContext

from eod_close.csv_format import write_bank_csv, write_ledger_csv
from eod_close.generator import CaseGenerator, Difficulty

pytestmark = pytest.mark.e2e

AXE = Path(__file__).parent / "vendor" / "axe-4.10.2.min.js"
PAGES = [
    "/",
    "/upload",
    "/day/1",
    "/training",
    "/play/intern/1",
    "/play/intern/1/work",
    "/how-it-works",
    "/how-it-works/busy-day/48213",
]
WIDTHS = {"phone": 390, "tablet": 820, "desktop": 1440}
RULES = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"]


@pytest.fixture(params=["dark", "light"])
def scan_context(browser: Browser, request: pytest.FixtureRequest) -> Iterator[BrowserContext]:
    # Reduced motion, so the scan sees the settled page: mid-way through the
    # reveal after Match, fading text would be measured at part opacity.
    context = browser.new_context(
        bypass_csp=True, color_scheme=request.param, reduced_motion="reduce"
    )
    yield context
    context.close()


def violations(context: BrowserContext, url: str, width: int) -> list[str]:
    """Load a page, open every collapsed group so its content is scanned, and run axe."""
    page = context.new_page()
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(url)
    page.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
    page.add_script_tag(path=AXE)
    found = page.evaluate(
        "async rules => (await axe.run(document, {runOnly: rules})).violations"
        ".map(v => `${v.id}: ${v.help} (${v.nodes.length}) e.g. ${v.nodes[0].target}`)",
        RULES,
    )
    page.close()
    return list(found)


@pytest.mark.parametrize("width", WIDTHS.values(), ids=WIDTHS.keys())
@pytest.mark.parametrize("path", PAGES)
def test_page_has_no_axe_violations(
    scan_context: BrowserContext, live_server: str, path: str, width: int
) -> None:
    assert violations(scan_context, f"{live_server}{path}", width) == []


@pytest.mark.parametrize("width", WIDTHS.values(), ids=WIDTHS.keys())
def test_reconciled_home_page_has_no_axe_violations(
    scan_context: BrowserContext, live_server: str, width: int
) -> None:
    """The workspace after Match: a card selected, a popover open and a description shown."""
    page = scan_context.new_page()
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(live_server)
    page.get_by_role("button", name="Match", exact=True).click()
    page.locator(".result-headline").wait_for()
    page.locator(".problem", has_text="Typo").click()
    page.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
    page.locator("#row-bank-B09 .why-button").click()
    page.locator("#why-bank-B09").wait_for()
    page.locator("#row-bank-B01 .ref-button").focus()
    page.add_script_tag(path=AXE)
    found = page.evaluate(
        "async rules => (await axe.run(document, {runOnly: rules})).violations"
        ".map(v => `${v.id}: ${v.help} (${v.nodes.length}) e.g. ${v.nodes[0].target}`)",
        RULES,
    )
    page.close()
    assert found == []


def test_busy_day_result_has_no_axe_violations(
    scan_context: BrowserContext, live_server: str
) -> None:
    """A day run with the button, then its lists paged and filtered through HTMX."""
    page = scan_context.new_page()
    page.goto(f"{live_server}/how-it-works")
    page.get_by_role("button", name="Run a busy day").click()
    page.locator("#busy-headline").wait_for()
    page.get_by_text("View the two lists").click()
    page.locator("#busy-lists").get_by_role("link", name="Next").click()
    page.locator("#busy-page", has_text="Page 2").wait_for()
    page.locator("#busy-lists").get_by_role("link", name="Problems").click()
    page.locator("#busy-page", has_text="Page 1").wait_for()
    page.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
    page.add_script_tag(path=AXE)
    found = page.evaluate(
        "async rules => (await axe.run(document, {runOnly: rules})).violations.map(v => v.id)",
        RULES,
    )
    page.close()
    assert found == []


def test_results_and_close_pages_have_no_axe_violations(
    scan_context: BrowserContext, live_server: str
) -> None:
    """The page reached by closing a training day, with the engine's full answer."""
    page = scan_context.new_page()
    page.goto(f"{live_server}/play/intern/4/work")
    page.get_by_role("button", name="Close the day").first.click()
    page.wait_for_load_state()
    page.add_script_tag(path=AXE)
    found = page.evaluate(
        "async rules => (await axe.run(document, {runOnly: rules})).violations.map(v => v.id)",
        RULES,
    )
    page.close()
    assert found == []


def test_long_upload_results_have_no_axe_violations(
    scan_context: BrowserContext, live_server: str, tmp_path: Path
) -> None:
    """A large day's results with every group open, so each scrolling box is scanned."""
    write_large_day(tmp_path)
    page = scan_context.new_page()
    page.goto(f"{live_server}/upload")
    page.get_by_label("Bank statement (CSV)").set_input_files(tmp_path / "bank.csv")
    page.get_by_label("Your records (CSV)").set_input_files(tmp_path / "ledger.csv")
    page.get_by_role("button", name="Match them").click()
    page.locator("#results #summary").wait_for()
    page.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
    page.add_script_tag(path=AXE)
    found = page.evaluate(
        "async rules => (await axe.run(document, {runOnly: rules})).violations.map(v => v.id)",
        RULES,
    )
    page.close()
    assert found == []


def write_large_day(folder: Path) -> None:
    """Write a Head of Ops day's two files, the size of a busy day."""
    case = CaseGenerator().generate(1, Difficulty.HEAD_OF_OPS)
    bank, ledger = io.StringIO(), io.StringIO()
    write_bank_csv(case.bank_statement, bank)
    write_ledger_csv(case.ledger, ledger)
    (folder / "bank.csv").write_text(bank.getvalue(), encoding="utf-8")
    (folder / "ledger.csv").write_text(ledger.getvalue(), encoding="utf-8")
