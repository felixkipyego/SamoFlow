# backend/tests/test_handoff_routes.py
# Step 4.4 [SECURITY]: live tests for POST /api/v1/conversations/{cid}/
# escalate (app/handoff/routes.py) -- against real test-db, matching
# tests/test_chat_routes.py's own established shape (real checkpointer-
# backed graph, no real OpenAI/Qdrant calls).
#
# The adversarial-pass items this file covers directly:
#   - cross-tenant / cross-visitor cid, and nonexistent-vs-someone-
#     else's-cid response-shape indistinguishability (mirrors Task 4.2's
#     own GET /conversations/{cid}/messages proof exactly);
#   - email header injection (CR/LF in contact name/email) -- rejected
#     at the schema boundary, never reaching send_email() at all;
#   - HTML injection into the notification email -- HTML-escaped;
#   - the markdown-unescape helper applied to assistant lines only,
#     never visitor lines, always followed by HTML-escape;
#   - escalation spam: per-visitor rate limit (docs/SPEC.md §7's own
#     "3 per rolling hour"), and "one open escalation per conversation"
#     (the same spec's own per-conversation control -- there is no
#     SEPARATE per-conversation rate limit named anywhere in docs/
#     SPEC.md; this conflict check already bounds a single conversation
#     to at most one open escalation at a time, which IS this spec's
#     own per-conversation spam control);
#   - a per-tenant hourly email cap, gating the SEND only, never row
#     creation;
#   - tenant scoping: an escalation only ever notifies its own
#     conversation's own tenant.
#
# NOT covered here, covered elsewhere instead:
#   - "a forged visitor id": this endpoint accepts no visitor-id-shaped
#     parameter from the client at all (only `cid` and the optional
#     contact fields) -- identical reasoning to Task 4.2's own adversarial
#     pass on GET /conversations/{cid}/messages, which found the same and
#     did not add a new test for it either (the shared get_current_
#     visitor JWT-verification dependency is the only relevant control,
#     unchanged by this step, already tested in test_auth_dependencies.py).
#   - prompt injection trying to force/suppress escalation DETECTION:
#     tests/test_agent_graph.py's own _is_escalation_intent() tests
#     cover this directly -- this endpoint is reached only by an
#     explicit visitor button-tap POST, never by detection itself.
import uuid

import pytest
import sqlalchemy as sa
from pydantic import ValidationError

import app.handoff.routes as handoff_routes_module
from app.agent import checkpointer
from app.agent import graph as graph_module
from app.agent.entrypoint import run_turn
from app.agent.graph import build_graph
from app.auth.dependencies import VerifiedIdentity
from app.auth.secrets import generate_visitor_secret, hash_visitor_secret
from app.auth.tokens import encode_session_token
from app.config import get_settings
from app.handoff.models import Escalation
from app.handoff.schemas import EscalationRequest
from app.main import create_app
from app.tenancy.models import SiteKey, Tenant, Visitor
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


async def _build_app(monkeypatch):
    await reset_checkpoint_env(monkeypatch)
    await checkpointer.run_checkpointer_setup()
    await checkpointer.open_checkpointer_pool()
    cp = checkpointer.get_checkpointer()
    graph = build_graph(cp)
    app = create_app()
    app.state.graph = graph
    app.state.usage_counters_ready = True
    return app


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
                Tenant(
                    id=ids["tenant"],
                    name="Acme",
                    status="active",
                    config={"escalation_email": "acme-support@example.com"},
                ),
                Tenant(
                    id=ids["other_tenant"],
                    name="Other",
                    status="active",
                    config={"escalation_email": "other-support@example.com"},
                ),
            ]
        )
        await session.flush()
        session.add(
            SiteKey(
                id=ids["site_key"],
                tenant_id=ids["tenant"],
                key="pk_live_handoff_test",
                environment="production",
                status="live",
                allowed_origins=[ORIGIN],
            )
        )
        await session.flush()
        session.add_all(
            [
                Visitor(
                    vid=ids["visitor"],
                    tenant_id=ids["tenant"],
                    site_key_id=ids["site_key"],
                    secret_hash=hash_visitor_secret(generate_visitor_secret()),
                ),
                Visitor(
                    vid=ids["other_visitor"],
                    tenant_id=ids["tenant"],
                    site_key_id=ids["site_key"],
                    secret_hash=hash_visitor_secret(generate_visitor_secret()),
                ),
            ]
        )
        await session.commit()
    return ids


def _token_for(ids, vid_key="visitor", tenant_key="tenant"):
    return encode_session_token(tenant_id=ids[tenant_key], vid=ids[vid_key], origin=ORIGIN)


async def _escalate(client, token, cid, body=None):
    return await client.post(
        f"/api/v1/conversations/{cid}/escalate",
        json=body or {},
        headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
    )


@pytest.fixture(autouse=True)
def _capture_sent_emails(monkeypatch):
    """Records every (to, subject, body, subtype) send_email() would
    have attempted, WITHOUT a real SMTP server -- patched where it is
    USED (app.handoff.routes' own imported name), matching this
    project's own established convention elsewhere (e.g. graph_module.
    get_chat_model). Returns the list; tests assert against it directly
    rather than needing a real, configured SMTP server.
    """
    sent = []

    async def _fake_send_email(to, subject, body, *, subtype="plain"):
        sent.append({"to": to, "subject": subject, "body": body, "subtype": subtype})
        return True

    monkeypatch.setattr(handoff_routes_module, "send_email", _fake_send_email)
    return sent


# --- cross-tenant / cross-visitor 404 indistinguishability ------------------


async def test_escalate_for_another_visitors_conversation_same_tenant_returns_404(
    monkeypatch, _seeded
):
    app = create_app()  # no graph needed -- 404 happens before it is ever read
    token = _token_for(_seeded)
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=_seeded["tenant"], session=session)
        other_conversation = await repo.create_conversation(vid=_seeded["other_visitor"])
        await session.commit()
        other_cid = other_conversation.cid

    async with await http_client(app) as client:
        response = await _escalate(client, token, other_cid)
    assert response.status_code == 404


async def test_escalate_for_another_tenants_conversation_returns_404(monkeypatch, _seeded):
    app = create_app()
    token = _token_for(_seeded)
    other_tenant_vid = uuid.uuid4()
    async with db_session() as session:
        session.add(
            Visitor(
                vid=other_tenant_vid,
                tenant_id=_seeded["other_tenant"],
                site_key_id=_seeded["site_key"],
                secret_hash=hash_visitor_secret(generate_visitor_secret()),
            )
        )
        await session.flush()
        other_repo = TenantScopedRepository(tenant_id=_seeded["other_tenant"], session=session)
        other_tenant_conversation = await other_repo.create_conversation(vid=other_tenant_vid)
        await session.commit()
        other_tenant_cid = other_tenant_conversation.cid

    async with await http_client(app) as client:
        response = await _escalate(client, token, other_tenant_cid)
    assert response.status_code == 404


async def test_escalate_404_is_indistinguishable_for_nonexistent_vs_someone_elses_cid(
    monkeypatch, _seeded
):
    app = create_app()
    token = _token_for(_seeded)
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=_seeded["tenant"], session=session)
        other_visitor_conversation = await repo.create_conversation(vid=_seeded["other_visitor"])
        await session.commit()
        real_but_not_mine_cid = other_visitor_conversation.cid
    nonexistent_cid = uuid.uuid4()

    async with await http_client(app) as client:
        nonexistent_response = await _escalate(client, token, nonexistent_cid)
        not_mine_response = await _escalate(client, token, real_but_not_mine_cid)

    assert nonexistent_response.status_code == not_mine_response.status_code == 404
    assert nonexistent_response.json() == not_mine_response.json()


async def test_escalate_now_carries_real_cors_headers_on_every_branch(
    monkeypatch, _seeded, _capture_sent_emails
):
    # [ops bugfix]: POST /conversations/{cid}/escalate never had an
    # OPTIONS preflight handler or an Access-Control-Allow-Origin header
    # on ANY of its real responses -- found while investigating why the
    # widget's chat input stayed stuck disabled; fixing that bug meant
    # removing the GET /conversations workaround this same gap also
    # broke, which led to confirming live that "Talk to a person" has
    # the identical gap (same root cause: no CORS support was ever built
    # for ANY of the three conversations/escalate endpoints, unlike
    # /session and /chat, which both got it from day one). Checks the
    # preflight, the real 200, the 404 (a `raise HTTPException` would
    # silently drop these headers -- see app/handoff/routes.py's own
    # comment) and the real 409 (double-open-escalation) branch.
    app = await _build_app(monkeypatch)
    token = _token_for(_seeded)
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=_seeded["tenant"], session=session)
        conversation = await repo.create_conversation(vid=_seeded["visitor"])
        await session.commit()
        cid = conversation.cid
    nonexistent_cid = uuid.uuid4()

    async with await http_client(app) as client:
        preflight = await client.options(
            f"/api/v1/conversations/{cid}/escalate", headers={"Origin": ORIGIN}
        )
        success = await _escalate(client, token, cid)
        not_found = await _escalate(client, token, nonexistent_cid)
        conflict = await _escalate(client, token, cid)

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == ORIGIN
    assert preflight.headers["vary"] == "Origin"

    assert success.status_code == 200
    assert success.headers["access-control-allow-origin"] == ORIGIN
    assert success.headers["vary"] == "Origin"

    assert not_found.status_code == 404
    assert not_found.headers["access-control-allow-origin"] == ORIGIN
    assert not_found.headers["vary"] == "Origin"

    assert conflict.status_code == 409
    assert conflict.headers["access-control-allow-origin"] == ORIGIN
    assert conflict.headers["vary"] == "Origin"


# --- contact field validation / email header injection ----------------------


@pytest.mark.parametrize("field", ["contact_name", "contact_email", "contact_phone"])
async def test_crlf_in_a_contact_field_is_rejected_before_any_email_is_attempted(
    monkeypatch, _seeded, field, _capture_sent_emails
):
    app = await _build_app(monkeypatch)
    token = _token_for(_seeded)
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=_seeded["tenant"], session=session)
        conversation = await repo.create_conversation(vid=_seeded["visitor"])
        await session.commit()
        cid = conversation.cid

    value = "evil\r\nBcc: attacker@evil.example"
    if field == "contact_email":
        value = "a@b.com\r\nBcc: attacker@evil.example"
    async with await http_client(app) as client:
        response = await _escalate(client, token, cid, {field: value})

    assert response.status_code == 422
    assert _capture_sent_emails == []  # never even attempted


async def test_an_invalid_email_shape_is_rejected(monkeypatch, _seeded):
    app = await _build_app(monkeypatch)
    token = _token_for(_seeded)
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=_seeded["tenant"], session=session)
        conversation = await repo.create_conversation(vid=_seeded["visitor"])
        await session.commit()
        cid = conversation.cid

    async with await http_client(app) as client:
        response = await _escalate(client, token, cid, {"contact_email": "not-an-email"})
    assert response.status_code == 422


async def test_an_unknown_extra_field_is_rejected():
    # extra="forbid" -- same discipline as every other request schema in
    # this codebase (ChatRequest, SessionRequest). Pure schema check, no
    # database needed.
    with pytest.raises(ValidationError):
        EscalationRequest.model_validate({"contact_name": "x", "status": "resolved"})


# --- happy path, HTML injection in contact fields and transcript -------------


async def test_a_real_escalation_is_created_and_the_notification_email_is_html_escaped(
    monkeypatch, _seeded, _capture_sent_emails
):
    app = await _build_app(monkeypatch)
    token = _token_for(_seeded)
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=_seeded["tenant"], session=session)
        conversation = await repo.create_conversation(vid=_seeded["visitor"])
        await session.commit()
        cid = conversation.cid

    malicious_name = "<script>alert(1)</script>"
    async with await http_client(app) as client:
        response = await _escalate(
            client,
            token,
            cid,
            {
                "contact_name": malicious_name,
                "contact_email": "visitor@example.com",
                "contact_phone": "+1 555 123 4567",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "new"
    escalation_id = uuid.UUID(body["escalation_id"])

    async with db_session() as session:
        row = await session.get(Escalation, escalation_id)
        assert row is not None
        assert row.tenant_id == _seeded["tenant"]
        assert row.vid == _seeded["visitor"]
        assert row.conversation_id == cid
        assert row.status == "new"
        assert row.contact_name == malicious_name  # stored RAW -- escaping is output-time only

    assert len(_capture_sent_emails) == 1
    sent = _capture_sent_emails[0]
    assert sent["to"] == "acme-support@example.com"  # the tenant's OWN configured address
    assert sent["subtype"] == "html"
    assert "<script>alert(1)</script>" not in sent["body"]  # never live markup
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in sent["body"]  # inert, escaped text


async def test_assistant_transcript_lines_are_markdown_unescaped_then_html_escaped(
    monkeypatch, _seeded, _capture_sent_emails
):
    # A real AIMessage produced through the real graph carries
    # _sanitize_answer_text()'s own backslash-escaped markdown (e.g. a
    # cited link reads literally as "\[text\]\(url\)"). The email body
    # must show the clean, unescaped text (markdown_unescape()), THEN
    # HTML-escape it -- never raw backslashes, and never un-escaped text
    # fed directly into the HTML body without the HTML-escape step.
    chunks = build_fake_answer_arg_chunks(
        can_answer=True, used_chunk_ids=["c1"], answer="See [our docs](https://x) for more!"
    )
    monkeypatch.setattr(
        graph_module, "get_chat_model", lambda: FakeAnswerToolChatModel(arg_chunks=chunks)
    )
    monkeypatch.setattr(
        graph_module,
        "_retrieve_node",
        _fake_retrieve_node(
            [
                {
                    "document_id": "doc-1",
                    "text": "irrelevant",
                    "source_url": "https://tenant.example/docs/page",
                    "heading_path": ["Docs"],
                    "score": 0.9,
                }
            ]
        ),
    )
    app = await _build_app(monkeypatch)
    token = _token_for(_seeded)
    identity = VerifiedIdentity(tenant_id=_seeded["tenant"], vid=_seeded["visitor"], org=ORIGIN)
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=_seeded["tenant"], session=session)
        conversation = await repo.create_conversation(vid=_seeded["visitor"])
        await session.commit()
        cid = conversation.cid

    results = []
    async for event in run_turn(app.state.graph, identity, cid, {"messages": ["tell me more"]}):
        results.append(event)
    assert results

    async with await http_client(app) as client:
        response = await _escalate(client, token, cid)
    assert response.status_code == 200

    assert len(_capture_sent_emails) == 1
    body = _capture_sent_emails[0]["body"]
    assert "\\[" not in body and "\\]" not in body  # no leftover escaped markdown
    assert "<script" not in body.lower()  # nothing unescaped ever reached the HTML body


def _fake_retrieve_node(chunks, best_dense_score=0.9):
    async def _node(state, config):  # noqa: ANN001
        return {"retrieved_chunks": chunks, "best_dense_score": best_dense_score}

    return _node


# --- escalation spam: per-visitor rate limit, one open per conversation -----


async def test_fourth_escalation_in_an_hour_for_the_same_visitor_is_rate_limited(
    monkeypatch, _seeded
):
    set_valid_env(monkeypatch, VALID_ENV, ESCALATION_RATE_LIMIT_PER_VISITOR="2")
    get_settings.cache_clear()
    app = await _build_app(monkeypatch)
    token = _token_for(_seeded)

    cids = []
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=_seeded["tenant"], session=session)
        for _ in range(3):
            conversation = await repo.create_conversation(vid=_seeded["visitor"])
            cids.append(conversation.cid)
        await session.commit()

    async with await http_client(app) as client:
        first = await _escalate(client, token, cids[0])
        second = await _escalate(client, token, cids[1])
        third = await _escalate(client, token, cids[2])

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert "retry_after" in third.json()
    assert "Retry-After" in third.headers


async def test_a_second_escalation_on_the_same_open_conversation_is_rejected_with_409(
    monkeypatch, _seeded
):
    app = await _build_app(monkeypatch)
    token = _token_for(_seeded)
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=_seeded["tenant"], session=session)
        conversation = await repo.create_conversation(vid=_seeded["visitor"])
        await session.commit()
        cid = conversation.cid

    async with await http_client(app) as client:
        first = await _escalate(client, token, cid)
        second = await _escalate(client, token, cid)

    assert first.status_code == 200
    assert second.status_code == 409


async def test_a_new_escalation_is_allowed_once_the_open_one_is_resolved(monkeypatch, _seeded):
    app = await _build_app(monkeypatch)
    token = _token_for(_seeded)
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=_seeded["tenant"], session=session)
        conversation = await repo.create_conversation(vid=_seeded["visitor"])
        await session.commit()
        cid = conversation.cid

    async with await http_client(app) as client:
        first = await _escalate(client, token, cid)
    assert first.status_code == 200

    async with db_session() as session:
        row = await session.get(Escalation, uuid.UUID(first.json()["escalation_id"]))
        row.status = "resolved"
        await session.commit()

    async with await http_client(app) as client:
        second = await _escalate(client, token, cid)
    assert second.status_code == 200


# --- per-tenant hourly email cap: gates the SEND only, never row creation ---


async def test_per_tenant_email_cap_skips_the_send_but_still_creates_the_row(
    monkeypatch, _seeded, _capture_sent_emails
):
    set_valid_env(monkeypatch, VALID_ENV, ESCALATION_TENANT_HOURLY_EMAIL_CAP="1")
    get_settings.cache_clear()
    app = await _build_app(monkeypatch)
    token = _token_for(_seeded)

    cids = []
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=_seeded["tenant"], session=session)
        for _ in range(2):
            conversation = await repo.create_conversation(vid=_seeded["visitor"])
            cids.append(conversation.cid)
        await session.commit()

    async with await http_client(app) as client:
        first = await _escalate(client, token, cids[0])
        second = await _escalate(client, token, cids[1])

    # Both escalations succeed (the row is created either way) -- only
    # the SECOND tenant-wide email send is skipped.
    assert first.status_code == 200
    assert second.status_code == 200
    assert len(_capture_sent_emails) == 1

    async with db_session() as session:
        rows = (
            await session.execute(
                sa.select(Escalation).where(Escalation.tenant_id == _seeded["tenant"])
            )
        ).scalars().all()
    assert len(rows) == 2  # both rows exist regardless of the email cap


# --- tenant scoping: an escalation only ever notifies its own tenant -------


async def test_escalation_only_ever_notifies_its_own_conversations_tenant(
    monkeypatch, _seeded, _capture_sent_emails
):
    app = await _build_app(monkeypatch)
    token = _token_for(_seeded)
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=_seeded["tenant"], session=session)
        conversation = await repo.create_conversation(vid=_seeded["visitor"])
        await session.commit()
        cid = conversation.cid

    async with await http_client(app) as client:
        response = await _escalate(client, token, cid)
    assert response.status_code == 200

    assert len(_capture_sent_emails) == 1
    assert _capture_sent_emails[0]["to"] == "acme-support@example.com"
    assert _capture_sent_emails[0]["to"] != "other-support@example.com"


async def test_no_escalation_email_configured_skips_the_send_but_still_creates_the_row(
    monkeypatch, _seeded, _capture_sent_emails
):
    app = await _build_app(monkeypatch)
    token = _token_for(_seeded)
    async with db_session() as session:
        tenant = await session.get(Tenant, _seeded["tenant"])
        tenant.config = {}  # no escalation_email key at all
        repo = TenantScopedRepository(tenant_id=_seeded["tenant"], session=session)
        conversation = await repo.create_conversation(vid=_seeded["visitor"])
        await session.commit()
        cid = conversation.cid

    async with await http_client(app) as client:
        response = await _escalate(client, token, cid)

    assert response.status_code == 200  # the row is created regardless
    assert _capture_sent_emails == []  # never guessed an address
