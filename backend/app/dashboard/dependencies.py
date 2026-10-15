# backend/app/dashboard/dependencies.py
# Step 6.1 [SECURITY]: the verified-dashboard-identity dependency -- the
# tenant-scoping layer this project's own Membership model needs on top
# of fastapi-users' own `current_active_user` (app/dashboard/
# auth_backend.py), which only ever proves "this is a real, active,
# logged-in User", nothing about which tenant they may act for.
#
# "Every dashboard endpoint tenant-scoped through the logged-in
# membership, never a client-supplied tenant id" (this task's own direct
# instruction) is enforced structurally the same way get_tenant_
# repository() already enforces it for the widget: this dependency takes
# no request body/query parameter that could name a tenant, so there is
# no code path here a forged tenant id could ever reach.
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.secrets import hash_visitor_secret
from app.dashboard.auth_backend import SESSION_COOKIE_NAME, current_active_user
from app.dashboard.models import DashboardSession, User
from app.dashboard.repository import get_membership_for_user
from app.db import get_db_session
from app.tenancy.repository import TenantScopedRepository

_NO_MEMBERSHIP_DETAIL = "no tenant membership for this account"
_SESSION_LOOKUP_FAILED_DETAIL = "dashboard session could not be verified"
# docs/SPEC.md §12: "a fresh sign-in within about 10 to 15 minutes" --
# the upper end of that named range, the default every future caller of
# require_recent_auth() below gets unless it overrides this explicitly.
RECENT_AUTH_DEFAULT_MAX_AGE_SECONDS = 900.0


@dataclass(frozen=True)
class DashboardIdentity:
    user_id: UUID
    email: str
    tenant_id: UUID
    role: str
    # The real sign-in time docs/SPEC.md §12's own "recent authentication"
    # rule needs -- this dashboard_sessions row's own `created_at` (a
    # session row is only ever created at a real, successful login, app/
    # dashboard/routes.py's own login() handler), never touched again
    # afterward (unlike `last_seen_at`, which slides on every request).
    authenticated_at: datetime


async def _get_session_created_at(session: AsyncSession, token: str) -> datetime | None:
    result = await session.execute(
        select(DashboardSession.created_at).where(
            DashboardSession.token == hash_visitor_secret(token)
        )
    )
    return result.scalar_one_or_none()


async def get_current_dashboard_user(
    request: Request,
    user: User = Depends(current_active_user),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> DashboardIdentity:
    membership = await get_membership_for_user(session, user.id)
    if membership is None:
        # An orphaned user (no real tenant membership) -- cannot happen
        # through the real seed/signup path (Step 6.1's own CLI always
        # creates both together), but a deleted membership must still
        # fail closed here rather than return a DashboardIdentity with
        # no real tenant to scope to. A DIFFERENT status/shape from
        # current_active_user's own 401 -- the session itself is
        # genuinely valid here, so 403 (not 401) is the honest code: the
        # account exists and is logged in, it simply has nothing to act
        # on yet.
        raise HTTPException(status_code=403, detail=_NO_MEMBERSHIP_DETAIL)

    # current_active_user already proved the cookie names a currently-
    # valid session; this is a second, cheap read of that SAME row for
    # its own created_at (fastapi-users' own current_user() dependency
    # deliberately returns only the User, never the access-token row it
    # resolved internally).
    token = request.cookies.get(SESSION_COOKIE_NAME)
    authenticated_at = await _get_session_created_at(session, token) if token else None
    if authenticated_at is None:
        raise HTTPException(status_code=401, detail=_SESSION_LOOKUP_FAILED_DETAIL)

    return DashboardIdentity(
        user_id=user.id,
        email=user.email,
        tenant_id=membership.tenant_id,
        role=membership.role,
        authenticated_at=authenticated_at,
    )


def get_dashboard_tenant_repository(
    identity: DashboardIdentity = Depends(get_current_dashboard_user),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> TenantScopedRepository:
    # Reuses the SAME tenant-scoped repository the widget's own
    # get_tenant_repository() constructs (app/auth/dependencies.py) --
    # tenant-scoped database access rule (PROJECT_SPEC.md §3) applies
    # identically regardless of which identity system authenticated the
    # caller. tenant_id comes only from identity.tenant_id (the logged-in
    # membership, resolved above) -- this dependency takes no Request
    # parameter, so it structurally cannot read a tenant id from a query
    # parameter, request body field, or header however a future dashboard
    # endpoint's own caller might try to supply one.
    return TenantScopedRepository(tenant_id=identity.tenant_id, session=session)


def require_recent_auth(
    max_age_seconds: float = RECENT_AUTH_DEFAULT_MAX_AGE_SECONDS,
) -> Callable[[DashboardIdentity], Awaitable[DashboardIdentity]]:
    """docs/SPEC.md §12: "Recent-authentication (a fresh sign-in within
    about 10 to 15 minutes; not 're-enter password') is required only
    for: deleting the tenant, removing a team member or changing roles,
    replacing database-sync credentials, and changing site keys or
    allowed origins." No endpoint in Step 6.1 itself needs this -- built
    now, per direct instruction, so each of those later (6.2+) endpoints
    only adds `Depends(require_recent_auth())` rather than designing
    this mechanism itself. A factory, not a bare dependency function:
    matches fastapi_users' own `current_user(...)` shape (app/dashboard/
    auth_backend.py) for the identical reason -- a caller that ever
    needs a different window than the shared default can override it
    without this module growing a second, near-duplicate dependency.
    """

    async def _dependency(
        identity: DashboardIdentity = Depends(get_current_dashboard_user),  # noqa: B008
    ) -> DashboardIdentity:
        age = datetime.now(UTC) - identity.authenticated_at
        if age > timedelta(seconds=max_age_seconds):
            raise HTTPException(status_code=403, detail="recent authentication required")
        return identity

    return _dependency
