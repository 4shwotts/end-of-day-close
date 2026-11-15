"""The FastAPI application: wires together the API, the pages and static files.

Run locally with::

    uvicorn eod_close.web.app:app --reload
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Final

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

from eod_close import __version__
from eod_close.web import api, pages

# Scripts, styles and images may only come from this site. The game's own
# JavaScript lives in static files, so no inline script is ever needed.
_CSP: Final = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
)
# FastAPI's interactive docs load Swagger UI and ReDoc from a CDN, so they
# are the one place the policy is relaxed. The game pages never use a CDN.
_DOCS_PATHS: Final = ("/docs", "/redoc")

DESCRIPTION: Final = """
A payment reconciliation engine with a game layer.

**All companies, banks, processors, people and figures are fictional. This is a
portfolio project, not real financial software.**

Amounts are always exact decimal strings, never JSON numbers.
"""


def create_app() -> FastAPI:
    """Build the application.

    Returns:
        The configured FastAPI app.
    """
    app = FastAPI(title="End-of-Day Close", version=__version__, description=DESCRIPTION)
    app.include_router(api.router)
    app.include_router(pages.router)
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Add security headers to every response.

        The interactive API docs load their scripts from a CDN, so they are
        exempt from the strict Content Security Policy; nothing else is.
        """
        response = await call_next(request)
        if not request.url.path.startswith(_DOCS_PATHS):
            response.headers["Content-Security-Policy"] = _CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    return app


app = create_app()
