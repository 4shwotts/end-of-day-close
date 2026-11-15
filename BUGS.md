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
