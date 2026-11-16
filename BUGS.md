# Bugs found

Real defects found by the test suite, with the test that now guards each one.

## BUG-001: a reference-less settlement could claim another processor's sale

- **Found by:** the accuracy evaluator. Head of Ops recall for batch payouts was 88.9% and for duplicates 92.9%.
- **Steps to reproduce:** reconcile a bank credit of £24.99 with a blank reference from `OTTERLINE`, dated Tuesday, against a ledger with one `QUILLFEATHER` sale of £24.99 booked on Monday.
- **Expected result:** no match. The money came from a different processor, so both entries become exceptions.
- **Actual result:** `DateWindowMatch` paired them. Downstream, the Quillfeather batch payout was missing a sale and could no longer be matched.
- **Root cause:** with a blank bank reference, the date-window strategy relied on amount and date alone. Common retail prices (£24.99 and so on) make shared amounts frequent, and the strategy ignored the bank's remitter.
- **Fix:** when the bank reference is blank, the bank's `counterparty` must equal the ledger entry's `processor_code`.
- **Guarding test:** `tests/unit/test_edge_cases.py::test_blank_reference_settlement_only_matches_the_same_processor`.

## BUG-002: which duplicate copy was flagged depended on row order

- **Found by:** the Hypothesis property `test_result_does_not_depend_on_row_order`.
- **Steps to reproduce:** reconcile two identical bank debits (`B000`, `B001`: blank reference, -£15.00, same date) against an empty ledger, then repeat with the two rows swapped.
- **Expected result:** the same decisions both times.
- **Actual result:** the first run flagged `B001` as a duplicate of `B000`; the swapped run flagged `B000` as a duplicate of `B001`.
- **Root cause:** when no copy had been matched, `DuplicateDetection` treated the first copy in file order as genuine.
- **Fix:** the genuine copy is the claimed copy, or failing that the copy, with the lowest entry ID.
- **Guarding test:** `tests/property/test_properties.py::test_result_does_not_depend_on_row_order`.

## BUG-003: which sales a batch payout covered depended on row order

- **Found by:** checking the batch strategy for the same weakness as BUG-002.
- **Steps to reproduce:** reconcile a £29.43 `QUILLFEATHER` payout against three Quillfeather sales booked on the previous day: `L1` £10.00, `L2` £20.00, `L3` £30.00. At the 1.9% fee, both {L1, L2} and {L3} come to £29.43 net. Repeat with the ledger reversed.
- **Expected result:** the same subset both times.
- **Actual result:** {L1, L2} in file order; {L3} when reversed.
- **Root cause:** `bounded_subset_sum` returns the first subset it reaches, and the candidate pool followed file order.
- **Fix:** the pool is sorted by entry ID before the search. When two subsets truly fit, the choice is now deterministic, but it may still not be the true one. This is listed as a known limitation.
- **Guarding test:** `tests/unit/test_edge_cases.py::test_batch_choice_does_not_depend_on_ledger_order`.

## BUG-004: starting a day with the day number left blank showed an error

- **Found by:** the Playwright test `test_start_page_works_from_the_keyboard`.
- **Steps to reproduce:** on the start page, choose a level, leave "Day number (optional)" empty and press "Start the day".
- **Expected result:** a random day starts.
- **Actual result:** a raw JSON validation error: `Input should be a valid integer ... "input": ""`.
- **Root cause:** browsers send an empty form field as `seed=` (an empty string) rather than leaving it out. The `/play` route declared `seed` as an optional integer, so FastAPI tried to parse `""` as a number and rejected the request. The API tests only ever sent a valid seed or none at all, so they did not cover the empty case.
- **Fix:** `/play` takes `seed` as text. Empty means a random day; digits within range are used; anything else is rejected with a clear message.
- **Guarding tests:** `tests/api/test_pages.py::test_blank_day_number_starts_a_random_day`, `tests/api/test_pages.py::test_invalid_day_number_is_rejected` and `tests/e2e/test_game_flow.py::test_start_page_works_from_the_keyboard`.

## BUG-005: the home page kept the first visitor's request and reused it

- **Found by:** the Playwright tests, when run after the API tests in the same session, while the reconcile tool was being made the home page. It never reached a release.
- **Steps to reproduce:** request `/` once through FastAPI's test client, then request `/` from a real browser against a running server.
- **Expected result:** the home page renders for the browser's own request.
- **Actual result:** the server failed with `Expected ASGI message 'http.response.start', but got 'http.response.debug'`.
- **Root cause:** the home page passed a module-level dictionary of the file-format columns straight to `TemplateResponse`, which adds the current request to the context it is given. The first request was stored in that shared dictionary, and every later home page was rendered with it. The crash came from the test client's request carrying a debug extension a real server does not support; in production the same fault would have rendered every visitor's page with the first visitor's request.
- **Fix:** the shared columns are a read-only mapping, and each page passes `TemplateResponse` a fresh copy.
- **Guarding tests:** `tests/api/test_pages.py::test_home_page_does_not_keep_an_earlier_request`, and the browser tests in `tests/e2e/test_game_flow.py` running after the API tests.

## BUG-006: after an update, the home page showed with an old stylesheet

- **Found by:** the owner, viewing the redesigned home page in a browser that had visited the site before.
- **Steps to reproduce:** visit the site, then update it to a version with a changed `app.css`, then visit it again in the same browser without a hard refresh.
- **Expected result:** the page uses the stylesheet that came with it.
- **Actual result:** the new page was laid out with the previous stylesheet. Lists kept their bullets, label and value pairs kept the browser's default indentation, and icons sat out of line, because the old file had no rules for the new page's classes.
- **Root cause:** pages linked `/static/app.css` at the same address for every version, and static files were served with `Last-Modified` and `ETag` but no `Cache-Control`. Browsers then treat a file as fresh for a share of its age (about a tenth of the time since it last changed) and reuse it without asking. A reproduction with Playwright confirmed it: after the stylesheet changed, the browser made no new request for it and rendered the new page with the old rules.
- **Fix:** pages link the stylesheet and scripts as `/static/<file>?v=<content hash>`, so the address changes whenever the file does. Versioned addresses are sent with `Cache-Control: public, max-age=31536000, immutable`; anything requested without a version, such as the font, is sent with `no-cache`, so the browser checks it with the server each time.
- **Guarding tests:** `tests/api/test_pages.py::test_pages_link_static_files_by_content_version`, `test_the_linked_stylesheet_loads_and_may_be_cached_for_good` and `test_unversioned_static_files_are_checked_every_time`, and `tests/e2e/test_styles.py::test_stylesheet_loads_and_is_applied`.

## BUG-007: wide tables could not be scrolled from the keyboard

- **Found by:** the axe-core accessibility scan (`tests/e2e/test_accessibility.py`), when it was added during the home page redesign.
- **Steps to reproduce:** open the results of a day, or the file format tables, on a phone or tablet, then use only the keyboard to read the columns that are off screen.
- **Expected result:** the table's box can take focus, and the arrow keys scroll it sideways (WCAG 2.1.1, keyboard).
- **Actual result:** the box could not take focus and held nothing focusable, so the hidden columns could only be reached with a mouse or touch. axe reported `scrollable-region-focusable` at phone and tablet widths.
- **Root cause:** each wide table sits in a `div` that scrolls sideways so the page itself never does, but the `div` had no `tabindex`. Boxes that contain form controls (the training work page) were reachable through those controls; the others were not.
- **Fix:** every table box with nothing focusable inside is now `role="region"` with `tabindex="0"` and a name (its heading or caption), so it can be focused, scrolled and announced. The work page's boxes keep their existing names without the extra role, which would duplicate the section landmarks around them.
- **Guarding tests:** `tests/e2e/test_accessibility.py`, which scans every page at phone, tablet and desktop widths in both themes and fails on any violation.

## BUG-008: closing a Head of Ops day failed with "Too many fields"

- **Found by:** the owner, playing a Head of Ops day in training mode on the live site.
- **Steps to reproduce:** start a Head of Ops day, then press **Close the day** (or **Check progress**).
- **Expected result:** the close screen with the score, or the progress line.
- **Actual result:** the page showed `{"detail":"Too many fields. Maximum number of fields is 1000."}` instead.
- **Root cause:** every line of the day sends two form fields, its pair number and its "if no partner" choice. A Head of Ops day has about 600 lines, so it sends about 1,200 fields, and Starlette's form parser refuses more than 1,000 by default. The smaller levels stay under the limit, which is why only the hardest level failed.
- **Fix:** both routes read the form through one helper that sets the limit from the day itself: two fields per line plus a small allowance. The limit still protects the server, but it can never be lower than the day needs.
- **Guarding test:** `tests/api/test_pages.py::test_the_largest_day_can_be_closed_and_checked`, which posts a full Head of Ops day to both routes.
