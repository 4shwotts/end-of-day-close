"""Run the web app with uvicorn: ``python -m eod_close.web``.

This is the container's entry point. It listens on the port in the ``PORT``
environment variable, which Render sets for the live demo (10000 by default),
and falls back to 8080 locally and in Docker Compose.
"""

from __future__ import annotations

import os

import uvicorn

DEFAULT_PORT = 8080


def port_from_environment(environ: dict[str, str] | None = None) -> int:
    """Read the listening port from ``PORT``.

    Args:
        environ: The environment to read; defaults to ``os.environ``.

    Returns:
        The port number.

    Raises:
        ValueError: If ``PORT`` is set but is not a valid port number.
    """
    raw = (environ if environ is not None else dict(os.environ)).get("PORT", "")
    if not raw:
        return DEFAULT_PORT
    port = int(raw)
    if not 0 < port < 65_536:
        raise ValueError(f"PORT must be between 1 and 65535, not {port}")
    return port


def main() -> None:
    """Start the server on all interfaces, as a container requires."""
    # 0.0.0.0 rather than localhost: inside a container, localhost is not
    # reachable from the host or from the platform's load balancer.
    uvicorn.run(
        "eod_close.web.app:app",
        host="0.0.0.0",
        port=port_from_environment(),
        # One process: the free hosting instance has 512 MB of memory, and a
        # container platform scales by adding containers, not workers.
        workers=1,
        log_level="info",
    )


if __name__ == "__main__":
    main()
