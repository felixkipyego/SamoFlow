# backend/app/admin/dependencies.py
#
# ============================================================================
# TEMPORARY. Task 2.2.f builds the ONLY admin-identity mechanism this
# project has until Step 6.1 replaces it outright with a real
# `platform_admin` role, using the same login system tenant users use
# (docs/SPEC.md §12). This module is a deliberate stopgap, not the start of
# a real admin-auth system to extend: it has no concept of WHICH admin
# acted, only that A valid admin key was presented (see AdminKeyVerified's
# own docstring). Do not add roles, scopes, or per-admin identity here --
# that is Step 6.1's job, done properly, with a real users/memberships
# table behind it. See PROJECT_SPEC.md's own Open marker for this.
# ============================================================================
#
# docs/SPEC.md §12: "during Phases 2 to 5 the admin endpoints... are
# protected by a single secret from the environment." This resolves a real
# conflict between that line and this project's own prior practice (every
# admin endpoint treated as Phase-6-only) -- see PROJECT_SPEC.md's Step 2.2
# breakdown, decision (d), for the full reasoning on why docs/SPEC.md wins
# here, narrowly, for exactly this one mechanism.
#
# Lives in app/admin/, not app/auth/: this is the first real (non-stub)
# content in this package, directly ahead of 2.2.g's own real admin
# endpoint here (revoke_domain()'s own route) -- app/auth/ is specifically
# the WIDGET's own visitor/session system (JWT-based), a different surface
# entirely. Keeping the two apart organizationally reinforces what the
# tests below prove structurally: a widget JWT and an admin key are
# genuinely separate, non-overlapping credentials, never mistakable for
# one another.
#
# This task builds ONLY this dependency. It is not wired to any route --
# not revoke_domain() (2.2.g's own job), not any other admin endpoint.
import hmac
from dataclasses import dataclass

from fastapi import HTTPException, Request

from app.config import get_settings

# A dedicated header, not Authorization: Bearer -- deliberately so a
# correctly-formed widget JWT (which always arrives via Authorization) is
# never even looked at by this dependency, and an admin key placed here is
# never looked at by get_current_visitor() (app/auth/dependencies.py),
# which only ever reads Authorization. Two different header names is what
# makes "structurally different, non-overlapping mechanisms" true by
# construction, not just by differently-worded failure messages.
_ADMIN_KEY_HEADER = "X-Admin-Key"

# Deliberately a different message from app.auth.dependencies._FAILURE_DETAIL
# ("session could not be verified") -- a different string, a different
# function, a different file: there is no shared code path between this
# failure and a widget JWT failure for anything to coincidentally collide
# on.
_FAILURE_DETAIL = "admin key could not be verified"


@dataclass(frozen=True)
class AdminKeyVerified:
    """Marker returned by require_admin_key() on success. Deliberately
    EMPTY -- carries no identity at all, unlike auth.dependencies.
    VerifiedIdentity (tenant_id, vid, org). The temporary admin-key
    mechanism has no concept of WHICH admin acted, only that *a* valid
    admin key was presented; there is no users/memberships table behind
    it to name one. Step 6.1's real platform_admin role will carry a real
    identity -- do not retrofit one onto this marker instead of replacing
    it outright.
    """


def _admin_unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail=_FAILURE_DETAIL)


def require_admin_key(request: Request) -> AdminKeyVerified:
    """FastAPI dependency: the one place this project ever checks the
    temporary admin key. No I/O (no database, nothing async) -- purely an
    in-memory header comparison, so this is a plain sync function, not
    async def; FastAPI runs sync dependencies correctly either way, and
    there is no blocking call here for the "everything is async" rule to
    apply to.

    A missing header and a wrong key both produce the identical 401 (no
    oracle, matching this project's own established discipline elsewhere
    for a failure's shape) -- comparison is hmac.compare_digest()
    (constant-time), matching app.auth.secrets.verify_visitor_secret()'s
    own precedent for comparing a presented secret against a configured
    one.
    """
    presented = request.headers.get(_ADMIN_KEY_HEADER)
    if presented is None or not hmac.compare_digest(presented, get_settings().admin_api_key_str()):
        raise _admin_unauthorized()
    return AdminKeyVerified()
