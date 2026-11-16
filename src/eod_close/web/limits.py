"""Rate limits for the endpoints that do real work on every request.

The live demo is a single small instance on a free plan, so a burst of
requests to the heavy endpoints (the race, the busy day, uploads and new
days) could slow it for everyone. Each visitor gets a sliding one-minute
window per kind of request; over the limit, the request is refused with
429 Too Many Requests and a ``Retry-After`` header.

The limits are kept in memory. That suits one instance: they reset when it
restarts, and Render's free plan runs exactly one. A service with several
instances would need a shared store instead.

Visitors are told apart by the address Render's proxy records for them,
the last entry of ``X-Forwarded-For``. The earlier entries come from the
visitor's own request and could be set to anything, so they are ignored.
Without the header (locally, or in tests) the connection's address is used.

Separately, ``BodySizeLimit`` caps the size of any request body. Starlette
reads a whole upload, writing anything over 1 MB to a temporary file,
before a route sees it, so the upload routes' own 2 MB check alone would
not stop a very large body from filling the disk.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final

from fastapi import HTTPException, Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send


@dataclass(frozen=True)
class Limit:
    """How many requests of one kind a visitor may make in a window.

    Attributes:
        requests: The most requests allowed in the window.
        seconds: The window's length.
    """

    requests: int
    seconds: float = 60.0


# Two 2 MB uploads plus room for the form's own framing. No other request
# comes close: the largest training day's answers are well under 1 MB.
MAX_BODY_BYTES: Final = 5 * 1024 * 1024

LIMITS: Final[Mapping[str, Limit]] = {
    # Up to about two seconds of CPU each at 10,000 payments a side.
    "race": Limit(6),
    # A busy day is generated and matched afresh for every view, page of
    # its lists and download, each taking tens of milliseconds.
    "busy-day": Limit(30),
    # Up to 2 MB of CSV parsed and matched per upload.
    "upload": Limit(20),
    # A new generated day: cheap, but still work, and easy to script.
    "new-day": Limit(60),
}
"""The limit for each kind of request, per visitor."""


class RateLimiter:
    """A sliding-window counter of requests, per visitor.

    Thread-safe: the server may handle requests on several threads.

    Args:
        limit: How many requests are allowed in how long.
        clock: Returns the current time in seconds; replaceable in tests.

    Complexity: O(1) amortised per request. Each visitor's timestamps are
    kept in a deque; expired ones are dropped from the front, and a visitor
    with none left is forgotten, so memory follows recent visitors only.
    """

    def __init__(self, limit: Limit, clock: Callable[[], float] = time.monotonic) -> None:
        self._limit = limit
        self._clock = clock
        self._seen: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, visitor: str) -> float | None:
        """Count a request, unless the visitor is over the limit.

        Args:
            visitor: Identifies who made the request.

        Returns:
            None if the request is allowed (and it is counted), or the
            number of seconds until the visitor may try again.
        """
        now = self._clock()
        with self._lock:
            times = self._seen.setdefault(visitor, deque())
            while times and now - times[0] >= self._limit.seconds:
                times.popleft()
            if len(times) >= self._limit.requests:
                return self._limit.seconds - (now - times[0])
            times.append(now)
            self._forget_idle(now)
            return None

    def _forget_idle(self, now: float) -> None:
        """Drop visitors whose every request has left the window.

        Only checked once there are many visitors, so a quiet instance does
        no extra work.
        """
        if len(self._seen) < 1_000:
            return
        for visitor in [
            v for v, t in self._seen.items() if not t or now - t[-1] >= self._limit.seconds
        ]:
            del self._seen[visitor]


def make_limiters(clock: Callable[[], float] = time.monotonic) -> dict[str, RateLimiter]:
    """Create one limiter for each kind of request in ``LIMITS``."""
    return {name: RateLimiter(limit, clock) for name, limit in LIMITS.items()}


def visitor_of(request: Request) -> str:
    """Identify who made a request, as described in the module docstring."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded.strip():
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def rate_limited(kind: str) -> Callable[[Request], None]:
    """A route dependency that refuses a visitor over the limit for ``kind``.

    The limiters live on ``app.state.limiters``; an app created without
    them (as the test suites do, since they make far more requests than any
    visitor) is not limited.

    Args:
        kind: A key of ``LIMITS``.

    Returns:
        The dependency.

    Raises:
        KeyError: If ``kind`` is not a known kind of request.
    """
    if kind not in LIMITS:
        raise KeyError(kind)

    def check(request: Request) -> None:
        limiters: dict[str, RateLimiter] | None = getattr(request.app.state, "limiters", None)
        if limiters is None:
            return
        wait = limiters[kind].hit(visitor_of(request))
        if wait is not None:
            seconds = max(1, math.ceil(wait))
            raise HTTPException(
                status_code=429,
                detail=f"Too many requests. Please try again in {seconds} seconds.",
                headers={"Retry-After": str(seconds)},
            )

    return check


class BodySizeLimit:
    """ASGI middleware that refuses request bodies over ``max_bytes``.

    A body that declares a larger ``Content-Length``, or that grows past the
    limit while being received, raises 413 Content Too Large the moment the
    route reads it. Raising it there, rather than answering from here, lets
    the app's own error handlers word the reply (JSON or a page).

    Args:
        app: The application to wrap.
        max_bytes: The largest body allowed.
    """

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self._app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Pass the request on, with a ``receive`` that counts the body."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length", b"")
        too_large = declared.isdigit() and int(declared) > self._max_bytes
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            if too_large:
                raise HTTPException(status_code=413, detail="The request is too large.")
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self._max_bytes:
                    raise HTTPException(status_code=413, detail="The request is too large.")
            return message

        await self._app(scope, limited_receive, send)
