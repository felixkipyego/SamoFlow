# backend/app/auth/routes.py
# Task 1.4.g: POST /api/v1/session (docs/SPEC.md §4.2), the first real HTTP
# endpoint in this project. Wires together 1.4.b (JWT), 1.4.c (visitor
# secrets), 1.4.e (site-key/visitor repository lookups) and 1.4.f (origin
# matching).
#
# Failure model (Step 1.4 breakdown decision (7)): unknown site key,
# missing/disallowed origin, a suspended site key, and a non-active tenant
# are ONE failure category -- a single, byte-identical response
# (_FAILURE_RESPONSE below), so none of the four conditions can be told
# apart from any other by response shape (no oracle). A wrong or unknown
# visitor_secret is NOT a failure at all: it is handled identically to no
# secret being sent (a first visit), per the same breakdown's decision (5).
#
# CORS is dynamic per site key (decision (9)), never FastAPI's static
# CORSMiddleware: [SECURITY] Step 7-follow-up moved the actual mechanism
# to app.auth.cors.WidgetCORSMiddleware (one shared implementation for
# every visitor-facing endpoint, not just this one) -- OPTIONS preflight
# answers there without touching the database at all, and a real
# response here only ever carries Access-Control-Allow-Origin once this
# function sets `request.state.cors_allowed_origin`, which happens at
# step (e) below, AFTER the origin check has actually passed, never
# before. Vary: Origin is present on every response (success, failure,
# or preflight) regardless of outcome, added by that same middleware.
#
# Task 1.5.c: two RateLimiter instances (1.5.a), configured from 1.5.b's
# four Settings fields, checked before ANY other work in create_session --
# even before the origin well-formedness check below, since rate limiting
# is at least as cheap as that check and this is the worst-case abuse path
# (a flood); "cheapest first" (docs/SPEC.md §9) taken to its logical start.
# Per-IP is checked FIRST, then (IP, site_key) -- Step 1.5 breakdown
# decision (b): per-IP is the broader, cheaper-to-reject check, and must
# trip on its own for docs/SPEC.md §16's "many new visitors from one IP
# hit the IP cap" scenario even when no individual site_key's own limit is
# anywhere near tripped -- see test_session.py's own 1.5.c tests for the
# constructed proof. Both limiters live on app.state, lazily created via
# _get_per_ip_limiter()/_get_site_key_limiter() below -- the EXACT shape
# app/auth/dependencies.py's _get_status_cache() (Task 1.4.h) already uses
# (getattr-fallback-then-construct-then-store), not eager construction in
# create_app(). This task's own instructions expected the wiring to live in
# create_app() itself "alongside 1.4.h's status_cache setup" -- checked
# first, as asked, and that assumption doesn't hold: status_cache is not
# actually initialized in create_app() either, it is lazily created in
# app/auth/dependencies.py in this exact shape. Following the real,
# verified pattern instead of the assumed one. This also means a fresh
# create_app() call gets fresh, empty limiter state automatically (no
# explicit reset needed between tests), and a test can pre-seed
# app.state.per_ip_limiter/site_key_limiter with its own injectable-clock
# RateLimiter before making any request -- exactly like StatusCache's own
# test convention.
import hashlib
import json
import math

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.origin import origin_is_allowed
from app.auth.schemas import SessionRequest, SessionResponse, WidgetAppearanceResponse
from app.auth.secrets import generate_visitor_secret, hash_visitor_secret
from app.auth.tokens import encode_session_token
from app.client_ip import get_client_ip
from app.config import get_settings
from app.db import get_db_session
from app.ingest.repository import IngestRepository
from app.ratelimit import RateLimiter
from app.request_limits import get_or_create_limiter
from app.tenancy.contrast import is_valid_css_color, pick_readable_text_color, to_hex_color
from app.tenancy.repository import TenantScopedRepository, get_site_key_by_key

router = APIRouter(prefix="/api/v1")

# One fixed body/status for every failure category -- constructed once,
# reused identically everywhere, so there is no risk of two call sites
# drifting into two subtly different "failure" responses over time.
_FAILURE_BODY = {"detail": "session request could not be completed"}
_FAILURE_STATUS = 403

# A NEW, distinct response shape from the 403 failure body above -- never
# reused or extended -- since a rate limit is a different failure category
# (docs/SPEC.md §11's own "rate limited (plain language, no internals)"
# widget state is separately named from the failure states the 403 covers;
# Step 1.5 breakdown decision (d)).
_RATE_LIMITED_STATUS = 429

# docs/SPEC.md §4.2 (session flow, point 5) / §5 "Widget look": these six
# fields are the only ones ever public. tenants.config also holds several
# PRIVATE fields (escalation_email, relevance_threshold, support details,
# retention_days, trace_text_enabled -- docs/SPEC.md §4.1's own data-model
# table) that must never reach this response.
# VERIFIED at Step 5.1 Part C (2026-10-15): the widget track, the first
# real consumer, confirmed these six key names are exactly what Step 5.2
# needs -- see app/auth/schemas.py's own SessionResponse for the full
# verification note (rule 10, "verify then remove ASSUMPTION").
_PUBLIC_WIDGET_CONFIG_FIELDS = (
    "title",
    "greeting",
    "colors",
    "logo_url",
    "position",
    "privacy_notice_url",
)


def _public_widget_config(config: dict, verified_domains: list[str]) -> dict:
    # Picks named keys one at a time -- never spreads/passes through the
    # raw dict -- so a private key structurally cannot leak here regardless
    # of what tenant.config actually holds.
    #
    # Step 6.2, additive (Felix's own pre-authorisation, docs/DECISIONS_
    # REGISTER.md "Backend contract gaps found by the widget track"):
    # `verified_domains` is a NEW key alongside the existing six, never a
    # replacement -- the widget's own renderer (Step 5.2) already works
    # safely without it (citation-only linking) and can widen to also
    # allow these domains once it chooses to read this new key; this
    # response change needs no coordinated widget-side deploy.
    public = {field: config[field] for field in _PUBLIC_WIDGET_CONFIG_FIELDS if field in config}
    public["verified_domains"] = verified_domains
    return public


def _failure_response() -> JSONResponse:
    # No explicit Vary: Origin here -- app.auth.cors.WidgetCORSMiddleware
    # now adds it to every response on this route uniformly, success or
    # failure (see that module's own header, point 4, for why this
    # specific response deliberately never also gets an
    # Access-Control-Allow-Origin header).
    return JSONResponse(status_code=_FAILURE_STATUS, content=_FAILURE_BODY)


def _rate_limited_response(retry_after: float) -> JSONResponse:
    # Retry-After: an integer number of seconds is the conventional form
    # for this header's "delay-seconds" value (RFC 9110 §10.2.3 -- the
    # only alternative form is a full HTTP-date, not needed here), rounded
    # UP (never down, and never to 0) so a client that waits exactly this
    # long never retries a moment too early. The JSON body's own
    # retry_after keeps one decimal place -- more precise than the header
    # for a programmatic caller, without exposing meaningless float noise.
    header_seconds = max(1, math.ceil(retry_after))
    return JSONResponse(
        status_code=_RATE_LIMITED_STATUS,
        content={"detail": "too many requests", "retry_after": round(retry_after, 1)},
        headers={"Retry-After": str(header_seconds)},
    )


def _site_key_rate_limit_key(ip: str, site_key: str) -> str:
    # site_key is fully client-controlled (SessionRequest.site_key has no
    # charset/length constraint) and this combined string becomes a single
    # RateLimiter key for the (ip, site_key) pair. Naive concatenation
    # (f"{ip}:{site_key}", or even ip + site_key with no separator at all)
    # would let a crafted site_key value collide two DIFFERENT (ip,
    # site_key) pairs onto the same key string -- e.g. ip="1.2.3.4",
    # site_key="5:x" and ip="1.2.3.4:5", site_key="x" would both produce
    # "1.2.3.4:5:x" -- letting one visitor's requests silently share, or
    # poison, another's rate-limit counter. Length-prefixing the IP first
    # (a netstring-style encoding) makes this provably injective regardless
    # of what characters either part contains: two different (ip,
    # site_key) pairs can never produce the same encoded string, since the
    # length prefix fixes exactly how many of the following characters
    # belong to ip.
    return f"{len(ip)}:{ip}:{site_key}"


def _get_per_ip_limiter(request: Request) -> RateLimiter:
    settings = request.app.state.settings
    return get_or_create_limiter(
        request,
        "per_ip_limiter",
        settings.session_rate_limit_per_ip,
        settings.session_rate_limit_per_ip_window_seconds,
    )


def _get_site_key_limiter(request: Request) -> RateLimiter:
    settings = request.app.state.settings
    return get_or_create_limiter(
        request,
        "site_key_limiter",
        settings.session_rate_limit_per_site_key,
        settings.session_rate_limit_per_site_key_window_seconds,
    )


@router.post("/session", response_model=SessionResponse)
async def create_session(
    payload: SessionRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008 (FastAPI's own Depends() idiom)
) -> Response:
    # Rate limiting first, before anything else in this handler (see this
    # module's own header comment for the full reasoning and the exact key
    # formats). get_client_ip() handles the "no request.client" case
    # itself, grouping it under "unknown" rather than crashing.
    ip = get_client_ip(request, get_settings())

    per_ip_limiter = _get_per_ip_limiter(request)
    if not per_ip_limiter.check(ip):
        return _rate_limited_response(per_ip_limiter.get_retry_after(ip))

    site_key_limiter = _get_site_key_limiter(request)
    site_key_limit_key = _site_key_rate_limit_key(ip, payload.site_key)
    if not site_key_limiter.check(site_key_limit_key):
        return _rate_limited_response(site_key_limiter.get_retry_after(site_key_limit_key))

    # (a) Origin must be present exactly once (a missing or repeated
    # Origin header is rejected here, before any database access;
    # well-formedness/allow-list matching is 1.4.f's job, at step (e)).
    origins = request.headers.getlist("origin")
    if len(origins) != 1:
        return _failure_response()
    origin = origins[0]

    # (b) Unknown site key.
    site_key = await get_site_key_by_key(session, payload.site_key)
    if site_key is None:
        return _failure_response()

    # (c) Suspended site key (draft and live both proceed).
    if site_key.status == "suspended":
        return _failure_response()

    # (d) Non-active tenant.
    repo = TenantScopedRepository(tenant_id=site_key.tenant_id, session=session)
    tenant = await repo.get_tenant()
    if tenant.status != "active":
        return _failure_response()

    # (e) Origin must match this site key's allow-list.
    if not origin_is_allowed(origin, site_key.allowed_origins):
        return _failure_response()

    # [SECURITY] This is the ONE point where this request's real Origin
    # header is positively verified against a real site key's own
    # allow-list -- app.auth.cors.WidgetCORSMiddleware echoes exactly
    # this value back on this response (and only this response: every
    # `_failure_response()` branch above deliberately never reaches this
    # line, by design -- see app/auth/cors.py's own header, point 4, for
    # why that is correct, not an oversight).
    request.state.cors_allowed_origin = origin

    test_mode = site_key.status == "draft"

    # (f)/(g): a wrong or absent secret is handled identically -- both are
    # a first visit (decision (5): "a wrong or unknown secret behaves
    # exactly like no secret at all").
    visitor = None
    if payload.visitor_secret is not None:
        visitor = await repo.get_visitor_by_secret(payload.visitor_secret, site_key_id=site_key.id)

    new_secret: str | None = None
    if visitor is None:
        new_secret = generate_visitor_secret()
        visitor = await repo.create_visitor(
            site_key_id=site_key.id, secret_hash=hash_visitor_secret(new_secret)
        )

    token = encode_session_token(tenant_id=site_key.tenant_id, vid=visitor.vid, origin=origin)

    ingest_repo = IngestRepository(tenant_id=site_key.tenant_id, session=session)
    verified_domains = [
        domain.domain for domain in await ingest_repo.list_domains() if domain.status == "verified"
    ]

    await session.commit()

    body = SessionResponse(
        session_token=token,
        visitor_secret=new_secret,
        test_mode=test_mode,
        widget_config=_public_widget_config(tenant.config, verified_domains),
    )
    return JSONResponse(status_code=200, content=body.model_dump())


# --- [SECURITY] Step 7-follow-up, Part C: GET /api/v1/widget-appearance ----
#
# The real fix for "the launcher shows the wrong color/position until the
# visitor's first click" (docs/DECISIONS_REGISTER.md's own register row for
# this task): the LOADER itself (widget/loader/src/widget.js) deliberately
# never calls the backend at all (Step 5.1's own "lazy, tiny, no network
# before the launcher click" decision) -- so before this endpoint existed,
# the launcher's own brand color/position were only ever learned once the
# chat bundle lazily loaded and called POST /session, which only happens
# AFTER the visitor's first click. Most visitors never click at all, so
# this was a real, continuous branding defect for every paying tenant, not
# a cosmetic one-time flash.
#
# [SECURITY] This deliberately SUPERSEDES the Step 5.1 "no network before
# the launcher click" decision (rule 21: cited, not silently overridden) --
# Felix's own direct instruction for this task authorises exactly this.
# It does NOT reopen Step 5.1's own underlying reasoning (keeping the
# loader tiny, and not creating a visitor/session on every page view): this
# endpoint creates no visitor, no session, no conversation and makes no LLM
# call -- it is a single, cheap, cacheable SELECT, a fundamentally
# different cost profile from POST /session, which is why it is a new,
# separate, PUBLIC endpoint rather than a change to /session itself.
#
# Deliberately NOT origin-restricted (unlike every route app.auth.cors.
# WidgetCORSMiddleware covers): this response carries no tenant secret and
# no visitor-specific data at all -- only the SAME primary color/position
# any visitor to the tenant's own real site could already see just by
# looking at it. Restricting it to one origin would add a security
# property this data does not need, while making it impossible for the
# loader itself (running on the TENANT's own site, not SamoFlow's) to ever
# read it without a widened, per-tenant allow-list this endpoint has no
# reason to need. Access-Control-Allow-Origin: "*" is the correct, standard
# CORS answer for genuinely public, non-credentialed, cacheable GET data --
# the one deliberate exception to "never a wildcard" elsewhere in this
# codebase's own newer CORS work, stated plainly, not a silent
# inconsistency. No preflight is ever sent for this route either (a plain
# GET with no custom request headers is a CORS "simple request" by
# definition), so no OPTIONS handler exists for it.
_APPEARANCE_NOT_FOUND_STATUS = 404
_APPEARANCE_NOT_FOUND_BODY = {"detail": "not found"}
_DEFAULT_APPEARANCE_PRIMARY = "#4f46e5"  # widget/loader/src/widget.css's own literal default
_VALID_POSITIONS = frozenset({"left", "right"})


def _get_appearance_per_ip_limiter(request: Request) -> RateLimiter:
    settings = request.app.state.settings
    return get_or_create_limiter(
        request,
        "appearance_per_ip_limiter",
        settings.widget_appearance_rate_limit_per_ip,
        settings.widget_appearance_rate_limit_per_ip_window_seconds,
    )


def _get_appearance_site_key_limiter(request: Request) -> RateLimiter:
    settings = request.app.state.settings
    return get_or_create_limiter(
        request,
        "appearance_site_key_limiter",
        settings.widget_appearance_rate_limit_per_site_key,
        settings.widget_appearance_rate_limit_per_site_key_window_seconds,
    )


def _appearance_not_found() -> JSONResponse:
    # [SECURITY] One fixed body/status, exactly like `_failure_response()`
    # above, and for the identical reason: an unknown site key, a
    # suspended one, and a non-active tenant must be indistinguishable --
    # no oracle for enumerating valid site keys by watching which ones
    # return real data versus an error.
    return JSONResponse(
        status_code=_APPEARANCE_NOT_FOUND_STATUS, content=_APPEARANCE_NOT_FOUND_BODY
    )


@router.get("/widget-appearance", response_model=WidgetAppearanceResponse)
async def get_widget_appearance(
    site_key: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> Response:
    ip = get_client_ip(request, get_settings())

    per_ip_limiter = _get_appearance_per_ip_limiter(request)
    if not per_ip_limiter.check(ip):
        return _rate_limited_response(per_ip_limiter.get_retry_after(ip))

    site_key_limiter = _get_appearance_site_key_limiter(request)
    site_key_limit_key = _site_key_rate_limit_key(ip, site_key)
    if not site_key_limiter.check(site_key_limit_key):
        return _rate_limited_response(site_key_limiter.get_retry_after(site_key_limit_key))

    # [SECURITY] Named, not silently left, timing-class asymmetry (PART
    # 0 audit, 2026-10-16): body and status are byte-identical across
    # all three failure reasons (test_widget_appearance.py's own
    # `test_unknown_suspended_or_inactive_tenant_all_get_the_identical_
    # generic_response`), but this branch and the suspended/unknown one
    # above do NOT cost the same number of queries -- an inactive-tenant
    # request does a SECOND query (get_tenant(), below) a merely-unknown
    # or merely-suspended site key never reaches. This is the exact same
    # shape `/session`'s own `create_session()` has for its own
    # identical four-reasons-one-response design (step (d) there also
    # does one more query than steps (b)/(c)) -- not a new gap this
    # endpoint introduces, and not fixed here: equalizing query count
    # (or deliberately padding latency) is its own fragile piece of
    # work that would need doing for BOTH endpoints together, and a
    # single extra local Postgres round-trip is a weak signal against
    # real network jitter for any remote attacker. Flagged in the
    # register rather than quietly assumed away.
    row = await get_site_key_by_key(session, site_key)
    if row is None or row.status == "suspended":
        return _appearance_not_found()

    repo = TenantScopedRepository(tenant_id=row.tenant_id, session=session)
    tenant = await repo.get_tenant()
    if tenant.status != "active":
        return _appearance_not_found()

    colors = tenant.config.get("colors")
    raw_primary = colors.get("primary") if isinstance(colors, dict) else None
    primary_hex = (
        to_hex_color(raw_primary)
        if isinstance(raw_primary, str) and is_valid_css_color(raw_primary)
        else None
    )
    primary = primary_hex or _DEFAULT_APPEARANCE_PRIMARY
    primary_text = pick_readable_text_color(primary)

    raw_position = tenant.config.get("position")
    position = raw_position if raw_position in _VALID_POSITIONS else "right"

    body = WidgetAppearanceResponse(primary=primary, primary_text=primary_text, position=position)
    payload = body.model_dump()

    # [SECURITY] Step 7-follow-up, Part C: cacheable by the VISITOR'S OWN
    # browser -- this is what makes "no extra request when the cache is
    # fresh" true using the browser's own native HTTP cache, not any
    # bespoke client-side mechanism. A short max-age (not a long one) is
    # the deliberate choice: a dashboard color/position change must show
    # up again within minutes, not be stuck behind an hours-long cache.
    etag = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
    headers = {
        "Cache-Control": "public, max-age=120",
        "ETag": f'"{etag}"',
        "Access-Control-Allow-Origin": "*",
    }

    if request.headers.get("if-none-match") == f'"{etag}"':
        return Response(status_code=304, headers=headers)

    return JSONResponse(status_code=200, content=payload, headers=headers)
