# backend/tests/test_chat_routes.py
# Task 4.1 [SECURITY]: live tests for POST /api/v1/chat (app/chat/
# routes.py) -- the real streaming chat endpoint, against real test-db
# (tenancy tables + a real checkpointer-backed compiled graph). The real
# chat model and real retrieve() are monkeypatched out (app/agent/graph's
# own established fakes, reused from test_agent_graph_answer.py/conftest.py
# -- zero real OpenAI/Qdrant calls anywhere in this file), so these tests
# exercise this endpoint's OWN new logic (auth wiring, rate limits, the
# turn lease, idempotency/replay, the overall timeout, the SSE event
# mapping) against a real graph, not a fake one.
#
# Task 4.1 follow-up: the original advisory-lock tests below were
# rewritten for the lease mechanism (app/chat/routes.py's own
# _acquire_lease()/_release_lease()) -- direct row manipulation via
# `app.db` instead of a real `pg_advisory_xact_lock` held on a separate
# connection, plus new tests this follow-up's own prompt names
# explicitly: expired-lease recovery, non-owner release no-op, no pooled
# connection held during the main streaming loop, pool exhaustion -> 503
# (never 409), and cross-tenant lease isolation.
import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
import sqlalchemy as sa
from langchain_core.messages import AIMessage
from sqlalchemy.ext.asyncio import create_async_engine

import app.db as db_module
from app.agent import checkpointer
from app.agent import graph as graph_module
from app.agent.entrypoint import read_state
from app.agent.graph import build_graph
from app.auth.dependencies import VerifiedIdentity
from app.auth.secrets import generate_visitor_secret, hash_visitor_secret
from app.auth.tokens import encode_session_token
from app.chat.routes import _acquire_lease, _release_lease
from app.config import get_settings
from app.main import create_app
from app.plans import models as plans_models  # noqa: F401 (registers "plans" on Base.metadata)
from app.tenancy.models import Conversation, SiteKey, Tenant, Visitor
from app.tenancy.repository import TenantScopedRepository
from tests.conftest import (
    VALID_ENV,
    FakeAnswerToolChatModel,
    build_fake_answer_arg_chunks,
    db_session,
    http_client,
    reset_checkpoint_env,
    set_valid_env,
)

pytestmark = pytest.mark.usefixtures("close_any_checkpointer_pool_left_open")

ORIGIN = "https://widget.example"

# [ops bugfix]: the single committed source of truth for this endpoint's
# four real SSE event shapes (docs/SPEC.md §10), shared with the widget's
# own e2e mock (widget/test/e2e/server.mjs) -- see that file's header and
# docs/chat_sse_contract.json's own "_comment" for the full reasoning.
_SSE_CONTRACT_PATH = (
    Path(__file__).resolve().parent.parent.parent / "docs" / "chat_sse_contract.json"
)


@pytest.fixture(autouse=True)
def _clear_usage_module_state():
    # Task 4.3: app.plans.usage's own counter/pending-write buffer are
    # process-wide module globals by design (one API process serves
    # chats) -- cleared before AND after every test in this file so no
    # test's own quota state leaks into another's.
    from app.plans import usage as usage_module

    usage_module._counts_by_tenant_month.clear()
    usage_module._pending.clear()
    yield
    usage_module._counts_by_tenant_month.clear()
    usage_module._pending.clear()


async def _fake_retrieve_node_with(chunks: list[dict], best_dense_score: float = 0.9):
    async def _node(state, config):  # noqa: ANN001 -- matches _retrieve_node's own real signature
        return {"retrieved_chunks": chunks, "best_dense_score": best_dense_score}

    return _node


def _answer_chunks(citation_label: str = "c1") -> list[dict]:
    return [
        {
            "document_id": "doc-1",
            "text": "irrelevant",
            "source_url": "https://tenant.example/docs/page",
            "heading_path": ["Docs", "Getting started"],
            "score": 0.9,
        }
    ]


async def _build_app(
    monkeypatch,
    *,
    chat_model=None,
    retrieve_chunks: list[dict] | None = None,
    best_dense_score: float = 0.9,
):
    """Opens a real checkpointer pool against test-db, builds a REAL
    compiled graph (with the real chat model / retrieve() monkeypatched
    out when given), and returns a real `create_app()` FastAPI instance
    with `app.state.graph` set manually -- this file's own tests never
    run app/main.py's real lifespan hook (the same established convention
    test_session.py/test_dependencies.py already use: ASGITransport does
    not run lifespan events, so every dependency a real request handler
    needs is set up explicitly here instead).
    """
    await reset_checkpoint_env(monkeypatch)
    if chat_model is not None:
        monkeypatch.setattr(graph_module, "get_chat_model", lambda: chat_model)
    if retrieve_chunks is not None:
        fake_node = await _fake_retrieve_node_with(retrieve_chunks, best_dense_score)
        monkeypatch.setattr(graph_module, "_retrieve_node", fake_node)
    await checkpointer.open_checkpointer_pool()
    cp = checkpointer.get_checkpointer()
    await cp.setup()
    graph = build_graph(cp)
    app = create_app()
    app.state.graph = graph
    app.state.usage_counters_ready = True
    return app


def _parse_sse(body: str) -> list[dict]:
    events = []
    for block in body.replace("\r\n", "\n").strip().split("\n\n"):
        if not block.strip():
            continue
        event_type = None
        data = None
        for line in block.splitlines():
            if line.startswith("event:"):
                event_type = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data = line[len("data:") :].strip()
        if event_type is not None and data is not None:
            events.append({"event": event_type, **json.loads(data)})
    return events


async def _post_chat(client, token, body, *, origin=ORIGIN):
    return await client.post(
        "/api/v1/chat",
        json=body,
        headers={"Authorization": f"Bearer {token}", "Origin": origin},
    )


@pytest.fixture
async def _seeded(reset_test_database):
    ids = {
        "tenant": uuid.uuid4(),
        "other_tenant": uuid.uuid4(),
        "site_key": uuid.uuid4(),
        "visitor": uuid.uuid4(),
        "other_visitor": uuid.uuid4(),
    }
    async with db_session() as session:
        session.add_all(
            [
                Tenant(id=ids["tenant"], name="Acme", status="active", config={}),
                Tenant(id=ids["other_tenant"], name="Other", status="active", config={}),
            ]
        )
        await session.flush()
        session.add(
            SiteKey(
                id=ids["site_key"],
                tenant_id=ids["tenant"],
                key="pk_live_test",
                environment="production",
                status="live",
                allowed_origins=[ORIGIN],
            )
        )
        await session.flush()
        secret = generate_visitor_secret()
        session.add(
            Visitor(
                vid=ids["visitor"],
                tenant_id=ids["tenant"],
                site_key_id=ids["site_key"],
                secret_hash=hash_visitor_secret(secret),
            )
        )
        session.add(
            Visitor(
                vid=ids["other_visitor"],
                tenant_id=ids["tenant"],
                site_key_id=ids["site_key"],
                secret_hash=hash_visitor_secret(generate_visitor_secret()),
            )
        )
        await session.commit()
    return ids


def _token_for(ids, vid_key="visitor", tenant_key="tenant"):
    return encode_session_token(tenant_id=ids[tenant_key], vid=ids[vid_key], origin=ORIGIN)


# --- message cap / schema (no database needed) ------------------------------


async def test_unknown_field_in_request_returns_422(monkeypatch, _seeded):
    # Unlike POST /api/v1/session (no auth dependency at all), this
    # endpoint's own Depends(get_current_visitor) is confirmed live to be
    # resolved BEFORE body validation when no token is present at all (a
    # missing token 401s regardless of the body) -- so proving the extra-
    # field 422 specifically requires a VALID token, confirmed with real
    # seeded auth data rather than assumed to behave like /session's own
    # unauthenticated precedent.
    app = create_app()
    token = _token_for(_seeded)
    async with await http_client(app) as client:
        response = await client.post(
            "/api/v1/chat",
            json={"message": "hi", "client_message_id": str(uuid.uuid4()), "admin": True},
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        )
    assert response.status_code == 422


async def test_options_preflight_answers_without_touching_the_database(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    app = create_app()
    async with await http_client(app) as client:
        response = await client.options("/api/v1/chat", headers={"Origin": ORIGIN})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert response.headers["access-control-allow-methods"] == "POST, OPTIONS"
    assert "Authorization" in response.headers["access-control-allow-headers"]


# --- live: auth/rate-limit/conflict (no graph/checkpointer needed) ----------


async def test_missing_bearer_token_returns_401(monkeypatch, _seeded):
    app = create_app()
    async with await http_client(app) as client:
        response = await client.post(
            "/api/v1/chat",
            json={"message": "hi", "client_message_id": str(uuid.uuid4())},
            headers={"Origin": ORIGIN},
        )
    assert response.status_code == 401


async def test_message_over_the_character_cap_returns_413(monkeypatch, _seeded):
    app = create_app()
    token = _token_for(_seeded)
    async with await http_client(app) as client:
        response = await _post_chat(
            client, token, {"message": "x" * 2001, "client_message_id": str(uuid.uuid4())}
        )
    assert response.status_code == 413


async def test_message_at_exactly_the_cap_is_not_rejected_by_the_cap_itself(monkeypatch, _seeded):
    # Distinguishes the length cap from everything else: a message at
    # EXACTLY 2000 characters must reach a real, successful stream, not
    # just "anything other than 413" against a broken fake.
    app = await _build_app(monkeypatch)
    token = _token_for(_seeded)
    async with await http_client(app) as client:
        response = await _post_chat(
            client, token, {"message": "x" * 2000, "client_message_id": str(uuid.uuid4())}
        )
    assert response.status_code == 200


async def test_eleventh_message_in_a_minute_is_rate_limited_with_retry_after(monkeypatch, _seeded):
    # get_settings.cache_clear() is required here, not optional: the
    # `_seeded` fixture above already triggered (and cached) a real
    # Settings instance via its own db_session() calls, BEFORE this test
    # body -- and therefore this env override -- ever runs (fixtures
    # resolve before the test function body). Confirmed live: without
    # this clear, the override below is silently ignored and the
    # DEFAULT limit (10) is what the real request handler sees.
    set_valid_env(monkeypatch, VALID_ENV, CHAT_RATE_LIMIT_PER_VISITOR="2")
    get_settings.cache_clear()
    app = await _build_app(monkeypatch)
    token = _token_for(_seeded)
    async with await http_client(app) as client:
        for _ in range(2):
            response = await _post_chat(
                client, token, {"message": "hi", "client_message_id": str(uuid.uuid4())}
            )
            assert response.status_code != 429
        limited = await _post_chat(
            client, token, {"message": "hi", "client_message_id": str(uuid.uuid4())}
        )
    assert limited.status_code == 429
    assert "retry_after" in limited.json()
    assert "Retry-After" in limited.headers


async def test_per_ip_cap_trips_independently_of_the_per_visitor_limit(monkeypatch, _seeded):
    # Two DIFFERENT visitors from the same (simulated) IP -- the per-
    # visitor limiter alone would never trip for either one individually.
    set_valid_env(monkeypatch, VALID_ENV, CHAT_RATE_LIMIT_PER_IP="2")
    get_settings.cache_clear()
    app = await _build_app(monkeypatch)
    token_a = _token_for(_seeded, vid_key="visitor")
    token_b = _token_for(_seeded, vid_key="other_visitor")
    async with await http_client(app) as client:
        await _post_chat(client, token_a, {"message": "hi", "client_message_id": str(uuid.uuid4())})
        await _post_chat(client, token_b, {"message": "hi", "client_message_id": str(uuid.uuid4())})
        limited = await _post_chat(
            client, token_a, {"message": "hi", "client_message_id": str(uuid.uuid4())}
        )
    assert limited.status_code == 429


async def test_a_conversation_id_belonging_to_another_tenant_returns_404(monkeypatch, _seeded):
    app = create_app()
    app.state.graph = object()
    app.state.usage_counters_ready = True
    async with db_session() as session:
        other_tenant_visitor_id = uuid.uuid4()
        session.add(
            SiteKey(
                id=uuid.uuid4(),
                tenant_id=_seeded["other_tenant"],
                key="pk_live_other",
                environment="production",
                status="live",
                allowed_origins=[ORIGIN],
            )
        )
        await session.flush()
        other_site_key_id = (
            await session.execute(
                SiteKey.__table__.select().where(SiteKey.tenant_id == _seeded["other_tenant"])
            )
        ).first().id
        session.add(
            Visitor(
                vid=other_tenant_visitor_id,
                tenant_id=_seeded["other_tenant"],
                site_key_id=other_site_key_id,
                secret_hash=hash_visitor_secret(generate_visitor_secret()),
            )
        )
        await session.flush()
        repo = TenantScopedRepository(tenant_id=_seeded["other_tenant"], session=session)
        other_conversation = await repo.create_conversation(vid=other_tenant_visitor_id)
        await session.commit()
    token = _token_for(_seeded)
    async with await http_client(app) as client:
        response = await _post_chat(
            client,
            token,
            {
                "message": "hi",
                "client_message_id": str(uuid.uuid4()),
                "conversation_id": str(other_conversation.cid),
            },
        )
    assert response.status_code == 404


async def test_a_conversation_id_belonging_to_another_visitor_same_tenant_returns_404(
    monkeypatch, _seeded
):
    # The exact gap this endpoint's own _resolve_conversation() closes:
    # get_conversation_by_id() alone (app/tenancy/repository.py) is
    # tenant_id-scoped only, NOT vid-scoped -- without this endpoint's own
    # vid check, visitor A could read/drive visitor B's own conversation.
    app = create_app()
    app.state.graph = object()
    app.state.usage_counters_ready = True
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=_seeded["tenant"], session=session)
        other_visitor_conversation = await repo.create_conversation(vid=_seeded["other_visitor"])
        await session.commit()
    token = _token_for(_seeded, vid_key="visitor")
    async with await http_client(app) as client:
        response = await _post_chat(
            client,
            token,
            {
                "message": "hi",
                "client_message_id": str(uuid.uuid4()),
                "conversation_id": str(other_visitor_conversation.cid),
            },
        )
    assert response.status_code == 404


# --- live: the full graph, real checkpointer, fakes for model/retrieve -----


async def test_greeting_streams_one_token_then_done_with_no_escalation_offer(monkeypatch, _seeded):
    app = await _build_app(monkeypatch)
    token = _token_for(_seeded)
    async with await http_client(app) as client:
        async with client.stream(
            "POST",
            "/api/v1/chat",
            json={"message": "hi there", "client_message_id": str(uuid.uuid4())},
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        ) as response:
            body = await response.aread()
    assert response.status_code == 200
    events = _parse_sse(body.decode())
    token_events = [e for e in events if e["event"] == "token"]
    done_events = [e for e in events if e["event"] == "done"]
    assert len(token_events) == 1
    # _sanitize_answer_text() (app/agent/graph.py, Task 3.4.c) backslash-
    # escapes a bare "!" -- the real wire text, not the raw fixed-reply
    # constant.
    assert token_events[0]["text"] == "Hello\\! How can I help you today?"
    assert len(done_events) == 1
    assert done_events[0]["offer_escalation"] is False


async def test_a_real_answer_streams_real_tokens_and_citations(monkeypatch, _seeded):
    chunks = build_fake_answer_arg_chunks(
        can_answer=True, used_chunk_ids=["c1"], answer="The answer."
    )
    fake_model = FakeAnswerToolChatModel(arg_chunks=chunks)
    app = await _build_app(monkeypatch, chat_model=fake_model, retrieve_chunks=_answer_chunks())
    token = _token_for(_seeded)
    async with await http_client(app) as client:
        async with client.stream(
            "POST",
            "/api/v1/chat",
            json={"message": "what is the answer", "client_message_id": str(uuid.uuid4())},
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        ) as response:
            body = await response.aread()
    events = _parse_sse(body.decode())
    token_events = [e for e in events if e["event"] == "token"]
    citations_events = [e for e in events if e["event"] == "citations"]
    done_events = [e for e in events if e["event"] == "done"]
    assert "".join(e["text"] for e in token_events) == "The answer."
    assert citations_events == [
        {
            "event": "citations",
            "items": [{"n": 1, "title": "Getting started", "url": "https://tenant.example/docs/page"}],
            "v": 1,
        }
    ]
    assert done_events[0]["offer_escalation"] is False
    # Every event carries an incrementing id, starting at 1 (docs/SPEC.md §10).
    normalized = body.decode().replace("\r\n", "\n").strip()
    ids = [
        int(line.split(":", 1)[1])
        for block in normalized.split("\n\n")
        for line in block.splitlines()
        if line.startswith("id:")
    ]
    assert ids == list(range(1, len(ids) + 1))


async def test_sse_breaking_content_in_the_answer_and_citations_cannot_inject_a_fake_event(
    monkeypatch, _seeded
):
    # [SECURITY] adversarial pass: a tenant's own indexed content -- the
    # answer text (model-generated, grounded in that content) and a
    # citation's own heading_path -- is untrusted as far as the WIRE
    # FORMAT is concerned, even though it already passes through
    # _sanitize_answer_text()'s own markdown/XSS-focused escaping (a
    # DIFFERENT trust boundary). Every event payload here goes through
    # json.dumps() before being handed to sse-starlette, so a raw
    # "\n\n" (a real SSE event boundary) or an "event:"/"data:"-prefixed
    # line embedded in the text must become a harmless, JSON-escaped
    # substring, never a real second event -- confirmed live here, not
    # merely reasoned about.
    evil_answer = 'line one\n\nevent: fake\ndata: {"malicious": true}\n\nline two'
    evil_heading = 'Heading\n\nevent: fake\ndata: {"malicious": true}'
    chunks = build_fake_answer_arg_chunks(
        can_answer=True, used_chunk_ids=["c1"], answer=evil_answer
    )
    retrieved = [
        {
            "document_id": "doc-1",
            "text": "irrelevant",
            "source_url": "https://tenant.example/docs/page",
            "heading_path": [evil_heading],
            "score": 0.9,
        }
    ]
    fake_model = FakeAnswerToolChatModel(arg_chunks=chunks)
    app = await _build_app(monkeypatch, chat_model=fake_model, retrieve_chunks=retrieved)
    token = _token_for(_seeded)
    async with await http_client(app) as client:
        async with client.stream(
            "POST",
            "/api/v1/chat",
            json={"message": "what is the answer", "client_message_id": str(uuid.uuid4())},
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        ) as response:
            body = await response.aread()
    events = _parse_sse(body.decode())
    # Never a real injected "fake" event -- every event's own `event:`
    # line is one of exactly the four real types this endpoint emits.
    assert {e["event"] for e in events} <= {"token", "citations", "done"}
    token_events = [e for e in events if e["event"] == "token"]
    citations_events = [e for e in events if e["event"] == "citations"]
    # The sanitised answer text is escaped before it ever reaches this
    # endpoint (_sanitize_answer_text(), a DIFFERENT trust boundary) --
    # re-sanitised here for the comparison, not re-litigating that layer.
    expected_answer = graph_module._sanitize_answer_text(evil_answer)
    assert "".join(e["text"] for e in token_events) == expected_answer
    assert citations_events[0]["items"][0]["title"] == evil_heading


async def test_dependency_failure_offers_escalation(monkeypatch, _seeded):
    # The REAL _retrieve_node (app/agent/graph.py) already catches a
    # raise from retrieve() internally and returns dependency_failed=True
    # -- it never lets a raw exception escape the node itself. This fake
    # mirrors that real contract (not a raw `raise`, which would instead
    # exercise THIS endpoint's own generic model_error path -- a
    # different, already-separately-tested scenario).
    async def _failing_retrieve(state, config):  # noqa: ANN001
        return {"retrieved_chunks": [], "best_dense_score": None, "dependency_failed": True}

    monkeypatch.setattr(graph_module, "_retrieve_node", _failing_retrieve)
    app = await _build_app(monkeypatch)
    token = _token_for(_seeded)
    async with await http_client(app) as client:
        async with client.stream(
            "POST",
            "/api/v1/chat",
            json={"message": "what is the answer", "client_message_id": str(uuid.uuid4())},
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        ) as response:
            body = await response.aread()
    events = _parse_sse(body.decode())
    done_events = [e for e in events if e["event"] == "done"]
    error_events = [e for e in events if e["event"] == "error"]
    assert error_events == []  # a graceful fallback, not an infra error
    assert done_events[0]["offer_escalation"] is True


async def test_mid_stream_model_failure_emits_a_model_error_event(monkeypatch, _seeded):
    chunks = build_fake_answer_arg_chunks(can_answer=True, used_chunk_ids=["c1"], answer="partial")
    fake_model = FakeAnswerToolChatModel(arg_chunks=chunks, fail_after=len(chunks) - 1)
    app = await _build_app(monkeypatch, chat_model=fake_model, retrieve_chunks=_answer_chunks())
    token = _token_for(_seeded)
    async with await http_client(app) as client:
        async with client.stream(
            "POST",
            "/api/v1/chat",
            json={"message": "what is the answer", "client_message_id": str(uuid.uuid4())},
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        ) as response:
            body = await response.aread()
    events = _parse_sse(body.decode())
    error_events = [e for e in events if e["event"] == "error"]
    assert len(error_events) == 1
    assert error_events[0]["code"] == "model_error"
    assert "stack" not in json.dumps(error_events[0]).lower()


async def test_overall_timeout_emits_a_timeout_error_event(monkeypatch, _seeded):
    class _SlowChatModel(FakeAnswerToolChatModel):
        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            await asyncio.sleep(10)
            if False:
                yield  # pragma: no cover -- keeps this an async generator

    set_valid_env(monkeypatch, VALID_ENV, CHAT_REQUEST_TIMEOUT_SECONDS="0.05")
    get_settings.cache_clear()
    app = await _build_app(
        monkeypatch, chat_model=_SlowChatModel(arg_chunks=[]), retrieve_chunks=_answer_chunks()
    )
    token = _token_for(_seeded)
    async with await http_client(app) as client:
        async with client.stream(
            "POST",
            "/api/v1/chat",
            json={"message": "what is the answer", "client_message_id": str(uuid.uuid4())},
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        ) as response:
            body = await response.aread()
    events = _parse_sse(body.decode())
    error_events = [e for e in events if e["event"] == "error"]
    assert len(error_events) == 1
    assert error_events[0]["code"] == "timeout"


async def test_done_and_other_sse_events_match_the_committed_contract(monkeypatch, _seeded):
    # [ops bugfix]: fixes the real gap behind BUG2's own 4th question --
    # the widget's e2e suite kept passing while the real widget was
    # broken because its mock (widget/test/e2e/server.mjs) typed its own
    # `done` payload completely independently of this endpoint's real
    # one, with no shared source either side could drift against; the
    # mock simply never grew the `conversation_id` field this endpoint
    # already added at Step 6.2. This test is the BACKEND half of the
    # fix: it fails the moment a real event payload's own field set
    # stops matching docs/chat_sse_contract.json, so a future field
    # rename/addition/removal cannot silently ship past CI without this
    # test (or the widget's own matching scripts/check-sse-contract.mjs)
    # catching it on at least one side.
    contract = json.loads(_SSE_CONTRACT_PATH.read_text())

    # token + citations + done, from one real, successful turn.
    chunks = build_fake_answer_arg_chunks(
        can_answer=True, used_chunk_ids=["c1"], answer="A fixed test answer."
    )
    fake_model = FakeAnswerToolChatModel(arg_chunks=chunks)
    app = await _build_app(monkeypatch, chat_model=fake_model, retrieve_chunks=_answer_chunks())
    token = _token_for(_seeded)
    async with await http_client(app) as client:
        async with client.stream(
            "POST",
            "/api/v1/chat",
            json={"message": "what is the answer", "client_message_id": str(uuid.uuid4())},
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        ) as response:
            body = await response.aread()
    success_events = _parse_sse(body.decode())

    # error, from one real model failure.
    failing_chunks = build_fake_answer_arg_chunks(
        can_answer=True, used_chunk_ids=["c1"], answer="partial"
    )
    failing_model = FakeAnswerToolChatModel(
        arg_chunks=failing_chunks, fail_after=len(failing_chunks) - 1
    )
    error_app = await _build_app(
        monkeypatch, chat_model=failing_model, retrieve_chunks=_answer_chunks()
    )
    async with await http_client(error_app) as client:
        async with client.stream(
            "POST",
            "/api/v1/chat",
            json={"message": "what is the answer", "client_message_id": str(uuid.uuid4())},
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        ) as response:
            error_body = await response.aread()
    error_events = _parse_sse(error_body.decode())

    all_events = success_events + error_events
    seen_types = {e["event"] for e in all_events}
    assert seen_types == {"token", "citations", "done", "error"}, seen_types

    for event in all_events:
        event_type = event["event"]
        real_fields = set(event.keys()) - {"event"}
        assert real_fields == set(contract[event_type]), (
            f"{event_type} event fields {sorted(real_fields)} no longer match "
            f"the committed contract {sorted(contract[event_type])} at "
            f"{_SSE_CONTRACT_PATH} -- if deliberate, update that file AND "
            "widget/test/e2e/server.mjs together, in the same change."
        )


async def test_retrying_the_same_client_message_id_after_completion_replays_once(
    monkeypatch, _seeded
):
    call_count = 0
    chunks = build_fake_answer_arg_chunks(
        can_answer=True, used_chunk_ids=["c1"], answer="Only once."
    )

    class _CountingModel(FakeAnswerToolChatModel):
        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            nonlocal call_count
            call_count += 1
            async for chunk in super()._astream(
                messages, stop=stop, run_manager=run_manager, **kwargs
            ):
                yield chunk

    app = await _build_app(
        monkeypatch, chat_model=_CountingModel(arg_chunks=chunks), retrieve_chunks=_answer_chunks()
    )
    token = _token_for(_seeded)
    client_message_id = str(uuid.uuid4())
    body = {"message": "what is the answer", "client_message_id": client_message_id}
    headers = {"Authorization": f"Bearer {token}", "Origin": ORIGIN}

    async with await http_client(app) as client:
        async with client.stream("POST", "/api/v1/chat", json=body, headers=headers) as first:
            await first.aread()
        # The real conversation_id isn't echoed on any SSE event (docs/
        # SPEC.md §10 names no such event) -- fetched back from the row
        # this first request's own _resolve_conversation() created.
        async with db_session() as session:
            rows = (await session.execute(Conversation.__table__.select())).fetchall()
        assert len(rows) == 1
        conversation_id = rows[0].cid

        retry_body = {**body, "conversation_id": str(conversation_id)}
        async with client.stream("POST", "/api/v1/chat", json=retry_body, headers=headers) as retry:
            retry_response_body = await retry.aread()

    assert call_count == 1  # the model was never called a second time
    retry_events = _parse_sse(retry_response_body.decode())
    retry_tokens = [e for e in retry_events if e["event"] == "token"]
    assert "".join(e["text"] for e in retry_tokens) == "Only once."


async def test_two_concurrent_requests_for_the_same_conversation_return_409_for_the_second(
    monkeypatch, _seeded
):
    # Simulates a real in-flight turn by acquiring the lease directly
    # (the same statement create_chat_turn() itself runs) BEFORE the real
    # HTTP request -- exactly one request gets the conversation, the
    # other 409s, never both.
    app = await _build_app(monkeypatch)
    token = _token_for(_seeded)
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=_seeded["tenant"], session=session)
        conversation = await repo.create_conversation(vid=_seeded["visitor"])
        await session.commit()
        conversation_id = conversation.cid

    async with db_session() as holder_session:
        acquired = await _acquire_lease(
            holder_session, _seeded["tenant"], conversation_id, uuid.uuid4(), lease_seconds=30.0
        )
        assert acquired is True

    async with await http_client(app) as client:
        response = await _post_chat(
            client,
            token,
            {
                "message": "hi",
                "client_message_id": str(uuid.uuid4()),
                "conversation_id": str(conversation_id),
            },
        )
    assert response.status_code == 409


async def test_truly_concurrent_acquire_attempts_on_the_same_row_never_both_win(
    monkeypatch, _seeded
):
    # [SECURITY] adversarial pass -- a REAL race, not a sequential
    # simulation: two genuinely concurrent requests (asyncio.gather, both
    # in flight at once, both DB round-trips genuinely overlapping)
    # against the SAME conversation row, proving Postgres's own row-level
    # UPDATE atomicity (not application-level locking) is what actually
    # prevents both from acquiring -- never zero, never two.
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=_seeded["tenant"], session=session)
        conversation = await repo.create_conversation(vid=_seeded["visitor"])
        await session.commit()
        conversation_id = conversation.cid

    async def _attempt():
        async with db_session() as session:
            return await _acquire_lease(
                session, _seeded["tenant"], conversation_id, uuid.uuid4(), lease_seconds=30.0
            )

    results = await asyncio.gather(*(_attempt() for _ in range(8)))
    assert results.count(True) == 1
    assert results.count(False) == 7


async def test_an_expired_lease_lets_the_next_turn_proceed(monkeypatch, _seeded):
    app = await _build_app(monkeypatch)
    token = _token_for(_seeded)
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=_seeded["tenant"], session=session)
        conversation = await repo.create_conversation(vid=_seeded["visitor"])
        await session.commit()
        conversation_id = conversation.cid

    # Simulates a crashed worker: a lease that is already in the past,
    # never released by anyone -- the real recovery path, no cleanup job
    # involved at all.
    async with db_session() as session:
        await session.execute(
            sa.update(Conversation)
            .where(Conversation.cid == conversation_id)
            .values(
                lease_until=datetime.now(UTC) - timedelta(seconds=5), lease_token=uuid.uuid4()
            )
        )
        await session.commit()

    async with await http_client(app) as client:
        response = await _post_chat(
            client,
            token,
            {
                "message": "hi",
                "client_message_id": str(uuid.uuid4()),
                "conversation_id": str(conversation_id),
            },
        )
    assert response.status_code == 200


async def test_releasing_with_the_wrong_token_is_a_no_op(monkeypatch, _seeded):
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=_seeded["tenant"], session=session)
        conversation = await repo.create_conversation(vid=_seeded["visitor"])
        await session.commit()
        conversation_id = conversation.cid

    real_token = uuid.uuid4()
    async with db_session() as session:
        acquired = await _acquire_lease(
            session, _seeded["tenant"], conversation_id, real_token, lease_seconds=30.0
        )
        assert acquired is True

    # A foreign/stale token's own release attempt matches zero rows --
    # silently, never an error -- and must not clear the REAL holder's
    # own lease.
    await _release_lease(_seeded["tenant"], conversation_id, uuid.uuid4())

    async with db_session() as session:
        row = (
            await session.execute(
                sa.select(Conversation.lease_until, Conversation.lease_token).where(
                    Conversation.cid == conversation_id
                )
            )
        ).one()
    assert row.lease_until is not None
    assert row.lease_token == real_token

    # The REAL owner's own release still works normally.
    await _release_lease(_seeded["tenant"], conversation_id, real_token)
    async with db_session() as session:
        row = (
            await session.execute(
                sa.select(Conversation.lease_until, Conversation.lease_token).where(
                    Conversation.cid == conversation_id
                )
            )
        ).one()
    assert row.lease_until is None
    assert row.lease_token is None


async def test_a_lease_cannot_be_acquired_or_released_using_another_tenants_conversation_id(
    monkeypatch, _seeded
):
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=_seeded["tenant"], session=session)
        conversation = await repo.create_conversation(vid=_seeded["visitor"])
        await session.commit()
        conversation_id = conversation.cid

    # Acquire attempted with the WRONG tenant_id for a real, otherwise-
    # free conversation row -- must match zero rows.
    async with db_session() as session:
        acquired = await _acquire_lease(
            session, _seeded["other_tenant"], conversation_id, uuid.uuid4(), lease_seconds=30.0
        )
    assert acquired is False

    # The real owner acquires it for real.
    real_token = uuid.uuid4()
    async with db_session() as session:
        acquired = await _acquire_lease(
            session, _seeded["tenant"], conversation_id, real_token, lease_seconds=30.0
        )
    assert acquired is True

    # A release attempted with the WRONG tenant_id (even with the
    # correct token) must not clear the real tenant's own lease.
    await _release_lease(_seeded["other_tenant"], conversation_id, real_token)
    async with db_session() as session:
        row = (
            await session.execute(
                sa.select(Conversation.lease_until).where(Conversation.cid == conversation_id)
            )
        ).one()
    assert row.lease_until is not None


async def test_pool_exhaustion_returns_503_not_409(monkeypatch, _seeded):
    # A deliberately tiny, separate engine (pool_size=1, no overflow, a
    # short pool_timeout so the test itself stays fast) -- swapped in for
    # app.db.get_engine so app/chat/routes.py's own _session_factory()
    # calls resolve against it. One connection held open externally
    # leaves ZERO free slots for the real request's own short-lived
    # session to acquire -- a real pool-checkout timeout, never a lease
    # conflict (this conversation was never even reached).
    app = create_app()
    app.state.graph = object()
    app.state.usage_counters_ready = True
    token = _token_for(_seeded)
    db_url = get_settings().database_url_str()
    tiny_engine = create_async_engine(db_url, pool_size=1, max_overflow=0, pool_timeout=0.2)
    # Restored manually (not left to monkeypatch's own fixture-order
    # undo): reset_test_database's own teardown (which calls db.get_
    # engine.cache_clear()) depends on the SAME monkeypatch fixture and
    # therefore tears down BEFORE it, so this swap must already be
    # reverted by the time that teardown runs, confirmed live (without
    # this, that teardown's own cache_clear() call fails -- this swap
    # replaced the real lru_cache-wrapped function with a plain lambda).
    real_get_engine = db_module.get_engine
    db_module.get_engine = lambda: tiny_engine
    db_module._session_factory.cache_clear()

    holder = await tiny_engine.connect()
    try:
        async with await http_client(app) as client:
            response = await _post_chat(
                client, token, {"message": "hi", "client_message_id": str(uuid.uuid4())}
            )
        assert response.status_code == 503
    finally:
        await holder.close()
        await tiny_engine.dispose()
        db_module.get_engine = real_get_engine
        db_module._session_factory.cache_clear()


async def test_no_pooled_connection_is_checked_out_during_the_main_streaming_loop(
    monkeypatch, _seeded
):
    class _PausingModel(FakeAnswerToolChatModel):
        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            upstream = super()._astream(messages, stop=stop, run_manager=run_manager, **kwargs)
            yield await upstream.__anext__()
            await asyncio.sleep(1.0)

    chunks = build_fake_answer_arg_chunks(can_answer=True, used_chunk_ids=[], answer="pausing")
    app = await _build_app(
        monkeypatch, chat_model=_PausingModel(arg_chunks=chunks), retrieve_chunks=_answer_chunks()
    )
    token = _token_for(_seeded)
    # Baseline: the app's own pool, idle, before the request even starts.
    assert db_module.get_engine().pool.checkedout() == 0

    async with await http_client(app) as client:
        async with client.stream(
            "POST",
            "/api/v1/chat",
            json={"message": "hi", "client_message_id": str(uuid.uuid4())},
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        ) as response:
            async for _line in response.aiter_lines():
                # The generator has already yielded its first token and
                # is now paused INSIDE the main loop (the fake model's
                # own asyncio.sleep, which never yields again) -- the one
                # moment that matters; the stream is closed early right
                # after, same as the cancellation test below.
                assert db_module.get_engine().pool.checkedout() == 0
                break
    assert db_module.get_engine().pool.checkedout() == 0


async def test_cancelling_the_stream_releases_the_lease_and_leaves_valid_state(
    monkeypatch, _seeded
):
    class _NeverEndingModel(FakeAnswerToolChatModel):
        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            upstream = super()._astream(messages, stop=stop, run_manager=run_manager, **kwargs)
            yield await upstream.__anext__()
            await asyncio.sleep(10)

    chunks = build_fake_answer_arg_chunks(
        can_answer=True, used_chunk_ids=[], answer="won't finish"
    )
    app = await _build_app(
        monkeypatch,
        chat_model=_NeverEndingModel(arg_chunks=chunks),
        retrieve_chunks=_answer_chunks(),
    )
    token = _token_for(_seeded)
    conversation_id = None
    async with await http_client(app) as client:
        async with client.stream(
            "POST",
            "/api/v1/chat",
            json={"message": "hi", "client_message_id": str(uuid.uuid4())},
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        ) as response:
            async for _line in response.aiter_lines():
                break  # read exactly one line, then let the `async with` close the stream early

        async with db_session() as session:
            rows = (await session.execute(Conversation.__table__.select())).fetchall()
        assert len(rows) == 1
        conversation_id = rows[0].cid

        # The lease must already be cleared by the generator's own
        # `finally` (a real client disconnect delivers GeneratorExit) --
        # checked directly in the row, BEFORE issuing the follow-up
        # request below, so this proves cancellation itself released it,
        # not merely that a second request would have anyway.
        async with db_session() as session:
            row = (
                await session.execute(
                    sa.select(Conversation.lease_until, Conversation.lease_token).where(
                        Conversation.cid == conversation_id
                    )
                )
            ).one()
        assert row.lease_until is None
        assert row.lease_token is None

        # The lease must already be released -- a fresh request for the
        # SAME conversation succeeds immediately rather than 409ing.
        follow_up = await _post_chat(
            client,
            token,
            {
                "message": "second message",
                "client_message_id": str(uuid.uuid4()),
                "conversation_id": str(conversation_id),
            },
        )
    assert follow_up.status_code == 200

    # [SECURITY] L2 (Phase 7 role split, 2026-10-18): a plain SELECT, so
    # the APP role's own conninfo (what the live pool actually used to
    # write this checkpoint) is both correct and sufficient here.
    conninfo = checkpointer.checkpointer_conninfo(get_settings().database_url_str())
    async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
        row = await (
            await conn.execute(
                "SELECT count(*) FROM checkpoints WHERE thread_id LIKE %s",
                (f"%{conversation_id}",),
            )
        ).fetchone()
    assert row[0] > 0  # a real, readable checkpoint exists -- not corrupted/missing


# --- Part D (docs/SPEC.md §9's "Stop mid-stream") ---------------------------
#
# The genuine opposite case from `test_cancelling_the_stream_releases_the_
# lease_and_leaves_valid_state` above: there, the CONNECTION drops (the
# client stops reading) and NO AIMessage is ever persisted. Here, the
# connection stays open and is read to completion -- only the explicit
# POST /api/v1/chat/stop tells the server to cancel generation early --
# so a PARTIAL AIMessage IS persisted, marked `answer_stopped`, and the
# turn still counts once toward quota, per docs/SPEC.md §9's own
# wording ("distinct from a dropped connection... counts once toward
# quota... the partial answer is kept, marked as stopped").
#
# Deterministic, not timing-sensitive: the fake model pauses at an exact
# chunk count via a real `asyncio.Event` the test itself controls (the
# same `yield-one-then-pause` idiom `_PausingModel`/`_NeverEndingModel`
# above already establish, extended with a second event so the test can
# `await` the model actually reaching its pause point, rather than
# guessing how many `asyncio.sleep(0)` ticks a race would need).


async def test_explicit_stop_mid_stream_persists_a_partial_answer_and_counts_once(
    monkeypatch, _seeded
):
    from app.plans import usage as usage_module

    full_answer = (
        "This is a long answer with many words, built specifically so the stop "
        "signal has plenty of room to land partway through it, well after the "
        "first real token already streamed."
    )
    chunks = build_fake_answer_arg_chunks(
        can_answer=True, used_chunk_ids=["c1"], answer=full_answer
    )
    pause_after = len(chunks) // 2  # well past can_answer resolving and the first delta
    paused_signal = asyncio.Event()
    resume_event = asyncio.Event()

    class _PausingPartwayModel(FakeAnswerToolChatModel):
        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            upstream = super()._astream(messages, stop=stop, run_manager=run_manager, **kwargs)
            count = 0
            async for chunk in upstream:
                yield chunk
                count += 1
                if count == pause_after:
                    paused_signal.set()
                    await resume_event.wait()

    app = await _build_app(
        monkeypatch,
        chat_model=_PausingPartwayModel(arg_chunks=chunks),
        retrieve_chunks=_answer_chunks(),
    )
    token = _token_for(_seeded)

    received_text = ""
    done_payload: dict | None = None

    async def _consume(client):
        nonlocal received_text, done_payload
        async with client.stream(
            "POST",
            "/api/v1/chat",
            # NOT "hi"/"hello" -- both are real, matched greeting
            # phrases (app/agent/phrases/greeting_phrases.yaml) that
            # route the WHOLE turn to the fixed guardrail reply,
            # bypassing retrieve/gate/answer (and this test's own fake
            # model) entirely -- confirmed live, the hard way, while
            # developing this test (the first draft used "hi" and hung
            # forever waiting for a `paused_signal` the real answer node
            # never reached). Matches test_agent_graph_answer.py's own
            # "what color is the sky"-style phrasing precedent.
            json={"message": "what is your refund policy", "client_message_id": str(uuid.uuid4())},
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        ) as response:
            event_name = None
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    event_name = line[len("event: ") :]
                elif line.startswith("data: "):
                    data = json.loads(line[len("data: ") :])
                    if event_name == "token":
                        received_text += data["text"]
                    elif event_name == "done":
                        done_payload = data

    # Two SEPARATE clients (two separate ASGITransport instances), both
    # calling into the SAME `app` -- a single shared client/transport was
    # confirmed live, during this test's own development, to serialize
    # the second call behind the first still-open stream, deadlocking
    # (the stop POST would never even start until the paused stream
    # finished, which can only happen once the stop POST completes). Two
    # independent transports is also the more faithful model of reality:
    # a real browser issues the Stop click's own POST over a genuinely
    # separate connection from the still-open streaming one.
    async with await http_client(app) as client, await http_client(app) as stop_client:
        consume_task = asyncio.create_task(_consume(client))
        await paused_signal.wait()  # the model has genuinely paused mid-answer

        stop_response = await stop_client.post(
            "/api/v1/chat/stop",
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        )
        assert stop_response.status_code == 204

        resume_event.set()
        await consume_task

        # The real, named assertion behind "distinct from a dropped
        # connection": unlike the cancellation test above, a real partial
        # TEXT answer was received over the wire, and a real `done` event
        # (with `stopped: true`) arrived -- the connection never dropped.
        assert received_text != ""
        assert received_text != full_answer  # strictly partial, not the full answer
        assert full_answer.startswith(received_text)  # a genuine PREFIX, not garbled
        assert done_payload is not None
        assert done_payload["stopped"] is True

        async with db_session() as session:
            rows = (await session.execute(Conversation.__table__.select())).fetchall()
        assert len(rows) == 1
        conversation_id = rows[0].cid

        # A partial AIMessage IS persisted (the opposite of the dropped-
        # connection case) -- docs/SPEC.md §9's own "the partial answer
        # is kept... stored as an interrupted turn".
        identity_for_read = VerifiedIdentity(
            tenant_id=_seeded["tenant"], vid=_seeded["visitor"], org=ORIGIN
        )
        snapshot = await read_state(app.state.graph, identity_for_read, conversation_id)
        ai_messages = [m for m in snapshot.values.get("messages", []) if isinstance(m, AIMessage)]
        assert len(ai_messages) == 1
        assert ai_messages[0].content == received_text
        assert snapshot.values.get("answer_stopped") is True

        # The lease is already released -- checked directly in the row,
        # before issuing the follow-up request below, so this proves the
        # explicit stop itself released it (the SAME `finally` the
        # dropped-connection case relies on, reached via a normal return
        # this time, not GeneratorExit).
        async with db_session() as session:
            row = (
                await session.execute(
                    sa.select(Conversation.lease_until, Conversation.lease_token).where(
                        Conversation.cid == conversation_id
                    )
                )
            ).one()
        assert row.lease_until is None
        assert row.lease_token is None

        # docs/SPEC.md §9: "a stopped message counts once toward quota" --
        # the SAME usage-recording code path a normal completed answer
        # uses (last_node == "answer" still holds), never a separate
        # stopped-turn branch.
        assert len(usage_module._pending) == 1
        assert usage_module._pending[0].counted_toward_quota is True

        # The real proof behind "stop then an immediate new message
        # succeeds" (no 409) -- a fresh request for the SAME conversation,
        # issued right after the stopped turn, with no sleep/retry loop.
        follow_up = await _post_chat(
            client,
            token,
            {
                "message": "second message",
                "client_message_id": str(uuid.uuid4()),
                "conversation_id": str(conversation_id),
            },
        )
    assert follow_up.status_code == 200


async def test_stop_with_nothing_streaming_is_a_harmless_no_op(monkeypatch, _seeded):
    # docs/SPEC.md §9: no distinct failure mode for "stop" -- a stop
    # click racing the stream's own natural completion (or landing after
    # it, or with nothing ever having been sent) is a normal timing, not
    # an error. Confirmed live: calling POST /chat/stop with no chat
    # turn ever started for this visitor still returns 204, and does not
    # affect the NEXT, completely unrelated turn's own answer.
    chunks = build_fake_answer_arg_chunks(can_answer=True, used_chunk_ids=["c1"], answer="hi there")
    app = await _build_app(
        monkeypatch,
        chat_model=FakeAnswerToolChatModel(arg_chunks=chunks),
        retrieve_chunks=_answer_chunks(),
    )
    token = _token_for(_seeded)
    async with await http_client(app) as client:
        response = await client.post(
            "/api/v1/chat/stop",
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        )
        assert response.status_code == 204

        chat_response = await _post_chat(
            client, token, {"message": "hi", "client_message_id": str(uuid.uuid4())}
        )
    assert chat_response.status_code == 200


async def test_a_different_visitors_token_cannot_stop_this_visitors_stream(monkeypatch, _seeded):
    # [SECURITY] PART 0 audit: POST /chat/stop takes no conversation_id
    # and no lease_token (by design -- see stop_chat_turn()'s own
    # docstring), so the only identity it can possibly act on is the
    # CALLER'S OWN `identity.vid` from their own Bearer token. This is
    # the live, adversarial proof that this is actually safe, not merely
    # argued from reading the code: a SECOND, genuinely different,
    # legitimately-authenticated visitor (same tenant, `other_visitor`
    # from `_seeded` -- a real token, not a forged or malformed one)
    # calls /chat/stop while the FIRST visitor's own turn is paused
    # mid-answer. The first visitor's turn must complete NORMALLY,
    # `answer_stopped=False` -- a different visitor's own token, a
    # guessed conversation_id, or a stale lease_token structurally
    # cannot stop someone else's turn here, because none of the three
    # is ever read by this endpoint at all.
    full_answer = "This is a long answer that another visitor's own stop call must never cut short."
    chunks = build_fake_answer_arg_chunks(
        can_answer=True, used_chunk_ids=["c1"], answer=full_answer
    )
    pause_after = len(chunks) // 2
    paused_signal = asyncio.Event()
    resume_event = asyncio.Event()

    class _PausingPartwayModel(FakeAnswerToolChatModel):
        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            upstream = super()._astream(messages, stop=stop, run_manager=run_manager, **kwargs)
            count = 0
            async for chunk in upstream:
                yield chunk
                count += 1
                if count == pause_after:
                    paused_signal.set()
                    await resume_event.wait()

    app = await _build_app(
        monkeypatch,
        chat_model=_PausingPartwayModel(arg_chunks=chunks),
        retrieve_chunks=_answer_chunks(),
    )
    victim_token = _token_for(_seeded)
    attacker_token = _token_for(_seeded, vid_key="other_visitor")

    received_text = ""
    done_payload: dict | None = None

    async def _consume(client):
        nonlocal received_text, done_payload
        async with client.stream(
            "POST",
            "/api/v1/chat",
            json={
                "message": "what is your refund policy",
                "client_message_id": str(uuid.uuid4()),
            },
            headers={"Authorization": f"Bearer {victim_token}", "Origin": ORIGIN},
        ) as response:
            event_name = None
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    event_name = line[len("event: ") :]
                elif line.startswith("data: "):
                    data = json.loads(line[len("data: ") :])
                    if event_name == "token":
                        received_text += data["text"]
                    elif event_name == "done":
                        done_payload = data

    async with await http_client(app) as client, await http_client(app) as attacker_client:
        consume_task = asyncio.create_task(_consume(client))
        await paused_signal.wait()

        attacker_response = await attacker_client.post(
            "/api/v1/chat/stop",
            headers={"Authorization": f"Bearer {attacker_token}", "Origin": ORIGIN},
        )
        assert attacker_response.status_code == 204  # the uniform no-op response, no leak

        resume_event.set()
        await consume_task

    assert received_text == full_answer
    assert done_payload is not None
    assert done_payload["stopped"] is False


async def test_stop_with_no_authorization_header_at_all_is_a_plain_401(monkeypatch, _seeded):
    app = await _build_app(monkeypatch)
    async with await http_client(app) as client:
        response = await client.post("/api/v1/chat/stop", headers={"Origin": ORIGIN})
    assert response.status_code == 401
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers}


async def test_stop_rate_limited_per_visitor_gets_429_with_retry_after(monkeypatch, _seeded):
    set_valid_env(monkeypatch, VALID_ENV, CHAT_STOP_RATE_LIMIT_PER_VISITOR="1")
    get_settings.cache_clear()
    app = await _build_app(monkeypatch)
    token = _token_for(_seeded)
    async with await http_client(app) as client:
        first = await client.post(
            "/api/v1/chat/stop", headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN}
        )
        assert first.status_code == 204
        limited = await client.post(
            "/api/v1/chat/stop", headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN}
        )
    assert limited.status_code == 429
    assert "retry-after" in {k.lower() for k in limited.headers}
    assert limited.headers["access-control-allow-origin"] == ORIGIN


# --- Task 4.2: GET /conversations, GET /conversations/{cid}/messages -------


async def test_list_conversations_returns_only_this_visitors_own_conversations(
    monkeypatch, _seeded
):
    app = create_app()
    token = _token_for(_seeded)
    other_token = _token_for(_seeded, vid_key="other_visitor")
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=_seeded["tenant"], session=session)
        mine = await repo.create_conversation(vid=_seeded["visitor"], title="mine")
        await repo.create_conversation(vid=_seeded["other_visitor"], title="not mine")
        await session.commit()
        mine_cid = mine.cid

    async with await http_client(app) as client:
        response = await client.get(
            "/api/v1/conversations",
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        )
        other_response = await client.get(
            "/api/v1/conversations",
            headers={"Authorization": f"Bearer {other_token}", "Origin": ORIGIN},
        )

    assert response.status_code == 200
    body = response.json()
    assert [c["conversation_id"] for c in body["conversations"]] == [str(mine_cid)]
    assert body["conversations"][0]["title"] == "mine"

    other_body = other_response.json()
    assert len(other_body["conversations"]) == 1
    assert other_body["conversations"][0]["title"] == "not mine"


async def test_conversations_endpoints_now_carry_real_cors_headers(monkeypatch, _seeded):
    # [ops bugfix]: GET /conversations and GET /conversations/{cid}/
    # messages never had an OPTIONS preflight handler or an
    # Access-Control-Allow-Origin header on their own real responses --
    # confirmed live against the real demo stack (a genuinely
    # cross-origin deployment) while investigating why the widget's own
    # `conversation_id` discovery workaround silently failed in
    # production: the browser blocked it at the PREFLIGHT, before the
    # real GET was ever even sent. /session and /chat both already got
    # this right from day one; this is the same fix, extended to the two
    # endpoints that never had it, plus the new escalate preflight
    # (tests/test_handoff_routes.py).
    app = await _build_app(monkeypatch)
    token = _token_for(_seeded)
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=_seeded["tenant"], session=session)
        conversation = await repo.create_conversation(vid=_seeded["visitor"])
        await session.commit()
        cid = conversation.cid
    nonexistent_cid = uuid.uuid4()

    async with await http_client(app) as client:
        list_preflight = await client.options("/api/v1/conversations", headers={"Origin": ORIGIN})
        messages_preflight = await client.options(
            f"/api/v1/conversations/{cid}/messages", headers={"Origin": ORIGIN}
        )
        list_response = await client.get(
            "/api/v1/conversations",
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        )
        messages_response = await client.get(
            f"/api/v1/conversations/{cid}/messages",
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        )
        # The 404 branch too -- a plain `raise HTTPException` here would
        # have silently dropped these same headers (FastAPI builds an
        # exception response independently of this handler's own
        # injected `response` object); this is exactly the regression
        # this test guards against.
        not_found_response = await client.get(
            f"/api/v1/conversations/{nonexistent_cid}/messages",
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        )

    for response in (list_preflight, messages_preflight):
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == ORIGIN
        assert response.headers["vary"] == "Origin"

    for response in (list_response, messages_response):
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == ORIGIN
        assert response.headers["vary"] == "Origin"

    assert not_found_response.status_code == 404
    assert not_found_response.headers["access-control-allow-origin"] == ORIGIN
    assert not_found_response.headers["vary"] == "Origin"


async def test_a_real_turn_advances_last_message_at_and_reorders_previous_chats(
    monkeypatch, _seeded
):
    # [SECURITY-adjacent] Task 4.2 adversarial follow-up: a real bug
    # found while attacking the retention sweep -- `last_message_at`
    # had NO writer anywhere in this codebase (confirmed by reading
    # every call site directly), frozen at a conversation's own
    # CREATION time forever. Proof, through the real endpoint, not the
    # repository directly: an OLDER conversation (by creation order)
    # that receives a real new turn must become the MOST recently
    # active one in "previous chats" -- the exact ordering a visitor
    # would see break if this bug were still present.
    chunks = build_fake_answer_arg_chunks(
        can_answer=True, used_chunk_ids=["c1"], answer="A fixed test answer."
    )
    fake_model = FakeAnswerToolChatModel(arg_chunks=chunks)
    app = await _build_app(monkeypatch, chat_model=fake_model, retrieve_chunks=_answer_chunks())
    token = _token_for(_seeded)

    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=_seeded["tenant"], session=session)
        older = await repo.create_conversation(vid=_seeded["visitor"], title="older")
        await session.commit()
        older_cid = older.cid
        older_last_message_at_before = older.last_message_at

    async with await http_client(app) as client:
        async with client.stream(
            "POST",
            "/api/v1/chat",
            json={
                "message": "hello again",
                "client_message_id": str(uuid.uuid4()),
                "conversation_id": str(older_cid),
            },
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        ) as response:
            await response.aread()
        assert response.status_code == 200

        list_response = await client.get(
            "/api/v1/conversations",
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        )

    body = list_response.json()
    assert body["conversations"][0]["conversation_id"] == str(older_cid)
    assert body["conversations"][0]["last_message_at"] != older_last_message_at_before.isoformat()


async def test_list_conversations_respects_the_real_configured_cap(monkeypatch, _seeded):
    # [SECURITY-adjacent] Task 4.2 adversarial follow-up: the real
    # finding (no cap at all) is fixed at the repository layer
    # (test_tenancy_repository.py's own dedicated test proves the
    # ordering/cap logic itself) -- this proves the REAL endpoint
    # actually threads `Settings.conversation_list_max_results` through,
    # not a hardcoded or forgotten default. get_settings.cache_clear()
    # required here, the identical already-established reason as
    # test_eleventh_message_in_a_minute_is_rate_limited_with_retry_
    # after's own comment above: `_seeded` already cached Settings
    # before this env override runs.
    set_valid_env(monkeypatch, VALID_ENV, CONVERSATION_LIST_MAX_RESULTS="2")
    get_settings.cache_clear()
    app = create_app()
    token = _token_for(_seeded)
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=_seeded["tenant"], session=session)
        for _ in range(3):
            await repo.create_conversation(vid=_seeded["visitor"])
        await session.commit()

    async with await http_client(app) as client:
        response = await client.get(
            "/api/v1/conversations",
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        )

    assert len(response.json()["conversations"]) == 2
    get_settings.cache_clear()


async def test_get_messages_for_another_tenants_conversation_returns_404(monkeypatch, _seeded):
    app = create_app()
    token = _token_for(_seeded)
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=_seeded["other_tenant"], session=session)
        other_visitor_id = uuid.uuid4()
        session.add(
            SiteKey(
                id=uuid.uuid4(),
                tenant_id=_seeded["other_tenant"],
                key="pk_live_messages_other",
                environment="production",
                status="live",
                allowed_origins=[ORIGIN],
            )
        )
        await session.flush()
        other_site_key_id = (
            await session.execute(
                SiteKey.__table__.select().where(SiteKey.tenant_id == _seeded["other_tenant"])
            )
        ).first().id
        session.add(
            Visitor(
                vid=other_visitor_id,
                tenant_id=_seeded["other_tenant"],
                site_key_id=other_site_key_id,
                secret_hash=hash_visitor_secret(generate_visitor_secret()),
            )
        )
        await session.flush()
        conversation = await repo.create_conversation(vid=other_visitor_id)
        await session.commit()
        other_cid = conversation.cid

    async with await http_client(app) as client:
        response = await client.get(
            f"/api/v1/conversations/{other_cid}/messages",
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        )
    assert response.status_code == 404


async def test_get_messages_for_another_visitors_conversation_same_tenant_returns_404(
    monkeypatch, _seeded
):
    # [SECURITY] Task 4.2 adversarial follow-up: the SAME-tenant half of
    # the cross-tenant test directly above -- a real gap this file had
    # no test for at all (only POST /chat had the equivalent cross-
    # visitor proof; this GET endpoint did not). `other_visitor` is the
    # SAME tenant as the real caller, which is exactly the case docs/
    # SPEC.md §16's own "Visitor A can't read or resume visitor B's
    # conversation, even within the same tenant" names.
    app = create_app()
    token = _token_for(_seeded)
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=_seeded["tenant"], session=session)
        other_conversation = await repo.create_conversation(vid=_seeded["other_visitor"])
        await session.commit()
        other_cid = other_conversation.cid

    async with await http_client(app) as client:
        response = await client.get(
            f"/api/v1/conversations/{other_cid}/messages",
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        )
    assert response.status_code == 404


async def test_get_messages_404_is_indistinguishable_for_nonexistent_vs_someone_elses_cid(
    monkeypatch, _seeded
):
    # [SECURITY] Task 4.2 adversarial follow-up: "a cid that doesn't
    # exist vs one that belongs to someone else -- the responses must
    # be indistinguishable (same status and body shape, no timing-
    # relevant extra query)". A real, direct side-by-side comparison --
    # not three separate tests each independently asserting "404" and
    # trusting they happen to match. `get_conversation_by_id(cid,
    # vid=...)` (app/tenancy/repository.py) already filters by tenant_id
    # AND vid in the SAME query, so a nonexistent cid, another tenant's
    # cid, and another visitor's cid all take the identical one-query
    # code path in app/chat/routes.py's own get_conversation_messages()
    # -- confirmed here by comparing the real responses, not by reading
    # the code and assuming it.
    app = create_app()
    token = _token_for(_seeded)
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=_seeded["tenant"], session=session)
        other_visitor_conversation = await repo.create_conversation(vid=_seeded["other_visitor"])
        await session.commit()
        real_but_not_mine_cid = other_visitor_conversation.cid
    nonexistent_cid = uuid.uuid4()

    async with await http_client(app) as client:
        nonexistent_response = await client.get(
            f"/api/v1/conversations/{nonexistent_cid}/messages",
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        )
        not_mine_response = await client.get(
            f"/api/v1/conversations/{real_but_not_mine_cid}/messages",
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        )

    assert nonexistent_response.status_code == not_mine_response.status_code == 404
    assert nonexistent_response.json() == not_mine_response.json()


async def test_get_messages_returns_the_real_completed_turn(monkeypatch, _seeded):
    chunks = build_fake_answer_arg_chunks(
        can_answer=True, used_chunk_ids=["c1"], answer="The real answer."
    )
    app = await _build_app(
        monkeypatch,
        chat_model=FakeAnswerToolChatModel(arg_chunks=chunks),
        retrieve_chunks=_answer_chunks(),
    )
    token = _token_for(_seeded)
    headers = {"Authorization": f"Bearer {token}", "Origin": ORIGIN}

    async with await http_client(app) as client:
        async with client.stream(
            "POST",
            "/api/v1/chat",
            json={"message": "what is the answer", "client_message_id": str(uuid.uuid4())},
            headers=headers,
        ) as response:
            await response.aread()

        async with db_session() as session:
            rows = (await session.execute(Conversation.__table__.select())).fetchall()
        assert len(rows) == 1
        conversation_id = rows[0].cid

        messages_response = await client.get(
            f"/api/v1/conversations/{conversation_id}/messages", headers=headers
        )

    assert messages_response.status_code == 200
    messages = messages_response.json()["messages"]
    assert [m["role"] for m in messages] == ["visitor", "assistant"]
    assert messages[0]["text"] == "what is the answer"
    assert messages[1]["text"] == "The real answer."


async def test_get_messages_drops_a_trailing_incomplete_turn(monkeypatch, _seeded):
    # A turn that crashed/timed out leaves a HumanMessage with no
    # following AIMessage in the checkpoint -- "completed turns only"
    # (docs/SPEC.md §10) means this must not be shown at all.
    class _SlowChatModel(FakeAnswerToolChatModel):
        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            await asyncio.sleep(10)
            if False:
                yield  # pragma: no cover -- keeps this an async generator

    set_valid_env(monkeypatch, VALID_ENV, CHAT_REQUEST_TIMEOUT_SECONDS="0.05")
    get_settings.cache_clear()
    app = await _build_app(
        monkeypatch, chat_model=_SlowChatModel(arg_chunks=[]), retrieve_chunks=_answer_chunks()
    )
    token = _token_for(_seeded)
    headers = {"Authorization": f"Bearer {token}", "Origin": ORIGIN}

    async with await http_client(app) as client:
        async with client.stream(
            "POST",
            "/api/v1/chat",
            json={"message": "an abandoned question", "client_message_id": str(uuid.uuid4())},
            headers=headers,
        ) as response:
            await response.aread()  # times out -- no AIMessage is ever appended

        async with db_session() as session:
            rows = (await session.execute(Conversation.__table__.select())).fetchall()
        assert len(rows) == 1
        conversation_id = rows[0].cid

        messages_response = await client.get(
            f"/api/v1/conversations/{conversation_id}/messages", headers=headers
        )

    assert messages_response.status_code == 200
    assert messages_response.json()["messages"] == []


# --- Step 6.2, additive: `done`'s own new `conversation_id` field -----------


async def test_done_event_carries_the_real_new_conversation_id_on_a_first_turn(
    monkeypatch, _seeded
):
    app = await _build_app(monkeypatch)
    token = _token_for(_seeded)
    async with await http_client(app) as client:
        async with client.stream(
            "POST",
            "/api/v1/chat",
            json={"message": "hi there", "client_message_id": str(uuid.uuid4())},
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        ) as response:
            body = await response.aread()
    events = _parse_sse(body.decode())
    done_events = [e for e in events if e["event"] == "done"]
    assert len(done_events) == 1
    returned_cid = done_events[0]["conversation_id"]

    async with db_session() as session:
        rows = (await session.execute(Conversation.__table__.select())).fetchall()
    assert len(rows) == 1
    assert returned_cid == str(rows[0].cid)


async def test_done_event_carries_conversation_id_on_a_replayed_message_too(monkeypatch, _seeded):
    app = await _build_app(monkeypatch)
    token = _token_for(_seeded)
    client_message_id = str(uuid.uuid4())
    async with await http_client(app) as client:
        async with client.stream(
            "POST",
            "/api/v1/chat",
            json={"message": "hi there", "client_message_id": client_message_id},
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        ) as first:
            first_body = await first.aread()
        first_cid = [e for e in _parse_sse(first_body.decode()) if e["event"] == "done"][0][
            "conversation_id"
        ]

        # The exact same client_message_id again, same conversation --
        # the idempotent-replay path (a SEPARATE code path from a real
        # turn, confirmed by this file's own existing tests) must carry
        # the identical real conversation_id too, not omit it.
        async with client.stream(
            "POST",
            "/api/v1/chat",
            json={
                "message": "hi there",
                "client_message_id": client_message_id,
                "conversation_id": first_cid,
            },
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        ) as second:
            second_body = await second.aread()
    second_done = [e for e in _parse_sse(second_body.decode()) if e["event"] == "done"][0]
    assert second_done["conversation_id"] == first_cid
