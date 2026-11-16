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

Real defects the tests found are recorded in [BUGS.md](BUGS.md), each with the test that
now guards it.

## Test suite layout

| Folder | What it checks |
|---|---|
| `tests/unit/` | Money, the domain model, the generator, CSV, each matching strategy, edge cases, game scoring, the evaluator, the CLIs, the Dockerfile, Compose and the CI workflow |
| `tests/property/` | Hypothesis properties: invariants that must hold for any input |
| `tests/accuracy/` | Precision and recall on held-out seeds; fails below the thresholds (marker `accuracy`) |
| `tests/api/` | The JSON API and the HTML pages, through FastAPI's test client |
| `tests/e2e/` | The Reconcile page's flow (instruction, Match, problem cards, highlighted rows, a total that adds up, the filter), upload, the busy day (marks, reruns, downloads, paged lists), the row popovers, and layout checks in Chromium and WebKit (no box wider than its card, no reference or amount wrapping, a popover never resizing its table), training mode and How it works in a real Chromium browser with Playwright, including keyboard use, no JavaScript and reduced motion; a check that the stylesheet is applied; and the axe-core accessibility scan (marker `e2e`) |

Branch coverage is about 96%, and the run fails below 90%.

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

## Running the checks

Install the project with its development tools into a virtual environment (see the
[README](README.md#running-it-locally)) and activate it. Then, from the project folder:

```
ruff check .
ruff format --check .
mypy
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

GitHub Actions runs the same checks on Linux on every push and pull request, split into
jobs: lint and type check, tests with coverage (Hypothesis `ci` profile), the accuracy
regression, the browser tests, and a container build with a health-checked smoke run.
CI does not deploy anything. Render deploys the live demo from `main` once all of these
pass.
