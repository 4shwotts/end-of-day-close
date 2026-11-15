"""Business-day arithmetic for settlement dates.

Payment processors and banks settle on business days, so "T+2" means two
working days after the transaction, not two calendar days. The generator uses
these helpers to plant realistic settlement dates, and the matching engine
uses them to build its date windows.
"""

from __future__ import annotations

from datetime import date, timedelta

# Monday is 0 and Friday is 4 in date.weekday().
_LAST_WEEKDAY = 4


def is_business_day(day: date) -> bool:
    """Return True if ``day`` is a weekday.

    UK bank holidays are deliberately not modelled: they would need a
    maintained calendar, and weekends already produce the realistic gaps.

    Args:
        day: The date to check.

    Returns:
        True for Monday to Friday.
    """
    return day.weekday() <= _LAST_WEEKDAY


def add_business_days(start: date, days: int) -> date:
    """Move forwards or backwards by a number of business days.

    Args:
        start: The starting date. It need not itself be a business day.
        days: How many business days to move; negative moves backwards.

    Returns:
        The resulting business day, or ``start`` itself when ``days`` is 0.
    """
    step = timedelta(days=1 if days >= 0 else -1)
    remaining = abs(days)
    current = start
    while remaining:
        current += step
        if is_business_day(current):
            remaining -= 1
    return current


def business_days_between(start: date, end: date) -> int:
    """Count the business days after ``start`` up to and including ``end``.

    This is the "N" in T+N: a transaction on Friday settling on Monday is
    T+1, not T+3.

    Args:
        start: The transaction date.
        end: The settlement date.

    Returns:
        The number of business days; negative if ``end`` is before ``start``.
    """
    if end < start:
        return -business_days_between(end, start)
    count = 0
    current = start
    while current < end:
        current += timedelta(days=1)
        if is_business_day(current):
            count += 1
    return count
