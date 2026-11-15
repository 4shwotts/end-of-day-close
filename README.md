# End-of-Day Close

A payment reconciliation engine with a game layer. You play an operations analyst at a
fictional payments company. Each round is one business day in which the bank settlement
file and the internal ledger do not agree, and your job is to close the day: link what
belongs together, flag what does not, and get every entry accounted for. The engine
solves the same day, so you can see how your answers compare with its own.

> All companies, banks, processors, people and figures in this project are fictional.
> This is a portfolio project, not real financial software.

Built with Python 3.13, FastAPI, Jinja2 and HTMX; tested with pytest, Hypothesis and
Playwright; checked by GitHub Actions; packaged with Docker and hosted on Render.

## Contents

- [What it does](#what-it-does)
- [How the engine works](#how-the-engine-works)
- [Accuracy and performance](#accuracy-and-performance)
- [Known limitations](#known-limitations)
- [The game](#the-game)
- [API](#api)
- [Running it locally](#running-it-locally)
- [Deployment](#deployment)
- [Project layout](#project-layout)

## What it does

A **reconciliation** checks that two records of the same money agree. Here they are the
bank statement (what actually arrived in or left the account) and the company's ledger
(what the company booked). On a real day they never agree exactly, for ordinary reasons:

| Problem | What happens |
|---|---|
| Settlement delay | The bank receives the money one to three business days after the sale. |
| Processor fee | The card processor deducts its fee, so the bank receives less than the sale. |
| FX rounding | A euro sale converts to sterling a few pence differently on each side. |
| Batch payout | One bank credit covers many sales from the same processor. |
| Refund or chargeback | Money goes back out, booked under a different reference on each side. |
| Duplicate | The same entry appears twice in one file. |
| Missing from bank or ledger | One side has an entry the other side does not. |
| Transposition | Two digits were swapped when the amount was keyed, such as £45.10 for £54.10. |

The **generator** builds a day like this from a seed and a difficulty level. It records
an answer key of everything it planted, which is used to score the engine and the
player. The **engine** never sees the answer key; it works from the two files alone.

## How the engine works

```
 bank CSV ─┐                         ┌─> matches     (each with its rule and confidence)
           ├─> 8 strategies, in order ┤
 ledger ───┘   ranked by confidence   └─> exceptions  (each with its reason)
```

The engine runs eight strategies, strongest evidence first. Each strategy proposes
candidates. A priority queue ranks them by confidence and the engine accepts them best
first, skipping any candidate that reuses an entry already claimed. Whatever is left at
the end becomes an exception. The report then checks the central invariant: every entry
appears exactly once, in one match or one exception.

| # | Strategy | Finds | Approach | Time |
|---|---|---|---|---|
| 1 | Exact | Same reference, amount and date | Hash map on (reference, amount, date) | O(n + m) |
| 2 | Fee-adjusted | Net receipts for gross sales | Applies each processor's published fee rule, then a hash lookup | O(n + m) |
| 3 | Date window | Same amount, up to 3 business days late | Groups by amount, binary search on date | O((n + m) log m) |
| 4 | Currency tolerance | Euro sales within 5p after conversion | Sorted amounts, binary search for the ±5p range | O((n + m) log m) |
| 5 | Batch | One payout covering several sales | Bounded subset-sum (dynamic programming), one booking day at a time | O(k·S), capped |
| 6 | Reversal | Refunds and chargebacks | Indexes by original reference, tries each suffix of the bank reference | O(n·p + m) |
| 7 | Duplicates | Identical entries on one side | Groups by (reference, amount, date) | O(n + m) |
| 8 | Transposition | Amounts off by swapped digits | Same reference and a difference that is a multiple of 9 | O(n + m) |

n and m are the bank and ledger entry counts. Each strategy's docstring explains its
algorithm, its complexity and why it was chosen over the obvious alternative.

Design decisions:

- **Order beats confidence across strategies.** An exact match is never displaced by a
  fuzzy one, however confident, so a fuzzy rule only sees what no stricter rule could
  explain.
- **Deterministic.** Ties are broken on entry IDs, never on file order, so the same two
  files always reconcile the same way. Two of the bugs the tests found were about this
  ([BUGS.md](BUGS.md)).
- **Tolerances are narrow and explicit.** The 5p tolerance only applies to
  foreign-currency sales. Applying it to sterling would hide a mis-keyed penny.
  Every threshold lives in one place,
  [engine/config.py](src/eod_close/engine/config.py), with the reason for its value.
- **Money is never a float.** `Money` wraps `Decimal`, refuses floats and excess
  precision, and rounds only where a processor's rule says to. The API sends amounts as
  decimal strings, never JSON numbers.
- **Bounded search.** The batch strategy caps its pool at 30 sales and its search at
  250,000 partial sums. Anything larger goes to the exceptions queue for a person
  rather than stalling the run.

## Accuracy and performance

Accuracy is measured against the answer key over 20 seeds at each level. A decision is
correct only if it groups exactly the right entries with the right outcome. The
regression test in [tests/accuracy/](tests/accuracy/test_accuracy.py) uses a held-out set
of seeds (1000 to 1019), separate from the seeds used while developing the engine, and
fails if any level drops below its threshold.

| Level | Decisions | Precision | Recall | Threshold |
|---|---:|---:|---:|---:|
| Intern | 300 | 100.0% | 100.0% | 100% |
| Analyst | 800 | 100.0% | 100.0% | 100% |
| Senior Analyst | 2,105 | 100.0% | 100.0% | 99.5% |
| Head of Ops | 6,347 | 99.4% | 99.4% | 98.5% |

The misses are all at Head of Ops, which deliberately adds ambiguity: blank bank
references, unrelated sales at the same common price, and batch payouts that hold back
a sale. Batch payouts (97.0% recall) and missing-from-bank entries (96.5%) are the
weakest types; all the others are at 98.7% or above.

The benchmark compares the engine with a naive pairwise reconciler that applies the
same rules but scans the whole ledger for each bank entry:

| Entries per side | Engine | Naive pairwise | Speed-up |
|---:|---:|---:|---:|
| 1,000 | 0.015 s | 0.03 s | 2× |
| 10,000 | 0.21 s | 5.1 s (estimated) | 25× |
| 100,000 | 4.3 s | 1,481 s (estimated) | 346× |

Naive times above 1,000 entries are extrapolated from a measured sample, since the full
run would take too long. Times are from one run on a development laptop and will vary;
the shape matters more than the numbers. The **How it works** page runs the same race
live and charts it.

The home page quotes these figures from a saved copy of this run,
`src/eod_close/evaluation/benchmark_snapshot.json`, refreshed with
`python -m eod_close.evaluation benchmark --save`. Its accuracy figures are not saved:
they are measured from the answer keys when the app first needs them.

## Known limitations

- **Ambiguous batches.** When two different sets of sales both add up to a payout, the
  engine picks one deterministically, which may not be the true one. Only the
  processor's payout report could settle it.
- **Ambiguous blank references.** A late bank credit with no reference, at a common
  price, can belong to more than one sale from the same processor. The engine takes
  the closest date, and the other sale becomes an exception.
- **Fixed processor rules.** Fee rules and settlement delays are defined in code for the
  five fictional processors. A real system would load them from configuration per
  merchant agreement.
- **One currency per account.** Statements and ledgers are in sterling; only the
  original amount of a foreign sale is in another currency.
- **No persistence.** Nothing is stored on the server. A day is regenerated from its
  level and seed, and the streak lives in the browser's local storage.
- **Slow first visit.** The live demo's free host sleeps when idle, so the first visit
  after a quiet spell can take up to a minute.

## The game

The start page offers four levels, each adding problem types in roughly the order a new
analyst would learn them:

| Level | Items | Adds |
|---|---:|---|
| Intern | 15 | Settlement delays and processor fees |
| Analyst | 40 | FX rounding, refunds, missing entries |
| Senior Analyst | 100 | Batch payouts, chargebacks, duplicates, blank references, shared prices |
| Head of Ops | 300 | Transpositions, held-back batch sales, more noise |

On the work page you give bank and ledger entries that belong together the same group
label (or tick them and choose **Link selected**), and mark the rest as exceptions with
a reason. **Check progress** tells you whether every entry is accounted for; **Close
the day** scores you and explains every planted discrepancy, item by item, alongside
what the engine decided.

Accuracy is correct decisions divided by the larger of the planted items and the
decisions you made, so splitting entries into lots of small guesses cannot raise your
score. Balanced days in a row build a streak. Every day has a number (its seed), so you
can replay it or send it to someone else.

The site also has:

- **Reconcile (the home page):** upload a bank and a ledger CSV in the documented format
  (sample files are provided), or open a generated sample day, and get the engine's
  report with every match explained and a next step for each problem.
- **How it works:** runs the engine and the naive baseline on synthetic days of the
  sizes you choose and charts the times.

The pages work without JavaScript; the scripts only add row linking, the timer, the
streak and the upload drop zones' highlight and file names. They are keyboard accessible,
meet WCAG AA contrast in light and dark mode (checked with axe-core on every page at
phone, tablet and desktop widths), and load nothing from another site under a strict
Content Security Policy. The font (Inter) and the icons are served from the app itself.

## API

The interactive documentation is at `/docs` when the app is running.

| Method | Path | Does |
|---|---|---|
| GET | `/api/health` | Health check for Docker and Render |
| POST | `/api/cases` | Generate a case; returns its seed, without the answer key |
| GET | `/api/cases/{difficulty}/{seed}` | Fetch a case again by level and seed |
| POST | `/api/cases/{difficulty}/{seed}/score` | Score a set of answers against the answer key and the engine |
| POST | `/api/reconcile` | Reconcile an uploaded bank CSV and ledger CSV |

## Running it locally

### With Python

Needs Python 3.13. From the project folder:

```
python -m venv .venv
.venv\Scripts\activate          # Windows; on macOS or Linux: source .venv/bin/activate
pip install -e ".[dev]"
uvicorn eod_close.web.app:app --reload
```

Open http://127.0.0.1:8000. [TESTING.md](TESTING.md) lists the checks and the
command-line tools (generator, engine, evaluator and benchmark).

### With Docker

Needs Docker Desktop.

```
docker compose up --build
```

Open http://localhost:8080. The image is built in two stages, runs as a non-root user,
has a health check, and Compose runs it with a read-only filesystem and no extra Linux
capabilities.

## Deployment

```
                  push / pull request
 your machine ───────────────────────────> GitHub Actions
 (Python or                                 ruff · mypy · tests · accuracy
  docker compose)                           browser tests · docker build + smoke run
                                                  │
                                                  │ all checks pass on main
                                                  ▼
                                            Render (free tier)
                                            builds the same Dockerfile and
                                            serves the live demo
```

The same `Dockerfile` runs everywhere: in Docker Compose on your machine, in CI, and on
Render. Nothing about the app changes between them.

**CI.** Every push and pull request runs the GitHub Actions pipeline in
[.github/workflows/ci.yml](.github/workflows/ci.yml): ruff, mypy, the tests with
coverage, the accuracy regression test, the Playwright browser tests, and a container
build that is started with a read-only filesystem, health-checked and checked not to run
as root. The workflow can only read the repository, and it holds no secrets.

**Live demo.** Render watches the `main` branch. Once CI passes on a new commit, Render
builds the `Dockerfile` and replaces the running service. Render checks
`/api/health` before sending visitors to a new version, so a build that does not start
never goes live. The app reads its port from Render's `PORT` variable.

Why Render: its free tier runs a Docker image directly from the repository, with HTTPS
and no cloud account, billing setup or deploy credentials to manage. The trade-off is
that a free service sleeps after 15 minutes without visitors, so the next visit waits
while it wakes.

## Project layout

```
src/eod_close/
  domain/       Money, entries, statements, processors, matches and the report
  generator/    Seeded cases, difficulty profiles and the answer key
  engine/       The eight strategies, the candidate queue and the engine
  evaluation/   Accuracy scoring, the naive baseline and the benchmark
  game.py       Scoring a player's answers
  csv_format.py The documented CSV format
  web/          FastAPI app, JSON API, HTMX pages, templates and static files
tests/          unit, property, accuracy, api and e2e (see TESTING.md)
Dockerfile      Multi-stage, non-root image with a health check
compose.yaml    Run the image locally with one command
```

[TESTING.md](TESTING.md) describes the test suite. [BUGS.md](BUGS.md) records the real
defects the tests found, with the cause, the fix and the test that now guards each one.
