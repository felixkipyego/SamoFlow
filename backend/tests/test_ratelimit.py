# backend/tests/test_ratelimit.py
# Task 1.5.a: RateLimiter is pure Python (no FastAPI, no Settings, no
# database), so every test here runs fully offline -- no test database, no
# network, no real sleeps. Time is driven entirely by _FakeClock, the same
# injectable-clock pattern already used for StatusCache (Task 1.4.h,
# test_dependencies.py).
from app.ratelimit import RateLimiter


class _FakeClock:
    def __init__(self, start: float = 1_000.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def test_exactly_limit_requests_allowed_then_the_next_is_rejected() -> None:
    clock = _FakeClock()
    limiter = RateLimiter(limit=3, window_seconds=60, clock=clock)

    assert limiter.check("k") is True
    assert limiter.check("k") is True
    assert limiter.check("k") is True
    assert limiter.check("k") is False


def test_a_blocked_key_is_allowed_again_once_the_full_window_elapses() -> None:
    clock = _FakeClock()
    limiter = RateLimiter(limit=2, window_seconds=60, clock=clock)

    assert limiter.check("k") is True
    assert limiter.check("k") is True
    assert limiter.check("k") is False

    clock.advance(60.0001)

    assert limiter.check("k") is True


def test_sliding_window_rejects_a_boundary_straddling_burst_a_fixed_window_would_allow() -> (
    None
):
    # limit=10, window=60. Concentrate all 10 allowed requests at the very
    # end of what a naive fixed window would call "bucket 1"
    # (t=1059.0..1059.9), then try one more just after t=1060 -- the start
    # of what a naive fixed window would call a fresh "bucket 2".
    #
    # A fixed window keyed on floor(t / 60) would see bucket 1 ending with
    # exactly 10 (at the limit, still allowed) and bucket 2 starting fresh
    # at 0 -- so it would WRONGLY allow the t=1060.1 request too, letting
    # 11 requests through in a 1.1-second span.
    #
    # A correct sliding window instead looks at the trailing 60 seconds
    # from t=1060.1, i.e. (1000.1, 1060.1] -- which still contains all 10
    # burst timestamps (1059.0..1059.9) -- so it must reject.
    clock = _FakeClock(start=1_059.0)
    limiter = RateLimiter(limit=10, window_seconds=60, clock=clock)

    for i in range(10):
        clock.advance(0.1 if i else 0.0)
        assert limiter.check("k") is True

    # clock is now at 1059.9. Advance just past the fixed-window boundary
    # at 1060, while still well within 60s of the burst.
    clock.advance(0.2)
    assert limiter.check("k") is False


def test_two_keys_never_share_a_counter() -> None:
    clock = _FakeClock()
    limiter = RateLimiter(limit=2, window_seconds=60, clock=clock)

    assert limiter.check("a") is True
    assert limiter.check("a") is True
    assert limiter.check("a") is False

    # "b" is unaffected by "a" being maxed out.
    assert limiter.check("b") is True
    assert limiter.check("b") is True
    assert limiter.check("b") is False


def test_a_rejected_attempt_is_not_counted_and_does_not_delay_recovery() -> None:
    clock = _FakeClock()
    limiter = RateLimiter(limit=2, window_seconds=60, clock=clock)

    assert limiter.check("k") is True  # t=0
    clock.advance(1.0)
    assert limiter.check("k") is True  # t=1
    clock.advance(1.0)
    assert limiter.check("k") is False  # t=2, rejected -- must not be recorded

    # If the rejection above HAD been recorded, the window would need to
    # wait until t=2+60=62 to admit again. Since it was not recorded, the
    # key becomes allowed again as soon as the two genuine attempts
    # (t=0, t=1) age out -- i.e. any time after t=60, well before t=62.
    clock.advance(58.0001)  # now t=60.0001: only the t=1 attempt still stale-checks

    assert limiter.check("k") is True


def test_expired_entries_are_actually_pruned_and_memory_does_not_grow_unbounded() -> (
    None
):
    clock = _FakeClock()
    limiter = RateLimiter(limit=1, window_seconds=60, clock=clock)

    for i in range(50):
        assert limiter.check(f"one-off-{i}") is True

    assert len(limiter._attempts) == 50

    # Advance well past the window for all 50 keys, then check an unrelated
    # new key -- this must trigger a full sweep that drops all 50 stale
    # entries, not just prune the key being checked.
    clock.advance(120.0)
    assert limiter.check("probe") is True

    assert len(limiter._attempts) == 1
    assert list(limiter._attempts.keys()) == ["probe"]
