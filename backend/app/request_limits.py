# backend/app/request_limits.py
# Step 4.4 duplication check, run before adding a THIRD near-identical
# copy of this helper (app/handoff/routes.py's own new escalation rate
# limits): app/auth/routes.py's own _get_or_create_limiter() and
# app/chat/routes.py's own _get_or_create_limiter() were already an
# acknowledged, deliberate 2-occurrence duplication (each router
# module's own comment: "duplicating one three-line function is below
# this project's own established 3rd-occurrence threshold... importing
# a private name across router modules would be a worse coupling").
# Step 4.4 needs the identical shape a third time -- crossing that
# threshold for real -- so it is extracted here instead of becoming a
# third private copy. NOT in app/ratelimit.py itself: that module is
# deliberately framework-agnostic ("no imports from fastapi... this
# module knows nothing about HTTP", its own header comment) -- this
# helper is the FastAPI-specific bridge from a `Request`'s own
# `app.state` to a `RateLimiter` instance, so it lives in its own
# top-level module instead of widening ratelimit.py's own stated
# layering.
from fastapi import Request

from app.ratelimit import RateLimiter


def get_or_create_limiter(
    request: Request, attr: str, limit: int, window_seconds: float
) -> RateLimiter:
    """One `RateLimiter` instance per (app, attr) -- stored on
    `request.app.state` so it survives across requests on the same app
    instance (fresh and empty again on every fresh `create_app()`, the
    same test-isolation property `app/chat/routes.py`'s own identical
    helper already relied on).
    """
    limiter = getattr(request.app.state, attr, None)
    if limiter is None:
        limiter = RateLimiter(limit=limit, window_seconds=window_seconds)
        setattr(request.app.state, attr, limiter)
    return limiter
