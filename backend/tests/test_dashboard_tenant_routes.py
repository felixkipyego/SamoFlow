# backend/tests/test_dashboard_tenant_routes.py
# Step 6.2 [SECURITY]: tests for /api/v1/dashboard/tenant/* -- tenant
# settings and site keys. Same fixture shape as test_dashboard_routes.py
# (owner_a/owner_b/two tenants, for cross-tenant proof). RESOLVED at the
# Step 6.3 duplication check: the identical helpers below were extracted
# into tests/dashboard_test_helpers.py once a real third consumer
# appeared (test_dashboard_source_routes.py) -- this file's own already-
# passing local copies are left as-is (rule 2: don't modify a completed
# task without flagging), not retrofitted onto the shared module.
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from fastapi_users.db import SQLAlchemyUserDatabase
from httpx import ASGITransport, AsyncClient

from app.dashboard.csrf import CSRF_COOKIE_NAME, CSRF_HEADER_NAME
from app.dashboard.models import DashboardSession, User
from app.dashboard.passwords import password_helper
from app.dashboard.repository import create_membership
from app.main import create_app
from app.tenancy.models import SiteKey, Tenant
from tests.conftest import db_session

_PASSWORD = "correct horse battery staple 1"  # noqa: S105


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
        site_key_a = SiteKey(
            tenant_id=ids["tenant_a"],
            key="pk_live_existing_a",
            allowed_origins=[],
            environment="production",
            status="draft",
        )
        session.add(site_key_a)
        await session.commit()
        ids["site_key_a_id"] = site_key_a.id
        ids["owner_a_id"] = owner_a.id
    return ids


# --- GET returns real defaults for a brand-new tenant --------------------


async def test_get_tenant_config_returns_real_defaults(_seeded):
    app = create_app()
    async with _dashboard_http_client(app) as client:
        await _login(client, "owner-a@example.com")
        response = await client.get("/api/v1/dashboard/tenant")
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Chat"
    assert body["greeting"] == "Hi! How can we help?"
    assert body["colors"] == {"primary": "#4f46e5"}
    assert body["position"] == "right"
    assert body["retention_days"] == 30
    assert body["escalation_retention_months"] == 12
    assert body["trace_text_enabled"] is True
    assert body["support_email"] is None


async def test_get_tenant_config_without_login_is_401(_seeded):
    app = create_app()
    async with _dashboard_http_client(app) as client:
        response = await client.get("/api/v1/dashboard/tenant")
    assert response.status_code == 401


# --- Support details ------------------------------------------------------


async def test_update_support_details_persists_and_returns_real_values(_seeded):
    app = create_app()
    async with _dashboard_http_client(app) as client:
        headers = await _login(client, "owner-a@example.com")
        response = await client.patch(
            "/api/v1/dashboard/tenant/support-details",
            json={
                "support_email": "help@acme.example",
                "support_phone": "+1 555 0100",
                "support_contact_link": "https://acme.example/contact",
                "support_hours": "Mon-Fri 9-5",
                "support_message": "We will get back to you soon.",
            },
            headers=headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["support_email"] == "help@acme.example"
        assert body["support_contact_link"] == "https://acme.example/contact"

        refetched = await client.get("/api/v1/dashboard/tenant")
    assert refetched.json()["support_phone"] == "+1 555 0100"


async def test_update_support_details_rejects_non_https_contact_link(_seeded):
    app = create_app()
    async with _dashboard_http_client(app) as client:
        headers = await _login(client, "owner-a@example.com")
        response = await client.patch(
            "/api/v1/dashboard/tenant/support-details",
            json={"support_contact_link": "javascript:alert(1)"},
            headers=headers,
        )
    assert response.status_code == 422


async def test_update_support_details_without_csrf_is_rejected(_seeded):
    app = create_app()
    async with _dashboard_http_client(app) as client:
        await _login(client, "owner-a@example.com")
        response = await client.patch(
            "/api/v1/dashboard/tenant/support-details", json={"support_email": "help@acme.example"}
        )
    assert response.status_code == 403


async def test_update_support_details_does_not_touch_other_sections(_seeded):
    # Proves the staleness-bug fix this task also made in update_source():
    # saving one card never wipes another's own keys in the same shared
    # JSONB column.
    app = create_app()
    async with _dashboard_http_client(app) as client:
        headers = await _login(client, "owner-a@example.com")
        await client.patch(
            "/api/v1/dashboard/tenant/retention",
            json={"retention_days": 45, "escalation_retention_months": 6},
            headers=headers,
        )
        await client.patch(
            "/api/v1/dashboard/tenant/support-details",
            json={"support_email": "help@acme.example"},
            headers=headers,
        )
        response = await client.get("/api/v1/dashboard/tenant")
    body = response.json()
    assert body["retention_days"] == 45
    assert body["support_email"] == "help@acme.example"


# --- Widget look -----------------------------------------------------------


async def test_update_widget_look_persists_valid_values(_seeded):
    app = create_app()
    async with _dashboard_http_client(app) as client:
        headers = await _login(client, "owner-a@example.com")
        response = await client.patch(
            "/api/v1/dashboard/tenant/widget-look",
            json={
                "title": "Acme Support",
                "greeting": "Welcome!",
                "colors": {"primary": "#112233"},
                "logo_url": "https://acme.example/logo.png",
                "position": "left",
                "privacy_notice_url": "https://acme.example/privacy",
            },
            headers=headers,
        )
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Acme Support"
    assert body["colors"] == {"primary": "#112233"}
    assert body["position"] == "left"


async def test_update_widget_look_rejects_invalid_color(_seeded):
    app = create_app()
    async with _dashboard_http_client(app) as client:
        headers = await _login(client, "owner-a@example.com")
        response = await client.patch(
            "/api/v1/dashboard/tenant/widget-look",
            json={
                "title": "Acme Support",
                "greeting": "Welcome!",
                "colors": {"primary": "red"},
                "logo_url": None,
                "position": "left",
                "privacy_notice_url": None,
            },
            headers=headers,
        )
    assert response.status_code == 422


async def test_update_widget_look_rejects_data_url_logo(_seeded):
    app = create_app()
    async with _dashboard_http_client(app) as client:
        headers = await _login(client, "owner-a@example.com")
        response = await client.patch(
            "/api/v1/dashboard/tenant/widget-look",
            json={
                "title": "Acme Support",
                "greeting": "Welcome!",
                "colors": {"primary": "#112233"},
                "logo_url": "data:text/html,<script>alert(1)</script>",
                "position": "left",
                "privacy_notice_url": None,
            },
            headers=headers,
        )
    assert response.status_code == 422


# --- Retention --------------------------------------------------------


async def test_update_retention_rejects_out_of_range(_seeded):
    app = create_app()
    async with _dashboard_http_client(app) as client:
        headers = await _login(client, "owner-a@example.com")
        response = await client.patch(
            "/api/v1/dashboard/tenant/retention",
            json={"retention_days": 999, "escalation_retention_months": 12},
            headers=headers,
        )
    assert response.status_code == 422


# --- Trace-text switch ------------------------------------------------


async def test_update_trace_text_persists(_seeded):
    app = create_app()
    async with _dashboard_http_client(app) as client:
        headers = await _login(client, "owner-a@example.com")
        response = await client.patch(
            "/api/v1/dashboard/tenant/trace-text",
            json={"trace_text_enabled": False},
            headers=headers,
        )
    assert response.status_code == 200
    assert response.json()["trace_text_enabled"] is False


# --- Site keys / origins: cross-tenant isolation + recent-auth ------------


async def test_list_site_keys_is_tenant_scoped(_seeded):
    app = create_app()
    async with _dashboard_http_client(app) as client:
        await _login(client, "owner-b@example.com")
        response = await client.get("/api/v1/dashboard/tenant/site-keys")
    assert response.status_code == 200
    assert response.json() == []  # tenant B's own key list, never tenant A's


async def test_create_site_key_requires_recent_auth(_seeded):
    # Same technique as test_dashboard_routes.py's own idle/absolute
    # expiry tests: the session itself stays otherwise valid (current_
    # active_user still accepts it), only its own `created_at` -- the
    # real sign-in time require_recent_auth() reads -- is pushed back
    # past the default 900s window.
    app = create_app()
    async with _dashboard_http_client(app) as client:
        headers = await _login(client, "owner-a@example.com")
        async with db_session() as session:
            await session.execute(
                sa.update(DashboardSession)
                .where(DashboardSession.user_id == _seeded["owner_a_id"])
                .values(created_at=datetime.now(UTC) - timedelta(seconds=1000))
            )
            await session.commit()
        response = await client.post(
            "/api/v1/dashboard/tenant/site-keys", json={}, headers=headers
        )
    assert response.status_code == 403


async def test_create_site_key_succeeds_with_fresh_login(_seeded):
    app = create_app()
    async with _dashboard_http_client(app) as client:
        headers = await _login(client, "owner-a@example.com")
        response = await client.post(
            "/api/v1/dashboard/tenant/site-keys", json={}, headers=headers
        )
    assert response.status_code == 201
    body = response.json()
    assert body["key"].startswith("pk_live_")
    assert body["status"] == "draft"
    assert body["allowed_origins"] == []


async def test_update_site_key_origins_normalises_and_persists(_seeded):
    app = create_app()
    async with _dashboard_http_client(app) as client:
        headers = await _login(client, "owner-a@example.com")
        response = await client.patch(
            f"/api/v1/dashboard/tenant/site-keys/{_seeded['site_key_a_id']}/origins",
            json={"allowed_origins": ["HTTPS://Example.COM:443"]},
            headers=headers,
        )
    assert response.status_code == 200
    body = response.json()
    assert body["allowed_origins"] == ["https://example.com"]


async def test_update_site_key_origins_rejects_a_malformed_origin(_seeded):
    app = create_app()
    async with _dashboard_http_client(app) as client:
        headers = await _login(client, "owner-a@example.com")
        response = await client.patch(
            f"/api/v1/dashboard/tenant/site-keys/{_seeded['site_key_a_id']}/origins",
            json={"allowed_origins": ["not a valid origin"]},
            headers=headers,
        )
    assert response.status_code == 422


async def test_update_site_key_origins_for_another_tenants_key_is_404(_seeded):
    # Tenant B's own login, targeting Tenant A's real site_key_id --
    # must look exactly like a missing id, never reveal it belongs to
    # someone else.
    app = create_app()
    async with _dashboard_http_client(app) as client:
        headers = await _login(client, "owner-b@example.com")
        response = await client.patch(
            f"/api/v1/dashboard/tenant/site-keys/{_seeded['site_key_a_id']}/origins",
            json={"allowed_origins": ["https://example.com"]},
            headers=headers,
        )
    assert response.status_code == 404


# --- Install snippet --------------------------------------------------


async def test_install_snippet_returns_the_real_site_key(_seeded):
    app = create_app()
    async with _dashboard_http_client(app) as client:
        await _login(client, "owner-a@example.com")
        response = await client.get("/api/v1/dashboard/tenant/install-snippet")
    assert response.status_code == 200
    assert response.json()["site_key"] == "pk_live_existing_a"


async def test_install_snippet_404s_with_no_site_key(_seeded):
    app = create_app()
    async with _dashboard_http_client(app) as client:
        await _login(client, "owner-b@example.com")
        response = await client.get("/api/v1/dashboard/tenant/install-snippet")
    assert response.status_code == 404
