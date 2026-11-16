"""Unit tests for the per-visitor rate limiter."""

import pytest

from eod_close.web.limits import LIMITS, Limit, RateLimiter, rate_limited


class FakeClock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def test_requests_within_the_limit_are_allowed() -> None:
    limiter = RateLimiter(Limit(3, 60), FakeClock())
    assert [limiter.hit("a") for _ in range(3)] == [None, None, None]


def test_the_next_request_is_refused_with_the_wait_until_the_oldest_expires() -> None:
    clock = FakeClock()
    limiter = RateLimiter(Limit(2, 60), clock)
    limiter.hit("a")
    clock.now += 10
    limiter.hit("a")
    clock.now += 5
    assert limiter.hit("a") == pytest.approx(45)


def test_the_window_slides() -> None:
    clock = FakeClock()
    limiter = RateLimiter(Limit(1, 60), clock)
    assert limiter.hit("a") is None
    clock.now += 59.9
    assert limiter.hit("a") is not None
    clock.now += 0.1
    assert limiter.hit("a") is None


def test_a_refused_request_does_not_count() -> None:
    """Otherwise a visitor who keeps trying would never get back in."""
    clock = FakeClock()
    limiter = RateLimiter(Limit(1, 60), clock)
    limiter.hit("a")
    for _ in range(10):
        clock.now += 5
        limiter.hit("a")
    clock.now += 10
    assert limiter.hit("a") is None


def test_visitors_are_counted_separately() -> None:
    limiter = RateLimiter(Limit(1, 60), FakeClock())
    assert limiter.hit("a") is None
    assert limiter.hit("a") is not None
    assert limiter.hit("b") is None


def test_idle_visitors_are_forgotten() -> None:
    """Memory follows recent visitors, not everyone who ever called."""
    clock = FakeClock()
    limiter = RateLimiter(Limit(5, 60), clock)
    for n in range(1_000):
        limiter.hit(f"visitor-{n}")
    clock.now += 60
    limiter.hit("newcomer")
    assert len(limiter._seen) == 1


def test_an_unknown_kind_of_request_is_a_mistake() -> None:
    with pytest.raises(KeyError):
        rate_limited("everything")


def test_the_heaviest_work_has_the_tightest_limit() -> None:
    assert LIMITS["race"].requests < LIMITS["upload"].requests < LIMITS["new-day"].requests
