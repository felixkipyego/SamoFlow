# backend/tests/test_admin_routes.py
# Step 6.1 [SECURITY]: /api/v1/admin (backend/app/admin/routes.py),
# gated by platform_admin sessions -- the real HTTP-level proof that
# current_platform_admin actually enforces is_superuser (app/dashboard/
# auth_backend.py), distinct from app/dashboard/routes.py's own tests
# (current_active_user, no superuser requirement) and from app/
# dashboard/admin_cli.py's own tests (exercise the repository
# primitives directly, never through an HTTP session at all).
import uuid

from fastapi_users.db import SQLAlchemyUserDatabase
from httpx import ASGITransport, AsyncClient

from app.auth.tokens import encode_session_token
from app.dashboard.csrf import CSRF_COOKIE_NAME, CSRF_HEADER_NAME
from app.dashboard.models import User
from app.dashboard.passwords import password_helper
from app.dashboard.repository import create_membership
from app.ingest.models import VerifiedDomain
from app.main import create_app
from app.tenancy.models import Tenant
from tests.conftest import db_session

_PASSWORD = "correct horse battery staple 1"  # noqa: S105
SESSION_COOKIE_NAME = "__Host-session"


def _dashboard_http_client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test")


async def _attach_csrf(client: AsyncClient) -> dict[str, str]:
    response = await client.get("/api/v1/dashboard/auth/csrf")
    assert response.status_code == 204
    token = client.cookies[CSRF_COOKIE_NAME]
    return {CSRF_HEADER_NAME: token}


async def _create_user(session, email: str, *, is_superuser: bool) -> User:
    # A real finding, live: POST /auth/login (app/dashboard/routes.py)
    # unconditionally requires a real tenant membership to succeed --
    # even for a platform admin, there is no "login with no tenant at
    # all" path today. Every real admin account this project's own
    # tooling can create (app/dashboard/seed_cli.py's `--platform-admin`
    # flag) already satisfies this, since seed_owner() always creates a
    # membership alongside the user -- this helper mirrors that real
    # shape (a membership of its own, unrelated to whatever OTHER
    # tenant a given test's own domain/site-key fixtures use) rather
    # than a user with no tenant at all, which the real login path
    # cannot authenticate regardless of is_superuser.
    user_db = SQLAlchemyUserDatabase(session, User)
    user = await user_db.create(
        {
            "email": email,
            "hashed_password": password_helper.hash(_PASSWORD),
            "is_superuser": is_superuser,
        }
    )
    own_tenant = Tenant(name=f"{email}'s own tenant", status="active")
    session.add(own_tenant)
    await session.flush()
    await create_membership(session, user_id=user.id, tenant_id=own_tenant.id)
    return user


async def _login(client: AsyncClient, email: str) -> None:
    headers = await _attach_csrf(client)
    response = await client.post(
        "/api/v1/dashboard/auth/login",
        json={"email": email, "password": _PASSWORD},
        headers=headers,
    )
    assert response.status_code == 200, response.text


async def test_admin_me_requires_a_session_at_all(reset_test_database):
    app = create_app()
    async with _dashboard_http_client(app) as client:
        response = await client.get("/api/v1/admin/me")
    assert response.status_code == 401


async def test_admin_me_rejects_a_real_ordinary_tenant_owner(reset_test_database):
    async with db_session() as session:
        await _create_user(session, "owner@example.com", is_superuser=False)
        await session.commit()

    app = create_app()
    async with _dashboard_http_client(app) as client:
        await _login(client, "owner@example.com")
        response = await client.get("/api/v1/admin/me")
    # 403, not 401: the session itself is genuinely valid -- confirmed
    # by fastapi_users' own Authenticator._authenticate() (read directly
    # before relying on this): a resolved-but-non-superuser user is a
    # real 403, distinct from "no/invalid session" (401).
    assert response.status_code == 403


async def test_admin_me_succeeds_for_a_real_platform_admin(reset_test_database):
    async with db_session() as session:
        admin = await _create_user(session, "felix@example.com", is_superuser=True)
        await session.commit()

    app = create_app()
    async with _dashboard_http_client(app) as client:
        await _login(client, "felix@example.com")
        response = await client.get("/api/v1/admin/me")
    assert response.status_code == 200
    assert response.json() == {"id": str(admin.id), "email": "felix@example.com"}


async def test_admin_me_rejects_a_widget_jwt(reset_test_database):
    token = encode_session_token(
        tenant_id=uuid.uuid4(), vid=uuid.uuid4(), origin="https://widget.example"
    )
    app = create_app()
    async with _dashboard_http_client(app) as client:
        client.cookies.set(SESSION_COOKIE_NAME, token)
        response = await client.get("/api/v1/admin/me")
    assert response.status_code == 401


async def test_revoke_domain_route_requires_platform_admin_not_just_any_session(
    reset_test_database,
):
    async with db_session() as session:
        tenant = Tenant(name="Acme Inc", status="active")
        session.add(tenant)
        await session.flush()
        domain = VerifiedDomain(
            tenant_id=tenant.id,
            domain="example.com",
            status="verified",
            method="dns",
            verification_token="tok",  # noqa: S106 (test fixture value, not a real secret)
        )
        session.add(domain)
        await _create_user(session, "owner@example.com", is_superuser=False)
        await session.commit()
        domain_id = domain.id

    app = create_app()
    async with _dashboard_http_client(app) as client:
        await _login(client, "owner@example.com")
        headers = await _attach_csrf(client)
        response = await client.post(
            f"/api/v1/admin/domains/{domain_id}/revoke", headers=headers
        )
    assert response.status_code == 403


async def test_revoke_domain_route_requires_csrf(reset_test_database):
    async with db_session() as session:
        tenant = Tenant(name="Acme Inc", status="active")
        session.add(tenant)
        await session.flush()
        domain = VerifiedDomain(
            tenant_id=tenant.id,
            domain="example.com",
            status="verified",
            method="dns",
            verification_token="tok",  # noqa: S106 (test fixture value, not a real secret)
        )
        session.add(domain)
        await _create_user(session, "felix@example.com", is_superuser=True)
        await session.commit()
        domain_id = domain.id

    app = create_app()
    async with _dashboard_http_client(app) as client:
        await _login(client, "felix@example.com")
        response = await client.post(f"/api/v1/admin/domains/{domain_id}/revoke")
    assert response.status_code == 403


async def test_revoke_domain_route_works_for_a_real_platform_admin_across_any_tenant(
    reset_test_database,
):
    # Cross-tenant by design -- a platform admin acts on ANY tenant's
    # domain, never scoped to one (unlike every dashboard-owner
    # endpoint, which is tenant-scoped through the logged-in
    # membership). This is the one real exception to that rule, and it
    # is intentional (docs/SPEC.md §12: "Cross-tenant actions exist
    # only in the platform admin area").
    async with db_session() as session:
        tenant = Tenant(name="Some Other Tenant", status="active")
        session.add(tenant)
        await session.flush()
        domain = VerifiedDomain(
            tenant_id=tenant.id,
            domain="example.com",
            status="verified",
            method="dns",
            verification_token="tok",  # noqa: S106 (test fixture value, not a real secret)
        )
        session.add(domain)
        await _create_user(session, "felix@example.com", is_superuser=True)
        await session.commit()
        domain_id = domain.id

    app = create_app()
    async with _dashboard_http_client(app) as client:
        await _login(client, "felix@example.com")
        headers = await _attach_csrf(client)
        response = await client.post(
            f"/api/v1/admin/domains/{domain_id}/revoke", headers=headers
        )
    assert response.status_code == 200
    assert response.json()["status"] == "revoked"


async def test_revoke_domain_route_returns_404_for_an_unknown_domain(reset_test_database):
    async with db_session() as session:
        await _create_user(session, "felix@example.com", is_superuser=True)
        await session.commit()

    app = create_app()
    async with _dashboard_http_client(app) as client:
        await _login(client, "felix@example.com")
        headers = await _attach_csrf(client)
        response = await client.post(
            f"/api/v1/admin/domains/{uuid.uuid4()}/revoke", headers=headers
        )
    assert response.status_code == 404
