# backend/tests/dashboard_test_helpers.py
# Step 6.3: extracted from test_dashboard_routes.py/test_dashboard_
# tenant_routes.py at their THIRD real occurrence (test_dashboard_
# source_routes.py makes it three) -- past this project's own
# established 3rd-occurrence extraction threshold. Every dashboard
# route test file should import from here instead of redefining these.
from fastapi_users.db import SQLAlchemyUserDatabase
from httpx import ASGITransport, AsyncClient

from app.dashboard.csrf import CSRF_COOKIE_NAME, CSRF_HEADER_NAME
from app.dashboard.models import User
from app.dashboard.passwords import password_helper

DASHBOARD_TEST_PASSWORD = "correct horse battery staple 1"  # noqa: S105


def dashboard_http_client(app) -> AsyncClient:
    # httpx's own cookie jar refuses to send a Secure-flagged cookie back
    # over a plain "http://" base_url (confirmed live, Step 6.1) -- every
    # __Host- cookie this project sets is Secure, so "https://test" is
    # required here even though ASGITransport has no real TLS either way.
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test")


async def attach_csrf(client: AsyncClient) -> dict[str, str]:
    response = await client.get("/api/v1/dashboard/auth/csrf")
    assert response.status_code == 204
    token = client.cookies[CSRF_COOKIE_NAME]
    return {CSRF_HEADER_NAME: token}


async def create_dashboard_user(
    session, email: str, password: str = DASHBOARD_TEST_PASSWORD
) -> User:
    user_db = SQLAlchemyUserDatabase(session, User)
    return await user_db.create({"email": email, "hashed_password": password_helper.hash(password)})


async def login_as(
    client: AsyncClient, email: str, password: str = DASHBOARD_TEST_PASSWORD
) -> dict[str, str]:
    headers = await attach_csrf(client)
    response = await client.post(
        "/api/v1/dashboard/auth/login", json={"email": email, "password": password}, headers=headers
    )
    assert response.status_code == 200
    return headers
