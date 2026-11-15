# End-of-Day Close: production image.
#
# Two stages: the first builds a wheel with the full toolchain; the second
# installs only that wheel and its runtime dependencies into a slim image, so
# build tools, tests and source files never reach production.

# ---- Build stage ---------------------------------------------------------
FROM python:3.13-slim AS build

WORKDIR /src
# Copy only what the build needs; .dockerignore keeps out tests, caches and
# the local virtual environment.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip wheel --no-cache-dir --wheel-dir /wheels .

# ---- Runtime stage -------------------------------------------------------
FROM python:3.13-slim AS runtime

# No .pyc files (the filesystem is read-only at run time) and unbuffered
# output, so logs reach the platform immediately.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

# A dedicated user with no login shell and no home directory. Running as
# non-root limits the damage if the app is ever compromised; a fixed UID
# keeps file ownership predictable across builds.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

USER 10001:10001
EXPOSE 8080

# The slim image has no curl, so the check uses Python's standard library.
# It reads PORT so it still works when the host sets a different port, as
# Render does.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT', '8080')}/api/health\", timeout=4)"]

# Exec form, so the server is PID 1 and receives the platform's stop signal
# directly and can shut down cleanly.
CMD ["python", "-m", "eod_close.web"]
