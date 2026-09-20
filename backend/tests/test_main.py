# backend/tests/test_main.py
# Tests for Task 1.1.e's app factory (backend/app/main.py) and health
# route (backend/app/health.py).
import importlib
import sys

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings, SettingsError, get_settings
from app.main import create_app

REQUIRED_VARS = ["APP_ENV", "DATABASE_URL", "QDRANT_URL", "API_HOST", "API_PORT"]

# 203.0.113.0/24 is TEST-NET-3 (RFC 5737): reserved for documentation, never
# routable. Used here to prove /health answers without contacting anything.
VALID_ENV = {
    "APP_ENV": "development",
    "DATABASE_URL": "postgresql+psycopg://user:pw@203.0.113.1:5432/widgetplatform",
    "QDRANT_URL": "http://203.0.113.1:6333",
    "API_HOST": "0.0.0.0",  # noqa: S104 (test fixture value, not a live bind)
    "API_PORT": "8000",
}


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    for name in REQUIRED_VARS:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _set_valid_env(monkeypatch, **overrides):
    for key, value in {**VALID_ENV, **overrides}.items():
        monkeypatch.setenv(key, value)


async def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_health_returns_200_with_exact_body(monkeypatch):
    _set_valid_env(monkeypatch)
    app = create_app()
    async with await _client(app) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"status": "ok"}


async def test_head_health_returns_200_with_empty_body(monkeypatch):
    _set_valid_env(monkeypatch)
    app = create_app()
    async with await _client(app) as client:
        response = await client.head("/health")
    assert response.status_code == 200
    assert response.content == b""


async def test_openapi_schema_lists_get_health_but_not_head(monkeypatch):
    _set_valid_env(monkeypatch)
    app = create_app()
    async with await _client(app) as client:
        response = await client.get("/openapi.json")
    operations = response.json()["paths"]["/health"]
    assert "get" in operations
    assert "head" not in operations


@pytest.mark.parametrize("method", ["post", "put", "delete", "options"])
async def test_other_methods_on_health_return_405(monkeypatch, method):
    _set_valid_env(monkeypatch)
    app = create_app()
    async with await _client(app) as client:
        response = await getattr(client, method)("/health")
    assert response.status_code == 405


async def test_health_works_with_unreachable_hosts(monkeypatch):
    # DATABASE_URL and QDRANT_URL both point at TEST-NET-3 (unreachable).
    # /health must still answer immediately: it never opens a connection.
    _set_valid_env(monkeypatch)
    app = create_app()
    async with await _client(app) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_app_with_no_environment_raises_settings_error():
    with pytest.raises(SettingsError):
        create_app()


def test_importing_app_main_succeeds_with_empty_environment():
    # Mirrors test_config.py's equivalent: a fresh import into a new module
    # object, not importlib.reload, so this file's own `from app.main import
    # create_app` binding is left untouched.
    sys.modules.pop("app.main", None)
    importlib.import_module("app.main")  # must not raise despite empty env


def test_app_state_settings_is_the_instance_passed_in(monkeypatch):
    _set_valid_env(monkeypatch)
    settings = Settings()
    app = create_app(settings=settings)
    assert app.state.settings is settings


def test_get_settings_is_used_when_none_passed(monkeypatch):
    _set_valid_env(monkeypatch)
    expected = get_settings()  # same cached instance create_app() will see
    app = create_app()
    assert app.state.settings is expected


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
async def test_docs_disabled_in_production(monkeypatch, path):
    _set_valid_env(monkeypatch, APP_ENV="production")
    app = create_app()
    async with await _client(app) as client:
        response = await client.get(path)
    assert response.status_code == 404


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
async def test_docs_enabled_in_development(monkeypatch, path):
    _set_valid_env(monkeypatch, APP_ENV="development")
    app = create_app()
    async with await _client(app) as client:
        response = await client.get(path)
    assert response.status_code == 200


async def test_unknown_path_returns_404_with_no_stack_trace(monkeypatch):
    _set_valid_env(monkeypatch)
    app = create_app()
    async with await _client(app) as client:
        response = await client.get("/this-path-does-not-exist")
    assert response.status_code == 404
    assert "Traceback" not in response.text
    assert "traceback" not in response.text.lower()
