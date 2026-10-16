# backend/tests/test_widget_cors.py
# [SECURITY] Step 7-follow-up: live proof of app.auth.cors.
# WidgetCORSMiddleware's own rules, across every visitor-facing route it
# covers (POST /session, POST /chat, GET /conversations, GET
# /conversations/{cid}/messages, POST /conversations/{cid}/escalate) and
# the one surface it must NEVER touch (dashboard/admin, D-6.1-cors-
# same-origin). Reuses `_seeded`/`_build_app`/`_token_for`/`ORIGIN` from
# test_chat_routes.py/test_handoff_routes.py directly (import, not a
# third copy) -- both already duplicate this exact fixture/helper set
# once each; a third, independent copy here would cross this project's
# own established 3rd-occurrence extraction threshold for no real
# benefit, so this file imports instead of re-defining.
#
# ruff: noqa: F811 (every test function below takes `_seeded` as its own
# parameter -- standard pytest fixture-import usage, not a real
# redefinition; ruff's static check cannot tell the two apart)
import uuid

import pytest

from app.auth.cors import WIDGET_ROUTES
from app.chat.routes import _acquire_lease
from app.config import get_settings
from app.main import create_app
from app.tenancy.repository import TenantScopedRepository
from tests.conftest import (
    VALID_ENV,
    FakeAnswerToolChatModel,
    build_fake_answer_arg_chunks,
    db_session,
    http_client,
    set_valid_env,
)
from tests.test_chat_routes import ORIGIN, _build_app, _seeded  # noqa: F401 (re-exported)
from tests.test_chat_routes import _token_for as _chat_token_for
from tests.test_handoff_routes import _escalate

pytestmark = pytest.mark.usefixtures("close_any_checkpointer_pool_left_open")

# Not on any real site key's allowed_origins -- used to prove a
# disallowed origin gets nothing, on every route, every branch.
DISALLOWED_ORIGIN = "https://evil.example"


def _create_app(monkeypatch):
    # Every test in this file that does NOT go through `_build_app()`
    # (which sets valid env itself, via reset_checkpoint_env()) needs
    # this instead -- tests/conftest.py's own `_isolated_env` autouse
    # fixture deletes every Settings-related env var before each test,
    # so a bare create_app() would otherwise fail validation.
    set_valid_env(monkeypatch, VALID_ENV)
    get_settings.cache_clear()
    return create_app()


def _answer_chunks() -> list[dict]:
    return [
        {
            "document_id": "doc-1",
            "text": "irrelevant",
            "source_url": "https://tenant.example/docs/page",
            "heading_path": ["Docs", "Getting started"],
            "score": 0.9,
        }
    ]


def _assert_no_oracle(response) -> None:
    """[SECURITY] The two blanket rules PART A names, checked on every
    response this file ever inspects: never a wildcard, never a
    credentials allowance (the widget authenticates with an in-memory
    Bearer token, docs/SPEC.md §4.3 -- never a cookie; none of its own
    fetch() calls set `credentials: "include"`, confirmed by reading
    session.js/chat-controller.js directly, so this header must never
    appear for any of these five routes).
    """
    assert response.headers.get("access-control-allow-origin") != "*"
    assert "access-control-allow-credentials" not in {
        k.lower() for k in response.headers
    }


# --- Part A rule 6: every visitor-facing route, enumerated once -------------


def test_widget_routes_is_the_single_complete_enumeration():
    # This IS the "list every visitor-facing route" the report cites --
    # a real, importable list, not prose that can drift from the code.
    names = {name for name, *_ in WIDGET_ROUTES}
    assert names == {
        "session",
        "chat",
        "chat_stop",
        "list_conversations",
        "conversation_messages",
        "escalate",
    }


# --- preflight, every route -------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected_methods", "expected_headers"),
    [
        ("/api/v1/session", "POST, OPTIONS", "Content-Type"),
        ("/api/v1/chat", "POST, OPTIONS", "Content-Type, Authorization"),
        ("/api/v1/chat/stop", "POST, OPTIONS", "Authorization"),
        ("/api/v1/conversations", "GET, OPTIONS", "Authorization"),
        (
            "/api/v1/conversations/00000000-0000-0000-0000-000000000000/messages",
            "GET, OPTIONS",
            "Authorization",
        ),
        (
            "/api/v1/conversations/00000000-0000-0000-0000-000000000000/escalate",
            "POST, OPTIONS",
            "Content-Type, Authorization",
        ),
    ],
)
async def test_preflight_answers_every_widget_route_centrally(
    monkeypatch, path, expected_methods, expected_headers
):
    # No app.state.graph, no database fixture at all -- the whole point
    # of a preflight is that it cannot need either (see app/auth/cors.py's
    # own header, point 1); a bare create_app() proves that structurally,
    # not just by convention.
    app = _create_app(monkeypatch)
    async with await http_client(app) as client:
        response = await client.options(path, headers={"Origin": ORIGIN})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert response.headers["access-control-allow-methods"] == expected_methods
    assert response.headers["access-control-allow-headers"] == expected_headers
    assert response.headers["vary"] == "Origin"
    _assert_no_oracle(response)


async def test_preflight_for_a_non_widget_path_is_untouched(monkeypatch):
    # A path this middleware does not recognise at all -- /health has no
    # OPTIONS handler, so this must fall through to FastAPI's own
    # default 405, never the widget preflight shape.
    app = _create_app(monkeypatch)
    async with await http_client(app) as client:
        response = await client.options("/health", headers={"Origin": ORIGIN})
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers}


# --- /session: success vs. the deliberate no-oracle failure path -----------


async def test_session_success_gets_the_real_verified_origin(monkeypatch, _seeded):
    app = create_app()
    async with await http_client(app) as client:
        response = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_test"},
            headers={"Origin": ORIGIN},
        )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert response.headers["vary"] == "Origin"
    _assert_no_oracle(response)


async def test_session_disallowed_origin_gets_no_acao_but_still_gets_vary(monkeypatch, _seeded):
    app = create_app()
    async with await http_client(app) as client:
        response = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_test"},
            headers={"Origin": DISALLOWED_ORIGIN},
        )
    assert response.status_code == 403
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers}
    assert response.headers["vary"] == "Origin"


async def test_session_unknown_site_key_from_an_otherwise_fine_origin_also_gets_no_acao(
    monkeypatch, _seeded
):
    # [SECURITY] The real reason /session's own failure path NEVER gets
    # an Access-Control-Allow-Origin header, even for an origin that
    # WOULD have been allowed for some real site key: Step 1.4 breakdown
    # decision (7) deliberately makes "unknown site key" and "disallowed
    # origin" byte-identical, precisely so an attacker cannot use CORS
    # success/failure as an oracle for which origins are on SOME site
    # key's allow-list. Adding ACAO to only this ONE of the four reasons
    # would recreate exactly that oracle -- confirmed here that it does
    # not.
    app = create_app()
    async with await http_client(app) as client:
        response = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_this_site_key_does_not_exist"},
            headers={"Origin": ORIGIN},
        )
    assert response.status_code == 403
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers}


# --- /chat: every branch the task names, plus the deliberate gaps ----------


async def test_chat_success_stream_gets_the_real_verified_origin(monkeypatch, _seeded):
    chunks = build_fake_answer_arg_chunks(can_answer=True, used_chunk_ids=["c1"], answer="hi")
    fake_model = FakeAnswerToolChatModel(arg_chunks=chunks)
    app = await _build_app(monkeypatch, chat_model=fake_model, retrieve_chunks=_answer_chunks())
    token = _chat_token_for(_seeded)
    async with await http_client(app) as client:
        async with client.stream(
            "POST",
            "/api/v1/chat",
            json={"message": "hi", "client_message_id": str(uuid.uuid4())},
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        ) as response:
            await response.aread()
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert response.headers["vary"] == "Origin"
    _assert_no_oracle(response)


async def test_chat_stop_success_gets_the_real_verified_origin(monkeypatch, _seeded):
    # Part D (docs/SPEC.md §9's "Stop mid-stream"): the same CORS
    # mechanism, proven on the new explicit-stop endpoint too -- a
    # no-op (nothing is actually streaming in this test) still goes
    # through get_current_visitor() and sets `request.state.cors_
    # allowed_origin` before returning 204, so it earns the real
    # verified-origin header identically to every other authenticated
    # widget route.
    app = await _build_app(monkeypatch)
    token = _chat_token_for(_seeded)
    async with await http_client(app) as client:
        response = await client.post(
            "/api/v1/chat/stop",
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        )
    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert response.headers["vary"] == "Origin"
    _assert_no_oracle(response)


async def test_chat_stop_disallowed_origin_gets_no_acao(monkeypatch, _seeded):
    app = await _build_app(monkeypatch)
    token = _chat_token_for(_seeded)
    async with await http_client(app) as client:
        response = await client.post(
            "/api/v1/chat/stop",
            headers={"Authorization": f"Bearer {token}", "Origin": DISALLOWED_ORIGIN},
        )
    assert response.status_code == 401
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers}


async def test_chat_429_rate_limited_still_gets_the_real_verified_origin(monkeypatch, _seeded):
    from tests.conftest import VALID_ENV, set_valid_env

    set_valid_env(monkeypatch, VALID_ENV, CHAT_RATE_LIMIT_PER_VISITOR="1")
    get_settings.cache_clear()
    app = await _build_app(monkeypatch)
    token = _chat_token_for(_seeded)
    async with await http_client(app) as client:
        await client.post(
            "/api/v1/chat",
            json={"message": "hi", "client_message_id": str(uuid.uuid4())},
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        )
        limited = await client.post(
            "/api/v1/chat",
            json={"message": "hi", "client_message_id": str(uuid.uuid4())},
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        )
    assert limited.status_code == 429
    assert limited.headers["access-control-allow-origin"] == ORIGIN
    assert limited.headers["vary"] == "Origin"
    _assert_no_oracle(limited)


async def test_chat_lease_busy_409_still_gets_the_real_verified_origin(monkeypatch, _seeded):
    app = await _build_app(monkeypatch)
    token = _chat_token_for(_seeded)
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
        response = await client.post(
            "/api/v1/chat",
            json={
                "message": "hi",
                "client_message_id": str(uuid.uuid4()),
                "conversation_id": str(conversation_id),
            },
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        )
    assert response.status_code == 409
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert response.headers["vary"] == "Origin"


async def test_chat_service_starting_up_503_still_gets_the_real_verified_origin(
    monkeypatch, _seeded
):
    # docs/SPEC.md §9's own readiness gate: `usage_counters_ready` False
    # until the startup reload finishes -- checked AFTER identity
    # resolves (app/chat/routes.py's own comment on check ordering), so
    # this is a real, reachable 503 that DOES get the header, unlike the
    # pool-exhaustion 503 below.
    app = await _build_app(monkeypatch)
    app.state.usage_counters_ready = False
    token = _chat_token_for(_seeded)
    async with await http_client(app) as client:
        response = await client.post(
            "/api/v1/chat",
            json={"message": "hi", "client_message_id": str(uuid.uuid4())},
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        )
    assert response.status_code == 503
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert response.headers["vary"] == "Origin"


async def test_chat_disallowed_origin_gets_no_acao_even_on_the_success_branch(monkeypatch, _seeded):
    # [SECURITY] Rule 4: a disallowed origin gets NOTHING, success or
    # error. A token issued FOR this visitor's real origin, replayed
    # with a DIFFERENT Origin header, fails get_current_visitor()'s own
    # origin-vs-`org`-claim check (app/auth/dependencies.py) -- 401,
    # before the handler body (and therefore `request.state.
    # cors_allowed_origin`) is ever reached.
    app = await _build_app(monkeypatch)
    token = _chat_token_for(_seeded)
    async with await http_client(app) as client:
        response = await client.post(
            "/api/v1/chat",
            json={"message": "hi", "client_message_id": str(uuid.uuid4())},
            headers={"Authorization": f"Bearer {token}", "Origin": DISALLOWED_ORIGIN},
        )
    assert response.status_code == 401
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers}


async def test_chat_oversized_message_413_deliberately_gets_no_acao(monkeypatch, _seeded):
    # [SECURITY] A real, narrow, DOCUMENTED limitation (app/auth/cors.py's
    # own header, point 3), not an oversight: the 413 check runs BEFORE
    # identity resolves (a deliberate cheapest-first ordering, app/chat/
    # routes.py's own comment) -- there is no verified origin to echo
    # yet at that point, so none is. The widget's own fallback ("Connection
    # lost. Please try again.", chat-controller.js) still shows a calm
    # message either way -- confirmed here, not assumed, so a future
    # change can see exactly what it would need to alter.
    app = await _build_app(monkeypatch)
    token = _chat_token_for(_seeded)
    async with await http_client(app) as client:
        response = await client.post(
            "/api/v1/chat",
            json={"message": "x" * 2001, "client_message_id": str(uuid.uuid4())},
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        )
    assert response.status_code == 413
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers}
    # [SECURITY] Vary: Origin is still set here -- the middleware adds it
    # to EVERY matched-route response unconditionally (app/auth/cors.py's
    # own dispatch(), before the ACAO decision), so a cache sitting in
    # front of this API never conflates one Origin's 413 with another's,
    # even on a branch that deliberately never gets ACAO itself.
    assert response.headers["vary"] == "Origin"


async def test_chat_pool_exhaustion_503_deliberately_gets_no_acao(monkeypatch, _seeded):
    # [SECURITY] Same class of documented limitation as the 413 case
    # above, for a different structural reason: THIS flavor of 503 means
    # the very FIRST database session of the request (the one
    # get_current_visitor() itself needs) could not even be opened, so
    # identity resolution never ran at all. Unlike the readiness-gate 503
    # above (which happens strictly AFTER identity resolves, inside the
    # same already-open session), there is no later point in this
    # specific code path where a connection is acquired a second time
    # (_acquire_lease() reuses the one session already open) -- so a
    # genuinely exhausted pool at request start structurally cannot be
    # fixed without a second, DB-free, pre-identity verification step,
    # which this task deliberately does not add. See the register.
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.db as db_module

    app = create_app()
    app.state.graph = object()
    app.state.usage_counters_ready = True
    token = _chat_token_for(_seeded)
    db_url = get_settings().database_url_str()
    tiny_engine = create_async_engine(db_url, pool_size=1, max_overflow=0, pool_timeout=0.2)
    real_get_engine = db_module.get_engine
    db_module.get_engine = lambda: tiny_engine
    db_module._session_factory.cache_clear()

    holder = await tiny_engine.connect()
    try:
        async with await http_client(app) as client:
            response = await client.post(
                "/api/v1/chat",
                json={"message": "hi", "client_message_id": str(uuid.uuid4())},
                headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
            )
        assert response.status_code == 503
        assert "access-control-allow-origin" not in {k.lower() for k in response.headers}
        # [SECURITY] Same unconditional guarantee as the 413 case above --
        # Vary: Origin survives even a pool-exhaustion failure this early.
        assert response.headers["vary"] == "Origin"
    finally:
        await holder.close()
        await tiny_engine.dispose()
        db_module.get_engine = real_get_engine
        db_module._session_factory.cache_clear()


# --- conversations / escalate: the blanket rules, on top of the prior ------
# task's own branch-by-branch coverage (test_chat_routes.py's own
# test_conversations_endpoints_now_carry_real_cors_headers,
# test_handoff_routes.py's own
# test_escalate_now_carries_real_cors_headers_on_every_branch) -----------


async def test_conversations_and_escalate_never_wildcard_or_credentials(monkeypatch, _seeded):
    app = await _build_app(monkeypatch)
    token = _chat_token_for(_seeded)
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=_seeded["tenant"], session=session)
        conversation = await repo.create_conversation(vid=_seeded["visitor"])
        await session.commit()
        cid = conversation.cid

    async with await http_client(app) as client:
        list_response = await client.get(
            "/api/v1/conversations",
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        )
        escalate_response = await _escalate(client, token, cid)

    for response in (list_response, escalate_response):
        assert response.headers["access-control-allow-origin"] == ORIGIN
        _assert_no_oracle(response)


async def test_conversations_messages_and_escalate_disallowed_origin_gets_no_acao(
    monkeypatch, _seeded
):
    # [SECURITY] Rule 4 ("a disallowed origin gets no ACAO on any
    # branch"), proven on these three routes specifically -- the prior
    # tests above only ever send the real, allowed ORIGIN; nothing in
    # this file previously exercised a disallowed origin against
    # /conversations, /conversations/{cid}/messages or /escalate (every
    # disallowed-origin test elsewhere covers /session, /chat or
    # /chat/stop only). The underlying mechanism is the SAME
    # get_current_visitor() origin-vs-`org`-claim check already proven
    # for /chat (test_chat_disallowed_origin_gets_no_acao_even_on_the_
    # success_branch above) -- a 401 before the handler body (and
    # therefore `request.state.cors_allowed_origin`) is ever reached --
    # confirmed directly here too, not merely assumed to carry over.
    app = await _build_app(monkeypatch)
    token = _chat_token_for(_seeded)
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=_seeded["tenant"], session=session)
        conversation = await repo.create_conversation(vid=_seeded["visitor"])
        await session.commit()
        cid = conversation.cid

    async with await http_client(app) as client:
        list_response = await client.get(
            "/api/v1/conversations",
            headers={"Authorization": f"Bearer {token}", "Origin": DISALLOWED_ORIGIN},
        )
        messages_response = await client.get(
            f"/api/v1/conversations/{cid}/messages",
            headers={"Authorization": f"Bearer {token}", "Origin": DISALLOWED_ORIGIN},
        )
        escalate_response = await client.post(
            f"/api/v1/conversations/{cid}/escalate",
            json={},
            headers={"Authorization": f"Bearer {token}", "Origin": DISALLOWED_ORIGIN},
        )

    for response in (list_response, messages_response, escalate_response):
        assert response.status_code == 401
        assert "access-control-allow-origin" not in {k.lower() for k in response.headers}


# --- dashboard/admin stays CORS-free (D-6.1-cors-same-origin) ---------------


async def test_dashboard_endpoint_never_gets_acao_even_with_a_valid_widget_origin(monkeypatch):
    # [SECURITY] The real isolation proof: a request carrying a REAL,
    # otherwise-valid widget Origin header, sent to a dashboard endpoint,
    # must never get Access-Control-Allow-Origin -- same-origin SPA+API
    # serving (D-6.1-cors-same-origin) means this surface has NO CORS
    # story at all, by design, regardless of which Origin shows up.
    # GET /api/v1/dashboard/auth/csrf needs no prior auth and touches no
    # tenant data, so this isolates the CORS question alone.
    app = _create_app(monkeypatch)
    async with await http_client(app) as client:
        response = await client.get(
            "/api/v1/dashboard/auth/csrf", headers={"Origin": ORIGIN}
        )
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers}


async def test_dashboard_options_is_not_answered_by_the_widget_preflight_either(monkeypatch):
    app = _create_app(monkeypatch)
    async with await http_client(app) as client:
        response = await client.options(
            "/api/v1/dashboard/auth/csrf", headers={"Origin": ORIGIN}
        )
    # No widget-style preflight short-circuit for this path -- whatever
    # FastAPI's own default OPTIONS handling is (405, since no OPTIONS
    # route is registered for it), it must NOT look like the widget's
    # own 200-with-Allow-Methods shape.
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers}
    assert "access-control-allow-methods" not in {k.lower() for k in response.headers}
