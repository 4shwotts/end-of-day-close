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

from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, BrowserContext

pytestmark = pytest.mark.e2e

AXE = Path(__file__).parent / "vendor" / "axe-4.10.2.min.js"
PAGES = ["/", "/upload", "/sample/1", "/training", "/play/intern/1", "/how-it-works"]
WIDTHS = {"phone": 390, "tablet": 820, "desktop": 1440}
RULES = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"]


@pytest.fixture(params=["dark", "light"])
def scan_context(browser: Browser, request: pytest.FixtureRequest) -> Iterator[BrowserContext]:
    # Reduced motion, so the scan sees the settled page: mid-way through the
    # reveal after Reconcile, fading text would be measured at part opacity.
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
    """The workspace after Reconcile, with a row selected and a tooltip open."""
    page = scan_context.new_page()
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(live_server)
    page.get_by_role("button", name="Reconcile").click()
    page.locator("#status-matched").wait_for()
    page.locator("#row-bank-B02 [data-select]").click()
    page.get_by_role("button", name="What is unexplained?").focus()
    page.add_script_tag(path=AXE)
    found = page.evaluate(
        "async rules => (await axe.run(document, {runOnly: rules})).violations"
        ".map(v => `${v.id}: ${v.help} (${v.nodes.length}) e.g. ${v.nodes[0].target}`)",
        RULES,
    )
    page.close()
    assert found == []


def test_results_and_close_pages_have_no_axe_violations(
    scan_context: BrowserContext, live_server: str
) -> None:
    """The pages reached by posting a form: upload results and a closed training day."""
    page = scan_context.new_page()
    page.goto(f"{live_server}/play/intern/4")
    page.get_by_role("button", name="Close the day").first.click()
    page.wait_for_load_state()
    page.add_script_tag(path=AXE)
    found = page.evaluate(
        "async rules => (await axe.run(document, {runOnly: rules})).violations.map(v => v.id)",
        RULES,
    )
    page.close()
    assert found == []
