# backend/app/handoff/routes.py
# Step 4.4 [SECURITY]: POST /api/v1/conversations/{cid}/escalate (docs/
# SPEC.md §7). Replaces the Step 1.1b stub (`raise NotImplementedError`).
#
# docs/SPEC.md §7: "The AI cannot submit anything. The visitor taps the
# button... and the visitor's browser submits POST .../escalate as a
# normal authenticated request." This endpoint is the ONLY way an
# `escalations` row is ever created -- the graph's own Coordinator node
# (app/agent/graph.py) only ever OFFERS the button, never calls this
# itself.
#
# [SECURITY] Email header injection: none of `contact_name`/
# `contact_email`/`contact_phone` is EVER placed into an email header.
# The recipient is always `tenant.config["escalation_email"]` (never
# visitor-supplied -- docs/SPEC.md §7: "never to a visitor-supplied
# one"); the subject below is a fixed, server-written string plus this
# ALREADY-VALIDATED `cid` (a real UUID, not raw visitor text). All three
# contact fields appear ONLY in the HTML-escaped email BODY (see
# _render_escalation_email_html() below). This is the real, structural
# defense -- app/handoff/schemas.py's own control-character rejection on
# these fields is a SECOND, independent layer (stdlib's own
# `email.message.EmailMessage` additionally raises `ValueError` on any
# header value containing CR/LF, confirmed live -- see that module's
# own header comment), not the only one.
import html
import logging
import math
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.entrypoint import read_state
from app.auth.dependencies import VerifiedIdentity, get_current_visitor
from app.db import get_db_session
from app.handoff.models import Escalation
from app.handoff.schemas import EscalationRequest, EscalationResponse
from app.notifications.service import send_email
from app.request_limits import get_or_create_limiter
from app.tenancy.repository import TenantScopedRepository
from app.text_escaping import markdown_unescape

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

_RATE_LIMITED_STATUS = 429
_CONFLICT_STATUS = 409
# docs/SPEC.md §7: every escalation today is visitor-button-triggered --
# see app/handoff/models.py's own `reason` column comment for why this
# is a fixed value, not visitor-supplied freeform text.
_REASON_VISITOR_REQUESTED = "visitor_requested"
# docs/SPEC.md §7: "escalations don't count against the AI message
# quota" -- status always starts "new" (app/handoff/models.py's own
# server_default), never set here.
_STATUS_NEW = "new"
_OPEN_STATUSES = ("new", "seen")


def _rate_limited_response(retry_after: float) -> JSONResponse:
    # Same shape as app/chat/routes.py's own _rate_limited_response() --
    # a 2nd occurrence, not a 3rd (that one stays private to its own
    # module; see app/request_limits.py's own header comment for the
    # extraction this project's duplication-check discipline DID require
    # once a third copy of THAT helper was needed).
    header_seconds = max(1, math.ceil(retry_after))
    return JSONResponse(
        status_code=_RATE_LIMITED_STATUS,
        content={"detail": "too many requests", "retry_after": round(retry_after, 1)},
        headers={"Retry-After": str(header_seconds)},
    )


def _get_per_visitor_limiter(request: Request):
    settings = request.app.state.settings
    return get_or_create_limiter(
        request,
        "escalation_per_visitor_limiter",
        settings.escalation_rate_limit_per_visitor,
        settings.escalation_rate_limit_window_seconds,
    )


def _get_per_tenant_email_limiter(request: Request):
    settings = request.app.state.settings
    return get_or_create_limiter(
        request,
        "escalation_tenant_email_limiter",
        settings.escalation_tenant_hourly_email_cap,
        # A real rolling hour, matching the cap's own name
        # ("...hourly_email_cap") -- not reusing escalation_rate_limit_
        # window_seconds, a DIFFERENT policy's own window that happens
        # to share the same value today but has no reason to stay tied
        # to it.
        3600.0,
    )


async def _conversation_messages(
    request: Request, identity: VerifiedIdentity, cid: uuid.UUID
) -> list[BaseMessage]:
    graph = request.app.state.graph
    snapshot = await read_state(graph, identity, cid)
    return list(snapshot.values.get("messages", []))


def _plain_transcript(messages: list[BaseMessage]) -> str:
    """The STORED `escalations.transcript` snapshot -- EXACTLY as
    persisted in the checkpoint, no transformation at all (see app/
    handoff/models.py's own `transcript` column comment for why: escaping
    is an output-time concern for whichever renderer displays this
    later, never baked in at write time).
    """
    lines = []
    for message in messages:
        if isinstance(message, HumanMessage):
            lines.append(f"Visitor: {message.content}")
        elif isinstance(message, AIMessage):
            lines.append(f"Assistant: {message.content}")
    return "\n\n".join(lines)


def _render_escalation_email_html(
    messages: list[BaseMessage],
    contact_name: str | None,
    contact_email: str | None,
    contact_phone: str | None,
) -> str:
    """[SECURITY] The HTML email body. Markdown-unescape THEN HTML-escape,
    in that fixed order, applied ONLY to assistant lines (the ONLY text
    `_sanitize_answer_text()` ever escaped in the first place --
    applying `markdown_unescape()` to a visitor's own raw message would
    be meaningless at best and could mangle a visitor's own literal
    backslash usage at worst, see app/text_escaping.py's own docstring).
    Visitor lines, and every contact field, are HTML-escaped directly --
    never passed through `markdown_unescape()` at all. Every piece of
    visitor-controlled text (message content, contact fields) is
    `html.escape()`-d before being placed in this HTML string -- the
    real XSS defense this function exists to apply; see PROJECT_SPEC.md's
    own adversarial-pass test proving a `<script>`-shaped contact name
    or message renders as inert escaped text, never live markup.
    """
    parts = ["<h2>New escalation request</h2>"]
    if contact_name:
        parts.append(f"<p><strong>Name:</strong> {html.escape(contact_name)}</p>")
    if contact_email:
        parts.append(f"<p><strong>Email:</strong> {html.escape(contact_email)}</p>")
    if contact_phone:
        parts.append(f"<p><strong>Phone:</strong> {html.escape(contact_phone)}</p>")
    parts.append("<h3>Transcript</h3>")
    for message in messages:
        if isinstance(message, HumanMessage):
            safe_text = html.escape(str(message.content))
            parts.append(f"<p><strong>Visitor:</strong> {safe_text}</p>")
        elif isinstance(message, AIMessage):
            safe_text = html.escape(markdown_unescape(str(message.content)))
            parts.append(f"<p><strong>Assistant:</strong> {safe_text}</p>")
    return "\n".join(parts)


@router.post("/conversations/{cid}/escalate", response_model=EscalationResponse)
async def submit_escalation(
    cid: uuid.UUID,
    payload: EscalationRequest,
    request: Request,
    identity: VerifiedIdentity = Depends(get_current_visitor),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> EscalationResponse | JSONResponse:
    """docs/SPEC.md §7. [SECURITY] Mirrors app/chat/routes.py's own
    `get_conversation_messages()` for the cross-tenant/cross-visitor
    404 shape EXACTLY -- the identical attack surface (another
    visitor's cid same tenant; another tenant's cid; a cid that does
    not exist at all) must be indistinguishable here too, proven by
    `tests/test_handoff_routes.py`'s own direct side-by-side response
    comparison, not three separate "it's 404" assertions trusted to
    match.
    """
    repo = TenantScopedRepository(tenant_id=identity.tenant_id, session=session)
    conversation = await repo.get_conversation_by_id(cid, vid=identity.vid)
    if conversation is None or conversation.vid != identity.vid:
        raise HTTPException(status_code=404, detail="conversation not found")

    limiter = _get_per_visitor_limiter(request)
    limiter_key = str(identity.vid)
    if not limiter.check(limiter_key):
        return _rate_limited_response(limiter.get_retry_after(limiter_key))

    # docs/SPEC.md §7: "one open escalation per conversation".
    existing_open = (
        await session.execute(
            select(Escalation.id).where(
                Escalation.conversation_id == cid,
                Escalation.status.in_(_OPEN_STATUSES),
            )
        )
    ).scalar_one_or_none()
    if existing_open is not None:
        return JSONResponse(
            status_code=_CONFLICT_STATUS,
            content={"detail": "an escalation is already open for this conversation"},
        )

    messages = await _conversation_messages(request, identity, cid)
    transcript = _plain_transcript(messages)

    escalation = Escalation(
        tenant_id=identity.tenant_id,
        vid=identity.vid,
        conversation_id=cid,
        reason=_REASON_VISITOR_REQUESTED,
        contact_name=payload.contact_name,
        contact_email=payload.contact_email,
        contact_phone=payload.contact_phone,
        transcript=transcript,
        status=_STATUS_NEW,
    )
    session.add(escalation)
    await session.commit()

    await _attempt_notification_email(
        request, repo, cid, messages, payload, escalation_id=escalation.id
    )

    return EscalationResponse(escalation_id=escalation.id, status=escalation.status)


async def _attempt_notification_email(
    request: Request,
    repo: TenantScopedRepository,
    cid: uuid.UUID,
    messages: list[BaseMessage],
    payload: EscalationRequest,
    *,
    escalation_id: uuid.UUID,
) -> None:
    """Best-effort: a failure here must never fail the escalation
    request itself -- the row is already committed (docs/SPEC.md §7:
    the record "also appears in the dashboard inbox" regardless of
    whether the email could be sent), matching app/chat/routes.py's own
    `_release_lease()` "log, never raise further" precedent.

    [SECURITY] Tenant scoping: `tenant.config.get("escalation_email")`
    is read from THIS conversation's OWN tenant (`repo`, already scoped
    to `identity.tenant_id` by construction) -- an escalation can
    therefore structurally never notify any tenant but its own. See
    tests/test_handoff_routes.py's own cross-tenant test proving two
    tenants with different `escalation_email` values each only ever
    receive their own escalations.
    """
    try:
        tenant = await repo.get_tenant()
        escalation_email = tenant.config.get("escalation_email")
        if escalation_email is None:
            logger.warning(
                "escalation %s: tenant %s has no escalation_email configured -- "
                "email not sent (row still created, visible in the dashboard inbox)",
                escalation_id,
                repo.tenant_id,
            )
            return

        limiter = _get_per_tenant_email_limiter(request)
        limiter_key = str(repo.tenant_id)
        if not limiter.check(limiter_key):
            logger.warning(
                "escalation %s: tenant %s hit its own hourly escalation-email cap -- "
                "email skipped (row still created)",
                escalation_id,
                repo.tenant_id,
            )
            return

        subject = f"New escalation request (conversation {cid})"
        body = _render_escalation_email_html(
            messages, payload.contact_name, payload.contact_email, payload.contact_phone
        )
        sent = await send_email(escalation_email, subject, body, subtype="html")
        if not sent:
            logger.warning("escalation %s: notification email send failed", escalation_id)
    except Exception:
        logger.exception("escalation %s: notification email attempt raised", escalation_id)
