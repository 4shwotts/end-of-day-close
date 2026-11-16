"""The FastAPI application: wires together the API, the pages and static files.

Run locally with::

    uvicorn eod_close.web.app:app --reload
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Final

from fastapi import FastAPI, Request, Response
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from eod_close import __version__
from eod_close.web import api, pages
from eod_close.web.limits import BodySizeLimit, make_limiters

logger = logging.getLogger(__name__)

# Scripts, styles and images may only come from this site. The game's own
# JavaScript lives in static files, so no inline script is ever needed.
_CSP: Final = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
)
# FastAPI's interactive docs load Swagger UI and ReDoc from a CDN, so they
# are the one place the policy is relaxed. The game pages never use a CDN.
_DOCS_PATHS: Final = ("/docs", "/redoc")
# The site uses no device features, so none are granted to it or to anything
# it might be framed with.
_PERMISSIONS_POLICY: Final = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
# Browsers only honour this over HTTPS, which the live demo always uses;
# over plain HTTP (running locally) they ignore it, so it is safe to send.
_HSTS: Final = "max-age=31536000"

# Paths whose errors are answered in JSON rather than as a page.
_JSON_PATHS: Final = ("/api", "/openapi.json")

DESCRIPTION: Final = """
A payment reconciliation engine with a game layer.

**All companies, banks, processors, people and figures are fictional. This is a
portfolio project, not real financial software.**

Amounts are always exact decimal strings, never JSON numbers.
"""


def _add_security_headers(request: Request, response: Response) -> Response:
    """Add the security headers every response carries.

    The interactive API docs load their scripts from a CDN, so they are
    exempt from the strict Content Security Policy; nothing else is.
    """
    if not request.url.path.startswith(_DOCS_PATHS):
        response.headers["Content-Security-Policy"] = _CSP
    if request.url.path.startswith("/static/"):
        # Pages link static files with a content hash (pages.static_url),
        # so a versioned address never changes meaning and may be kept
        # for good. Anything fetched without one, such as the font the
        # stylesheet loads, must be checked with the server each time,
        # or a browser can keep showing an old copy (BUG-006).
        versioned = "v" in request.query_params
        response.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable" if versioned else "no-cache"
        )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = _PERMISSIONS_POLICY
    response.headers["Strict-Transport-Security"] = _HSTS
    return response


def _wants_json(request: Request) -> bool:
    return request.url.path.startswith(_JSON_PATHS)


def _error_page(
    request: Request, status: int, message: str, headers: Mapping[str, str] | None = None
) -> Response:
    """Answer a page request that failed, in words a visitor can act on.

    An HTMX request gets a short message aimed at the page's error box
    (base.html) instead of the part of the page it meant to replace, so a
    failed click never wipes out what the visitor was looking at.
    """
    context = {"status": status, "message": message}
    if pages.is_htmx(request):
        response = pages.templates.TemplateResponse(
            request, "_error.html", context, status_code=status, headers=headers
        )
        response.headers["HX-Retarget"] = "#request-error"
        response.headers["HX-Reswap"] = "innerHTML"
        return response
    return pages.templates.TemplateResponse(
        request, "error.html", context, status_code=status, headers=headers
    )


# What a visitor is told for each kind of failure. Details of what went
# wrong stay in the server's log, never on the page.
_PAGE_MESSAGES: Final = {
    404: "There is no page at this address.",
    405: "That page cannot be used that way.",
    413: "That upload is too large. Each file may be up to 2 MB.",
    422: "Something in that request was not valid. Please go back and try again.",
}


def create_app(*, rate_limits: bool = False) -> FastAPI:
    """Build the application.

    Args:
        rate_limits: Whether to limit how often each visitor may call the
            heavy endpoints (see ``limits``). The deployed app does; the test
            suites, which make far more requests than any visitor, do not.

    Returns:
        The configured FastAPI app.
    """
    app = FastAPI(title="End-of-Day Close", version=__version__, description=DESCRIPTION)
    if rate_limits:
        app.state.limiters = make_limiters()
    app.include_router(api.router)
    app.include_router(pages.router)
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    app.add_middleware(BodySizeLimit)

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Add security headers to every response."""
        return _add_security_headers(request, await call_next(request))

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        """Errors the app raised on purpose: JSON for the API, a page for people."""
        if _wants_json(request):
            return await http_exception_handler(request, exc)
        message = _PAGE_MESSAGES.get(exc.status_code, str(exc.detail))
        return _error_page(request, exc.status_code, message, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> Response:
        """Invalid input: the API explains which field; a page just says so."""
        if _wants_json(request):
            return await request_validation_exception_handler(request, exc)
        return _error_page(request, 422, _PAGE_MESSAGES[422])

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> Response:
        """Anything unforeseen: logged in full, but no details sent back.

        A stack trace would tell a visitor nothing useful and an attacker a
        great deal. This handler runs outside the middleware above, so it
        adds the security headers itself.
        """
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        response: Response
        if _wants_json(request):
            response = JSONResponse({"detail": "Internal server error."}, status_code=500)
        else:
            response = _error_page(
                request, 500, "Something went wrong on our side. Please try again."
            )
        return _add_security_headers(request, response)

    return app


app = create_app(rate_limits=True)
