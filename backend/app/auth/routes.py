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
# CORSMiddleware: preflight (OPTIONS, below) answers without touching the
# database at all -- it has no Depends(get_db_session) in its signature, so
# it structurally cannot query anything, and it does not read the request
# body (site_key/visitor_secret are never parsed for OPTIONS). A real
# response only ever carries Access-Control-Allow-Origin once the origin
# check has actually passed; Vary: Origin is present on every response
# (success, failure, or preflight) regardless of outcome.
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.origin import origin_is_allowed
from app.auth.schemas import SessionRequest, SessionResponse
from app.auth.secrets import generate_visitor_secret, hash_visitor_secret
from app.auth.tokens import encode_session_token
from app.db import get_db_session
from app.tenancy.repository import TenantScopedRepository, get_site_key_by_key

router = APIRouter(prefix="/api/v1")

# One fixed body/status for every failure category -- constructed once,
# reused identically everywhere, so there is no risk of two call sites
# drifting into two subtly different "failure" responses over time.
_FAILURE_BODY = {"detail": "session request could not be completed"}
_FAILURE_STATUS = 403

# docs/SPEC.md §4.2 (session flow, point 5) / §5 "Widget look": these six
# fields are the only ones ever public. tenants.config also holds several
# PRIVATE fields (escalation_email, relevance_threshold, support details,
# retention_days, trace_text_enabled -- docs/SPEC.md §4.1's own data-model
# table) that must never reach this response.
# ASSUMPTION: docs/SPEC.md names these fields by description, not by a
# fixed JSON key -- no prior task defines tenants.config's actual key
# names (the dashboard, which will actually write them, is a later phase),
# so these six key names are this task's own choice, not a spec-mandated
# schema.
_PUBLIC_WIDGET_CONFIG_FIELDS = (
    "title",
    "greeting",
    "colors",
    "logo_url",
    "position",
    "privacy_notice_url",
)


def _public_widget_config(config: dict) -> dict:
    # Picks named keys one at a time -- never spreads/passes through the
    # raw dict -- so a private key structurally cannot leak here regardless
    # of what tenant.config actually holds.
    return {field: config[field] for field in _PUBLIC_WIDGET_CONFIG_FIELDS if field in config}


def _failure_response() -> JSONResponse:
    return JSONResponse(
        status_code=_FAILURE_STATUS,
        content=_FAILURE_BODY,
        headers={"Vary": "Origin"},
    )


@router.options("/session", include_in_schema=False)
async def session_preflight(request: Request) -> Response:
    # No Depends(get_db_session), no body parsing: this handler cannot
    # touch the database or inspect site_key/visitor_secret even if it
    # wanted to. It only tells the browser an actual POST to this origin,
    # with these methods/headers, would be allowed to be ATTEMPTED --
    # actual authorization happens on the real POST request itself, above.
    origins = request.headers.getlist("origin")
    headers = {
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        "Vary": "Origin",
    }
    if len(origins) == 1:
        headers["Access-Control-Allow-Origin"] = origins[0]
    return Response(status_code=200, headers=headers)


@router.post("/session", response_model=SessionResponse)
async def create_session(
    payload: SessionRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008 (FastAPI's own Depends() idiom)
) -> Response:
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
    await session.commit()

    body = SessionResponse(
        session_token=token,
        visitor_secret=new_secret,
        test_mode=test_mode,
        widget_config=_public_widget_config(tenant.config),
    )
    return JSONResponse(
        status_code=200,
        content=body.model_dump(),
        headers={"Access-Control-Allow-Origin": origin, "Vary": "Origin"},
    )
