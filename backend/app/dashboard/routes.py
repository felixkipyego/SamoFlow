# backend/app/dashboard/routes.py
# Step 6.1 [SECURITY]: dashboard login/logout/me (docs/SPEC.md §12).
# Served under /api/v1/dashboard, same origin as the dashboard SPA itself
# (this task's own cookie/origin design: Caddy routes /api on the
# dashboard hostname in production, so the dashboard needs no CORS at
# all -- unlike app/auth/routes.py's own dynamic-per-site-key CORS, there
# is no Access-Control-* header anywhere in this module).
#
# Custom JSON routes, not fastapi-users' own generated get_auth_router():
# this project's own CSRF/rate-limit checks must run BEFORE any password
# verification, in a specific cheapest-first order (docs/SPEC.md §9's own
# ordering philosophy) that the generated router has no hook for, and the
# generated router's own login endpoint expects an OAuth2 form body
# (username/password form fields), not this project's own JSON API shape.
# Only the library's own building blocks are reused directly: user_
# manager.authenticate() (real argon2id verification, with its own
# built-in timing-safe handling of an unknown email -- see app/dashboard/
# auth_backend.py's own header comment) and auth_backend.login()/logout()
# (the real session-cookie lifecycle, via this project's own
# HashedDatabaseStrategy).
#
# Failure model: every login failure (unknown email, wrong password,
# inactive account, no real membership) returns the SAME generic 401
# body -- "no oracle", matching app.auth.routes.create_session()'s own
# established philosophy for its own failure categories.
import math
from types import SimpleNamespace

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.dashboard.auth_backend import (
    SESSION_COOKIE_NAME,
    HashedDatabaseStrategy,
    UserManager,
    auth_backend,
    current_active_user,
    get_database_strategy,
    get_user_manager,
)
from app.dashboard.csrf import generate_csrf_token, require_csrf, set_csrf_cookie
from app.dashboard.dependencies import DashboardIdentity, get_current_dashboard_user
from app.dashboard.models import User
from app.dashboard.repository import get_membership_for_user
from app.dashboard.schemas import (
    DashboardSessionResponse,
    DashboardTenant,
    DashboardUser,
    LoginRequest,
)
from app.db import get_db_session
from app.request_limits import get_or_create_limiter
from app.tenancy.repository import TenantScopedRepository

router = APIRouter(prefix="/api/v1/dashboard")

_FAILURE_BODY = {"detail": "login could not be completed"}
_FAILURE_STATUS = 401
_RATE_LIMITED_STATUS = 429


def _failure_response() -> JSONResponse:
    return JSONResponse(status_code=_FAILURE_STATUS, content=_FAILURE_BODY)


def _rate_limited_response(retry_after: float) -> JSONResponse:
    # Same shape as app.auth.routes._rate_limited_response() -- an
    # integer Retry-After header (RFC 9110 §10.2.3), rounded up, never 0.
    header_seconds = max(1, math.ceil(retry_after))
    return JSONResponse(
        status_code=_RATE_LIMITED_STATUS,
        content={"detail": "too many requests", "retry_after": round(retry_after, 1)},
        headers={"Retry-After": str(header_seconds)},
    )


def _get_per_ip_limiter(request: Request):
    settings = request.app.state.settings
    return get_or_create_limiter(
        request,
        "dashboard_login_per_ip_limiter",
        settings.dashboard_login_rate_limit_per_ip,
        settings.dashboard_login_rate_limit_per_ip_window_seconds,
    )


def _get_per_account_limiter(request: Request):
    settings = request.app.state.settings
    return get_or_create_limiter(
        request,
        "dashboard_login_per_account_limiter",
        settings.dashboard_login_rate_limit_per_account,
        settings.dashboard_login_rate_limit_per_account_window_seconds,
    )


def _copy_set_cookie_headers(source: Response, destination: Response) -> None:
    # auth_backend.login()/logout() each return their own small Response
    # (204, via CookieTransport) purely for its Set-Cookie header --
    # this project's own route handlers need a JSON body instead, so the
    # cookie header(s) are copied onto the real response. getlist(), not
    # a dict merge: a dict would silently drop one of two same-named
    # headers if this ever set more than one cookie at once.
    for value in source.headers.getlist("set-cookie"):
        destination.raw_headers.append((b"set-cookie", value.encode("latin-1")))


@router.get("/auth/csrf", status_code=204)
async def issue_csrf_cookie(response: Response) -> Response:
    # Idempotent and unauthenticated on purpose: called once by the SPA
    # before it ever renders the login form (see app/dashboard/csrf.py's
    # own header comment for why this closes the login-CSRF gap too). A
    # caller that already has a valid CSRF cookie gets a fresh one anyway
    # -- rotating it costs nothing and there is no server-side state to
    # keep in sync.
    #
    # FastAPI's own injected `response: Response` parameter does NOT
    # inherit the route decorator's `status_code=204` when the handler
    # returns that SAME object directly -- confirmed live (a real,
    # reproduced bug, not assumed): the injected instance starts with
    # `status_code=None`, and returning it as-is crashes deep inside
    # uvicorn's own access-log/status-line formatting (KeyError: None)
    # rather than failing at the FastAPI layer where it would be obvious.
    # Must be set explicitly here.
    response.status_code = 204
    set_csrf_cookie(response, generate_csrf_token())
    return response


@router.post("/auth/login", response_model=DashboardSessionResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    user_manager: UserManager = Depends(get_user_manager),  # noqa: B008
    strategy: HashedDatabaseStrategy = Depends(get_database_strategy),  # noqa: B008
):
    # Cheapest-first (docs/SPEC.md §9's own ordering philosophy, applied
    # here): per-IP rate limit, then CSRF (no I/O either), then the
    # per-account rate limit, then the real database/password work.
    ip = request.client.host if request.client else "unknown"
    per_ip_limiter = _get_per_ip_limiter(request)
    if not per_ip_limiter.check(ip):
        return _rate_limited_response(per_ip_limiter.get_retry_after(ip))

    require_csrf(request)

    per_account_limiter = _get_per_account_limiter(request)
    account_key = payload.email.strip().lower()
    if not per_account_limiter.check(account_key):
        return _rate_limited_response(per_account_limiter.get_retry_after(account_key))

    # BaseUserManager.authenticate() (fastapi_users.manager) itself pays
    # the real argon2 hashing cost even for an unknown email -- the
    # timing-safe "no oracle" defense this task's own adversarial pass
    # requires, built into the library, not reimplemented here.
    credentials = SimpleNamespace(username=payload.email, password=payload.password)
    user = await user_manager.authenticate(credentials)
    if user is None or not user.is_active:
        return _failure_response()

    membership = await get_membership_for_user(session, user.id)
    if membership is None:
        return _failure_response()

    cookie_response = await auth_backend.login(strategy, user)

    repo = TenantScopedRepository(tenant_id=membership.tenant_id, session=session)
    tenant = await repo.get_tenant()
    body = DashboardSessionResponse(
        user=DashboardUser(id=user.id, email=user.email),
        tenant=DashboardTenant(id=tenant.id, name=tenant.name),
    )
    response = JSONResponse(status_code=200, content=body.model_dump(mode="json"))
    _copy_set_cookie_headers(cookie_response, response)
    return response


@router.post("/auth/logout", status_code=204)
async def logout(
    request: Request,
    user: User = Depends(current_active_user),  # noqa: B008
    strategy: HashedDatabaseStrategy = Depends(get_database_strategy),  # noqa: B008
) -> Response:
    require_csrf(request)

    # A session cookie that is already missing by the time logout is
    # called (e.g. a replayed/expired request) still clears whatever the
    # browser may hold and returns 204 -- logout is idempotent, matching
    # delete_conversation()'s own "wrong/missing id is a silent no-op"
    # precedent -- never a 401 for an already-authenticated caller.
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token is None:
        return Response(status_code=204)
    return await auth_backend.logout(strategy, user, token)


@router.get("/auth/me", response_model=DashboardSessionResponse)
async def me(
    identity: DashboardIdentity = Depends(get_current_dashboard_user),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> DashboardSessionResponse:
    repo = TenantScopedRepository(tenant_id=identity.tenant_id, session=session)
    tenant = await repo.get_tenant()
    return DashboardSessionResponse(
        user=DashboardUser(id=identity.user_id, email=identity.email),
        tenant=DashboardTenant(id=tenant.id, name=tenant.name),
    )
