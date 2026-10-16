# backend/tests/test_dashboard_escalation_routes.py
# Step 6.4 [SECURITY]: tests for /api/v1/dashboard/escalations/* and
# /api/v1/dashboard/usage. Same fixture shape as
# test_dashboard_tenant_routes.py (owner_a/owner_b, two tenants, for
# cross-tenant proof), plus a real Visitor/SiteKey pair -- Escalation.
# vid is a real FK, not nullable.
import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from fastapi_users.db import SQLAlchemyUserDatabase
from httpx import ASGITransport, AsyncClient

from app.auth.secrets import generate_visitor_secret, hash_visitor_secret
from app.dashboard.csrf import CSRF_COOKIE_NAME, CSRF_HEADER_NAME
from app.dashboard.models import User
from app.dashboard.passwords import password_helper
from app.dashboard.repository import create_membership
from app.handoff.models import Escalation
from app.main import create_app
from app.plans.models import Plan, UsageEvent
from app.tenancy.models import Conversation, SiteKey, Tenant, Visitor
from tests.conftest import db_session

_PASSWORD = "correct horse battery staple 1"  # noqa: S105
_ORIGIN = "https://tenant.example"


def _dashboard_http_client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test")


async def _attach_csrf(client: AsyncClient) -> dict[str, str]:
    response = await client.get("/api/v1/dashboard/auth/csrf")
    assert response.status_code == 204
    token = client.cookies[CSRF_COOKIE_NAME]
    return {CSRF_HEADER_NAME: token}


async def _create_user(session, email: str, password: str = _PASSWORD) -> User:
    user_db = SQLAlchemyUserDatabase(session, User)
    return await user_db.create({"email": email, "hashed_password": password_helper.hash(password)})


async def _login(client: AsyncClient, email: str) -> dict[str, str]:
    headers = await _attach_csrf(client)
    response = await client.post(
        "/api/v1/dashboard/auth/login",
        json={"email": email, "password": _PASSWORD},
        headers=headers,
    )
    assert response.status_code == 200
    return headers


@pytest.fixture
async def _seeded(reset_test_database):
    ids = {"tenant_a": uuid.uuid4(), "tenant_b": uuid.uuid4()}
    site_key_a = uuid.uuid4()
    site_key_b = uuid.uuid4()
    vid_a = uuid.uuid4()
    vid_b = uuid.uuid4()
    async with db_session() as session:
        session.add_all(
            [
                Tenant(id=ids["tenant_a"], name="Tenant A", status="active"),
                Tenant(id=ids["tenant_b"], name="Tenant B", status="active"),
            ]
        )
        await session.flush()
        owner_a = await _create_user(session, "owner-a@example.com")
        owner_b = await _create_user(session, "owner-b@example.com")
        await session.flush()
        await create_membership(session, user_id=owner_a.id, tenant_id=ids["tenant_a"])
        await create_membership(session, user_id=owner_b.id, tenant_id=ids["tenant_b"])
        session.add_all(
            [
                SiteKey(
                    id=site_key_a,
                    tenant_id=ids["tenant_a"],
                    key="pk_live_escalation_test_a",
                    environment="production",
                    status="live",
                    allowed_origins=[_ORIGIN],
                ),
                SiteKey(
                    id=site_key_b,
                    tenant_id=ids["tenant_b"],
                    key="pk_live_escalation_test_b",
                    environment="production",
                    status="live",
                    allowed_origins=[_ORIGIN],
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                Visitor(
                    vid=vid_a,
                    tenant_id=ids["tenant_a"],
                    site_key_id=site_key_a,
                    secret_hash=hash_visitor_secret(generate_visitor_secret()),
                ),
                Visitor(
                    vid=vid_b,
                    tenant_id=ids["tenant_b"],
                    site_key_id=site_key_b,
                    secret_hash=hash_visitor_secret(generate_visitor_secret()),
                ),
            ]
        )
        await session.commit()
        ids["vid_a"] = vid_a
        ids["vid_b"] = vid_b
    return ids


def _make_escalation(
    *,
    tenant_id: uuid.UUID,
    vid: uuid.UUID,
    status: str = "new",
    transcript: str = "Visitor: hi\n\nAssistant: hello",
) -> Escalation:
    return Escalation(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        vid=vid,
        conversation_id=None,
        reason="visitor_requested",
        contact_name="Jane",
        contact_email="jane@example.com",
        contact_phone=None,
        transcript=transcript,
        status=status,
    )


# --- List / open count ------------------------------------------------


async def test_list_escalations_is_tenant_scoped(_seeded):
    async with db_session() as session:
        session.add_all(
            [
                _make_escalation(tenant_id=_seeded["tenant_a"], vid=_seeded["vid_a"]),
                _make_escalation(tenant_id=_seeded["tenant_b"], vid=_seeded["vid_b"]),
            ]
        )
        await session.commit()

    app = create_app()
    async with _dashboard_http_client(app) as client:
        await _login(client, "owner-a@example.com")
        response = await client.get("/api/v1/dashboard/escalations")
    assert response.status_code == 200
    body = response.json()
    assert len(body["escalations"]) == 1
    assert body["escalations"][0]["contact_name"] == "Jane"
    # The list response never carries the full transcript -- a light
    # summary only.
    assert "transcript" not in body["escalations"][0]


async def test_list_escalations_without_login_is_401(_seeded):
    app = create_app()
    async with _dashboard_http_client(app) as client:
        response = await client.get("/api/v1/dashboard/escalations")
    assert response.status_code == 401


async def test_open_count_counts_new_and_seen_but_not_resolved(_seeded):
    async with db_session() as session:
        session.add_all(
            [
                _make_escalation(
                    tenant_id=_seeded["tenant_a"], vid=_seeded["vid_a"], status="new"
                ),
                _make_escalation(
                    tenant_id=_seeded["tenant_a"], vid=_seeded["vid_a"], status="seen"
                ),
                _make_escalation(
                    tenant_id=_seeded["tenant_a"], vid=_seeded["vid_a"], status="resolved"
                ),
            ]
        )
        await session.commit()

    app = create_app()
    async with _dashboard_http_client(app) as client:
        await _login(client, "owner-a@example.com")
        response = await client.get("/api/v1/dashboard/escalations/open-count")
    assert response.status_code == 200
    assert response.json()["count"] == 2


# --- Transcript (markdown-unescape, plain text) -----------------------


async def test_transcript_is_markdown_unescaped(_seeded):
    escalation_id = uuid.uuid4()
    async with db_session() as session:
        session.add(
            Escalation(
                id=escalation_id,
                tenant_id=_seeded["tenant_a"],
                vid=_seeded["vid_a"],
                conversation_id=None,
                reason="visitor_requested",
                # Only the real six escaped characters (app/agent/
                # graph.py's own `_ANSWER_TEXT_ESCAPE_TABLE`: \, !, [,
                # ], <, >) -- `(`/`)` are never escaped by
                # `_sanitize_answer_text()`, so a transcript fixture
                # using them would not exercise this at all.
                transcript="Visitor: hi\n\n"
                r"Assistant: sure, here: \[a link\](https://example.com)",
                status="new",
            )
        )
        await session.commit()

    app = create_app()
    async with _dashboard_http_client(app) as client:
        await _login(client, "owner-a@example.com")
        response = await client.get(f"/api/v1/dashboard/escalations/{escalation_id}")
    assert response.status_code == 200
    body = response.json()
    assert "\\[" not in body["transcript"]
    assert "[a link](https://example.com)" in body["transcript"]


async def test_get_escalation_for_another_tenant_is_404(_seeded):
    escalation_id = uuid.uuid4()
    async with db_session() as session:
        session.add(
            _make_escalation(tenant_id=_seeded["tenant_b"], vid=_seeded["vid_b"], transcript="x")
        )
        await session.commit()

    app = create_app()
    async with _dashboard_http_client(app) as client:
        await _login(client, "owner-a@example.com")
        response = await client.get(f"/api/v1/dashboard/escalations/{escalation_id}")
    assert response.status_code == 404


async def test_get_unknown_escalation_is_404(_seeded):
    app = create_app()
    async with _dashboard_http_client(app) as client:
        await _login(client, "owner-a@example.com")
        response = await client.get(f"/api/v1/dashboard/escalations/{uuid.uuid4()}")
    assert response.status_code == 404


# --- Status updates -----------------------------------------------------


async def test_update_status_to_resolved_persists(_seeded):
    escalation_id = uuid.uuid4()
    async with db_session() as session:
        session.add(
            Escalation(
                id=escalation_id,
                tenant_id=_seeded["tenant_a"],
                vid=_seeded["vid_a"],
                conversation_id=None,
                reason="visitor_requested",
                transcript="x",
                status="new",
            )
        )
        await session.commit()

    app = create_app()
    async with _dashboard_http_client(app) as client:
        headers = await _login(client, "owner-a@example.com")
        response = await client.patch(
            f"/api/v1/dashboard/escalations/{escalation_id}/status",
            json={"status": "resolved"},
            headers=headers,
        )
    assert response.status_code == 200
    assert response.json()["status"] == "resolved"


async def test_update_status_rejects_new_as_a_value(_seeded):
    escalation_id = uuid.uuid4()
    async with db_session() as session:
        session.add(_make_escalation(tenant_id=_seeded["tenant_a"], vid=_seeded["vid_a"]))
        await session.commit()

    app = create_app()
    async with _dashboard_http_client(app) as client:
        headers = await _login(client, "owner-a@example.com")
        response = await client.patch(
            f"/api/v1/dashboard/escalations/{escalation_id}/status",
            json={"status": "new"},
            headers=headers,
        )
    assert response.status_code == 422


async def test_update_status_without_csrf_is_rejected(_seeded):
    escalation = _make_escalation(tenant_id=_seeded["tenant_a"], vid=_seeded["vid_a"], status="new")
    async with db_session() as session:
        session.add(escalation)
        await session.commit()
    escalation_id = escalation.id

    app = create_app()
    async with _dashboard_http_client(app) as client:
        await _login(client, "owner-a@example.com")
        response = await client.patch(
            f"/api/v1/dashboard/escalations/{escalation_id}/status",
            json={"status": "resolved"},
        )
    assert response.status_code == 403


async def test_update_status_for_another_tenants_escalation_is_404(_seeded):
    escalation_id = uuid.uuid4()
    async with db_session() as session:
        session.add(
            Escalation(
                id=escalation_id,
                tenant_id=_seeded["tenant_b"],
                vid=_seeded["vid_b"],
                conversation_id=None,
                reason="visitor_requested",
                transcript="x",
                status="new",
            )
        )
        await session.commit()

    app = create_app()
    async with _dashboard_http_client(app) as client:
        headers = await _login(client, "owner-a@example.com")
        response = await client.patch(
            f"/api/v1/dashboard/escalations/{escalation_id}/status",
            json={"status": "resolved"},
            headers=headers,
        )
    assert response.status_code == 404


# --- Delete ---------------------------------------------------------------


async def test_delete_escalation_removes_it(_seeded):
    escalation_id = uuid.uuid4()
    async with db_session() as session:
        session.add(
            Escalation(
                id=escalation_id,
                tenant_id=_seeded["tenant_a"],
                vid=_seeded["vid_a"],
                conversation_id=None,
                reason="visitor_requested",
                transcript="x",
                status="new",
            )
        )
        await session.commit()

    app = create_app()
    async with _dashboard_http_client(app) as client:
        headers = await _login(client, "owner-a@example.com")
        response = await client.delete(
            f"/api/v1/dashboard/escalations/{escalation_id}", headers=headers
        )
    assert response.status_code == 204

    async with db_session() as session:
        remaining = (
            await session.execute(sa.select(Escalation.id).where(Escalation.id == escalation_id))
        ).scalar_one_or_none()
    assert remaining is None


async def test_delete_escalation_without_csrf_is_rejected(_seeded):
    escalation_id = uuid.uuid4()
    async with db_session() as session:
        session.add(
            Escalation(
                id=escalation_id,
                tenant_id=_seeded["tenant_a"],
                vid=_seeded["vid_a"],
                conversation_id=None,
                reason="visitor_requested",
                transcript="x",
                status="new",
            )
        )
        await session.commit()

    app = create_app()
    async with _dashboard_http_client(app) as client:
        await _login(client, "owner-a@example.com")
        response = await client.delete(f"/api/v1/dashboard/escalations/{escalation_id}")
    assert response.status_code == 403


async def test_delete_for_another_tenants_escalation_is_404(_seeded):
    escalation_id = uuid.uuid4()
    async with db_session() as session:
        session.add(
            Escalation(
                id=escalation_id,
                tenant_id=_seeded["tenant_b"],
                vid=_seeded["vid_b"],
                conversation_id=None,
                reason="visitor_requested",
                transcript="x",
                status="new",
            )
        )
        await session.commit()

    app = create_app()
    async with _dashboard_http_client(app) as client:
        headers = await _login(client, "owner-a@example.com")
        response = await client.delete(
            f"/api/v1/dashboard/escalations/{escalation_id}", headers=headers
        )
    assert response.status_code == 404


# --- Usage ------------------------------------------------------------


async def test_usage_with_no_plan_assigned(_seeded):
    app = create_app()
    async with _dashboard_http_client(app) as client:
        await _login(client, "owner-a@example.com")
        response = await client.get("/api/v1/dashboard/usage")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "plan_assigned": False,
        "quota": None,
        "used_this_month": 0,
        "fraction_used": None,
        "state": "no_plan",
    }


async def _assign_plan(tenant_id: uuid.UUID, quota: int) -> None:
    plan_id = uuid.uuid4()
    async with db_session() as session:
        session.add(
            Plan(
                id=plan_id,
                name=f"plan-{plan_id}",
                limits={
                    "ai_message_quota_per_month": quota,
                    "total_indexed_pages": 5000,
                    "crawl_page_cap": 500,
                    "upload_max_size_bytes": 10_000_000,
                    "db_sync_row_cap": 50_000,
                    "refresh_interval_min_allowed": "daily",
                    "refresh_now_daily_allowance": 3,
                },
            )
        )
        await session.flush()
        tenant = (
            await session.execute(sa.select(Tenant).where(Tenant.id == tenant_id))
        ).scalar_one()
        tenant.plan_id = plan_id
        await session.commit()


async def _add_usage_events(tenant_id: uuid.UUID, vid: uuid.UUID, count: int) -> None:
    conversation_id = uuid.uuid4()
    async with db_session() as session:
        session.add(Conversation(cid=conversation_id, tenant_id=tenant_id, vid=vid))
        await session.flush()
        session.add_all(
            [
                UsageEvent(
                    tenant_id=tenant_id,
                    vid=vid,
                    conversation_id=conversation_id,
                    counted_toward_quota=True,
                    model="gpt-6-luna",
                    prompt_tokens=10,
                    completion_tokens=10,
                    estimated_cost_usd=0.0001,
                    created_at=datetime.now(UTC),
                )
                for _ in range(count)
            ]
        )
        await session.commit()


async def test_usage_state_normal_below_80_percent(_seeded):
    await _assign_plan(_seeded["tenant_a"], quota=100)
    await _add_usage_events(_seeded["tenant_a"], _seeded["vid_a"], count=10)

    app = create_app()
    async with _dashboard_http_client(app) as client:
        await _login(client, "owner-a@example.com")
        response = await client.get("/api/v1/dashboard/usage")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "normal"
    assert body["used_this_month"] == 10
    assert body["quota"] == 100
    assert body["fraction_used"] == pytest.approx(0.10)


async def test_usage_state_warning_at_80_percent(_seeded):
    await _assign_plan(_seeded["tenant_a"], quota=100)
    await _add_usage_events(_seeded["tenant_a"], _seeded["vid_a"], count=80)

    app = create_app()
    async with _dashboard_http_client(app) as client:
        await _login(client, "owner-a@example.com")
        response = await client.get("/api/v1/dashboard/usage")
    assert response.json()["state"] == "warning"


async def test_usage_state_exceeded_at_100_percent(_seeded):
    await _assign_plan(_seeded["tenant_a"], quota=100)
    await _add_usage_events(_seeded["tenant_a"], _seeded["vid_a"], count=100)

    app = create_app()
    async with _dashboard_http_client(app) as client:
        await _login(client, "owner-a@example.com")
        response = await client.get("/api/v1/dashboard/usage")
    assert response.json()["state"] == "exceeded"


async def test_usage_is_tenant_scoped(_seeded):
    await _assign_plan(_seeded["tenant_a"], quota=100)
    await _add_usage_events(_seeded["tenant_a"], _seeded["vid_a"], count=100)

    app = create_app()
    async with _dashboard_http_client(app) as client:
        await _login(client, "owner-b@example.com")
        response = await client.get("/api/v1/dashboard/usage")
    assert response.status_code == 200
    body = response.json()
    assert body["plan_assigned"] is False
    assert body["used_this_month"] == 0
