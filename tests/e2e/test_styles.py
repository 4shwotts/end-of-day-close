"""Checks that the stylesheet really reaches the browser and is applied (BUG-006)."""

import pytest
from playwright.sync_api import Page, Response

pytestmark = pytest.mark.e2e


def test_stylesheet_loads_and_is_applied(page: Page, live_server: str) -> None:
    """A 200 for the CSS is not enough: a stale or blocked file also fails this test."""
    stylesheets: list[Response] = []
    page.on(
        "response",
        lambda response: stylesheets.append(response) if ".css" in response.url else None,
    )
    page.emulate_media(color_scheme="dark")
    page.goto(live_server)
    assert [r.status for r in stylesheets] == [200]
    assert "?v=" in stylesheets[0].url

    # Rules that exist only in the current stylesheet: no bullets on the
    # navigation, the dark page colour, and the workspace's own layout.
    nav_list = page.locator(".site-header nav ul")
    assert nav_list.evaluate("el => getComputedStyle(el).listStyleType") == "none"
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(14, 20, 18)"
    assert page.locator(".status-bar").evaluate("el => getComputedStyle(el).display") == "flex"
    assert page.locator(".lists").evaluate("el => getComputedStyle(el).display") == "grid"
