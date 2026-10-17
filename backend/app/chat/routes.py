# backend/app/chat/routes.py
# Task 4.1 [SECURITY]: POST /api/v1/chat (docs/SPEC.md §10), the real
# streaming chat endpoint -- the first real caller of app/agent/
# entrypoint.py's run_turn()/read_state(), and the first place
# app/agent/graph.py's compiled graph is ever actually driven against
# real visitor traffic.
#
# This module resolves several previously-open §9 markers, all owned by
# this exact step (see PROJECT_SPEC.md's own decision-log entry for the
# full reasoning behind each):
#   - compiling the graph once, in app/main.py's lifespan hook (not here);
#   - per-thread turn serialization, closing the resume_turn() TOCTOU
#     window (a short TURN LEASE on the conversation row, see below);
#   - the overall per-turn wall-clock timeout and what the stream does on
#     a dependency failure / mid-stream model failure;
#   - offer_escalation, computed here from existing graph state, never a
#     new AgentState field;
#   - the real client_message_id retry/idempotency mechanism docs/SPEC.md
#     §10's own "Dropped connections" paragraph names but never specifies.
#
# Task 4.1 FOLLOW-UP [SECURITY-adjacent: concurrency/availability]: the
# original design used `pg_try_advisory_xact_lock`, held for the whole
# turn by holding its own transaction (and connection) open for the
# stream's full duration -- capping concurrent conversations at the app
# pool size, with pool exhaustion surfacing as a misleading 409. Replaced
# with a short LEASE on `conversations.lease_until`/`lease_token` (Task
# 4.1-follow-up migration `e0c758a08dee`): acquired and released with ONE
# short UPDATE statement each, through an ordinary, briefly-held session
# -- never held open across the stream. The lease duration (chat_request_
# timeout_seconds + chat_lease_margin_seconds, both Settings fields) is
# chosen to always exceed the overall per-turn timeout, so a genuinely
# still-running turn can never lose its own lease to expiry. A crashed
# worker's own lease simply expires on its own -- no cleanup job needed.
#
# A SECOND, real connection-holding gap was found and closed in the SAME
# pass, not left for later: `Depends(get_current_visitor)` itself depends
# on `Depends(get_db_session)` -- and FastAPI's own dependency exit stack
# (confirmed live, a minimal repro, not assumed from documentation) does
# NOT release a yield-dependency's connection until the ENTIRE response
# body (the whole SSE stream) has been sent, for ANY dependency declared
# on a streaming endpoint, nested or not, regardless of how briefly the
# handler body itself uses it. Declaring `identity`/`session` as Depends()
# PARAMETERS on this route would therefore hold a SECOND app-pool
# connection open for the whole stream even after the lease redesign
# below. Fixed by calling `get_current_visitor()` as a PLAIN function,
# passing it a short-lived, manually-opened-and-closed session -- the
# exact same session also used for conversation resolution and the lease
# acquire, all three sequential, fast operations, closed BEFORE
# `EventSourceResponse` is ever constructed. Costs this route its
# automatic OpenAPI auth-scheme annotation (accepted -- this is an
# internal API with `docs_enabled` off in production anyway, docs/SPEC.md
# names no OpenAPI/Swagger requirement).
import asyncio
import json
import logging
import math
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from langchain_core.messages import AIMessage, HumanMessage
from opentelemetry import context
from sqlalchemy import text
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.agent.entrypoint import read_state, run_turn
from app.agent.graph import render_support_details_text
from app.auth.dependencies import VerifiedIdentity, get_current_visitor
from app.chat.schemas import (
    ChatRequest,
    ConversationListResponse,
    ConversationMessage,
    ConversationMessagesResponse,
    ConversationSummary,
)
from app.config import get_settings
from app.db import _session_factory, get_db_session
from app.plans import usage
from app.plans.service import get_plan_limits
from app.plans.usage import PendingUsageEvent, estimate_cost_usd
from app.ratelimit import RateLimiter
from app.request_limits import get_or_create_limiter
from app.telemetry.service import (
    effective_trace_text_enabled,
    tag_current_span_with_identity,
    trace_text_context,
)
from app.tenancy.models import Conversation
from app.tenancy.repository import TenantScopedRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

# docs/SPEC.md §8: "messages capped at 2,000 characters" -- enforced here,
# explicitly, rather than as a pydantic Field(max_length=...) on
# ChatRequest, because this ONE failure has its own named status code
# (413, docs/SPEC.md §10) -- a pydantic length violation would 422
# instead (see app/chat/schemas.py's own header comment for the full
# reasoning).
_MESSAGE_CHAR_CAP = 2000
_MESSAGE_TOO_LONG_STATUS = 413
_RATE_LIMITED_STATUS = 429
# 409, not 429: none of "a conversation is already leased" (the turn
# lease), "this visitor already has a stream open" (the concurrent-
# stream cap), or "this retried message was already superseded by a
# later turn" (the idempotency check below) is a TIME-WINDOW failure with
# a meaningful Retry-After countdown the way the two RateLimiter-backed
# 429s below are -- each is a STATE conflict (something else is already
# in progress, or has already moved on), the conventional meaning of 409.
# Named once, reused for all three, rather than inventing three different
# codes for the same underlying shape.
_CONFLICT_STATUS = 409
# A DIFFERENT failure class from all three 409s above: the database
# connection pool itself has no free slot to even ATTEMPT the lease
# acquire -- never reported as "conversation busy" (a real lease
# conflict), since that would be actively misleading about the cause.
_SERVICE_UNAVAILABLE_STATUS = 503

# Three fixed, generic error codes (Felix's Phase 4 decision, recorded at
# the Phase 4 kickoff and PROJECT_SPEC.md's own Step 4.1 entry) -- never a
# raw exception message or stack trace reaches a visitor (docs/SPEC.md
# §10: "error {code, message}, no stack traces").
_ERROR_MODEL = "model_error"
_ERROR_TIMEOUT = "timeout"
_ERROR_DEPENDENCY = "dependency_unavailable"
_ERROR_MESSAGES = {
    _ERROR_MODEL: "Something went wrong while generating the answer. Please try again.",
    _ERROR_TIMEOUT: "The request took too long. Please try again.",
    _ERROR_DEPENDENCY: "Something went wrong on our end. Please try again in a moment.",
}

# docs/SPEC.md §6.1: the Fallback & Guardrail specialist's own fixed
# replies never call the stream writer (Task 3.4.b/e/f) -- their own
# visible text only ever reaches this endpoint via the "updates" channel's
# per-node `messages` key (see app/agent/entrypoint.py's own Task 4.1
# header comment for why "custom" alone would surface nothing for these
# paths). Replayed here as ONE token event (never incrementally streamed
# word-by-word, since the graph itself never streamed them that way) plus
# "done" -- never a SECOND, duplicate token event for the "answer" node's
# own text, which already streamed via "custom" above it.
_FIXED_REPLY_NODES = frozenset(
    {"guardrail", "no_answer_fallback", "dependency_failure", "fallback", "coordinator"}
)
# docs/SPEC.md §6.1: "Offers the escalation button when a person may be
# needed" -- Task 3.4.e's own §9 marker (owner Step 4.1) left WHICH
# fallback types warrant the offer as this step's own call to make, not
# decided there. Decision, recorded here and in PROJECT_SPEC.md: "no
# relevant answer" and a genuine dependency failure both mean "the bot
# could not help this turn", the one shared condition docs/SPEC.md's own
# wording names -- greeting/thanks ("guardrail") has nothing to escalate,
# and a real answer ("answer") means the visitor was already helped.
# "fallback"/"coordinator" are today UNREACHABLE (Settings 4.3/4.4 stubs,
# `_quota_exhausted()`/`_is_escalation_intent()` both hardcoded False) --
# included here anyway, now, rather than revisited later, since docs/
# SPEC.md §6.1 is explicit that BOTH of those cases offer escalation too
# (quota-exhausted: "support details, escalation offered"; the Coordinator
# IS the escalation flow itself) -- no new decision needed once 4.3/4.4
# make them reachable.
_ESCALATION_OFFERED_NODES = frozenset(
    {"no_answer_fallback", "dependency_failure", "fallback", "coordinator"}
)


def _rate_limited_response(retry_after: float) -> JSONResponse:
    header_seconds = max(1, math.ceil(retry_after))
    return JSONResponse(
        status_code=_RATE_LIMITED_STATUS,
        content={"detail": "too many requests", "retry_after": round(retry_after, 1)},
        headers={"Retry-After": str(header_seconds)},
    )


def _get_per_ip_limiter(request: Request) -> RateLimiter:
    settings = request.app.state.settings
    return get_or_create_limiter(
        request,
        "chat_per_ip_limiter",
        settings.chat_rate_limit_per_ip,
        settings.chat_rate_limit_per_ip_window_seconds,
    )


def _get_per_visitor_limiter(request: Request) -> RateLimiter:
    settings = request.app.state.settings
    return get_or_create_limiter(
        request,
        "chat_per_visitor_limiter",
        settings.chat_rate_limit_per_visitor,
        settings.chat_rate_limit_per_visitor_window_seconds,
    )


def _get_stop_per_visitor_limiter(request: Request) -> RateLimiter:
    # [SECURITY] batch follow-up (2026-10-16): POST /chat/stop had NO
    # rate limiting at all -- a real, confirmed gap. A SEPARATE instance
    # from `_get_per_visitor_limiter()` above (its own disjoint key
    # space/state) -- hammering /chat/stop must never consume a
    # visitor's own /chat quota, and vice versa.
    settings = request.app.state.settings
    return get_or_create_limiter(
        request,
        "chat_stop_per_visitor_limiter",
        settings.chat_stop_rate_limit_per_visitor,
        settings.chat_stop_rate_limit_per_visitor_window_seconds,
    )


def _get_stop_per_ip_limiter(request: Request) -> RateLimiter:
    settings = request.app.state.settings
    return get_or_create_limiter(
        request,
        "chat_stop_per_ip_limiter",
        settings.chat_stop_rate_limit_per_ip,
        settings.chat_stop_rate_limit_per_ip_window_seconds,
    )


def _get_active_streams(request: Request) -> set[uuid.UUID]:
    # docs/SPEC.md §9: "1 concurrent stream per visitor" -- an in-process
    # set of vids CURRENTLY streaming, the same app.state-lazy-singleton
    # shape as StatusCache/RateLimiter above (fresh and empty on every
    # fresh create_app(), no explicit reset needed between tests). A
    # single API instance only (docs/SPEC.md §9's own standing scope for
    # the whole in-process-limiter design) -- the SAME scale boundary
    # every other in-process mechanism in this file already accepts.
    streams = getattr(request.app.state, "chat_active_streams", None)
    if streams is None:
        streams = set()
        request.app.state.chat_active_streams = streams
    return streams


def _get_stop_requests(request: Request) -> set[uuid.UUID]:
    """Part D (docs/SPEC.md §9's "Stop mid-stream"): the in-process set
    of vids who have asked their CURRENTLY STREAMING turn to stop --
    same app.state-lazy-singleton shape as `_get_active_streams()`
    above, for the identical reason (fresh/empty per `create_app()`, no
    explicit reset needed between tests, single-API-instance scope).
    A vid is added by `POST /chat/stop` only while it is ALSO present in
    `_get_active_streams()`'s own set (so stopping a visitor with
    nothing running is a harmless no-op, never a stale flag that lingers
    into that visitor's NEXT, unrelated turn), and removed by `_stream_
    turn()`'s own `finally` below unconditionally, win or lose -- the
    same cleanup guarantee `active_streams.discard()` already has.
    """
    stops = getattr(request.app.state, "chat_stop_requests", None)
    if stops is None:
        stops = set()
        request.app.state.chat_stop_requests = stops
    return stops


async def _acquire_lease(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    lease_token: uuid.UUID,
    lease_seconds: float,
) -> bool:
    """ONE short UPDATE, through the caller's own short-lived session --
    never a connection held open for the lease's own duration. Matches
    only a row that is tenant-owned AND currently free (`lease_until` is
    NULL, or already in the past) -- a row matching neither condition
    returns zero rows, the real "busy" signal `create_chat_turn()` turns
    into a 409. The tenant_id filter here is NOT redundant with whatever
    already verified `conversation_id` belongs to this tenant earlier in
    the request (`_resolve_conversation()`) -- it is this statement's OWN
    independent proof that a lease can never be acquired against another
    tenant's row, even if a future caller ever reached this function
    without that earlier check.

    [SECURITY-adjacent, Task 4.2 adversarial follow-up]: also sets
    `last_message_at = now()` -- a REAL bug found here, not merely
    reasoned about: this column had NO other writer anywhere in this
    codebase (confirmed by reading every call site directly), so it sat
    frozen at a conversation's own CREATION time forever, for its whole
    life. Two real consequences, both found live: "previous chats"
    (`list_conversations()`, ordered by this column) sorted by creation
    order, not actual recent activity; and the nightly retention sweep
    (`list_expired_conversations()`) would delete a conversation --
    checkpoint thread and all -- based on how long ago it was FIRST
    created, not how long it had actually been idle, meaning a
    conversation a visitor chats in every day could still be deleted
    out from under them the moment it crossed the tenant's own
    `retention_days` since creation. Fixed here, in the SAME single
    UPDATE already running on every turn (no new round trip): on EVERY
    lease acquisition, successful or not (an attempted turn, even one
    that later times out or fails, is still real, recent visitor
    activity) -- the one point in this request's own lifecycle that
    already proves a real visitor is actively using this exact
    conversation right now.
    """
    result = await session.execute(
        text(
            "UPDATE conversations "
            "SET lease_until = now() + make_interval(secs => :secs), lease_token = :token, "
            "last_message_at = now() "
            "WHERE cid = :cid AND tenant_id = :tenant_id "
            "AND (lease_until IS NULL OR lease_until < now()) "
            "RETURNING cid"
        ),
        {
            "secs": lease_seconds,
            "token": lease_token,
            "cid": conversation_id,
            "tenant_id": tenant_id,
        },
    )
    row = result.first()
    await session.commit()
    return row is not None


async def _release_lease(
    tenant_id: uuid.UUID, conversation_id: uuid.UUID, lease_token: uuid.UUID
) -> None:
    """Opens its OWN fresh, short-lived session -- the request's own
    auth/conversation-resolution session is already closed by the time
    this runs on every real path except the `_Stale` early-exit (see
    `create_chat_turn()`'s own call site), and reusing a long-lived one
    here would reintroduce exactly the connection-hold problem this
    whole redesign exists to remove. Matched on `lease_token` AND
    `tenant_id`: a stale, foreign, or already-expired-and-reassigned
    token's own release attempt matches zero rows -- a silent, safe
    no-op, never an error and never a chance to clear a DIFFERENT
    holder's own lease. Best-effort: a failure here is logged, never
    raised further -- the lease's own TTL is the real backstop (an
    unreleased lease still expires on its own), so a release-time error
    must not mask whatever the stream's own real outcome already was.
    """
    try:
        async with _session_factory()() as session:
            await session.execute(
                text(
                    "UPDATE conversations SET lease_until = NULL, lease_token = NULL "
                    "WHERE cid = :cid AND tenant_id = :tenant_id AND lease_token = :token"
                ),
                {"cid": conversation_id, "tenant_id": tenant_id, "token": lease_token},
            )
            await session.commit()
    except Exception:
        logger.exception("failed to release the turn lease for conversation %s", conversation_id)


async def _resolve_conversation(
    repo: TenantScopedRepository, identity: VerifiedIdentity, conversation_id: uuid.UUID | None
) -> Conversation:
    """docs/SPEC.md §10: "omitting conversation_id starts a new
    conversation". docs/SPEC.md §16: "Another visitor's conversation_id
    returns 404, and client-sent thread_id, client_id or configurable is
    ignored." Task 4.2 made `get_conversation_by_id()` (app/tenancy/
    repository.py) itself vid-aware -- the explicit `conversation.vid !=
    identity.vid` re-check below is now a deliberate, redundant DEFENSE-
    IN-DEPTH backstop (per the Phase 4 kickoff decision this closes),
    never the only layer again.
    """
    if conversation_id is None:
        conversation = await repo.create_conversation(vid=identity.vid)
        await repo.session.commit()
        return conversation

    conversation = await repo.get_conversation_by_id(conversation_id, vid=identity.vid)
    if conversation is None or conversation.vid != identity.vid:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conversation


def _sse_event(event_id: int, event: str, data: dict) -> dict:
    # docs/SPEC.md §10: "Every event carries an incrementing id and v: 1".
    # sse-starlette's own ServerSentEvent takes `data` as a STRING (or an
    # object with its own __str__) -- json.dumps() here, once, rather
    # than at each of this module's several call sites.
    return {"event": event, "id": str(event_id), "data": json.dumps({**data, "v": 1})}


def _build_citations_event(event_id: int, citations: list[dict]) -> dict:
    # docs/SPEC.md §10: citations {items: [{n, title, url}]}. The real,
    # persisted Citation shape (app/agent/graph.py, Task 3.4.c) is
    # deliberately `{url, heading_path}` only -- NO title field (that
    # task's own decision log: adding one would re-touch an already-
    # twice-touched completed file for a field citation building did not
    # strictly need). Derived here, at the wire boundary, instead:
    # heading_path's own last element reads as a natural human title; a
    # page with no heading_path at all (a real, valid case -- a page with
    # no headings) falls back to the URL itself rather than an empty
    # string. `n` is the citation's own 1-indexed position in this list
    # -- the answer text carries no inline numbered markers of its own
    # (confirmed live: `_SYSTEM_INSTRUCTION` never asks the model to cite
    # inline), so `n` is a display-order index for the widget's own
    # separate "Sources" list, not a cross-reference into the answer body.
    items = []
    for index, citation in enumerate(citations, start=1):
        heading_path = citation.get("heading_path") or []
        title = heading_path[-1] if heading_path else citation["url"]
        items.append({"n": index, "title": title, "url": citation["url"]})
    return _sse_event(event_id, "citations", {"items": items})


# [SECURITY] Step 7-follow-up: every OPTIONS preflight handler that used
# to live here (chat_preflight/conversations_preflight/
# conversation_messages_preflight) is REMOVED -- app.auth.cors.
# WidgetCORSMiddleware now answers every one of this file's own
# visitor-facing OPTIONS requests centrally, before the request ever
# reaches a route handler at all. See that module's own header for the
# full design and docs/DECISIONS_REGISTER.md for the register row.


class _Stale(Exception):
    """Raised internally when a retried client_message_id has already
    been superseded by a later turn on the same thread (see
    _check_idempotency()'s own docstring) -- converted to a 409 event by
    its one caller, never leaked further.
    """


async def _check_idempotency(
    graph, identity: VerifiedIdentity, conversation_id: uuid.UUID, client_message_id: uuid.UUID
) -> tuple[AIMessage | None, dict]:
    """docs/SPEC.md §10's own "Dropped connections" paragraph: "a retry
    reuses the same client_message_id, so the message never appears
    twice." The mechanism this decides (an open §9 marker before this
    task): the retried HumanMessage is built with `id=str(client_message_
    id)` -- LangGraph's own `add_messages` reducer (confirmed live, not
    assumed) REPLACES an existing message sharing that same id in place,
    rather than appending a second copy, so the human message itself can
    never appear twice no matter how many times a turn is retried.

    The remaining half this function resolves: if that turn ALREADY
    completed (a real AIMessage already follows it in state), blindly
    re-running would still APPEND a second, brand-new AIMessage (nothing
    dedups the model's own fresh output the way add_messages dedups the
    human message it was given an id for) -- so this function inspects
    current state FIRST, while still holding the conversation's own turn
    lease, and returns the ALREADY-PERSISTED AIMessage (plus its own
    turn's state dict) instead of a fresh one to run, whenever this exact
    client_message_id was already answered as the thread's own MOST
    RECENT turn. Raises `_Stale` if this id exists but is no longer the
    most recent turn (a genuinely stale retry of a superseded message) --
    deliberately rejected rather than silently replayed with citations/
    can_answer state that may belong to a LATER turn instead.
    """
    snapshot = await read_state(graph, identity, conversation_id)
    messages = snapshot.values.get("messages", [])
    for index, existing in enumerate(messages):
        if isinstance(existing, HumanMessage) and existing.id == str(client_message_id):
            if index == len(messages) - 1:
                return None, snapshot.values  # not yet answered -- safe to run
            if index == len(messages) - 2 and isinstance(messages[index + 1], AIMessage):
                return messages[index + 1], snapshot.values  # already answered -- replay
            raise _Stale(f"client_message_id {client_message_id} was superseded by a later turn")
    return None, snapshot.values  # brand new id -- safe to run


async def _stream_turn(
    active_streams: set[uuid.UUID],
    stop_requests: set[uuid.UUID],
    identity: VerifiedIdentity,
    conversation_id: uuid.UUID,
    lease_token: uuid.UUID,
    graph,
    message: str,
    client_message_id: uuid.UUID,
    existing_answer: AIMessage | None,
    existing_values: dict,
    chat_request_timeout_seconds: float,
    quota_exhausted: bool,
    trace_text_enabled: bool,
    support_text: str | None,
) -> AsyncIterator[dict]:
    """The real SSE body. Holds NO database connection of its own for the
    main loop's own duration -- the lease and the idempotency check were
    already resolved by the caller (create_chat_turn() below), using its
    own short-lived session, closed before this generator is even
    constructed; this function's only database touch is the lease
    RELEASE, one short UPDATE through a brand-new session, in the
    `finally` below. That `finally` runs on success, on a raised
    exception, AND on the generator being `.aclose()`'d early (a real
    client disconnect -- Python's own async generator semantics deliver
    this as a GeneratorExit at the current `await`, which propagates
    through this function's own `try/finally` exactly like any other
    exception; confirmed live, not assumed -- see this task's own
    dedicated cancellation test).

    Task 4.3: `usage.record_usage_event()` (called below, on the
    success path only) is a PURE in-memory operation -- no database
    access of its own (app/plans/usage.py's own design, "updates them
    locally per message") -- so calling it from here does not reintroduce
    a connection-hold risk. Deliberately NOT called for a replay (not a
    new turn -- would double-count an already-recorded one), nor for the
    `timeout`/`model_error` early returns below (a real attempted turn
    that produced no visible or billable output at all; recording a
    free, reason-coded usage row for these too is a reasonable future
    extension, not built here -- out of scope for this task, which
    focuses on the cases docs/SPEC.md §9 names explicitly: a real
    answer, and quota-exhausted).

    Part D: `stop_requests` is threaded into `run_turn()` as a plain
    `lambda: identity.vid in stop_requests` closure -- `_answer_node`
    (app/agent/graph.py) calls it fresh on every emitted delta via
    `app.agent.context.stop_requested_from_config()`, never a value
    snapshotted once here (it must see a Stop click that lands AFTER
    this generator started). `counted_toward_quota`/usage-recording
    below are UNCHANGED by a stop: `_answer_node` still returns through
    its own normal `can_answer=True` path (see that node's own
    docstring), so `last_node == "answer"` still holds and this turn
    still counts once toward quota either way, matching docs/SPEC.md
    §9's "a stopped message counts once toward quota" literally, via
    the SAME code that already counts a normal completed answer once --
    no separate stopped-turn branch needed here.
    """
    event_id = 0

    # Step 4.5: docs/SPEC.md §13: "Spans are tagged with tenant and
    # visitor" and "the switch is applied per request before spans are
    # exported" -- both done HERE, inside this generator's own body,
    # not in create_chat_turn() above: this is the actual `asyncio`
    # task that runs the graph (`run_turn()`, below) and therefore the
    # one whose own OTel context the baggage attach below needs to
    # wrap. Attaching it in create_chat_turn() instead would attach it
    # to THAT function's own task, which returns (handing the
    # `EventSourceResponse` back to Starlette) before this generator's
    # own body ever executes -- the attach would have no effect on the
    # spans this function's own `run_turn()` call produces.
    tag_current_span_with_identity(identity.tenant_id, identity.vid)
    trace_context_token = context.attach(trace_text_context(trace_text_enabled))

    try:
        if existing_answer is not None:
            # Replay, no real model call: the loop below never runs.
            event_id += 1
            yield _sse_event(event_id, "token", {"text": existing_answer.content})
            citations = existing_values.get("citations") or []
            if citations:
                event_id += 1
                yield _build_citations_event(event_id, citations)
            event_id += 1
            # Conservative, documented default: a replay never offers
            # escalation, regardless of which fixed-reply node originally
            # produced it -- distinguishing that would require persisting
            # which node produced each historical AIMessage, which
            # nothing in AgentState does today (Task 3.4.e's own decision
            # not to add an offer_escalation state field, reused here
            # rather than re-litigated).
            yield _sse_event(
                event_id,
                "done",
                {
                    "message_id": str(uuid.uuid4()),
                    "usage": None,
                    "offer_escalation": False,
                    "conversation_id": str(conversation_id),
                    # Part D: a replay is never a stopped turn -- the
                    # original turn already completed (that's what makes
                    # it replayable at all).
                    "stopped": False,
                },
            )
            return

        human_message = HumanMessage(content=message, id=str(client_message_id))
        graph_input = {"messages": [human_message]}

        try:
            async with asyncio.timeout(chat_request_timeout_seconds):
                last_node: str | None = None
                last_output: dict = {}
                async for mode, chunk in run_turn(
                    graph,
                    identity,
                    conversation_id,
                    graph_input,
                    quota_exhausted=quota_exhausted,
                    support_text=support_text,
                    stop_requested=lambda: identity.vid in stop_requests,
                ):
                    if mode == "custom":
                        event_id += 1
                        yield _sse_event(event_id, "token", {"text": chunk["text"]})
                    elif mode == "updates":
                        for node_name, output in chunk.items():
                            last_node, last_output = node_name, output
                            if node_name in _FIXED_REPLY_NODES and output.get("messages"):
                                text_out = output["messages"][-1].content
                                event_id += 1
                                yield _sse_event(event_id, "token", {"text": text_out})
        except TimeoutError:
            logger.warning("chat turn exceeded the overall request timeout")
            event_id += 1
            yield _sse_event(
                event_id,
                "error",
                {"code": _ERROR_TIMEOUT, "message": _ERROR_MESSAGES[_ERROR_TIMEOUT]},
            )
            return
        except Exception as exc:  # noqa: BLE001 -- converted to a fixed, generic error event, never raw
            logger.warning("chat turn failed: %s", exc)
            event_id += 1
            yield _sse_event(
                event_id, "error", {"code": _ERROR_MODEL, "message": _ERROR_MESSAGES[_ERROR_MODEL]}
            )
            return

        citations = last_output.get("citations") or []
        if citations:
            event_id += 1
            yield _build_citations_event(event_id, citations)

        # Task 4.3: True ONLY when `last_node == "answer"` -- confirmed
        # by reading `_route_after_answer()` (app/agent/graph.py)
        # directly: a `can_answer=False` verdict from the answer node
        # itself routes to a SEPARATE node (`no_answer_fallback`) before
        # the turn ends, so `last_node` can equal `"answer"` only when a
        # real, completed, `can_answer=True` answer was produced -- never
        # for a fallback/guardrail/quota-exhausted/escalation turn, all
        # of which are free per docs/SPEC.md §9 ("Fallback replies and
        # escalations are free and don't count").
        counted_toward_quota = last_node == "answer"
        resolved_model = last_output.get("resolved_model") if counted_toward_quota else None
        prompt_tokens = last_output.get("prompt_tokens") if counted_toward_quota else None
        completion_tokens = last_output.get("completion_tokens") if counted_toward_quota else None
        estimated_cost_usd = (
            estimate_cost_usd(resolved_model, prompt_tokens, completion_tokens)
            if resolved_model is not None and prompt_tokens is not None
            else None
        )
        await usage.record_usage_event(
            PendingUsageEvent(
                tenant_id=identity.tenant_id,
                vid=identity.vid,
                conversation_id=conversation_id,
                counted_toward_quota=counted_toward_quota,
                model=resolved_model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                estimated_cost_usd=estimated_cost_usd,
                created_at=datetime.now(UTC),
            )
        )

        event_id += 1
        yield _sse_event(
            event_id,
            "done",
            {
                "message_id": str(uuid.uuid4()),
                "usage": (
                    {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "estimated_cost_usd": estimated_cost_usd,
                    }
                    if counted_toward_quota
                    else None
                ),
                "offer_escalation": last_node in _ESCALATION_OFFERED_NODES,
                # Step 6.2, additive (Felix's own pre-authorisation,
                # docs/DECISIONS_REGISTER.md "Backend contract gaps found
                # by the widget track"): a NEW key on the existing `done`
                # payload, present on every turn (not only the first) --
                # the widget's own CURRENT workaround
                # (ChatController._discoverConversationIdIfUnknown(),
                # widget/chat/src/chat-controller.js) keeps working
                # unchanged; it simply has a cheaper, direct way to learn
                # this value available once it chooses to read it.
                "conversation_id": str(conversation_id),
                # Part D: True only when `_answer_node` itself broke its
                # own streaming loop early on an explicit stop request --
                # see that node's own docstring for why this can only
                # ever be True when `counted_toward_quota` above is also
                # True (a stop is never honoured before real answer text
                # exists to stop).
                "stopped": bool(last_output.get("answer_stopped")),
            },
        )
    finally:
        active_streams.discard(identity.vid)
        stop_requests.discard(identity.vid)
        await _release_lease(identity.tenant_id, conversation_id, lease_token)
        context.detach(trace_context_token)


@router.post("/chat")
async def create_chat_turn(payload: ChatRequest, request: Request) -> Response:
    # docs/SPEC.md §10: "Auth, rate-limit and quota failures return real
    # HTTP codes (401, 413, 429) before the stream opens." Quota is now
    # real (Task 4.3) -- the readiness gate for it is checked further
    # below, at the SAME point in this function §9's own "order of
    # checks" names ("JWT, cached tenant/plan status, rate limit, quota,
    # then embedding and the AI call"): AFTER identity/rate-limit, never
    # before -- a missing/invalid token or an over-cap message must
    # still 401/413 even while the quota system is still loading, since
    # those checks are structurally prior in that same ordering.
    if len(payload.message) > _MESSAGE_CHAR_CAP:
        return JSONResponse(
            status_code=_MESSAGE_TOO_LONG_STATUS, content={"detail": "message too long"}
        )

    settings = get_settings()

    # Identity resolution, rate limits, conversation resolution and the
    # lease acquire all happen HERE, synchronously, through ONE short-
    # lived session -- closed before EventSourceResponse is ever
    # constructed, per this module's own header comment (the real reason
    # `identity`/a database session are NOT Depends() parameters on this
    # route). "This conversation is busy" and "this message was already
    # answered/superseded" are therefore real 409s returned before the
    # stream opens (docs/SPEC.md §10's own "before the stream opens"
    # principle, extended to these two Step-4.1-specific conflict cases
    # too), never an SSE error event after the fact. `request.app.state.
    # graph` is read only AFTER identity resolves -- never before, so a
    # plain missing-token 401 (or any other auth failure) never depends
    # on the graph existing at all.
    try:
        async with _session_factory()() as session:
            identity = await get_current_visitor(request, session)
            # [SECURITY] Step 7-follow-up: set as EARLY as possible,
            # right after the real Origin header is positively verified
            # against this token's own `org` claim (get_current_visitor()
            # already did that check) -- every branch below this line,
            # success or error (429/503/409x3/the final stream), picks
            # this up automatically via app.auth.cors.WidgetCORSMiddleware,
            # with no per-branch header to remember. The 413 check above
            # this `try` block, and a 401 raised BY get_current_visitor()
            # itself, both run before this line and are deliberately left
            # without a header -- see app/auth/cors.py's own header, point
            # 3, for why that is the safe default, not an oversight.
            request.state.cors_allowed_origin = identity.org
            graph = request.app.state.graph

            ip = request.client.host if request.client else "unknown"
            per_ip_limiter = _get_per_ip_limiter(request)
            if not per_ip_limiter.check(ip):
                return _rate_limited_response(per_ip_limiter.get_retry_after(ip))

            per_visitor_limiter = _get_per_visitor_limiter(request)
            visitor_key = str(identity.vid)
            if not per_visitor_limiter.check(visitor_key):
                return _rate_limited_response(per_visitor_limiter.get_retry_after(visitor_key))

            # Task 4.3: the readiness HALF of docs/SPEC.md §9's own
            # quota-reload guarantee -- "a restart cannot reset
            # counters... until the reload finishes, /api/v1/chat
            # returns 503 with Retry-After, so no chat is answered over
            # quota." Checked here, AFTER auth/rate-limit (both
            # structurally prior in §9's own check order) and BEFORE
            # the quota verdict itself is computed below, which would
            # otherwise silently read an empty/incomplete in-memory
            # counter. `usage_counters_ready` is set True exactly once,
            # after app/main.py's own lifespan finishes the real startup
            # reload (`app.plans.usage.sync_usage_counters()`) -- never
            # flipped back to False afterward (a later periodic refresh
            # failing is logged, not treated as "unready again"; see
            # that lifespan's own comment).
            if not getattr(request.app.state, "usage_counters_ready", False):
                return JSONResponse(
                    status_code=_SERVICE_UNAVAILABLE_STATUS,
                    content={"detail": "service starting up, please retry"},
                    headers={"Retry-After": "5"},
                )

            active_streams = _get_active_streams(request)
            if identity.vid in active_streams:
                return JSONResponse(
                    status_code=_CONFLICT_STATUS,
                    content={"detail": "a stream for this visitor is already in progress"},
                )

            repo = TenantScopedRepository(tenant_id=identity.tenant_id, session=session)
            conversation = await _resolve_conversation(repo, identity, payload.conversation_id)

            # Task 4.3: docs/SPEC.md §9's own check order ("quota, then
            # embedding and the AI call") -- decided HERE, with the
            # already-open session, before the graph ever runs, never
            # re-derived inside it (see app.agent.context.
            # quota_exhausted_from_config()'s own docstring for why).
            # `plan_limits is None` (no plan assigned yet -- docs/SPEC.md
            # §5.4: "Plans are assigned manually in an admin screen")
            # means no quota to enforce, matching get_plan_limits()'s own
            # documented contract exactly.
            tenant = await repo.get_tenant()
            plan_limits = await get_plan_limits(tenant, session)
            quota_exhausted = plan_limits is not None and usage.is_over_quota(
                tenant.id, plan_limits
            )

            # Step 6.2: the SAME already-fetched `tenant.config` the
            # retention/trace-text checks right below also read --
            # rendered here, with the real DB session open, never
            # inside the graph itself (app.agent.context.support_text_
            # from_config()'s own docstring has the full reasoning).
            support_text = render_support_details_text(tenant.config)

            # Step 4.5: docs/SPEC.md §13's own per-tenant trace-text
            # switch -- resolved HERE, with the already-open session and
            # the already-fetched `tenant` row, the IDENTICAL "resolve
            # retention_days with a platform-default fallback, clamped
            # to [1, 90]" logic `handle_retention_sweep()` (app/ingest/
            # job_handlers.py) already uses -- not extracted into a
            # shared helper (a 2nd occurrence, below this project's own
            # established 3rd-occurrence threshold).
            retention_days = tenant.config.get(
                "retention_days", settings.default_chat_retention_days
            )
            if not isinstance(retention_days, int) or not (1 <= retention_days <= 90):
                retention_days = settings.default_chat_retention_days
            trace_text_enabled = effective_trace_text_enabled(tenant.config, retention_days)

            lease_token = uuid.uuid4()
            lease_seconds = (
                settings.chat_request_timeout_seconds + settings.chat_lease_margin_seconds
            )
            acquired = await _acquire_lease(
                session, identity.tenant_id, conversation.cid, lease_token, lease_seconds
            )
            if not acquired:
                return JSONResponse(
                    status_code=_CONFLICT_STATUS,
                    content={"detail": "this conversation is busy, please retry"},
                )

            try:
                existing_answer, existing_values = await _check_idempotency(
                    graph, identity, conversation.cid, payload.client_message_id
                )
            except _Stale:
                await _release_lease(identity.tenant_id, conversation.cid, lease_token)
                return JSONResponse(
                    status_code=_CONFLICT_STATUS,
                    content={"detail": "this message has already been answered"},
                )
    except PoolTimeoutError:
        # A real pool-checkout timeout (no free app-pool connection at
        # all) -- never reported as "conversation busy" (a real lease
        # conflict, decided only once a connection already exists).
        logger.warning("database connection pool exhausted while starting a chat turn")
        return JSONResponse(
            status_code=_SERVICE_UNAVAILABLE_STATUS,
            content={"detail": "service temporarily unavailable, please retry"},
        )

    active_streams.add(identity.vid)
    stop_requests = _get_stop_requests(request)
    stop_requests.discard(identity.vid)

    return EventSourceResponse(
        _stream_turn(
            active_streams,
            stop_requests,
            identity,
            conversation.cid,
            lease_token,
            graph,
            payload.message,
            payload.client_message_id,
            existing_answer,
            existing_values,
            settings.chat_request_timeout_seconds,
            quota_exhausted,
            trace_text_enabled,
            support_text,
        ),
        ping=15,
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat/stop", status_code=204)
async def stop_chat_turn(
    request: Request,
    identity: VerifiedIdentity = Depends(get_current_visitor),  # noqa: B008
) -> Response:
    """Part D (docs/SPEC.md §9's "Stop mid-stream"): the EXPLICIT stop
    signal, deliberately a separate endpoint rather than relying on the
    widget simply dropping/aborting its own `/chat` connection -- the
    spec's own wording requires stop to be "distinct from a dropped
    connection", and a raw aborted fetch is, at the transport level,
    indistinguishable from a real network failure. The widget's own
    `AbortController` (widget/chat/src/chat-controller.js) is used to
    stop the WIDGET's own local reading/rendering of the stream
    immediately (so the input re-enables without waiting on a round
    trip); THIS endpoint is the one thing that actually tells the
    backend to cancel generation.

    No request body, no `conversation_id` -- `_get_active_streams()`'s
    own existing cap is already "1 concurrent stream per visitor"
    (docs/SPEC.md §9), so a visitor's own vid alone unambiguously
    identifies which turn (if any) to stop; it is the same identity the
    concurrent-stream guard already keys on.

    Idempotent and a deliberate no-op, never an error, when nothing is
    running for this visitor (already finished, already stopped, or
    never started) -- matches `/chat`'s own "busy conversation" 409
    being the ONLY conflict signal this API surfaces; "stop" has no
    comparable failure mode worth reporting, and a visitor's own stop
    click racing the stream's natural completion is a normal, expected
    timing, not an error.

    [SECURITY] batch follow-up (2026-10-16): rate-limited like every
    other authenticated widget endpoint -- a real, confirmed gap this
    endpoint shipped with none of (every OTHER route here has at least
    one limiter). Checked AFTER identity resolves (unlike `/chat`'s own
    manual ordering, which checks before -- this handler uses a plain
    `Depends(get_current_visitor)`, so identity is already resolved, or
    the request already 401'd, before this body ever runs; this
    endpoint does no expensive work either way, so there is no DoS-cost
    reason to restructure around that), set AFTER `request.state.cors_
    allowed_origin` so a 429 still carries the real verified-origin
    header, matching `/chat`'s own 429 branch.
    """
    request.state.cors_allowed_origin = identity.org

    ip = request.client.host if request.client else "unknown"
    per_ip_limiter = _get_stop_per_ip_limiter(request)
    if not per_ip_limiter.check(ip):
        return _rate_limited_response(per_ip_limiter.get_retry_after(ip))

    per_visitor_limiter = _get_stop_per_visitor_limiter(request)
    visitor_key = str(identity.vid)
    if not per_visitor_limiter.check(visitor_key):
        return _rate_limited_response(per_visitor_limiter.get_retry_after(visitor_key))

    active_streams = _get_active_streams(request)
    if identity.vid in active_streams:
        _get_stop_requests(request).add(identity.vid)
    return Response(status_code=204)


# --- Task 4.2: conversations endpoints (docs/SPEC.md §10) -------------------
#
# Both endpoints below are ordinary, FAST, non-streaming JSON responses --
# unlike POST /chat, `Depends(get_current_visitor)`/`Depends(get_db_
# session)` ARE used normally here (as plain FastAPI dependencies). The
# whole reason Task 4.1's own follow-up avoided that pattern was FastAPI's
# own confirmed-live behavior of holding a yield-dependency's connection
# open until the ENTIRE response body is sent -- for a response that
# completes in milliseconds (these two), that is a non-issue, not a
# connection-hold bug; rebuilding the manual-session dance here would be
# needless complexity with no real benefit (rule 11).


@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations_endpoint(
    request: Request,
    identity: VerifiedIdentity = Depends(get_current_visitor),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> ConversationListResponse:
    # [SECURITY] Step 7-follow-up: see create_chat_turn()'s own matching
    # comment -- app.auth.cors.WidgetCORSMiddleware attaches the real
    # CORS header to whatever response follows this line, success or
    # error, with no header to set per branch.
    request.state.cors_allowed_origin = identity.org
    repo = TenantScopedRepository(tenant_id=identity.tenant_id, session=session)
    conversations = await repo.list_conversations(
        identity.vid, max_results=get_settings().conversation_list_max_results
    )
    return ConversationListResponse(
        conversations=[
            ConversationSummary(
                conversation_id=c.cid,
                title=c.title,
                created_at=c.created_at,
                last_message_at=c.last_message_at,
            )
            for c in conversations
        ]
    )


@router.get("/conversations/{cid}/messages", response_model=ConversationMessagesResponse)
async def get_conversation_messages(
    cid: uuid.UUID,
    request: Request,
    identity: VerifiedIdentity = Depends(get_current_visitor),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> ConversationMessagesResponse:
    """docs/SPEC.md §10: "history, showing completed turns only".

    Reads the real LangGraph checkpoint via `read_state()` (the same
    guarded entry point `/chat` itself uses) -- no separate `messages`
    table exists (docs/SPEC.md §15's own data model names none). "Completed
    turns only": if the thread's own LAST message is a `HumanMessage` with
    no following `AIMessage` (a turn that crashed, timed out, or is still
    genuinely in flight), it is dropped from the response rather than shown
    half-finished.

    **Real, named limitation, not silently worked around**: only `role`
    and `text` are returned, never `citations` -- `AgentState.citations`
    (app/agent/graph.py) is a per-turn OVERWRITE field (Task 3.4.e, reset
    every turn by design), so only the MOST RECENT turn's own citations
    are ever recoverable from current graph state at all; reconstructing
    every past turn's own citations would need a dedicated, independently-
    persisted messages table, which is a real architectural decision
    (whether/when to build one) this task does not make unilaterally --
    see PROJECT_SPEC.md's own new §9 marker.
    """
    # [SECURITY] Step 7-follow-up: see list_conversations_endpoint()'s
    # own matching comment -- set once, unconditionally, so EVERY branch
    # below (including the 404) is covered by the middleware, not only
    # the success path.
    request.state.cors_allowed_origin = identity.org

    repo = TenantScopedRepository(tenant_id=identity.tenant_id, session=session)
    conversation = await repo.get_conversation_by_id(cid, vid=identity.vid)
    if conversation is None or conversation.vid != identity.vid:
        # [SECURITY] A plain `raise HTTPException` is fine here -- unlike
        # the FastAPI-injected `response: Response` DI parameter the
        # prior widget bugfix batch had to work around, Starlette's own
        # `ExceptionMiddleware` sits BELOW app.auth.cors.
        # WidgetCORSMiddleware in the stack, so it converts this
        # exception into a real Response BEFORE this middleware's own
        # `call_next()` ever returns -- confirmed live (not assumed): a
        # direct probe raising HTTPException after setting `request.
        # state.cors_allowed_origin` showed the header correctly attached
        # by the outer middleware. See PROJECT_SPEC.md's own decision-log
        # entry for this task for the exact script and output.
        raise HTTPException(status_code=404, detail="conversation not found")

    graph = request.app.state.graph
    snapshot = await read_state(graph, identity, cid)
    messages = list(snapshot.values.get("messages", []))
    if messages and isinstance(messages[-1], HumanMessage):
        messages = messages[:-1]  # an incomplete/in-flight turn -- not shown

    return ConversationMessagesResponse(
        messages=[
            ConversationMessage(
                role="visitor" if isinstance(message, HumanMessage) else "assistant",
                text=message.content,
            )
            for message in messages
        ]
    )
