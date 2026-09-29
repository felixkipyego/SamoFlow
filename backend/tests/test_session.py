# backend/tests/test_session.py
# Task 1.4.g: tests for POST /api/v1/session (backend/app/auth/routes.py),
# the first real HTTP endpoint in this project.
#
# Two groups:
#   - tests that need no real database (an unroutable DATABASE_URL, same
#     TEST-NET-3 convention test_main.py's own /health tests use): proving
#     a code path structurally never touches Postgres, since a real
#     attempt against 203.0.113.1 would be observably slow/erroring, not a
#     fast, clean response;
#   - live tests against the real test database (require_test_database()),
#     seeding one active tenant with two live-status site keys (one
#     suspended), one draft-status site key, one suspended tenant, and one
#     existing visitor with a known secret -- covering every
#     failure/success combination the task names.
import uuid

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient

from app import db
from app.auth.secrets import generate_visitor_secret, hash_visitor_secret
from app.auth.tokens import decode_session_token
from app.db import Base
from app.main import create_app
from app.plans import models as plans_models  # noqa: F401 (registers "plans" on Base.metadata)
from app.tenancy.models import SiteKey, Tenant, Visitor
from tests.conftest import VALID_ENV, db_session, require_test_database, set_valid_env

ALLOWED_ORIGIN = "https://widget.example"
DRAFT_ALLOWED_ORIGIN = "https://draft-widget.example"
DISALLOWED_ORIGIN = "https://not-allowed.example"

# Unroutable (RFC 5737 TEST-NET-3): proves a code path never actually
# attempts a database connection, the same way test_main.py's own /health
# tests prove it never contacts Postgres or Qdrant.
_UNROUTABLE_DATABASE_URL = "postgresql+psycopg://user:pw@203.0.113.1:5432/widgetplatform"


async def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _normalized(response):
    # Full header set minus Date (the only genuinely non-deterministic one)
    # plus status and raw body bytes -- used to prove two responses are
    # byte-identical, not just "both a 403".
    headers = sorted((k.lower(), v) for k, v in response.headers.items() if k.lower() != "date")
    return response.status_code, response.content, headers


@pytest.fixture
async def _seeded(monkeypatch):
    # Same per-test cache-clear/schema/db_session plumbing as
    # test_tenancy_repository.py's own _seeded_tenants fixture.
    database_url = require_test_database()
    set_valid_env(monkeypatch, VALID_ENV, DATABASE_URL=database_url)
    db.get_engine.cache_clear()
    db._session_factory.cache_clear()

    sync_engine = sa.create_engine(database_url, poolclass=sa.pool.NullPool)
    Base.metadata.drop_all(sync_engine)
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    ids = {
        "tenant_active": uuid.uuid4(),
        "tenant_suspended": uuid.uuid4(),
        "site_key_live": uuid.uuid4(),
        "site_key_draft": uuid.uuid4(),
        "site_key_suspended": uuid.uuid4(),
        "site_key_suspended_tenant": uuid.uuid4(),
        "existing_visitor": uuid.uuid4(),
    }
    existing_secret = generate_visitor_secret()

    async with db_session() as session:
        session.add_all(
            [
                Tenant(
                    id=ids["tenant_active"],
                    name="Active Tenant",
                    status="active",
                    config={
                        "title": "Acme Support",
                        "greeting": "Hi there!",
                        "colors": {"primary": "#112233"},
                        "logo_url": "https://cdn.example/logo.png",
                        "position": "right",
                        "privacy_notice_url": "https://acme.example/privacy",
                        # Private-looking fields (docs/SPEC.md §4.1's own
                        # data-model table): must never appear in a session
                        # response.
                        "escalation_email": "distinctive@example.com",
                        "relevance_threshold": 0.42,
                    },
                ),
                Tenant(id=ids["tenant_suspended"], name="Suspended Tenant", status="suspended"),
            ]
        )
        await session.flush()
        session.add_all(
            [
                SiteKey(
                    id=ids["site_key_live"],
                    key="pk_live_live_key",
                    tenant_id=ids["tenant_active"],
                    allowed_origins=[ALLOWED_ORIGIN],
                    environment="production",
                    status="live",
                ),
                SiteKey(
                    id=ids["site_key_draft"],
                    key="pk_live_draft_key",
                    tenant_id=ids["tenant_active"],
                    allowed_origins=[DRAFT_ALLOWED_ORIGIN],
                    environment="production",
                    status="draft",
                ),
                SiteKey(
                    id=ids["site_key_suspended"],
                    key="pk_live_suspended_key",
                    tenant_id=ids["tenant_active"],
                    allowed_origins=[ALLOWED_ORIGIN],
                    environment="production",
                    status="suspended",
                ),
                SiteKey(
                    id=ids["site_key_suspended_tenant"],
                    key="pk_live_suspended_tenant_key",
                    tenant_id=ids["tenant_suspended"],
                    allowed_origins=[ALLOWED_ORIGIN],
                    environment="production",
                    status="live",
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                Visitor(
                    vid=ids["existing_visitor"],
                    tenant_id=ids["tenant_active"],
                    site_key_id=ids["site_key_live"],
                    secret_hash=hash_visitor_secret(existing_secret),
                ),
            ]
        )
        await session.commit()

    yield {"ids": ids, "existing_secret": existing_secret}

    await db.get_engine().dispose()
    db.get_engine.cache_clear()
    db._session_factory.cache_clear()


# -- No-database tests -------------------------------------------------------


async def test_unknown_field_in_request_returns_422_before_any_database_access(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV, DATABASE_URL=_UNROUTABLE_DATABASE_URL)
    app = create_app()
    async with await _client(app) as client:
        response = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_whatever", "admin": True},
            headers={"Origin": ALLOWED_ORIGIN},
        )
    # 422, not the 200/403 shape a valid body against this site_key alone
    # would produce -- distinguishable from both.
    assert response.status_code == 422


async def test_options_preflight_answers_without_touching_the_database(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV, DATABASE_URL=_UNROUTABLE_DATABASE_URL)
    app = create_app()
    async with await _client(app) as client:
        response = await client.options("/api/v1/session", headers={"Origin": ALLOWED_ORIGIN})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert response.headers["access-control-allow-methods"] == "POST, OPTIONS"
    assert response.headers["access-control-allow-headers"] == "Content-Type"
    assert response.headers["vary"] == "Origin"


# -- Live tests ---------------------------------------------------------------


async def test_all_failure_categories_produce_byte_identical_responses(_seeded):
    # ONE test proving all five failure categories are indistinguishable
    # from each other -- not five separate "it's a 403" tests.
    app = create_app()
    async with await _client(app) as client:
        missing_origin = await client.post(
            "/api/v1/session", json={"site_key": "pk_live_live_key"}
        )
        unknown_site_key = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_does_not_exist"},
            headers={"Origin": ALLOWED_ORIGIN},
        )
        suspended_site_key = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_suspended_key"},
            headers={"Origin": ALLOWED_ORIGIN},
        )
        non_active_tenant = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_suspended_tenant_key"},
            headers={"Origin": ALLOWED_ORIGIN},
        )
        disallowed_origin = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_live_key"},
            headers={"Origin": DISALLOWED_ORIGIN},
        )

    scenarios = [
        missing_origin,
        unknown_site_key,
        suspended_site_key,
        non_active_tenant,
        disallowed_origin,
    ]
    normalized = [_normalized(r) for r in scenarios]
    assert all(n == normalized[0] for n in normalized), normalized
    assert normalized[0][0] == 403


async def test_failure_response_carries_vary_origin_but_never_acao(_seeded):
    app = create_app()
    async with await _client(app) as client:
        response = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_does_not_exist"},
            headers={"Origin": ALLOWED_ORIGIN},
        )
    assert response.headers.get("vary") == "Origin"
    assert "access-control-allow-origin" not in response.headers


async def test_fabricated_and_real_but_wrong_site_keys_fail_identically(_seeded):
    app = create_app()
    async with await _client(app) as client:
        fabricated = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_totally_fabricated_abc123"},
            headers={"Origin": ALLOWED_ORIGIN},
        )
        different_fabricated = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_another_nonexistent_xyz789"},
            headers={"Origin": ALLOWED_ORIGIN},
        )
    assert _normalized(fabricated) == _normalized(different_fabricated)


async def test_repeated_origin_header_fails_identically_to_missing_origin(_seeded):
    app = create_app()
    async with await _client(app) as client:
        repeated = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_live_key"},
            headers=[("origin", ALLOWED_ORIGIN), ("origin", ALLOWED_ORIGIN)],
        )
        missing = await client.post("/api/v1/session", json={"site_key": "pk_live_live_key"})
    assert _normalized(repeated) == _normalized(missing)


async def test_literal_null_origin_fails_identically_to_a_disallowed_origin(_seeded):
    app = create_app()
    async with await _client(app) as client:
        null_origin = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_live_key"},
            headers={"Origin": "null"},
        )
        disallowed = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_live_key"},
            headers={"Origin": DISALLOWED_ORIGIN},
        )
    assert _normalized(null_origin) == _normalized(disallowed)


async def test_first_visit_without_secret_creates_visitor_and_returns_new_secret(_seeded):
    app = create_app()
    async with await _client(app) as client:
        response = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_live_key"},
            headers={"Origin": ALLOWED_ORIGIN},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["test_mode"] is False
    assert isinstance(body["visitor_secret"], str)
    claims = decode_session_token(body["session_token"])
    assert claims.tenant_id == _seeded["ids"]["tenant_active"]
    assert claims.origin == ALLOWED_ORIGIN
    assert claims.vid != _seeded["ids"]["existing_visitor"]


async def test_return_visit_with_correct_secret_returns_no_new_secret(_seeded):
    ids = _seeded["ids"]
    app = create_app()
    async with await _client(app) as client:
        response = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_live_key", "visitor_secret": _seeded["existing_secret"]},
            headers={"Origin": ALLOWED_ORIGIN},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["visitor_secret"] is None
    claims = decode_session_token(body["session_token"])
    assert claims.vid == ids["existing_visitor"]


async def test_wrong_secret_is_treated_as_first_visit_not_a_failure(_seeded):
    app = create_app()
    async with await _client(app) as client:
        response = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_live_key", "visitor_secret": generate_visitor_secret()},
            headers={"Origin": ALLOWED_ORIGIN},
        )
    assert response.status_code == 200
    assert isinstance(response.json()["visitor_secret"], str)


@pytest.mark.parametrize("bad_secret", ["x", "!!!not-a-real-secret!!!", ""])
async def test_malformed_or_wrong_length_secret_is_treated_as_first_visit_not_a_500(
    _seeded, bad_secret
):
    app = create_app()
    async with await _client(app) as client:
        response = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_live_key", "visitor_secret": bad_secret},
            headers={"Origin": ALLOWED_ORIGIN},
        )
    assert response.status_code == 200
    assert isinstance(response.json()["visitor_secret"], str)


async def test_draft_key_succeeds_from_its_own_allowed_origin_with_test_mode(_seeded):
    app = create_app()
    async with await _client(app) as client:
        response = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_draft_key"},
            headers={"Origin": DRAFT_ALLOWED_ORIGIN},
        )
    assert response.status_code == 200
    assert response.json()["test_mode"] is True


async def test_draft_key_fails_identically_from_a_non_allowed_origin(_seeded):
    app = create_app()
    async with await _client(app) as client:
        from_disallowed_origin = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_draft_key"},
            headers={"Origin": DISALLOWED_ORIGIN},
        )
        reference_failure = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_does_not_exist"},
            headers={"Origin": ALLOWED_ORIGIN},
        )
    assert _normalized(from_disallowed_origin) == _normalized(reference_failure)


async def test_success_response_never_leaks_private_tenant_config_fields(_seeded):
    app = create_app()
    async with await _client(app) as client:
        response = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_live_key"},
            headers={"Origin": ALLOWED_ORIGIN},
        )
    assert response.status_code == 200
    assert "distinctive@example.com" not in response.text
    body = response.json()
    assert body["widget_config"] == {
        "title": "Acme Support",
        "greeting": "Hi there!",
        "colors": {"primary": "#112233"},
        "logo_url": "https://cdn.example/logo.png",
        "position": "right",
        "privacy_notice_url": "https://acme.example/privacy",
    }
    assert "escalation_email" not in body["widget_config"]
    assert "relevance_threshold" not in body["widget_config"]


async def test_success_response_echoes_exact_origin_and_carries_vary(_seeded):
    app = create_app()
    async with await _client(app) as client:
        response = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_live_key"},
            headers={"Origin": ALLOWED_ORIGIN},
        )
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert response.headers["vary"] == "Origin"
