"""End-of-Day Close: a payment reconciliation engine with a game layer.

Packages and modules:
    ``eod_close.domain``: money, entries, statements, processors and reports.
    ``eod_close.generator``: seeded cases with a planted answer key.
    ``eod_close.csv_format``: the documented CSV file format.
    ``eod_close.engine``: the matching strategies and the engine that runs them.
    ``eod_close.evaluation``: accuracy scoring and the benchmark.
    ``eod_close.game``: scoring a player's attempt against the answer key.
    ``eod_close.web``: the FastAPI app, JSON API and HTMX pages.

All data it produces is fictional.
"""

__version__ = "0.1.0"
