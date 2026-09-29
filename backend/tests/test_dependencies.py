# backend/tests/test_dependencies.py
# Task 1.4.h: tests for the verified-identity dependency
# (backend/app/auth/dependencies.py). Not yet wired into any real endpoint
# (that starts at 1.4.i+), so these tests exercise it through a small
# throwaway FastAPI app defined here -- the same httpx.AsyncClient +
# ASGITransport pattern test_main.py/test_session.py already established,
# giving full HTTP-level control over headers and status codes rather than
# hand-building a Starlette Request object.
#
# All tests need the real database (require_test_database()): even a
# structurally-invalid request (bad Authorization header) still goes
# through Depends(get_db_session), and the status-cache tests need real
# tenant/site-key rows to read on a cache miss.
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
import sqlalchemy as sa
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from app import db
from app.auth.dependencies import StatusCache, VerifiedIdentity, get_current_visitor
from app.auth.tokens import encode_session_token
from app.db import Base
from app.plans import models as plans_models  # noqa: F401 (registers "plans" on Base.metadata)
from app.tenancy.models import SiteKey, Tenant, Visitor
from tests.conftest import VALID_ENV, db_session, require_test_database, set_valid_env

ORIGIN = "https://widget.example"
OTHER_ORIGIN = "https://not-the-origin-it-was-issued-for.example"


class _FakeClock:
    # Injectable time source (Task 1.4.h design point 3): starts at an
    # arbitrary fixed point and only ever moves when a test tells it to --
    # never real time, so TTL expiry is deterministic, not timing-flaky.
    def __init__(self, start: float = 1_000.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def _build_test_app() -> FastAPI:
    app = FastAPI()

    @app.get("/whoami")
    async def whoami(
        identity: VerifiedIdentity = Depends(get_current_visitor),  # noqa: B008
    ):
        return {"tenant_id": str(identity.tenant_id), "vid": str(identity.vid), "org": identity.org}

    return app


async def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def _seeded(monkeypatch):
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
        "site_key_live": uuid.uuid4(),
        "site_key_draft": uuid.uuid4(),
        "site_key_suspended": uuid.uuid4(),
        "visitor_live": uuid.uuid4(),
        "visitor_draft": uuid.uuid4(),
        "visitor_suspended_key": uuid.uuid4(),
    }

    async with db_session() as session:
        session.add(Tenant(id=ids["tenant_active"], name="Active Tenant", status="active"))
        await session.flush()
        session.add_all(
            [
                SiteKey(
                    id=ids["site_key_live"],
                    key="pk_live_live_key",
                    tenant_id=ids["tenant_active"],
                    allowed_origins=[ORIGIN],
                    environment="production",
                    status="live",
                ),
                SiteKey(
                    id=ids["site_key_draft"],
                    key="pk_live_draft_key",
                    tenant_id=ids["tenant_active"],
                    allowed_origins=[ORIGIN],
                    environment="production",
                    status="draft",
                ),
                SiteKey(
                    id=ids["site_key_suspended"],
                    key="pk_live_suspended_key",
                    tenant_id=ids["tenant_active"],
                    allowed_origins=[ORIGIN],
                    environment="production",
                    status="suspended",
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                Visitor(
                    vid=ids["visitor_live"],
                    tenant_id=ids["tenant_active"],
                    site_key_id=ids["site_key_live"],
                    secret_hash="unused-hash",  # noqa: S106 (never read by these tests)
                ),
                Visitor(
                    vid=ids["visitor_draft"],
                    tenant_id=ids["tenant_active"],
                    site_key_id=ids["site_key_draft"],
                    secret_hash="unused-hash",  # noqa: S106
                ),
                Visitor(
                    vid=ids["visitor_suspended_key"],
                    tenant_id=ids["tenant_active"],
                    site_key_id=ids["site_key_suspended"],
                    secret_hash="unused-hash",  # noqa: S106
                ),
            ]
        )
        await session.commit()

    yield ids

    await db.get_engine().dispose()
    db.get_engine.cache_clear()
    db._session_factory.cache_clear()


def _token_for(tenant_id: uuid.UUID, vid: uuid.UUID, origin: str = ORIGIN) -> str:
    return encode_session_token(tenant_id, vid, origin)


async def _get(app, *, token: str | None = None, origins: list[str] | None = None):
    headers: list[tuple[str, str]] = []
    if token is not None:
        headers.append(("authorization", f"Bearer {token}"))
    if origins is not None:
        headers.extend(("origin", o) for o in origins)
    async with await _client(app) as client:
        return await client.get("/whoami", headers=headers)


# -- Authorization header parsing (4a/4b) ------------------------------------


async def test_missing_authorization_header_is_401(_seeded):
    app = _build_test_app()
    response = await _get(app, origins=[ORIGIN])
    assert response.status_code == 401


@pytest.mark.parametrize(
    "bad_header",
    [
        "Basic dXNlcjpwYXNz",  # wrong scheme
        "bearer sometoken",  # wrong case
        "Bearer",  # no token at all
        "Bearer ",  # empty token
        "Bearer token extra",  # extra text after the token
        "BearerNoSpace",  # no separating space
    ],
)
async def test_malformed_authorization_header_is_401(_seeded, bad_header):
    app = _build_test_app()
    async with await _client(app) as client:
        response = await client.get(
            "/whoami", headers=[("authorization", bad_header), ("origin", ORIGIN)]
        )
    assert response.status_code == 401


# -- JWT failure wiring (4c) --------------------------------------------------


async def test_expired_token_is_401(_seeded):
    ids = _seeded
    settings_key = VALID_ENV["JWT_SIGNING_KEY"]
    # Hand-built, expired 10 minutes ago -- proves the WIRING (1.4.b's
    # InvalidSessionToken reaches this dependency as a 401), not
    # re-deriving 1.4.b's own cryptographic test suite.
    payload = {
        "iss": "widgetplatform",
        "aud": "widget-api",
        "sub": str(ids["visitor_live"]),
        "tid": str(ids["tenant_active"]),
        "org": ORIGIN,
        "exp": datetime.now(UTC) - timedelta(minutes=10),
    }
    token = jwt.encode(payload, settings_key, algorithm="HS256")
    app = _build_test_app()
    response = await _get(app, token=token, origins=[ORIGIN])
    assert response.status_code == 401


async def test_tampered_signature_token_is_401(_seeded):
    ids = _seeded
    token = _token_for(ids["tenant_active"], ids["visitor_live"])
    header, payload, signature = token.split(".")
    flipped = ("A" if signature[0] != "A" else "B") + signature[1:]
    tampered = f"{header}.{payload}.{flipped}"
    app = _build_test_app()
    response = await _get(app, token=tampered, origins=[ORIGIN])
    assert response.status_code == 401


async def test_token_signed_with_a_different_key_is_401(_seeded):
    ids = _seeded
    payload = {
        "iss": "widgetplatform",
        "aud": "widget-api",
        "sub": str(ids["visitor_live"]),
        "tid": str(ids["tenant_active"]),
        "org": ORIGIN,
        "exp": datetime.now(UTC) + timedelta(minutes=15),
    }
    token = jwt.encode(payload, "a-completely-unrelated-signing-key-32ch", algorithm="HS256")
    app = _build_test_app()
    response = await _get(app, token=token, origins=[ORIGIN])
    assert response.status_code == 401


# -- Origin vs org claim (4d) -------------------------------------------------


async def test_valid_token_replayed_from_a_different_origin_is_401(_seeded):
    ids = _seeded
    # Otherwise perfectly valid: real signature, unexpired, correct aud/iss,
    # issued for ORIGIN -- but the request now claims a DIFFERENT Origin.
    token = _token_for(ids["tenant_active"], ids["visitor_live"], origin=ORIGIN)
    app = _build_test_app()
    response = await _get(app, token=token, origins=[OTHER_ORIGIN])
    assert response.status_code == 401


async def test_repeated_origin_header_with_a_valid_token_is_401(_seeded):
    ids = _seeded
    token = _token_for(ids["tenant_active"], ids["visitor_live"], origin=ORIGIN)
    app = _build_test_app()
    response = await _get(app, token=token, origins=[ORIGIN, ORIGIN])
    assert response.status_code == 401


# -- Success path (4e) --------------------------------------------------------


async def test_valid_token_from_its_own_origin_with_an_active_tenant_and_live_key_succeeds(
    _seeded,
):
    ids = _seeded
    token = _token_for(ids["tenant_active"], ids["visitor_live"], origin=ORIGIN)
    app = _build_test_app()
    response = await _get(app, token=token, origins=[ORIGIN])
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "tenant_id": str(ids["tenant_active"]),
        "vid": str(ids["visitor_live"]),
        "org": ORIGIN,
    }


async def test_valid_token_for_a_draft_site_key_still_succeeds(_seeded):
    # Matches 1.4.g's own rule: draft and live both continue to work for an
    # already-issued session, only suspended fails.
    ids = _seeded
    token = _token_for(ids["tenant_active"], ids["visitor_draft"], origin=ORIGIN)
    app = _build_test_app()
    response = await _get(app, token=token, origins=[ORIGIN])
    assert response.status_code == 200


async def test_valid_token_for_a_suspended_site_key_is_401(_seeded):
    ids = _seeded
    token = _token_for(ids["tenant_active"], ids["visitor_suspended_key"], origin=ORIGIN)
    app = _build_test_app()
    response = await _get(app, token=token, origins=[ORIGIN])
    assert response.status_code == 401


# -- Cache: suspended-tenant rejection is served from the CACHE, and the
#    TTL boundary picks up a status change (4f/4g) --------------------------


async def test_suspended_tenant_status_is_served_from_cache_until_ttl_elapses(_seeded):
    ids = _seeded
    clock = _FakeClock()
    app = _build_test_app()
    app.state.status_cache = StatusCache(clock=clock)
    token = _token_for(ids["tenant_active"], ids["visitor_live"], origin=ORIGIN)

    # Seed the tenant as SUSPENDED and make a first request: cache miss ->
    # fresh DB read -> "suspended" -> cached -> rejected.
    async with db_session() as session:
        await session.execute(
            sa.update(Tenant).where(Tenant.id == ids["tenant_active"]).values(status="suspended")
        )
        await session.commit()

    first = await _get(app, token=token, origins=[ORIGIN])
    assert first.status_code == 401

    # Flip the DB back to ACTIVE directly (bypassing the cache entirely),
    # WITHOUT advancing the clock. If the dependency were doing a fresh
    # read every time, this next request would now succeed -- it must
    # still fail, proving the STALE cached "suspended" value (not a fresh
    # read) is what determines the outcome (4f).
    async with db_session() as session:
        await session.execute(
            sa.update(Tenant).where(Tenant.id == ids["tenant_active"]).values(status="active")
        )
        await session.commit()

    still_cached = await _get(app, token=token, origins=[ORIGIN])
    assert still_cached.status_code == 401

    # Advance the clock past the 60-second TTL: the cache entry is now
    # expired, forcing a fresh read that sees the real (active) status --
    # proving the TTL BOUNDARY ITSELF is what picks up the change (4g).
    clock.advance(60.0)
    after_ttl = await _get(app, token=token, origins=[ORIGIN])
    assert after_ttl.status_code == 200


# -- Cross-tenant cache isolation (4h) ----------------------------------------


async def test_cross_tenant_cache_entries_never_collide(_seeded, monkeypatch):
    ids = _seeded
    database_url = require_test_database()

    # A second tenant, suspended from the start, with its own site key and
    # visitor -- entirely separate ids from the first tenant's.
    tenant_b = uuid.uuid4()
    site_key_b = uuid.uuid4()
    visitor_b = uuid.uuid4()
    async with db_session() as session:
        session.add(Tenant(id=tenant_b, name="Tenant B", status="suspended"))
        await session.flush()
        session.add(
            SiteKey(
                id=site_key_b,
                key="pk_live_tenant_b_key",
                tenant_id=tenant_b,
                allowed_origins=[ORIGIN],
                environment="production",
                status="live",
            )
        )
        await session.flush()
        session.add(
            Visitor(
                vid=visitor_b,
                tenant_id=tenant_b,
                site_key_id=site_key_b,
                secret_hash="unused-hash",  # noqa: S106
            )
        )
        await session.commit()

    clock = _FakeClock()
    app = _build_test_app()
    app.state.status_cache = StatusCache(clock=clock)

    token_a = _token_for(ids["tenant_active"], ids["visitor_live"], origin=ORIGIN)
    token_b = _token_for(tenant_b, visitor_b, origin=ORIGIN)

    response_a = await _get(app, token=token_a, origins=[ORIGIN])
    response_b = await _get(app, token=token_b, origins=[ORIGIN])

    assert response_a.status_code == 200
    assert response_b.status_code == 401

    # Direct proof, not just the behavioral outcome above: the cache's own
    # entries for the two (tenant_id, site_key_id) keys are distinct and
    # correct -- tenant A's key was never populated with B's status or
    # vice versa.
    cache = app.state.status_cache
    entry_a = cache.get(ids["tenant_active"], ids["site_key_live"])
    entry_b = cache.get(tenant_b, site_key_b)
    assert entry_a.tenant_status == "active"
    assert entry_b.tenant_status == "suspended"
    assert database_url  # keeps require_test_database()'s guard exercised


# -- Deleted tenant/visitor (4i) ----------------------------------------------


async def test_token_for_a_vid_that_was_never_seeded_is_401(_seeded):
    ids = _seeded
    token = _token_for(ids["tenant_active"], uuid.uuid4(), origin=ORIGIN)
    app = _build_test_app()
    response = await _get(app, token=token, origins=[ORIGIN])
    assert response.status_code == 401


async def test_token_for_a_tenant_deleted_after_issuance_is_401_not_a_500(_seeded):
    ids = _seeded
    token = _token_for(ids["tenant_active"], ids["visitor_live"], origin=ORIGIN)

    # ON DELETE CASCADE (1.2.b's decision): deleting the tenant also
    # deletes its visitors, exactly like a real hard-delete would.
    async with db_session() as session:
        await session.execute(sa.delete(Tenant).where(Tenant.id == ids["tenant_active"]))
        await session.commit()

    app = _build_test_app()
    response = await _get(app, token=token, origins=[ORIGIN])
    assert response.status_code == 401
