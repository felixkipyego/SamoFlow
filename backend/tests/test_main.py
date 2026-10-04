# backend/tests/test_main.py
# Tests for Task 1.1.e's app factory (backend/app/main.py) and health
# route (backend/app/health.py). Task 1.4.j adds /ready's own tests here
# too, right next to /health's -- one small module, not two test files.
# Task 1.4.k adds the lifespan hook's own tests: driven via
# app.router.lifespan_context(app), the standard Starlette mechanism for
# manually triggering startup/shutdown outside a real ASGI server run
# (confirmed live: httpx's installed ASGITransport has no lifespan= option
# of its own, so this is the correct way, not an assumption).
import time

import pytest

from app import db, qdrant
from app.config import Settings, SettingsError, get_settings
from app.main import create_app
from app.plans import models as plans_models  # noqa: F401 (registers "plans" on Base.metadata)
from app.tenancy import models as tenancy_models  # noqa: F401 (registers tenancy tables)
from tests.conftest import fresh_import, http_client, require_test_database, set_valid_env

# 203.0.113.0/24 is TEST-NET-3 (RFC 5737): reserved for documentation, never
# routable. Used here to prove /health answers without contacting anything.
VALID_ENV = {
    "APP_ENV": "development",
    "DATABASE_URL": "postgresql+psycopg://user:pw@203.0.113.1:5432/widgetplatform",
    "QDRANT_URL": "http://203.0.113.1:6333",
    "QDRANT_API_KEY": "test-qdrant-key",  # noqa: S105 (test fixture value, not a real secret)
    "JWT_SIGNING_KEY": "test-jwt-signing-key-at-least-32-chars",  # noqa: S105
    "DB_CONNECTION_ENCRYPTION_KEY": "test-db-connection-key-at-least-32-chars",  # noqa: S105
    "ADMIN_API_KEY": "test-admin-key-at-least-32-characters-long",  # noqa: S105
    "API_HOST": "0.0.0.0",  # noqa: S104 (test fixture value, not a live bind)
    "API_PORT": "8000",
}


async def test_health_returns_200_with_exact_body(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    app = create_app()
    async with await http_client(app) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"status": "ok"}


async def test_head_health_returns_200_with_empty_body(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    app = create_app()
    async with await http_client(app) as client:
        response = await client.head("/health")
    assert response.status_code == 200
    assert response.content == b""


async def test_openapi_schema_lists_get_health_but_not_head(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    app = create_app()
    async with await http_client(app) as client:
        response = await client.get("/openapi.json")
    operations = response.json()["paths"]["/health"]
    assert "get" in operations
    assert "head" not in operations


@pytest.mark.parametrize("method", ["post", "put", "delete", "options"])
async def test_other_methods_on_health_return_405(monkeypatch, method):
    set_valid_env(monkeypatch, VALID_ENV)
    app = create_app()
    async with await http_client(app) as client:
        response = await getattr(client, method)("/health")
    assert response.status_code == 405


def test_create_app_with_no_environment_raises_settings_error():
    with pytest.raises(SettingsError):
        create_app()


def test_importing_app_main_succeeds_with_empty_environment():
    fresh_import("app.main")  # must not raise despite empty env


def test_app_state_settings_is_the_instance_passed_in(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    settings = Settings()
    app = create_app(settings=settings)
    assert app.state.settings is settings


def test_get_settings_is_used_when_none_passed(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    expected = get_settings()  # same cached instance create_app() will see
    app = create_app()
    assert app.state.settings is expected


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
async def test_docs_disabled_in_production(monkeypatch, path):
    set_valid_env(monkeypatch, VALID_ENV, APP_ENV="production")
    app = create_app()
    async with await http_client(app) as client:
        response = await client.get(path)
    assert response.status_code == 404


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
async def test_docs_enabled_in_development(monkeypatch, path):
    set_valid_env(monkeypatch, VALID_ENV, APP_ENV="development")
    app = create_app()
    async with await http_client(app) as client:
        response = await client.get(path)
    assert response.status_code == 200


async def test_unknown_path_returns_404_with_no_stack_trace(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    app = create_app()
    async with await http_client(app) as client:
        response = await client.get("/this-path-does-not-exist")
    assert response.status_code == 404
    assert "Traceback" not in response.text
    assert "traceback" not in response.text.lower()


# -- /ready (Task 1.4.j) ------------------------------------------------------


async def test_health_still_does_not_touch_the_database(monkeypatch):
    # Existing coverage above (test_health_returns_200_with_exact_body)
    # already proves this implicitly, since VALID_ENV's own DATABASE_URL is
    # unroutable -- stated explicitly here, right next to /ready's own
    # sibling test below, so the two routes are clearly contrasted.
    set_valid_env(monkeypatch, VALID_ENV)
    app = create_app()
    async with await http_client(app) as client:
        response = await client.get("/health")
    assert response.status_code == 200


async def test_ready_is_a_different_route_that_does_touch_the_database(monkeypatch):
    # The exact same unroutable DATABASE_URL /health's own test above uses
    # -- /health returns 200 regardless, but /ready must NOT, proving the
    # two are genuinely different routes, not aliases of each other.
    set_valid_env(monkeypatch, VALID_ENV)
    db.get_engine.cache_clear()
    db._session_factory.cache_clear()
    app = create_app()
    async with await http_client(app) as client:
        response = await client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not ready"}
    db.get_engine.cache_clear()
    db._session_factory.cache_clear()


async def test_ready_returns_200_when_postgres_is_reachable(reset_test_database):
    app = create_app()
    async with await http_client(app) as client:
        response = await client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


async def test_ready_returns_503_without_leaking_the_password_and_respects_its_timeout(
    monkeypatch,
):
    # Covers both (c) and (d) in one scenario, since they share the exact
    # same setup: a distinctive password in an otherwise-unroutable
    # DATABASE_URL (RFC 5737 TEST-NET-3, the same convention used
    # throughout this project) that, left untimed, would hang far longer
    # than /ready's own 2-second internal timeout -- confirmed live: a raw
    # socket connect() to 203.0.113.1 in this environment does not fail
    # fast, it hangs until ITS OWN timeout, so this is a genuine
    # black-holing address, not a fast-refusing one.
    distinctive_password = "distinctive-pw-1-4-j"  # noqa: S105 (test fixture value)
    set_valid_env(
        monkeypatch,
        VALID_ENV,
        DATABASE_URL=f"postgresql+psycopg://user:{distinctive_password}@203.0.113.1:5432/widgetplatform",
    )
    db.get_engine.cache_clear()
    db._session_factory.cache_clear()
    app = create_app()

    start = time.monotonic()
    async with await http_client(app) as client:
        response = await client.get("/ready")
    elapsed = time.monotonic() - start

    assert response.status_code == 503
    assert response.json() == {"status": "not ready"}
    assert elapsed < 5, f"/ready took {elapsed:.1f}s, expected well under 5s"

    body_text = response.text
    assert distinctive_password not in body_text
    assert "psycopg" not in body_text
    assert "203.0.113.1" not in body_text

    db.get_engine.cache_clear()
    db._session_factory.cache_clear()


# -- Lifespan hook (Task 1.4.k) -----------------------------------------------


def _clear_engine_and_qdrant_caches():
    db.get_engine.cache_clear()
    db._session_factory.cache_clear()
    qdrant.get_qdrant_client.cache_clear()


async def test_lifespan_never_creates_engine_or_qdrant_client_when_unused(monkeypatch):
    # (a) Nothing touches Postgres or Qdrant during this app's short life --
    # proves the lazy contract survives the lifespan hook itself: shutdown
    # must not be what FIRST creates either singleton just to tear it down.
    # cache_info().currsize is pure introspection on the lru_cache wrapper
    # (this project's own established "created yet?" mechanism, first used
    # in app/qdrant.py's own test fixture, Task 1.3.b) -- it never calls the
    # wrapped function itself.
    set_valid_env(monkeypatch, VALID_ENV)
    _clear_engine_and_qdrant_caches()

    app = create_app()
    async with app.router.lifespan_context(app):
        pass

    assert db.get_engine.cache_info().currsize == 0
    assert qdrant.get_qdrant_client.cache_info().currsize == 0


async def test_lifespan_disposes_engine_and_closes_qdrant_client_that_were_actually_used(
    monkeypatch,
):
    # (b) Real usage, then a real shutdown, checking ACTUAL state -- not
    # merely that dispose()/close() didn't raise. Postgres: a real request
    # to /ready against the real test database. Qdrant: no HTTP-layer route
    # touches it yet (Step 3.1's retrieve() is the first one), so
    # get_qdrant_client() is called directly here to simulate "something
    # used it during this process's life" -- the task's own explicitly
    # allowed substitute; a fake, unreachable URL is fine, since
    # construction alone (never a real call) is all that is needed to prove
    # closure afterward.
    database_url = require_test_database()
    set_valid_env(
        monkeypatch, VALID_ENV, DATABASE_URL=database_url, QDRANT_URL="http://127.0.0.1:1"
    )
    _clear_engine_and_qdrant_caches()

    app = create_app()
    async with app.router.lifespan_context(app):
        async with await http_client(app) as client:
            response = await client.get("/ready")
        assert response.status_code == 200

        engine = db.get_engine()
        pool_before_shutdown = engine.pool
        qdrant_client = qdrant.get_qdrant_client()
        assert qdrant_client._client.closed is False

    # Shutdown has now run (the `async with` block above exited).
    # engine.dispose() (confirmed live against the real database) replaces
    # engine.pool with a brand-new Pool object -- a genuine, direct proof of
    # disposal, not an inference from "no exception was raised".
    assert engine.pool is not pool_before_shutdown
    assert qdrant_client._client.closed is True

    _clear_engine_and_qdrant_caches()


async def test_lifespan_second_cycle_gets_fresh_engine_and_qdrant_client(monkeypatch):
    # C1 (duplication check after 1.4.f/g/h/k): two full startup/shutdown
    # cycles in the same process. Without clearing both caches on successful
    # disposal, the second cycle would hand back the first cycle's disposed
    # engine (harmless, SQLAlchemy allows reuse) and its permanently-closed
    # Qdrant client (broken -- confirmed live, any call after close() raises
    # "Cannot send a request, as the client has been closed"). Both
    # resources are touched in each cycle -- Postgres via a real /ready
    # request, Qdrant via a direct get_qdrant_client() call, the same
    # substitute test (b) above already uses -- and the two cycles' objects
    # are asserted to be genuinely different, not merely both non-raising.
    database_url = require_test_database()
    set_valid_env(
        monkeypatch, VALID_ENV, DATABASE_URL=database_url, QDRANT_URL="http://127.0.0.1:1"
    )
    _clear_engine_and_qdrant_caches()

    app = create_app()

    async with app.router.lifespan_context(app):
        async with await http_client(app) as client:
            response = await client.get("/ready")
        assert response.status_code == 200
        first_engine = db.get_engine()
        first_qdrant_client = qdrant.get_qdrant_client()

    assert first_qdrant_client._client.closed is True

    async with app.router.lifespan_context(app):
        async with await http_client(app) as client:
            response = await client.get("/ready")
        assert response.status_code == 200
        second_engine = db.get_engine()
        second_qdrant_client = qdrant.get_qdrant_client()
        assert second_qdrant_client._client.closed is False

    assert second_engine is not first_engine
    assert second_qdrant_client is not first_qdrant_client
    assert second_qdrant_client._client.closed is True

    _clear_engine_and_qdrant_caches()


async def test_lifespan_engine_disposal_failure_does_not_prevent_qdrant_closure(monkeypatch):
    # (c) The engine's own dispose() is made to raise -- the Qdrant client
    # must still be closed, and shutdown itself must still complete without
    # raising (if it did, this test would error right here, at the
    # `async with` block's own __aexit__).
    set_valid_env(monkeypatch, VALID_ENV, QDRANT_URL="http://127.0.0.1:1")
    _clear_engine_and_qdrant_caches()

    app = create_app()
    async with app.router.lifespan_context(app):
        engine = db.get_engine()
        qdrant_client = qdrant.get_qdrant_client()

        async def _raise_dispose(self):
            raise RuntimeError("simulated engine disposal failure")

        # AsyncEngine has no per-instance __dict__ (a plain instance-attribute
        # monkeypatch raises "attribute is read-only") -- patched on the
        # class instead; monkeypatch restores it automatically, and only one
        # engine instance exists in this test regardless.
        monkeypatch.setattr(type(engine), "dispose", _raise_dispose)

    assert qdrant_client._client.closed is True

    _clear_engine_and_qdrant_caches()


async def test_lifespan_qdrant_closure_failure_does_not_prevent_engine_disposal(monkeypatch):
    # (c), the reverse: the Qdrant client's own close() is made to raise --
    # the engine must still be disposed, and shutdown itself must still
    # complete without raising.
    set_valid_env(monkeypatch, VALID_ENV, QDRANT_URL="http://127.0.0.1:1")
    _clear_engine_and_qdrant_caches()

    app = create_app()
    async with app.router.lifespan_context(app):
        engine = db.get_engine()
        pool_before_shutdown = engine.pool
        qdrant_client = qdrant.get_qdrant_client()

        async def _raise_close():
            raise RuntimeError("simulated Qdrant client closure failure")

        monkeypatch.setattr(qdrant_client, "close", _raise_close)

    assert engine.pool is not pool_before_shutdown

    _clear_engine_and_qdrant_caches()
