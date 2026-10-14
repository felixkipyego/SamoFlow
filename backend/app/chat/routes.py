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
#     window (a Postgres pg_try_advisory_xact_lock, transaction-scoped,
#     per Felix's own direct refinement -- PgBouncer-transaction-pooling
#     safe, unlike a session-scoped advisory lock);
#   - the overall per-turn wall-clock timeout and what the stream does on
#     a dependency failure / mid-stream model failure;
#   - offer_escalation, computed here from existing graph state, never a
#     new AgentState field;
#   - the real client_message_id retry/idempotency mechanism docs/SPEC.md
#     §10's own "Dropped connections" paragraph names but never specifies.
#
# [SECURITY] deliberate exception to this project's own standing
# engineering rule ("never hold a DB connection while streaming",
# PROJECT_SPEC.md §3): a transaction-scoped advisory lock can only be held
# by holding its own transaction (and therefore its own connection) open
# for as long as the lock must be held -- which is exactly as long as one
# turn's own in-flight operation, by construction, regardless of which
# concrete mechanism enforces "at most one in-flight operation per
# thread". This is a REAL, named trade-off (one dedicated pool slot per
# CONCURRENTLY-STREAMING conversation thread, not per request), not an
# oversight -- see this task's own PROJECT_SPEC.md entry for the full
# reasoning and the new §9 marker (owner Phase 7) asking its own load test
# to confirm the app pool is not exhausted by realistic concurrency. This
# dedicated lock connection is NEVER the same connection/session as the
# one used for the conversation-resolution step below (that one,
# Depends(get_db_session), is closed well before streaming starts) -- the
# standing rule is still honored for every connection that does not
# itself need to hold a lock for the turn's own duration.
import asyncio
import json
import logging
import math
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.agent.entrypoint import read_state, run_turn
from app.auth.dependencies import VerifiedIdentity, get_current_visitor
from app.chat.schemas import ChatRequest
from app.config import get_settings
from app.db import get_db_session, get_engine
from app.ratelimit import RateLimiter
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
# 409, not 429: none of "a conversation is already mid-turn" (the
# advisory lock), "this visitor already has a stream open" (the
# concurrent-stream cap), or "this retried message was already superseded
# by a later turn" (the idempotency check below) is a TIME-WINDOW failure
# with a meaningful Retry-After countdown the way the two RateLimiter-
# backed 429s below are -- each is a STATE conflict (something else is
# already in progress, or has already moved on), the conventional meaning
# of 409. Named once, reused for all three, rather than inventing three
# different codes for the same underlying shape.
_CONFLICT_STATUS = 409

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


def _get_or_create_limiter(
    request: Request, attr: str, limit: int, window_seconds: float
) -> RateLimiter:
    # Identical shape to app/auth/routes.py's own _get_or_create_limiter()
    # -- not imported/reused directly, since that one is a private,
    # unexported helper of a different router module; duplicating one
    # three-line function is below this project's own established 3rd-
    # occurrence threshold (PROJECT_SPEC.md §3's duplication-check
    # convention), and importing a private name across router modules
    # would be a worse coupling than the few extra lines.
    limiter = getattr(request.app.state, attr, None)
    if limiter is None:
        limiter = RateLimiter(limit=limit, window_seconds=window_seconds)
        setattr(request.app.state, attr, limiter)
    return limiter


def _get_per_ip_limiter(request: Request) -> RateLimiter:
    settings = request.app.state.settings
    return _get_or_create_limiter(
        request,
        "chat_per_ip_limiter",
        settings.chat_rate_limit_per_ip,
        settings.chat_rate_limit_per_ip_window_seconds,
    )


def _get_per_visitor_limiter(request: Request) -> RateLimiter:
    settings = request.app.state.settings
    return _get_or_create_limiter(
        request,
        "chat_per_visitor_limiter",
        settings.chat_rate_limit_per_visitor,
        settings.chat_rate_limit_per_visitor_window_seconds,
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


def _advisory_lock_key(conversation_id: uuid.UUID) -> int:
    # Postgres pg_try_advisory_xact_lock(key) takes one signed bigint.
    # conversation_id is already a globally-unique, randomly-generated
    # UUID primary key (Task 1.2.d) -- masking its own 128-bit integer
    # form down to 63 bits (fits a signed bigint, never negative) is a
    # reasonable, documented approximation, not a correctness guarantee:
    # a hash collision between two DIFFERENT conversation_ids would only
    # ever cause one extra, spurious 409 (two unrelated conversations
    # briefly serialized against each other), never a cross-conversation
    # data leak -- advisory locks have no ACL/content of their own, they
    # are a pure serialization hint. The birthday bound on a 63-bit space
    # makes this negligible at any realistic conversation volume.
    return conversation_id.int & ((1 << 63) - 1)


async def _resolve_conversation(
    repo: TenantScopedRepository, identity: VerifiedIdentity, conversation_id: uuid.UUID | None
) -> Conversation:
    """docs/SPEC.md §10: "omitting conversation_id starts a new
    conversation". docs/SPEC.md §16: "Another visitor's conversation_id
    returns 404, and client-sent thread_id, client_id or configurable is
    ignored." `get_conversation_by_id()` (app/tenancy/repository.py) is
    tenant_id-scoped only today -- the `vid` check below is this
    endpoint's OWN obligation until Step 4.2 makes the repository method
    itself vid-aware (PROJECT_SPEC.md's own §9 marker, owner Step 4.2) --
    not a redundant belt-and-suspenders check, the ONLY vid check that
    exists anywhere right now.
    """
    if conversation_id is None:
        conversation = await repo.create_conversation(vid=identity.vid)
        await repo.session.commit()
        return conversation

    conversation = await repo.get_conversation_by_id(conversation_id)
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


@router.options("/chat", include_in_schema=False)
async def chat_preflight(request: Request) -> Response:
    # Mirrors app/auth/routes.py's own session_preflight() exactly --
    # no Depends(get_db_session), no body parsing, cannot touch the
    # database. Authorization is additionally an allowed request header
    # here (the bearer token), unlike /session's own preflight, which
    # needs none.
    origins = request.headers.getlist("origin")
    headers = {
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
        "Vary": "Origin",
    }
    if len(origins) == 1:
        headers["Access-Control-Allow-Origin"] = origins[0]
    return Response(status_code=200, headers=headers)


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
    current state FIRST, while still holding the per-thread advisory
    lock, and returns the ALREADY-PERSISTED AIMessage (plus its own
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
    conn,
    active_streams: set[uuid.UUID],
    identity: VerifiedIdentity,
    conversation_id: uuid.UUID,
    graph,
    message: str,
    client_message_id: uuid.UUID,
    existing_answer: AIMessage | None,
    existing_values: dict,
    chat_request_timeout_seconds: float,
) -> AsyncIterator[dict]:
    """The real SSE body. Takes ownership of an ALREADY-OPEN `conn`
    (the caller -- create_chat_turn() below -- already acquired the
    per-thread advisory lock on it and already ran the idempotency check,
    both BEFORE this generator -- and therefore before the stream itself
    -- ever starts, so a busy lock or a stale retry can return a clean
    409 instead of an SSE error event; see this module's own header
    comment for why the lock cannot simply be released before streaming
    begins). Releases `conn` (and therefore the lock) and clears this
    visitor's own active-stream marker in a `finally` that runs on
    success, on a raised exception, AND on the generator being
    `.aclose()`'d early (a real client disconnect -- Python's own async
    generator semantics deliver this as a GeneratorExit at the current
    `await`, which propagates through this function's own `try/finally`
    exactly like any other exception; confirmed live, not assumed -- see
    this task's own dedicated cancellation test).
    """
    event_id = 0

    async with AsyncExitStack() as stack:
        stack.push_async_callback(conn.close)
        stack.callback(active_streams.discard, identity.vid)

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
                {"message_id": str(uuid.uuid4()), "usage": None, "offer_escalation": False},
            )
            return

        human_message = HumanMessage(content=message, id=str(client_message_id))
        graph_input = {"messages": [human_message]}

        try:
            async with asyncio.timeout(chat_request_timeout_seconds):
                last_node: str | None = None
                last_output: dict = {}
                async for mode, chunk in run_turn(graph, identity, conversation_id, graph_input):
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

        event_id += 1
        yield _sse_event(
            event_id,
            "done",
            {
                "message_id": str(uuid.uuid4()),
                "usage": None,
                "offer_escalation": last_node in _ESCALATION_OFFERED_NODES,
            },
        )


@router.post("/chat")
async def create_chat_turn(
    payload: ChatRequest,
    request: Request,
    identity: VerifiedIdentity = Depends(get_current_visitor),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> Response:
    # docs/SPEC.md §10: "Auth, rate-limit and quota failures return real
    # HTTP codes (401, 413, 429) before the stream opens." 401 is
    # Depends(get_current_visitor)'s own job, above, before this body ever
    # runs. Quota: TODO(4.3), no quota system exists yet -- Task 4.3's own
    # job, per docs/SPEC.md §9's own "order of checks", deliberately not
    # stubbed here with a fake always-False check, matching this
    # project's own "name what's real vs deferred accurately" discipline.
    if len(payload.message) > _MESSAGE_CHAR_CAP:
        return JSONResponse(
            status_code=_MESSAGE_TOO_LONG_STATUS, content={"detail": "message too long"}
        )

    ip = request.client.host if request.client else "unknown"
    per_ip_limiter = _get_per_ip_limiter(request)
    if not per_ip_limiter.check(ip):
        return _rate_limited_response(per_ip_limiter.get_retry_after(ip))

    per_visitor_limiter = _get_per_visitor_limiter(request)
    if not per_visitor_limiter.check(str(identity.vid)):
        return _rate_limited_response(per_visitor_limiter.get_retry_after(str(identity.vid)))

    active_streams = _get_active_streams(request)
    if identity.vid in active_streams:
        return JSONResponse(
            status_code=_CONFLICT_STATUS,
            content={"detail": "a stream for this visitor is already in progress"},
        )

    repo = TenantScopedRepository(tenant_id=identity.tenant_id, session=session)
    conversation = await _resolve_conversation(repo, identity, payload.conversation_id)

    # Lock acquisition and the idempotency check both happen HERE,
    # synchronously, BEFORE EventSourceResponse is ever constructed --
    # deliberately, so "this conversation is busy" and "this message was
    # already answered/superseded" are real 409s returned before the
    # stream opens (docs/SPEC.md §10's own "before the stream opens"
    # principle, extended from auth/rate-limit/quota to these two
    # Step-4.1-specific conflict cases too), never an SSE error event
    # after the fact. `conn` is deliberately NOT closed on the success
    # path below -- ownership passes to _stream_turn(), which closes it
    # (releasing the lock) when the stream itself ends; see this
    # module's own header comment for the full reasoning.
    settings = get_settings()
    graph = request.app.state.graph
    conn = get_engine().connect()
    await conn.__aenter__()
    try:
        acquired = (
            await conn.execute(
                text("SELECT pg_try_advisory_xact_lock(:key)"),
                {"key": _advisory_lock_key(conversation.cid)},
            )
        ).scalar_one()
        if not acquired:
            await conn.__aexit__(None, None, None)
            return JSONResponse(
                status_code=_CONFLICT_STATUS,
                content={"detail": "this conversation is busy, please retry"},
            )

        try:
            existing_answer, existing_values = await _check_idempotency(
                graph, identity, conversation.cid, payload.client_message_id
            )
        except _Stale:
            await conn.__aexit__(None, None, None)
            return JSONResponse(
                status_code=_CONFLICT_STATUS,
                content={"detail": "this message has already been answered"},
            )
    except BaseException:
        await conn.__aexit__(*sys.exc_info())
        raise

    active_streams.add(identity.vid)

    return EventSourceResponse(
        _stream_turn(
            conn,
            active_streams,
            identity,
            conversation.cid,
            graph,
            payload.message,
            payload.client_message_id,
            existing_answer,
            existing_values,
            settings.chat_request_timeout_seconds,
        ),
        ping=15,
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": identity.org,
            "Vary": "Origin",
        },
    )
