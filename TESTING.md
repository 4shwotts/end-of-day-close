# Testing

## Approach

The engine makes financial decisions, so the tests check behaviour from several angles:

- **Examples** pin down each rule at its exact boundary: a payment three business days
  late matches, four days late does not; a 5p FX difference matches, 6p does not.
- **Properties** (Hypothesis) check invariants for any input, not just chosen examples:
  every entry ends up in exactly one place, reordering the rows never changes the
  result, and money arithmetic stays exact.
- **Accuracy** is measured against the generator's answer key on a held-out set of seeds,
  with fixed thresholds, so a change that makes the engine worse fails the build.
- **The real interface** is driven in Chromium with Playwright, including keyboard use
  and file drag and drop, and every page is scanned with axe-core for accessibility
  problems at phone, tablet and desktop widths in both colour themes.
- **The deployment files** are parsed and checked, so dropping the non-root user, the
  health check or the Compose hardening fails in CI rather than on the live host.
- **Misuse** is tested as well as use: a crafted upload cannot plant a spreadsheet
  formula in a downloaded report, an over-large or non-CSV upload is refused, a visitor
  who calls a heavy page too often is slowed down, and an unexpected error shows a plain
  message with no internal details.

Real defects the tests found are recorded in [BUGS.md](BUGS.md), each with the test that
now guards it.

## Test suite layout

| Folder | What it checks |
|---|---|
| `tests/unit/` | Money, the domain model, the generator, CSV (including formula-injection protection), each matching strategy, edge cases, game scoring, the evaluator, the rate limiter, the CLIs, the Dockerfile, Compose and the CI workflow |
| `tests/property/` | Hypothesis properties: invariants that must hold for any input |
| `tests/accuracy/` | Precision and recall on held-out seeds; fails below the thresholds (marker `accuracy`) |
| `tests/api/` | The JSON API and the HTML pages, through FastAPI's test client, plus error handling, rate limits, request size limits and security headers |
| `tests/e2e/` | The Reconcile page's flow (instruction, Match, problem cards, highlighted rows, a total that adds up, the filter), upload, the busy day (marks, reruns, downloads, paged lists), the row popovers, and layout checks in Chromium and WebKit (no box wider than its card, no reference or amount wrapping, a popover never resizing its table), training mode and How it works in a real Chromium browser with Playwright, including keyboard use, no JavaScript and reduced motion; a check that the stylesheet is applied; and the axe-core accessibility scan (marker `e2e`) |

### Totals

| Level | Tests |
|---|---:|
| Unit | 279 |
| Property | 11 |
| Accuracy | 15 |
| API and pages | 101 |
| Browser (e2e) | 195 |
| **Total** | **601** |

Counted with `pytest --collect-only`; a test run once per browser, width or colour theme
counts once for each. Branch coverage is about 96%, and the run fails below 90%.

### Accuracy thresholds

Measured over seeds 1000 to 1019, which were not used while developing the engine:

| Level | Precision and recall must be at least |
|---|---:|
| Intern | 100% |
| Analyst | 100% |
| Senior Analyst | 99.5% |
| Head of Ops | 98.5% |

Each problem type, across all levels, must reach 95%, except batch payouts (90%: there
are only about 100 in the sample, so one miss costs a whole percentage point) and
missing-from-bank (93%: it absorbs the other half of every ambiguous blank-reference
settlement). The current results are in the [README](README.md#accuracy-and-performance).

## Deliberately not tested

Some things are left out on purpose, because a test would cost more than it would catch,
or would not be reliable:

- **Speed in the test suite.** Timings depend on the machine, so no test fails on a
  slow run. The benchmark is run by hand and its results saved (see below).
- **Firefox.** The browser tests use Chromium for everything and WebKit (Safari's engine)
  for layout. Firefox is left out to keep CI fast; the site uses no Firefox-specific
  features.
- **Screen readers themselves.** axe-core checks the markup that screen readers rely on,
  and the tests drive the pages by keyboard, but nobody has tested them with NVDA or
  VoiceOver.
- **Pixel-perfect appearance.** There are no screenshot comparisons, which break on every
  font or colour change. The layout tests check geometry instead: nothing overflows its
  card, and nothing moves when a filter changes.
- **Load.** The rate limits are tested, but the site has not been load-tested. It runs as
  a single small instance and keeps no state between requests.
- **Rate limits across several servers.** The limits are kept in memory, which suits one
  instance (Render's free plan runs exactly one). Several instances would need a shared
  store, which this project does not have.
- **The hosting.** Render's proxy, HTTPS and free-tier sleep are not reproduced locally.
  CI builds the Docker image and checks it starts, runs as a non-root user and answers
  its health check; the live site is checked by hand after each deploy.
- **Other people's code.** FastAPI, Starlette, HTMX and Jinja are trusted to work as
  documented. Their known vulnerabilities are checked by `pip-audit` in CI.

## Running the checks

Install the project with its development tools into a virtual environment (see the
[README](README.md#running-it-locally)) and activate it. Then, from the project folder:

```
ruff check .
ruff format --check .
mypy
pip-audit --skip-editable
pytest --cov
```

`mypy` runs in strict mode. The browser tests need Chromium and WebKit, downloaded once
with `playwright install chromium webkit`; the layout checks in `tests/e2e/test_layout.py`
run in both, so a table that overflows its card in Safari is caught too. To run everything
else without the browsers, use `pytest -m "not e2e"`.

Other useful commands:

```
pytest -m accuracy                      # the accuracy regression only
pytest -m e2e                           # the browser tests only
python -m eod_close.generator --seed 42 --difficulty head-of-ops --out cases/demo
python -m eod_close.engine --seed 42 --difficulty head-of-ops
python -m eod_close.evaluation accuracy --seeds 20
python -m eod_close.evaluation benchmark --sizes 1000 10000 100000
python -m eod_close.evaluation benchmark --save   # refresh the home page's speed figures
```

The generator writes a day as CSV files with its answer key. The engine command
reconciles the same day and summarises what was planted against what it found. The
evaluation commands print the accuracy and benchmark tables shown in the README.

The property tests run 100 examples each by default. Setting `HYPOTHESIS_PROFILE=ci`
runs 500, as CI does.

## In CI

GitHub Actions runs the same checks on Linux on every push to any branch and on every
pull request, split into jobs: lint, type check and dependency audit; tests with coverage
(Hypothesis `ci` profile); the accuracy regression; the browser tests; and a container
build with a health-checked smoke run, which also writes the image's size to the run's
summary page.

Each run keeps its reports for 14 days, under **Artifacts** on the run's page: the
coverage report, JUnit test results for each job, and, when a browser test fails, a
Playwright trace that replays the failure step by step at
[trace.playwright.dev](https://trace.playwright.dev).

CI does not deploy anything. Render deploys the live demo from `main` once all of these
pass.
