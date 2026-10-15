# backend/app/dashboard/csrf.py
# Step 6.1 [SECURITY]: CSRF protection on every state-changing dashboard
# request (this task's own direct instruction).
#
# Design: the plain "double-submit cookie" pattern (OWASP CSRF Prevention
# Cheat Sheet) -- a random token is set as a readable (non-HttpOnly)
# cookie, and every state-changing request must echo the SAME value in a
# custom header. A cross-site attacker cannot forge a matching pair: they
# cannot READ the cookie's value (browser same-origin policy on
# document.cookie/fetch response bodies), and a plain cross-site form
# submission cannot ADD a custom header at all -- only `fetch`/XHR from
# same-origin JavaScript can read the cookie and set the header together.
#
# NOT bound to the session server-side (no csrf_token_hash column, no
# extra query on every request): the double-submit property above does
# not depend on server-side state to hold, and what WOULD defeat a plain
# (unbound) double-submit cookie -- an attacker who can set/overwrite the
# cookie for this exact origin themselves (cookie tossing from a sibling
# subdomain, or any non-HttpOnly-cookie-writing bug) -- is independently
# closed by giving this cookie the __Host- prefix too: __Host- cookies
# are scoped to the exact host with no Domain attribute, so a sibling
# subdomain (the task's own named example, pm.softwareske.com) cannot set
# one for dashboard.softwareske.com no matter what it tries. Simplest
# correct design (rule 11) -- a server-side-bound synchronizer token would
# buy no additional real protection here, only an extra DB column and an
# extra query per state-changing request.
#
# Issued by GET /api/v1/dashboard/auth/csrf (app/dashboard/routes.py),
# called once by the SPA before it ever renders the login form -- this
# closes the "login CSRF" gap too (forcing a victim to authenticate into
# an attacker's own account): login itself requires this header/cookie
# pair to match, exactly like every other state-changing dashboard
# request, not a special-cased exemption for the one endpoint that
# creates the first real session.
import hmac
import secrets

from fastapi import HTTPException, Request, Response

CSRF_COOKIE_NAME = "__Host-csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"

# Matches generate_visitor_secret()'s own entropy (32 raw bytes / 256
# bits, app/auth/secrets.py) -- not reused directly only because this
# value is never hashed/stored, so there is nothing to gain from sharing
# that function's own hashing-oriented shape; secrets.token_urlsafe() is
# the same stdlib primitive either way.
_TOKEN_BYTES = 32

_FAILURE_DETAIL = "CSRF token missing or invalid"


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def set_csrf_cookie(response: Response, token: str) -> None:
    # Secure, Path=/, no Domain (the __Host- prefix's own three
    # requirements) -- Secure cookies ARE accepted by browsers over plain
    # HTTP for http://localhost/http://127.0.0.1 specifically (the W3C
    # Secure Contexts spec's own loopback allowance), so local dev needs
    # no downgrade here. Deliberately NOT HttpOnly: the double-submit
    # pattern requires same-origin JavaScript to be able to read this
    # value and echo it back in the header above.
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        path="/",
        secure=True,
        httponly=False,
        samesite="strict",
    )


def require_csrf(request: Request) -> None:
    """Call at the top of every state-changing dashboard route handler,
    before any database work. Raises 403 if the header is missing, the
    cookie is missing, or the two do not match exactly (constant-time
    comparison -- a timing side channel on a 256-bit random token is not
    a realistic path to forging it, but compare_digest costs nothing
    extra and matches this project's own established convention for
    every other secret-ish comparison, e.g. app.admin.dependencies.
    require_admin_key()).
    """
    header_value = request.headers.get(CSRF_HEADER_NAME)
    cookie_value = request.cookies.get(CSRF_COOKIE_NAME)
    if (
        header_value is None
        or cookie_value is None
        or not hmac.compare_digest(header_value, cookie_value)
    ):
        raise HTTPException(status_code=403, detail=_FAILURE_DETAIL)
