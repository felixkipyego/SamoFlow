# backend/app/auth/dependencies.py
# Task 1.4.h: the verified-identity dependency (docs/SPEC.md §4.3's "Origin
# binding" and "Kill switch" rules). Every authenticated endpoint from here
# forward depends on this module -- it is the ONLY place a request's caller
# identity is established from a bearer token.
#
# VerifiedIdentity is immutable and carries exactly (tenant_id, vid, org) --
# nothing else. Downstream code must never read tenant_id/vid/site info from
# anywhere but this value (never re-derive it from a request body/query
# field -- see PROJECT_SPEC.md's own open-marker note on this for Step
# 3.1's retrieve()).
#
# One exception, one status code: every failure path here raises the same
# HTTPException(401, _FAILURE_DETAIL) -- a missing header, a malformed
# scheme, a forged/expired token, an Origin/org mismatch, a deleted
# tenant/visitor, and a suspended tenant/site-key are all indistinguishable
# from the outside, the same "no oracle" philosophy 1.4.g's session
# endpoint already applies to ITS OWN failures.
#
# The JWT (1.4.b) carries tenant_id/vid/org, but no site_key_id -- so
# checking "site key status" here requires one extra lookup (the visitor's
# own site_key_id, via vid) that the 60-second cache below does NOT cover.
# This is a deliberate, minimal cost: a visitor's site_key_id is immutable
# once set (no code path ever changes visitors.site_key_id), so this one
# indexed lookup (by primary key, scoped by tenant_id) is cheap and never
# stale, unlike the two STATUSES (tenant.status, site_keys.status), which
# really can change at any time and are what the cache exists to avoid
# re-reading on every request (docs/SPEC.md §4.3: "tenant and key status
# come from an in-process cache with a 60s TTL, so suspension takes effect
# within about a minute without a DB hit per request").
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import InvalidSessionToken, decode_session_token
from app.db import get_db_session
from app.tenancy.models import SiteKey, Tenant, Visitor

_FAILURE_DETAIL = "session could not be verified"
_CACHE_TTL_SECONDS = 60.0

# Site-key states that continue to work for an already-issued session
# (matches 1.4.g's own session-creation rule: draft and live both succeed,
# only suspended fails).
_ACTIVE_SITE_KEY_STATUSES = ("live", "draft")


@dataclass(frozen=True)
class VerifiedIdentity:
    tenant_id: uuid.UUID
    vid: uuid.UUID
    org: str


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail=_FAILURE_DETAIL)


def _extract_bearer_token(header_value: str | None) -> str | None:
    # Exactly "Bearer <token>": one space, a non-empty token, no trailing
    # text. Case-sensitive "Bearer" (the conventional canonical form).
    if header_value is None:
        return None
    scheme, sep, token = header_value.partition(" ")
    if scheme != "Bearer" or not sep or not token or " " in token:
        return None
    return token


@dataclass
class _CachedStatus:
    # None means "no such row" (tenant/site key deleted or never existed) --
    # treated as a plain, cacheable status value, not a special case: it
    # fails the "must be active/live" check the same way "suspended" does,
    # and caching it too means a token for a permanently-deleted identity
    # doesn't cause a fresh DB read on every single request either.
    tenant_status: str | None
    site_key_status: str | None
    cached_at: float


class StatusCache:
    # Task 1.4.h: an in-process TTL cache for tenant/site-key STATUS only
    # (never for the visitor's own site_key_id -- see this module's header
    # comment for why that lookup is not cached). Keyed by
    # (tenant_id, site_key_id) together: site_key_id alone would already be
    # globally unique (it's a UUID primary key), but keying by both matches
    # this project's established tenant-scoping philosophy (never trust an
    # id in isolation) and is what makes cross-tenant isolation trivially
    # provable -- two different tenants' entries can never collide or be
    # read for one another, by construction of the key itself.
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._entries: dict[tuple[uuid.UUID, uuid.UUID], _CachedStatus] = {}

    def get(self, tenant_id: uuid.UUID, site_key_id: uuid.UUID) -> _CachedStatus | None:
        entry = self._entries.get((tenant_id, site_key_id))
        if entry is None:
            return None
        if self.clock() - entry.cached_at >= _CACHE_TTL_SECONDS:
            return None
        return entry

    def set(
        self,
        tenant_id: uuid.UUID,
        site_key_id: uuid.UUID,
        tenant_status: str | None,
        site_key_status: str | None,
    ) -> _CachedStatus:
        entry = _CachedStatus(tenant_status, site_key_status, self.clock())
        self._entries[(tenant_id, site_key_id)] = entry
        return entry


def _get_status_cache(request: Request) -> StatusCache:
    # Lives on app.state (the same place Settings itself already lives,
    # app/main.py's create_app()) -- not a module-level lru_cache singleton
    # like get_settings()/get_engine(): a fresh FastAPI app (create_app())
    # gets a fresh, empty cache automatically, with no explicit
    # cache_clear() needed between tests, and tests can pre-seed
    # app.state.status_cache with an injectable-clock instance before
    # making any request.
    cache = getattr(request.app.state, "status_cache", None)
    if cache is None:
        cache = StatusCache()
        request.app.state.status_cache = cache
    return cache


async def _fetch_visitor_site_key_id(
    session: AsyncSession, tenant_id: uuid.UUID, vid: uuid.UUID
) -> uuid.UUID | None:
    result = await session.execute(
        select(Visitor.site_key_id).where(Visitor.vid == vid, Visitor.tenant_id == tenant_id)
    )
    return result.scalar_one_or_none()


async def _fetch_current_statuses(
    session: AsyncSession, tenant_id: uuid.UUID, site_key_id: uuid.UUID
) -> tuple[str | None, str | None]:
    tenant_status = (
        await session.execute(select(Tenant.status).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    site_key_status = (
        await session.execute(
            select(SiteKey.status).where(SiteKey.id == site_key_id, SiteKey.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    return tenant_status, site_key_status


async def get_current_visitor(
    request: Request,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008 (FastAPI's own Depends() idiom)
) -> VerifiedIdentity:
    token = _extract_bearer_token(request.headers.get("authorization"))
    if token is None:
        raise _unauthorized()

    try:
        claims = decode_session_token(token)
    except InvalidSessionToken:
        raise _unauthorized() from None

    # Origin-vs-org: a direct equality check against the token's OWN org
    # claim, never origin_is_allowed() (1.4.f) -- that function checks a
    # SITE KEY's allow-list, a different question from "is this the exact
    # origin this specific token was issued for". A missing or repeated
    # Origin header has no single well-defined value to compare, so it
    # fails the same way a mismatch does.
    origins = request.headers.getlist("origin")
    if len(origins) != 1 or origins[0] != claims.origin:
        raise _unauthorized()

    site_key_id = await _fetch_visitor_site_key_id(session, claims.tenant_id, claims.vid)
    if site_key_id is None:
        raise _unauthorized()

    cache = _get_status_cache(request)
    cached = cache.get(claims.tenant_id, site_key_id)
    if cached is None:
        tenant_status, site_key_status = await _fetch_current_statuses(
            session, claims.tenant_id, site_key_id
        )
        cached = cache.set(claims.tenant_id, site_key_id, tenant_status, site_key_status)

    if cached.tenant_status != "active" or cached.site_key_status not in _ACTIVE_SITE_KEY_STATUSES:
        raise _unauthorized()

    return VerifiedIdentity(tenant_id=claims.tenant_id, vid=claims.vid, org=claims.origin)
