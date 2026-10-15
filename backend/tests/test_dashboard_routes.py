# backend/tests/test_dashboard_routes.py
# Step 6.1 [SECURITY]: tests for /api/v1/dashboard/auth/* (backend/app/
# dashboard/routes.py), including the adversarial pass this task's own
# prompt requires: credential stuffing / lockout bypass, session
# fixation, CSRF on every state-changing route, cookie scope, cross-
# tenant access, logout/revocation actually ending access, and a widget
# JWT never satisfying a dashboard session.
#
# httpx's own cookie jar refuses to send a Secure-flagged cookie back
# over a plain "http://" base_url (confirmed live before writing this
# file -- a real, observed httpx behavior, not assumed) -- every
# __Host- cookie this module sets is Secure, so _dashboard_http_client()
# below uses "https://test" (ASGITransport has no real TLS either way;
# only httpx's own cookie-jar scheme check cares).
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from fastapi_users.db import SQLAlchemyUserDatabase
from httpx import ASGITransport, AsyncClient

from app.auth.tokens import encode_session_token
from app.dashboard.auth_backend import SESSION_COOKIE_NAME
from app.dashboard.csrf import CSRF_COOKIE_NAME, CSRF_HEADER_NAME
from app.dashboard.models import DashboardSession, User
from app.dashboard.passwords import password_helper
from app.dashboard.repository import create_membership
from app.main import create_app
from app.tenancy.models import Tenant
from tests.conftest import VALID_ENV, db_session, set_valid_env

_PASSWORD = "correct horse battery staple 1"  # noqa: S105 (test fixture value)
_WRONG_PASSWORD = "not the right password at all"  # noqa: S105


def _dashboard_http_client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test")


def _dashboard_client_from_ip(app, ip: str) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app, client=(ip, 12345)), base_url="https://test"
    )


def _create_app_with_env_override(monkeypatch, **overrides):
    set_valid_env(monkeypatch, VALID_ENV, **overrides)
    from app.config import get_settings

    get_settings.cache_clear()
    return create_app()


async def _attach_csrf(client: AsyncClient) -> dict[str, str]:
    """Fetches a CSRF cookie (stored into `client`'s own jar) and returns
    the matching header to send on a state-changing request.
    """
    response = await client.get("/api/v1/dashboard/auth/csrf")
    assert response.status_code == 204
    token = client.cookies[CSRF_COOKIE_NAME]
    return {CSRF_HEADER_NAME: token}


async def _create_user(session, email: str, password: str = _PASSWORD) -> User:
    user_db = SQLAlchemyUserDatabase(session, User)
    return await user_db.create({"email": email, "hashed_password": password_helper.hash(password)})


@pytest.fixture
async def _seeded(reset_test_database):
    ids = {
        "tenant_a": uuid.uuid4(),
        "tenant_b": uuid.uuid4(),
    }
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
        orphan = await _create_user(session, "orphan@example.com")
        await session.flush()
        await create_membership(session, user_id=owner_a.id, tenant_id=ids["tenant_a"])
        await create_membership(session, user_id=owner_b.id, tenant_id=ids["tenant_b"])
        # `orphan` deliberately has NO membership row.
        await session.commit()
        ids["owner_a_id"] = owner_a.id
        ids["owner_b_id"] = owner_b.id
        ids["orphan_id"] = orphan.id
    return ids


def _normalized(response):
    headers = sorted((k.lower(), v) for k, v in response.headers.items() if k.lower() != "date")
    return response.status_code, response.content, headers


# --- CSRF ----------------------------------------------------------------


async def test_csrf_cookie_has_the_expected_host_prefixed_attributes(_seeded):
    app = create_app()
    async with _dashboard_http_client(app) as client:
        response = await client.get("/api/v1/dashboard/auth/csrf")
    assert response.status_code == 204
    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith(f"{CSRF_COOKIE_NAME}=")
    assert "Secure" in set_cookie
    assert "Path=/" in set_cookie
    assert "samesite=strict" in set_cookie.lower()
    assert "Domain=" not in set_cookie
    # Deliberately NOT HttpOnly -- the SPA's own JavaScript must be able
    # to read this one and echo it back as a header.
    assert "HttpOnly" not in set_cookie


async def test_login_without_any_csrf_cookie_or_header_is_rejected(_seeded):
    app = create_app()
    async with _dashboard_http_client(app) as client:
        response = await client.post(
            "/api/v1/dashboard/auth/login",
            json={"email": "owner-a@example.com", "password": _PASSWORD},
        )
    assert response.status_code == 403


async def test_login_with_mismatched_csrf_cookie_and_header_is_rejected(_seeded):
    app = create_app()
    async with _dashboard_http_client(app) as client:
        await _attach_csrf(client)
        response = await client.post(
            "/api/v1/dashboard/auth/login",
            json={"email": "owner-a@example.com", "password": _PASSWORD},
            headers={CSRF_HEADER_NAME: "attacker-guessed-value"},
        )
    assert response.status_code == 403
    assert SESSION_COOKIE_NAME not in response.cookies


async def test_logout_without_csrf_is_rejected_even_with_a_valid_session(_seeded):
    app = create_app()
    async with _dashboard_http_client(app) as client:
        headers = await _attach_csrf(client)
        login = await client.post(
            "/api/v1/dashboard/auth/login",
            json={"email": "owner-a@example.com", "password": _PASSWORD},
            headers=headers,
        )
        assert login.status_code == 200

        logout = await client.post("/api/v1/dashboard/auth/logout")
        assert logout.status_code == 403

        # The session must still be valid -- the rejected logout had no
        # effect.
        me = await client.get("/api/v1/dashboard/auth/me")
        assert me.status_code == 200


# --- Login: success, failure shape, no oracle -----------------------------


async def test_login_success_sets_a_host_prefixed_session_cookie_and_returns_tenant(_seeded):
    app = create_app()
    async with _dashboard_http_client(app) as client:
        headers = await _attach_csrf(client)
        response = await client.post(
            "/api/v1/dashboard/auth/login",
            json={"email": "Owner-A@Example.com", "password": _PASSWORD},
            headers=headers,
        )
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == "owner-a@example.com"
    assert body["tenant"]["name"] == "Tenant A"

    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith(f"{SESSION_COOKIE_NAME}=")
    assert "Secure" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Path=/" in set_cookie
    assert "Domain=" not in set_cookie
    assert "samesite=strict" in set_cookie.lower()


async def test_wrong_password_and_unknown_email_fail_identically(_seeded):
    app = create_app()
    async with _dashboard_http_client(app) as client:
        headers = await _attach_csrf(client)
        wrong_password = await client.post(
            "/api/v1/dashboard/auth/login",
            json={"email": "owner-a@example.com", "password": _WRONG_PASSWORD},
            headers=headers,
        )
    async with _dashboard_http_client(app) as client:
        headers = await _attach_csrf(client)
        unknown_email = await client.post(
            "/api/v1/dashboard/auth/login",
            json={"email": "nobody-at-all@example.com", "password": _PASSWORD},
            headers=headers,
        )
    assert wrong_password.status_code == 401
    assert unknown_email.status_code == 401
    assert _normalized(wrong_password)[:2] == _normalized(unknown_email)[:2]
    assert SESSION_COOKIE_NAME not in wrong_password.cookies
    assert SESSION_COOKIE_NAME not in unknown_email.cookies


async def test_a_user_with_no_membership_is_refused_login_identically(_seeded):
    app = create_app()
    async with _dashboard_http_client(app) as client:
        headers = await _attach_csrf(client)
        response = await client.post(
            "/api/v1/dashboard/auth/login",
            json={"email": "orphan@example.com", "password": _PASSWORD},
            headers=headers,
        )
    assert response.status_code == 401
    assert response.json() == {"detail": "login could not be completed"}


async def test_inactive_user_with_correct_password_is_refused_login(_seeded):
    async with db_session() as session:
        result = await session.execute(sa.select(User).where(User.email == "owner-a@example.com"))
        user = result.scalar_one()
        user.is_active = False
        await session.commit()

    app = create_app()
    async with _dashboard_http_client(app) as client:
        headers = await _attach_csrf(client)
        response = await client.post(
            "/api/v1/dashboard/auth/login",
            json={"email": "owner-a@example.com", "password": _PASSWORD},
            headers=headers,
        )
    assert response.status_code == 401


async def test_unknown_field_in_login_body_returns_422(_seeded):
    app = create_app()
    async with _dashboard_http_client(app) as client:
        headers = await _attach_csrf(client)
        response = await client.post(
            "/api/v1/dashboard/auth/login",
            json={"email": "owner-a@example.com", "password": _PASSWORD, "tenant_id": "forged"},
            headers=headers,
        )
    assert response.status_code == 422


# --- Session fixation -------------------------------------------------


async def test_login_never_reuses_a_client_supplied_session_cookie_value(_seeded):
    app = create_app()
    attacker_chosen_value = "attacker-chosen-session-token"
    async with _dashboard_http_client(app) as client:
        client.cookies.set(SESSION_COOKIE_NAME, attacker_chosen_value)
        headers = await _attach_csrf(client)
        login = await client.post(
            "/api/v1/dashboard/auth/login",
            json={"email": "owner-a@example.com", "password": _PASSWORD},
            headers=headers,
        )
        assert login.status_code == 200
        # Read the real Set-Cookie header directly, not client.cookies --
        # httpx's own cookie jar raises CookieConflict once a manually
        # `.set()` cookie (no domain) and a real server-issued one (the
        # request's own host) coexist under the same name, a test-client
        # bookkeeping artifact with no real-browser equivalent, not a
        # server bug.
        set_cookie = login.headers["set-cookie"]
        assert attacker_chosen_value not in set_cookie

    # The attacker's own original, pre-chosen value must never become a
    # valid session -- probing it directly (a fresh client, cookie set by
    # hand) must fail.
    async with _dashboard_http_client(app) as client:
        client.cookies.set(SESSION_COOKIE_NAME, attacker_chosen_value)
        response = await client.get("/api/v1/dashboard/auth/me")
    assert response.status_code == 401


# --- Logout / revocation actually ends access -----------------------------


async def test_logout_revokes_the_session_and_subsequent_requests_are_401(_seeded):
    app = create_app()
    async with _dashboard_http_client(app) as client:
        headers = await _attach_csrf(client)
        await client.post(
            "/api/v1/dashboard/auth/login",
            json={"email": "owner-a@example.com", "password": _PASSWORD},
            headers=headers,
        )
        session_token = client.cookies[SESSION_COOKIE_NAME]

        logout = await client.post("/api/v1/dashboard/auth/logout", headers=headers)
        assert logout.status_code == 204

        me = await client.get("/api/v1/dashboard/auth/me")
        assert me.status_code == 401

    # The exact, now-revoked token must not work from a fresh client
    # either -- proves server-side revocation (the row is deleted, app/
    # dashboard/auth_backend.py's own destroy_token()), not merely that
    # this client's own cookie jar was cleared.
    async with _dashboard_http_client(app) as replay_client:
        replay_client.cookies.set(SESSION_COOKIE_NAME, session_token)
        replayed = await replay_client.get("/api/v1/dashboard/auth/me")
    assert replayed.status_code == 401

    async with db_session() as session:
        count = (
            await session.execute(sa.select(sa.func.count()).select_from(DashboardSession))
        ).scalar_one()
        assert count == 0


async def test_logout_without_a_session_cookie_is_401_not_403(_seeded):
    app = create_app()
    async with _dashboard_http_client(app) as client:
        headers = await _attach_csrf(client)
        response = await client.post("/api/v1/dashboard/auth/logout", headers=headers)
    assert response.status_code == 401


async def test_me_without_a_session_cookie_is_401(_seeded):
    app = create_app()
    async with _dashboard_http_client(app) as client:
        response = await client.get("/api/v1/dashboard/auth/me")
    assert response.status_code == 401


# --- A widget JWT never satisfies a dashboard session ----------------------


async def test_a_real_widget_jwt_on_the_session_cookie_does_not_satisfy_me(_seeded):
    # Dashboard sessions are separate from widget tokens (docs/SPEC.md
    # §12) -- a real, validly-signed widget JWT, presented as the
    # dashboard session cookie value, must fail exactly like any other
    # wrong value. It is not a lookup key app/dashboard/auth_backend.py's
    # own HashedDatabaseStrategy would ever recognize (it hashes
    # whatever string it is given and looks for an EXACT match), and
    # even a byte-for-byte collision is cryptographically negligible.
    token = encode_session_token(
        tenant_id=uuid.uuid4(), vid=uuid.uuid4(), origin="https://widget.example"
    )
    app = create_app()
    async with _dashboard_http_client(app) as client:
        client.cookies.set(SESSION_COOKIE_NAME, token)
        response = await client.get("/api/v1/dashboard/auth/me")
    assert response.status_code == 401


async def test_a_real_widget_jwt_as_an_authorization_bearer_header_does_not_satisfy_me(_seeded):
    # The other direction: CookieTransport only ever reads the
    # __Host-session cookie (app/dashboard/auth_backend.py) -- an
    # Authorization: Bearer header, even a genuinely valid widget JWT,
    # is never even looked at by a dashboard endpoint.
    token = encode_session_token(
        tenant_id=uuid.uuid4(), vid=uuid.uuid4(), origin="https://widget.example"
    )
    app = create_app()
    async with _dashboard_http_client(app) as client:
        response = await client.get(
            "/api/v1/dashboard/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 401


# --- Idle and absolute expiry ----------------------------------------


async def test_idle_expired_session_is_rejected(_seeded, monkeypatch):
    app = _create_app_with_env_override(monkeypatch, DASHBOARD_SESSION_IDLE_SECONDS="60")
    async with _dashboard_http_client(app) as client:
        headers = await _attach_csrf(client)
        await client.post(
            "/api/v1/dashboard/auth/login",
            json={"email": "owner-a@example.com", "password": _PASSWORD},
            headers=headers,
        )
        token = client.cookies[SESSION_COOKIE_NAME]

    async with db_session() as session:
        await session.execute(
            sa.update(DashboardSession)
            .where(DashboardSession.user_id == _seeded["owner_a_id"])
            .values(last_seen_at=datetime.now(UTC) - timedelta(seconds=120))
        )
        await session.commit()

    async with _dashboard_http_client(app) as client:
        client.cookies.set(SESSION_COOKIE_NAME, token)
        response = await client.get("/api/v1/dashboard/auth/me")
    assert response.status_code == 401


async def test_absolute_expired_session_is_rejected_even_if_recently_touched(_seeded, monkeypatch):
    app = _create_app_with_env_override(monkeypatch, DASHBOARD_SESSION_ABSOLUTE_SECONDS="1800")
    async with _dashboard_http_client(app) as client:
        headers = await _attach_csrf(client)
        await client.post(
            "/api/v1/dashboard/auth/login",
            json={"email": "owner-a@example.com", "password": _PASSWORD},
            headers=headers,
        )
        token = client.cookies[SESSION_COOKIE_NAME]

    async with db_session() as session:
        await session.execute(
            sa.update(DashboardSession)
            .where(DashboardSession.user_id == _seeded["owner_a_id"])
            .values(
                created_at=datetime.now(UTC) - timedelta(hours=1),
                last_seen_at=datetime.now(UTC),
            )
        )
        await session.commit()

    async with _dashboard_http_client(app) as client:
        client.cookies.set(SESSION_COOKIE_NAME, token)
        response = await client.get("/api/v1/dashboard/auth/me")
    assert response.status_code == 401


async def test_a_valid_session_refreshes_its_own_idle_window_on_each_request(_seeded):
    app = create_app()
    async with _dashboard_http_client(app) as client:
        headers = await _attach_csrf(client)
        await client.post(
            "/api/v1/dashboard/auth/login",
            json={"email": "owner-a@example.com", "password": _PASSWORD},
            headers=headers,
        )
        token = client.cookies[SESSION_COOKIE_NAME]

    earlier = datetime.now(UTC) - timedelta(minutes=5)
    async with db_session() as session:
        await session.execute(
            sa.update(DashboardSession)
            .where(DashboardSession.user_id == _seeded["owner_a_id"])
            .values(last_seen_at=earlier)
        )
        await session.commit()

    async with _dashboard_http_client(app) as client:
        client.cookies.set(SESSION_COOKIE_NAME, token)
        response = await client.get("/api/v1/dashboard/auth/me")
    assert response.status_code == 200

    async with db_session() as session:
        result = await session.execute(
            sa.select(DashboardSession.last_seen_at).where(
                DashboardSession.user_id == _seeded["owner_a_id"]
            )
        )
        assert result.scalar_one() > earlier


# --- Cross-tenant isolation -------------------------------------------


async def test_me_never_returns_another_tenant(_seeded):
    # /auth/me is a GET with no body at all -- there is structurally no
    # field here a forged tenant id could even be placed in. This test
    # documents that property by exercising both real sessions and
    # confirming each sees only its own tenant.
    app = create_app()
    async with _dashboard_http_client(app) as client_a:
        headers = await _attach_csrf(client_a)
        await client_a.post(
            "/api/v1/dashboard/auth/login",
            json={"email": "owner-a@example.com", "password": _PASSWORD},
            headers=headers,
        )
        me_a = await client_a.get("/api/v1/dashboard/auth/me")
    async with _dashboard_http_client(app) as client_b:
        headers = await _attach_csrf(client_b)
        await client_b.post(
            "/api/v1/dashboard/auth/login",
            json={"email": "owner-b@example.com", "password": _PASSWORD},
            headers=headers,
        )
        me_b = await client_b.get("/api/v1/dashboard/auth/me")

    assert me_a.json()["tenant"]["name"] == "Tenant A"
    assert me_b.json()["tenant"]["name"] == "Tenant B"
    assert me_a.json()["tenant"]["id"] != me_b.json()["tenant"]["id"]


# --- Credential stuffing / lockout: per-IP and per-account, independent --


async def test_per_ip_rate_limit_trips_regardless_of_which_account_is_targeted(
    _seeded, monkeypatch
):
    # Credential stuffing: many DIFFERENT accounts, same IP. Proves the
    # per-IP limiter is checked first and does not depend on any one
    # account's own state.
    app = _create_app_with_env_override(monkeypatch, DASHBOARD_LOGIN_RATE_LIMIT_PER_IP="2")
    async with _dashboard_http_client(app) as client:
        headers = await _attach_csrf(client)
        first = await client.post(
            "/api/v1/dashboard/auth/login",
            json={"email": "owner-a@example.com", "password": _WRONG_PASSWORD},
            headers=headers,
        )
        second = await client.post(
            "/api/v1/dashboard/auth/login",
            json={"email": "owner-b@example.com", "password": _WRONG_PASSWORD},
            headers=headers,
        )
        third = await client.post(
            "/api/v1/dashboard/auth/login",
            json={"email": "a-third-account@example.com", "password": _WRONG_PASSWORD},
            headers=headers,
        )
    assert first.status_code != 429
    assert second.status_code != 429
    assert third.status_code == 429
    assert "retry-after" in {k.lower() for k in third.headers}


async def test_per_ip_rate_limit_does_not_block_a_different_ip(_seeded, monkeypatch):
    # IP rotation (a real attacker technique, named explicitly in this
    # task's own adversarial-pass requirement): exhausting one IP's own
    # budget must not affect a different one.
    app = _create_app_with_env_override(monkeypatch, DASHBOARD_LOGIN_RATE_LIMIT_PER_IP="1")
    async with _dashboard_client_from_ip(app, "203.0.113.5") as client:
        headers = await _attach_csrf(client)
        first = await client.post(
            "/api/v1/dashboard/auth/login",
            json={"email": "owner-a@example.com", "password": _WRONG_PASSWORD},
            headers=headers,
        )
    assert first.status_code != 429

    async with _dashboard_client_from_ip(app, "203.0.113.9") as other_ip_client:
        headers = await _attach_csrf(other_ip_client)
        from_other_ip = await other_ip_client.post(
            "/api/v1/dashboard/auth/login",
            json={"email": "owner-a@example.com", "password": _WRONG_PASSWORD},
            headers=headers,
        )
    assert from_other_ip.status_code != 429


async def test_per_account_rate_limit_trips_even_across_rotated_ips(_seeded, monkeypatch):
    # The real lockout-bypass case this task names: an attacker rotating
    # IPs to dodge the per-IP limiter must still be stopped by the
    # per-ACCOUNT limiter, which is keyed by email, not by IP. A high
    # per-IP ceiling isolates this test to the per-account mechanism.
    app = _create_app_with_env_override(
        monkeypatch,
        DASHBOARD_LOGIN_RATE_LIMIT_PER_IP="100",
        DASHBOARD_LOGIN_RATE_LIMIT_PER_ACCOUNT="2",
    )
    results = []
    for i in range(3):
        async with _dashboard_client_from_ip(app, f"203.0.113.{10 + i}") as client:
            headers = await _attach_csrf(client)
            response = await client.post(
                "/api/v1/dashboard/auth/login",
                json={"email": "owner-a@example.com", "password": _WRONG_PASSWORD},
                headers=headers,
            )
            results.append(response)
    assert results[0].status_code != 429
    assert results[1].status_code != 429
    assert results[2].status_code == 429


async def test_per_account_rate_limit_does_not_affect_a_different_account(_seeded, monkeypatch):
    app = _create_app_with_env_override(
        monkeypatch,
        DASHBOARD_LOGIN_RATE_LIMIT_PER_IP="100",
        DASHBOARD_LOGIN_RATE_LIMIT_PER_ACCOUNT="1",
    )
    async with _dashboard_http_client(app) as client:
        headers = await _attach_csrf(client)
        exhausted = await client.post(
            "/api/v1/dashboard/auth/login",
            json={"email": "owner-a@example.com", "password": _WRONG_PASSWORD},
            headers=headers,
        )
        assert exhausted.status_code != 429
        blocked = await client.post(
            "/api/v1/dashboard/auth/login",
            json={"email": "owner-a@example.com", "password": _PASSWORD},
            headers=headers,
        )
        assert blocked.status_code == 429

        # A DIFFERENT account, same IP/client: unaffected by owner-a's
        # own exhausted budget.
        other_account = await client.post(
            "/api/v1/dashboard/auth/login",
            json={"email": "owner-b@example.com", "password": _PASSWORD},
            headers=headers,
        )
    assert other_account.status_code == 200


async def test_per_account_rate_limit_also_blocks_the_correct_password_once_exhausted(
    _seeded, monkeypatch
):
    # The real "lockout" property: once the per-account budget is spent,
    # even the CORRECT password is refused until the window clears --
    # not merely a limit on failed guesses.
    app = _create_app_with_env_override(
        monkeypatch,
        DASHBOARD_LOGIN_RATE_LIMIT_PER_IP="100",
        DASHBOARD_LOGIN_RATE_LIMIT_PER_ACCOUNT="1",
    )
    async with _dashboard_http_client(app) as client:
        headers = await _attach_csrf(client)
        await client.post(
            "/api/v1/dashboard/auth/login",
            json={"email": "owner-a@example.com", "password": _WRONG_PASSWORD},
            headers=headers,
        )
        locked_out_with_correct_password = await client.post(
            "/api/v1/dashboard/auth/login",
            json={"email": "owner-a@example.com", "password": _PASSWORD},
            headers=headers,
        )
    assert locked_out_with_correct_password.status_code == 429
