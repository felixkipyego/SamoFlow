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
from httpx import ASGITransport, AsyncClient

from app.auth.secrets import generate_visitor_secret, hash_visitor_secret
from app.auth.tokens import decode_session_token
from app.config import get_settings
from app.main import create_app
from app.plans import models as plans_models  # noqa: F401 (registers "plans" on Base.metadata)
from app.ratelimit import RateLimiter
from app.tenancy.models import SiteKey, Tenant, Visitor
from tests.conftest import VALID_ENV, db_session, http_client, set_valid_env

ALLOWED_ORIGIN = "https://widget.example"
DRAFT_ALLOWED_ORIGIN = "https://draft-widget.example"
DISALLOWED_ORIGIN = "https://not-allowed.example"

# Unroutable (RFC 5737 TEST-NET-3): proves a code path never actually
# attempts a database connection, the same way test_main.py's own /health
# tests prove it never contacts Postgres or Qdrant.
_UNROUTABLE_DATABASE_URL = "postgresql+psycopg://user:pw@203.0.113.1:5432/widgetplatform"


def _normalized(response):
    # Full header set minus Date (the only genuinely non-deterministic one)
    # plus status and raw body bytes -- used to prove two responses are
    # byte-identical, not just "both a 403".
    headers = sorted((k.lower(), v) for k, v in response.headers.items() if k.lower() != "date")
    return response.status_code, response.content, headers


@pytest.fixture
async def _seeded(reset_test_database):
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


# -- No-database tests -------------------------------------------------------


async def test_unknown_field_in_request_returns_422_before_any_database_access(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV, DATABASE_URL=_UNROUTABLE_DATABASE_URL)
    app = create_app()
    async with await http_client(app) as client:
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
    async with await http_client(app) as client:
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
    async with await http_client(app) as client:
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
    async with await http_client(app) as client:
        response = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_does_not_exist"},
            headers={"Origin": ALLOWED_ORIGIN},
        )
    assert response.headers.get("vary") == "Origin"
    assert "access-control-allow-origin" not in response.headers


async def test_fabricated_and_real_but_wrong_site_keys_fail_identically(_seeded):
    app = create_app()
    async with await http_client(app) as client:
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
    async with await http_client(app) as client:
        repeated = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_live_key"},
            headers=[("origin", ALLOWED_ORIGIN), ("origin", ALLOWED_ORIGIN)],
        )
        missing = await client.post("/api/v1/session", json={"site_key": "pk_live_live_key"})
    assert _normalized(repeated) == _normalized(missing)


async def test_literal_null_origin_fails_identically_to_a_disallowed_origin(_seeded):
    app = create_app()
    async with await http_client(app) as client:
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
    async with await http_client(app) as client:
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
    async with await http_client(app) as client:
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
    async with await http_client(app) as client:
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
    async with await http_client(app) as client:
        response = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_live_key", "visitor_secret": bad_secret},
            headers={"Origin": ALLOWED_ORIGIN},
        )
    assert response.status_code == 200
    assert isinstance(response.json()["visitor_secret"], str)


async def test_draft_key_succeeds_from_its_own_allowed_origin_with_test_mode(_seeded):
    app = create_app()
    async with await http_client(app) as client:
        response = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_draft_key"},
            headers={"Origin": DRAFT_ALLOWED_ORIGIN},
        )
    assert response.status_code == 200
    assert response.json()["test_mode"] is True


async def test_draft_key_fails_identically_from_a_non_allowed_origin(_seeded):
    app = create_app()
    async with await http_client(app) as client:
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
    async with await http_client(app) as client:
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
    async with await http_client(app) as client:
        response = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_live_key"},
            headers={"Origin": ALLOWED_ORIGIN},
        )
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert response.headers["vary"] == "Origin"


# -- Task 1.5.c: rate limiting -------------------------------------------
#
# _FakeClock below is a third near-identical copy of the same tiny
# injectable-clock helper already in test_ratelimit.py (1.5.a) and
# test_dependencies.py (1.4.h) -- worth centralizing (e.g. into
# conftest.py) at the now-overdue duplication check (PROJECT_SPEC.md §8),
# not fixed here since that is a separate, already-flagged task.


class _FakeClock:
    def __init__(self, start: float = 1_000.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def _client_from_ip(app, ip: str) -> AsyncClient:
    # http_client() (tests/conftest.py) hardcodes ASGITransport's default
    # client tuple ("127.0.0.1", 123) -- every request through it looks
    # like it comes from the same source IP. Proving the per-IP limiter is
    # scoped by request.client.host requires a SECOND, distinct fake source
    # IP -- ASGITransport's own `client` constructor argument, not
    # exercised by any existing test.
    return AsyncClient(
        transport=ASGITransport(app=app, client=(ip, 12345)), base_url="http://test"
    )


async def test_eleventh_request_from_one_site_key_within_the_window_is_429(_seeded, monkeypatch):
    # A low configured limit, not 11 real requests against the production
    # default of 10 -- simpler and clearer, same proof either way.
    monkeypatch.setenv("SESSION_RATE_LIMIT_PER_SITE_KEY", "3")
    # _seeded already forced a get_settings() read (via db_session()'s own
    # get_engine() call, during fixture setup, before this env override) --
    # clear the cache so create_app() below reads the override, not the
    # stale cached Settings instance from before it (established pattern:
    # test_config.py's test_get_settings_is_cached_and_resets_after_cache_clear).
    get_settings.cache_clear()
    app = create_app()
    async with await http_client(app) as client:
        for _ in range(3):
            response = await client.post(
                "/api/v1/session",
                json={"site_key": "pk_live_live_key"},
                headers={"Origin": ALLOWED_ORIGIN},
            )
            # Each of the first 3 succeeds or fails on its own merits
            # (never on rate-limiting grounds).
            assert response.status_code != 429
        blocked = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_live_key"},
            headers={"Origin": ALLOWED_ORIGIN},
        )
    assert blocked.status_code == 429
    body = blocked.json()
    assert body["detail"] == "too many requests"
    assert isinstance(body["retry_after"], int | float)
    assert 0 < body["retry_after"] <= 60
    assert "retry-after" in blocked.headers
    assert blocked.headers["retry-after"].isdigit()
    assert int(blocked.headers["retry-after"]) > 0


async def test_different_ip_or_site_key_pair_is_unaffected_by_another_pairs_limit(
    _seeded, monkeypatch
):
    monkeypatch.setenv("SESSION_RATE_LIMIT_PER_SITE_KEY", "1")
    get_settings.cache_clear()  # see the sibling test above for why
    app = create_app()
    async with await http_client(app) as client:
        first = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_live_key"},
            headers={"Origin": ALLOWED_ORIGIN},
        )
        assert first.status_code != 429

        blocked_same_pair = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_live_key"},
            headers={"Origin": ALLOWED_ORIGIN},
        )
        assert blocked_same_pair.status_code == 429

        # Same IP, a DIFFERENT site_key: unaffected.
        different_site_key = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_draft_key"},
            headers={"Origin": DRAFT_ALLOWED_ORIGIN},
        )
        assert different_site_key.status_code != 429

    # A DIFFERENT IP, the SAME site_key (already exhausted for the first
    # IP): also unaffected.
    async with _client_from_ip(app, "203.0.113.7") as other_ip_client:
        different_ip = await other_ip_client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_live_key"},
            headers={"Origin": ALLOWED_ORIGIN},
        )
    assert different_ip.status_code != 429


async def test_per_ip_limiter_trips_across_many_different_site_keys(_seeded, monkeypatch):
    # docs/SPEC.md §16: "many new visitors from one IP hit the IP cap" --
    # four DIFFERENT site keys, each hit exactly once (so no individual
    # site_key's own limit is anywhere near tripped), from the same IP.
    monkeypatch.setenv("SESSION_RATE_LIMIT_PER_IP", "3")
    get_settings.cache_clear()  # see test_eleventh_request_... above for why
    app = create_app()
    site_keys = [
        "pk_live_live_key",
        "pk_live_draft_key",
        "pk_live_suspended_key",
        "pk_live_suspended_tenant_key",
    ]
    async with await http_client(app) as client:
        results = [
            await client.post(
                "/api/v1/session", json={"site_key": key}, headers={"Origin": ALLOWED_ORIGIN}
            )
            for key in site_keys
        ]
    assert all(r.status_code != 429 for r in results[:3])
    assert results[3].status_code == 429


async def test_per_ip_limiter_checked_first_and_can_trip_before_any_site_key_state_exists(
    _seeded, monkeypatch
):
    monkeypatch.setenv("SESSION_RATE_LIMIT_PER_IP", "1")
    get_settings.cache_clear()  # see test_eleventh_request_... above for why
    app = create_app()
    async with await http_client(app) as client:
        first = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_live_key"},
            headers={"Origin": ALLOWED_ORIGIN},
        )
        assert first.status_code != 429

        # A site_key NEVER seen before by either limiter -- the (IP,
        # site_key) limiter has zero prior state for this exact key, and
        # would allow it if checked alone. It must still be blocked here,
        # proving the per-IP check runs first and short-circuits before
        # the site-key check (and before any DB lookup) ever happens.
        second = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_a_brand_new_never_used_site_key"},
            headers={"Origin": ALLOWED_ORIGIN},
        )
    assert second.status_code == 429


async def test_retry_after_is_a_real_countdown_not_the_static_window(_seeded):
    # Direct access to the SAME clock instance the app uses: pre-seed
    # app.state with our own RateLimiter/_FakeClock BEFORE any request,
    # exactly like StatusCache's own test convention (test_dependencies.py,
    # Task 1.4.h) -- the getattr-fallback shape both use means the lazy
    # construction path in app/auth/routes.py never triggers here.
    clock = _FakeClock()
    app = create_app()
    app.state.site_key_limiter = RateLimiter(limit=1, window_seconds=60, clock=clock)
    app.state.per_ip_limiter = RateLimiter(limit=30, window_seconds=60, clock=clock)

    async with await http_client(app) as client:
        first = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_live_key"},
            headers={"Origin": ALLOWED_ORIGIN},
        )
        assert first.status_code != 429

        clock.advance(20.0)  # 20s into the 60s window -- 40s should remain

        blocked = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_live_key"},
            headers={"Origin": ALLOWED_ORIGIN},
        )
    assert blocked.status_code == 429
    assert blocked.json()["retry_after"] == 40.0
    assert blocked.headers["retry-after"] == "40"


async def test_options_preflight_is_never_rate_limited(_seeded, monkeypatch):
    # Folds in Task 1.5.d: confirms the OPTIONS preflight handler (1.4.g)
    # is untouched by 1.5.c -- it has no rate-limit-checking code at all
    # (see app/auth/routes.py's session_preflight), so it cannot be blocked
    # regardless of how exhausted either limiter is.
    monkeypatch.setenv("SESSION_RATE_LIMIT_PER_IP", "1")
    get_settings.cache_clear()  # see test_eleventh_request_... above for why
    app = create_app()
    async with await http_client(app) as client:
        await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_live_key"},
            headers={"Origin": ALLOWED_ORIGIN},
        )
        exhausted = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_live_key"},
            headers={"Origin": ALLOWED_ORIGIN},
        )
        assert exhausted.status_code == 429

        preflight = await client.options("/api/v1/session", headers={"Origin": ALLOWED_ORIGIN})
    assert preflight.status_code == 200


async def test_a_successful_non_rate_limited_response_is_unaffected_by_1_5_c(_seeded):
    # Same assertions test_first_visit_without_secret_creates_visitor_and_
    # returns_new_secret (1.4.g) already established, plus an explicit
    # check that no Retry-After header leaks onto a successful response --
    # proving 1.5.c adds no observable change to the success path.
    app = create_app()
    async with await http_client(app) as client:
        response = await client.post(
            "/api/v1/session",
            json={"site_key": "pk_live_live_key"},
            headers={"Origin": ALLOWED_ORIGIN},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["test_mode"] is False
    assert isinstance(body["visitor_secret"], str)
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert response.headers["vary"] == "Origin"
    assert "retry-after" not in response.headers
