# backend/tests/test_widget_appearance.py
# [SECURITY] Step 7-follow-up, Part C: live tests for GET /api/v1/
# widget-appearance (app/auth/routes.py) -- reuses test_session.py's
# own `_seeded` fixture directly (import, not a third near-identical
# copy): it already seeds exactly the site-key/tenant combinations this
# endpoint needs to prove against (live/draft/suspended site keys, an
# active tenant with a real primary color + position, a suspended
# tenant).
#
# ruff: noqa: F811 (every test function below takes `_seeded` as its own
# parameter -- standard pytest fixture-import usage, not a real
# redefinition; ruff's static check cannot tell the two apart)
import uuid

import pytest

from app.config import get_settings
from app.main import create_app
from app.tenancy.models import SiteKey, Tenant
from tests.conftest import VALID_ENV, db_session, http_client, set_valid_env
from tests.test_session import _seeded  # noqa: F401 (pytest fixture import)

pytestmark = pytest.mark.usefixtures("close_any_checkpointer_pool_left_open")


def _create_app(monkeypatch, **overrides):
    set_valid_env(monkeypatch, VALID_ENV, **overrides)
    get_settings.cache_clear()
    return create_app()


async def test_a_live_site_key_returns_the_real_primary_color_and_position(monkeypatch, _seeded):
    app = _create_app(monkeypatch)
    async with await http_client(app) as client:
        response = await client.get(
            "/api/v1/widget-appearance", params={"site_key": "pk_live_live_key"}
        )
    assert response.status_code == 200
    body = response.json()
    # The seeded tenant's own real config: colors.primary="#112233",
    # position="right" (test_session.py's own `_seeded` fixture).
    assert body["primary"] == "#112233"
    assert body["position"] == "right"
    # pick_readable_text_color("#112233") -- a near-black navy -- must be
    # white, the same real WCAG math test_contrast.py already proves.
    assert body["primary_text"] == "#ffffff"
    # [SECURITY] Part C rule 3: the response contains NO fields beyond
    # the allow-list -- not title/greeting/logo_url/verified_domains/
    # anything else `_public_widget_config()` would include.
    assert set(body.keys()) == {"primary", "primary_text", "position"}


async def test_a_draft_site_key_also_succeeds_matching_sessions_own_rule(monkeypatch, _seeded):
    # docs/SPEC.md §4.2 / Step 1.4.g decision: draft and live both work
    # for an already-issued session; the appearance endpoint mirrors
    # that same "only suspended fails" rule.
    app = _create_app(monkeypatch)
    async with await http_client(app) as client:
        response = await client.get(
            "/api/v1/widget-appearance", params={"site_key": "pk_live_draft_key"}
        )
    assert response.status_code == 200


@pytest.mark.parametrize(
    "site_key",
    [
        "pk_live_this_site_key_does_not_exist",  # unknown
        "pk_live_suspended_key",  # suspended site key, active tenant
        "pk_live_suspended_tenant_key",  # live site key, suspended tenant
    ],
)
async def test_unknown_suspended_or_inactive_tenant_all_get_the_identical_generic_response(
    monkeypatch, _seeded, site_key
):
    # [SECURITY] Part C rule 3: "unknown/revoked site key reveals
    # nothing beyond a generic response" -- proven here as a real
    # no-oracle property, not just for the unknown case alone: all
    # three structurally-different reasons must be byte-for-byte
    # indistinguishable, the identical discipline app/auth/routes.py's
    # own `_failure_response()` already established for /session.
    app = _create_app(monkeypatch)
    async with await http_client(app) as client:
        response = await client.get("/api/v1/widget-appearance", params={"site_key": site_key})
    assert response.status_code == 404
    assert response.json() == {"detail": "not found"}


async def test_missing_colors_config_falls_back_to_the_loader_css_default(
    monkeypatch, reset_test_database
):
    # A real, named tenant with NO colors configured at all (a tenant
    # that never touched the widget-look settings screen) -- must fall
    # back to the exact literal default widget/loader/src/widget.css's
    # own CSS already hardcodes, never a blank/null/error.
    tenant_id = uuid.uuid4()
    async with db_session() as session:
        session.add(Tenant(id=tenant_id, name="No Colors Yet", status="active", config={}))
        await session.flush()
        session.add(
            SiteKey(
                id=uuid.uuid4(),
                key="pk_live_no_colors",
                tenant_id=tenant_id,
                allowed_origins=["https://widget.example"],
                environment="production",
                status="live",
            )
        )
        await session.commit()

    app = _create_app(monkeypatch)
    async with await http_client(app) as client:
        response = await client.get(
            "/api/v1/widget-appearance", params={"site_key": "pk_live_no_colors"}
        )
    assert response.status_code == 200
    body = response.json()
    assert body["primary"] == "#4f46e5"
    assert body["position"] == "right"


@pytest.mark.parametrize(
    "malformed_primary",
    [
        "rgb(evil)",
        "url(https://evil.example/x.png)",
        "javascript:alert(1)",
        "var(--evil)",
        "#12345",  # not a valid hex length
        "",
    ],
)
async def test_malformed_primary_color_in_config_is_rejected_falls_back_to_default(
    monkeypatch, malformed_primary, reset_test_database
):
    # [SECURITY] Part C rule 3: a malformed color value sitting in
    # `tenants.config` (however it got there -- a hand-edited JSONB row,
    # a future bug in the dashboard's own write path) must never be
    # echoed back verbatim; `is_valid_css_color()` is the SAME validator
    # the dashboard's own WidgetColors schema already uses, reused here,
    # never a second copy.
    tenant_id = uuid.uuid4()
    async with db_session() as session:
        session.add(
            Tenant(
                id=tenant_id,
                name="Malformed Color Tenant",
                status="active",
                config={"colors": {"primary": malformed_primary}},
            )
        )
        await session.flush()
        session.add(
            SiteKey(
                id=uuid.uuid4(),
                key="pk_live_malformed_color",
                tenant_id=tenant_id,
                allowed_origins=["https://widget.example"],
                environment="production",
                status="live",
            )
        )
        await session.commit()

    app = _create_app(monkeypatch)
    async with await http_client(app) as client:
        response = await client.get(
            "/api/v1/widget-appearance", params={"site_key": "pk_live_malformed_color"}
        )
    assert response.status_code == 200
    assert response.json()["primary"] == "#4f46e5"


async def test_response_carries_cache_control_etag_and_wildcard_acao_never_credentials(
    monkeypatch, _seeded
):
    app = _create_app(monkeypatch)
    async with await http_client(app) as client:
        response = await client.get(
            "/api/v1/widget-appearance", params={"site_key": "pk_live_live_key"}
        )
    assert response.status_code == 200
    assert "max-age=120" in response.headers["cache-control"]
    assert response.headers["etag"]
    # [SECURITY] This ONE endpoint is the deliberate exception to "never
    # a wildcard" -- genuinely public, non-credentialed, cacheable data
    # (see app/auth/routes.py's own header for the full reasoning).
    assert response.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in {k.lower() for k in response.headers}


async def test_a_repeat_request_with_if_none_match_gets_a_real_304(monkeypatch, _seeded):
    app = _create_app(monkeypatch)
    async with await http_client(app) as client:
        first = await client.get(
            "/api/v1/widget-appearance", params={"site_key": "pk_live_live_key"}
        )
        etag = first.headers["etag"]
        second = await client.get(
            "/api/v1/widget-appearance",
            params={"site_key": "pk_live_live_key"},
            headers={"If-None-Match": etag},
        )
    assert second.status_code == 304
    assert second.content == b""


async def test_per_ip_rate_limit_trips_with_retry_after(monkeypatch, _seeded):
    set_valid_env(monkeypatch, VALID_ENV, WIDGET_APPEARANCE_RATE_LIMIT_PER_IP="2")
    get_settings.cache_clear()
    app = create_app()
    async with await http_client(app) as client:
        for _ in range(2):
            response = await client.get(
                "/api/v1/widget-appearance", params={"site_key": "pk_live_live_key"}
            )
            assert response.status_code != 429
        limited = await client.get(
            "/api/v1/widget-appearance", params={"site_key": "pk_live_live_key"}
        )
    assert limited.status_code == 429
    assert "retry-after" in {k.lower() for k in limited.headers}


async def test_no_preflight_handler_needed_a_plain_get_has_no_custom_headers(monkeypatch, _seeded):
    # Documents, rather than merely asserts, WHY no OPTIONS route exists
    # for this endpoint: a plain cross-origin GET with no custom request
    # headers is a CORS "simple request" by definition -- no browser
    # ever sends a preflight for it, so there is nothing to answer.
    app = _create_app(monkeypatch)
    async with await http_client(app) as client:
        response = await client.options("/api/v1/widget-appearance")
    assert response.status_code in (404, 405)
