# backend/app/auth/cors.py
# [SECURITY] Step 7-follow-up: ONE real CORS mechanism for every
# visitor-facing (widget) endpoint -- closes D-chat-own-error-paths-cors-
# gap, the highest production risk the prior widget bugfix batch flagged
# (docs/DECISIONS_REGISTER.md). Supersedes that batch's own per-route
# `@router.options(...)` handlers (session_preflight/chat_preflight/
# conversations_preflight/conversation_messages_preflight/
# escalate_preflight, and the inline `response.headers[...] = ...`
# calls on each success/error branch) -- all five are removed; this
# middleware is now the only place that ever writes a CORS header.
#
# Design, and why a middleware rather than a path-scoped router:
#
# 1. PREFLIGHT (OPTIONS) needs no per-site-key knowledge at all -- its
#    only job is telling the browser whether the real request's method/
#    headers would be ATTEMPTED, never an authorization decision (the
#    browser does not trust a preflight's own Access-Control-Allow-
#    Origin as an allow/deny verdict; it only uses the preflight to
#    decide whether to send the real request, then independently checks
#    THAT response's own header). So the preflight branch below echoes
#    the raw, single Origin header unconditionally, exactly like
#    /session's and /chat's own prior preflight handlers already did --
#    this is standard, spec-conformant CORS behaviour, not a weaker
#    check than the real-request path below.
#
# 2. The REAL request's response is where the actual decision lives, and
#    that decision is inherently per-site-key (POST /session) or
#    per-token (every other endpoint, via VerifiedIdentity.org, already
#    checked against the real Origin header by get_current_visitor() --
#    see app/auth/dependencies.py) -- information only each HANDLER has,
#    since only it has done the real DB/JWT lookup. A middleware running
#    BEFORE the handler cannot make this decision without re-implementing
#    that lookup a second, independent time (exactly the kind of
#    duplicated-security-logic this project's own rules forbid). So the
#    handler, and ONLY the handler, decides -- by setting
#    `request.state.cors_allowed_origin` the moment it has verified the
#    request's real Origin header is genuinely this request's own origin
#    (never a static site-key allow-list lookup repeated here) -- and
#    this middleware's only job on the way back out is to attach the
#    header IF that was set, to WHATEVER response comes back, success or
#    error, without the handler ever having to remember to add a header
#    on each individual return statement. This is the actual fix for the
#    flagged risk: a developer adding a 7th error branch to /chat cannot
#    forget the CORS header, because there is no per-branch header to
#    forget -- the middleware applies it uniformly to every branch after
#    the one line that sets `request.state.cors_allowed_origin`.
#
# 3. A response is left with NO Access-Control-Allow-Origin at all
#    whenever the handler never reached (or deliberately never reached)
#    that line -- a 401 (invalid token, or a token whose own `org` claim
#    does not match the real Origin header -- app/auth/dependencies.py's
#    own origin-vs-org check), a 413 (oversized message, checked BEFORE
#    identity resolves, by deliberate design -- see app/chat/routes.py's
#    own comment on check ordering), and a 422 (malformed request body,
#    raised by FastAPI's own validation before the handler body runs)
#    all fall into this bucket. This is the SAFE default, not a gap left
#    open: in every one of these cases, there is no verified relationship
#    yet between the real Origin header and any real site key/tenant, so
#    adding a header would mean either breaking /session's own
#    deliberate no-oracle design (Step 1.4 breakdown decision 7 -- see
#    below) or inventing an unverified guess. Confirmed as a real,
#    narrow, DELIBERATE limitation, not silently dropped -- see
#    PROJECT_SPEC.md §9's own matching marker for the small number of
#    real-world cases this leaves uncovered and why fixing them would
#    need a different, bigger design question (re-deriving "is this
#    origin allowed for ANY site key" independently of a valid token).
#
# 4. /session's own FAILURE path (`_failure_response()`,
#    app/auth/routes.py) is a deliberate exception to "every named
#    branch gets covered": Step 1.4 breakdown decision (7) made every
#    one of its four distinct failure reasons (unknown site key,
#    disallowed origin, suspended site key, non-active tenant) return
#    the IDENTICAL byte-for-byte response, specifically so none can be
#    told apart from any other -- a deliberate no-oracle design. Adding
#    Access-Control-Allow-Origin to only SOME of those four reasons
#    (e.g. "allowed origin, but an otherwise-unknown site key") would
#    itself become a NEW oracle (an attacker could fuzz site keys and
#    learn, from CORS success/failure alone, which origins are on SOME
#    site key's allow-list) -- so this path is left exactly as it always
#    was: no Access-Control-Allow-Origin on any of its four reasons,
#    ever. This is confirmed correct here, not silently unexamined.
import re
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# The single source of truth for every visitor-facing route's own CORS
# shape -- the methods/headers a preflight for that route may advertise,
# and (via this same list) the complete enumeration PROJECT_SPEC.md's
# own report cites as "every visitor-facing route, before and after."
# `pattern` must match the route's REAL path exactly (anchored both
# ends) -- a route not listed here gets NO CORS handling of any kind
# from this middleware (the dashboard/admin surface, by design, per
# D-6.1-cors-same-origin -- same-origin SPA+API serving needs none).
WIDGET_ROUTES: tuple[tuple[str, re.Pattern[str], str, str], ...] = (
    ("session", re.compile(r"^/api/v1/session$"), "POST, OPTIONS", "Content-Type"),
    ("chat", re.compile(r"^/api/v1/chat$"), "POST, OPTIONS", "Content-Type, Authorization"),
    (
        # Part D (docs/SPEC.md §9's "Stop mid-stream"): the explicit stop
        # signal -- app/chat/routes.py's own `stop_chat_turn()`. No
        # request body, so no Content-Type needed, only Authorization,
        # matching `escalate`'s own identical no-JSON-body-header shape
        # below.
        "chat_stop",
        re.compile(r"^/api/v1/chat/stop$"),
        "POST, OPTIONS",
        "Authorization",
    ),
    (
        "list_conversations",
        re.compile(r"^/api/v1/conversations$"),
        "GET, OPTIONS",
        "Authorization",
    ),
    (
        "conversation_messages",
        re.compile(r"^/api/v1/conversations/[^/]+/messages$"),
        "GET, OPTIONS",
        "Authorization",
    ),
    (
        "escalate",
        re.compile(r"^/api/v1/conversations/[^/]+/escalate$"),
        "POST, OPTIONS",
        "Content-Type, Authorization",
    ),
)


def _match_widget_route(path: str) -> tuple[str, re.Pattern[str], str, str] | None:
    for route in WIDGET_ROUTES:
        if route[1].match(path):
            return route
    return None


def build_preflight_response(request: Request, *, methods: str, headers: str) -> Response:
    # [SECURITY] No Depends(get_db_session), no body parsing: a CORS
    # preflight cannot touch the database or inspect any request body
    # even if it wanted to -- see this module's own header, point 1, for
    # why that is correct CORS behaviour, not a weaker check. The ONE
    # real rule enforced here: the Origin header must be present exactly
    # once (never zero, never repeated) before it is ever echoed back --
    # matching every other endpoint's own well-formedness rule.
    origins = request.headers.getlist("origin")
    response_headers = {
        "Access-Control-Allow-Methods": methods,
        "Access-Control-Allow-Headers": headers,
        "Vary": "Origin",
    }
    if len(origins) == 1:
        response_headers["Access-Control-Allow-Origin"] = origins[0]
    return Response(status_code=200, headers=response_headers)


class WidgetCORSMiddleware(BaseHTTPMiddleware):
    """[SECURITY] The one real CORS mechanism for /session, /chat,
    /conversations, /conversations/{cid}/messages and
    /conversations/{cid}/escalate. See this module's own header for the
    full design and the deliberate cases left without a header.

    Verified live (not assumed) that Starlette's BaseHTTPMiddleware does
    NOT buffer a streaming response in this project's pinned version
    (starlette 1.7.0, confirmed via `import starlette; starlette.
    __version__`): a real uvicorn server (not the in-process ASGITransport
    test client, which DOES buffer and would have given a false pass)
    serving a 10-event, 0.2s-apart SSE generator through this exact
    middleware shape delivered each event incrementally (0.02s, 0.23s,
    0.43s... for the first three), and a client disconnecting after 3 of
    10 events still ran the generator's own `finally` block (the turn
    lease release) -- both proven with a direct timing/cleanup script,
    not inferred from the library's changelog. See PROJECT_SPEC.md's own
    decision-log entry for this task for the full script and output.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        route = _match_widget_route(request.url.path)
        if route is None:
            return await call_next(request)

        _, _pattern, methods, headers = route

        if request.method == "OPTIONS":
            # Never reaches the real route handler at all -- matching
            # every prior preflight handler's own "cannot touch the
            # database" guarantee, now enforced structurally (there is
            # no handler in the call chain for OPTIONS on these paths).
            return build_preflight_response(request, methods=methods, headers=headers)

        response = await call_next(request)

        # "Vary: Origin" is added to EVERY response on a matched widget
        # route, success or error, ACAO or not -- a cache sitting in
        # front of this API must never conflate one Origin's response
        # with another's, regardless of whether this particular branch
        # happened to earn an Access-Control-Allow-Origin header too.
        response.headers["Vary"] = "Origin"

        allowed_origin = getattr(request.state, "cors_allowed_origin", None)
        if allowed_origin is None:
            return response

        # [SECURITY] Defense in depth: never trust a handler-set value
        # blindly -- it must be byte-identical to the REAL request's own
        # Origin header, never a different one (a site key's own
        # allow-list entry, a cached/stale value, or any other mistake).
        # A mismatch here would mean a real bug in a handler, not a
        # legitimate case to paper over, so it is treated the same as
        # "never set": no header added, nothing echoed.
        real_origins = request.headers.getlist("origin")
        if len(real_origins) != 1 or real_origins[0] != allowed_origin:
            return response

        response.headers["Access-Control-Allow-Origin"] = allowed_origin
        # [SECURITY] Never Access-Control-Allow-Credentials: the widget
        # authenticates with a Bearer token held in memory only (docs/
        # SPEC.md §4.3), never a cookie -- none of its fetch() calls set
        # `credentials: "include"` (confirmed by reading session.js/
        # chat-controller.js directly), so the browser never attaches
        # any cookie to a cross-origin request here regardless, and this
        # header is never sent for any of these five routes.
        return response
