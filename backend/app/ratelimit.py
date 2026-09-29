# backend/app/ratelimit.py
# Task 1.5.a: a pure, framework-agnostic rate limiter (docs/SPEC.md §9:
# "An in-process limiter behind a RateLimiter interface on a single API
# instance"). No imports from fastapi, app.config, or anything web-specific
# -- same layering discipline as app/tenancy/repository.py's own
# TenantScopedRepository: this module knows nothing about HTTP, Settings,
# or the database. 1.5.b (Settings) and 1.5.c (wiring into
# POST /api/v1/session) build on top of this, not into it.
#
# Sliding window log, not a fixed window: a fixed window (bucketing by
# wall-clock minute, say) lets a caller send up to 2x the limit in a short
# burst straddling a bucket boundary -- limit's worth just before the
# boundary, limit's worth just after, both buckets individually "under
# limit". That is exactly the deliberate-abuse shape this exists to catch
# (see test_sliding_window_rejects_a_boundary_straddling_burst_a_fixed_
# window_would_allow below for a constructed proof, not just an assertion).
# Not a token-bucket or leaky-bucket algorithm either: those solve a
# different problem (smoothing/allowing controlled bursts over time), and
# this project's own limit is a flat "N per trailing window" count with no
# smoothing requirement -- a sliding-window LOG (each key's own list of
# recent request timestamps, pruned as it goes) implements that directly,
# with no extra parameters (bucket size, refill rate) to get wrong, and no
# more code than the fixed-window version it replaces (rule 11).
#
# Limit and window are fixed at construction, not passed per check(): one
# RateLimiter instance is one policy, applied uniformly to every key it
# manages. Passing them per call would let two calls for the SAME key use
# two different limits/windows, which would corrupt that key's own
# timestamp log's meaning (the log only makes sense relative to one
# window). 1.5.c's (IP, site_key) policy and 1.5.e's broader per-IP policy
# are two DIFFERENT policies, so they get two separate RateLimiter
# instances (with disjoint key spaces by construction, since each caller
# builds its own key string) -- not one instance juggling two limits.
#
# Task 1.5.c adds get_retry_after() below (a small addition to this
# already-Done module, needed by the 429 response's own Retry-After header
# and JSON body -- 1.5.a did not anticipate this method, so it is added
# here rather than reimplemented at the call site, matching 1.5.a's own
# "no imports from anything web-specific" layering discipline).
import time
from collections import deque
from collections.abc import Callable


class RateLimiter:
    def __init__(
        self,
        limit: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._limit = limit
        self._window_seconds = window_seconds
        self._clock = clock
        # key -> deque of the timestamps (self._clock() values) of that
        # key's own recent ALLOWED attempts, oldest first (append-only at
        # the right end, pruned from the left end -- valid only because
        # self._clock is assumed non-decreasing, exactly like
        # time.monotonic() itself guarantees).
        self._attempts: dict[str, deque[float]] = {}

    def check(self, key: str) -> bool:
        now = self._clock()
        cutoff = now - self._window_seconds

        # Bounded memory (Task 1.5.a design point 4): prune every tracked
        # key's expired prefix on every check(), not just the key being
        # checked -- a key that is checked exactly once and never returns
        # would otherwise keep its now-permanently-stale entry (and the
        # memory it holds) forever, since nothing else would ever trigger
        # cleanup for it. A full sweep is O(number of currently tracked
        # keys) per check; for this project's realistic scale (one
        # in-process limiter on a single API instance, docs/SPEC.md §9),
        # that cost is negligible, and there is no evidence yet that a more
        # elaborate eviction scheme (LRU cap, background sweep thread) is
        # needed (rule 11).
        self._prune_expired(cutoff)

        attempts = self._attempts.get(key)
        if attempts is not None and len(attempts) >= self._limit:
            # Rejected attempts are never recorded (design point 3): a
            # client that backs off correctly after a 429 must not have
            # its next legitimate attempt further delayed by the rejected
            # one -- only genuinely allowed attempts age out of the
            # window and free up room.
            return False

        self._attempts.setdefault(key, deque()).append(now)
        return True

    def get_retry_after(self, key: str) -> float:
        # How many seconds until `key` would next be allowed -- a real
        # countdown against that key's own oldest currently-counted
        # timestamp (it ages out of the trailing window at
        # timestamp + window_seconds), not a static restatement of
        # self._window_seconds. Only meaningful for a key that is
        # CURRENTLY at or over the limit (i.e. the same key a check() call
        # that just returned False was made against): returns 0.0 for any
        # key that is not currently blocked, rather than a misleading wait
        # time for a key that could be checked again right now. Read-only
        # (no pruning): a key that just failed check() was already pruned
        # by that same call (check()'s own full sweep), so attempts[0] here
        # is already the correct, unexpired oldest timestamp.
        attempts = self._attempts.get(key)
        if attempts is None or len(attempts) < self._limit:
            return 0.0
        remaining = attempts[0] + self._window_seconds - self._clock()
        return max(0.0, remaining)

    def _prune_expired(self, cutoff: float) -> None:
        emptied_keys = []
        for tracked_key, timestamps in self._attempts.items():
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()
            if not timestamps:
                emptied_keys.append(tracked_key)
        for tracked_key in emptied_keys:
            del self._attempts[tracked_key]
