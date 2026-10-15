# backend/tests/test_dashboard_dependencies.py
# Step 6.1: app/dashboard/dependencies.py's own require_recent_auth() --
# built now (no 6.1 endpoint consumes it yet, per direct instruction),
# so this is its only real exercise today. Constructed directly against
# a real DashboardIdentity (no HTTP layer, no database) -- the pure
# age-comparison logic is the only real behavior here; get_current_
# dashboard_user()'s own 403-no-membership/401-no-session paths are
# already covered end to end via test_dashboard_routes.py's own /auth/me
# tests, not repeated here.
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from app.dashboard.dependencies import DashboardIdentity, require_recent_auth


def _identity(authenticated_at: datetime) -> DashboardIdentity:
    return DashboardIdentity(
        user_id=uuid.uuid4(),
        email="owner@example.com",
        tenant_id=uuid.uuid4(),
        role="owner",
        authenticated_at=authenticated_at,
    )


async def test_require_recent_auth_allows_a_fresh_sign_in():
    dependency = require_recent_auth(max_age_seconds=900)
    identity = _identity(datetime.now(UTC) - timedelta(minutes=1))
    result = await dependency(identity)
    assert result is identity


async def test_require_recent_auth_rejects_a_stale_sign_in():
    dependency = require_recent_auth(max_age_seconds=900)
    identity = _identity(datetime.now(UTC) - timedelta(minutes=16))
    with pytest.raises(HTTPException) as exc_info:
        await dependency(identity)
    assert exc_info.value.status_code == 403


async def test_require_recent_auth_respects_a_caller_supplied_window():
    dependency = require_recent_auth(max_age_seconds=60)
    identity = _identity(datetime.now(UTC) - timedelta(minutes=2))
    with pytest.raises(HTTPException) as exc_info:
        await dependency(identity)
    assert exc_info.value.status_code == 403


async def test_require_recent_auth_boundary_is_exclusive():
    dependency = require_recent_auth(max_age_seconds=900)
    # Comfortably inside the window -- a value right at the exact
    # boundary would be a flaky test (real wall-clock time elapses
    # between constructing `identity` and the dependency's own
    # datetime.now() call).
    identity = _identity(datetime.now(UTC) - timedelta(seconds=899))
    result = await dependency(identity)
    assert result is identity
