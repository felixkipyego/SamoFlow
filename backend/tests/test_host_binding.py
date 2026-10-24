# backend/tests/test_host_binding.py
# [SECURITY] L1 PART C follow-up (advisor review, 2026-10-17): app/host_
# binding.py's require_dashboard_host()/require_api_host() -- the SECOND,
# narrower defence-in-depth layer on top of app/main.py's TrustedHost
# Middleware (which only answers "is this Host one this deployment
# recognises at all"). Three layers of proof here, each catching a
# different failure class a single test style would miss:
#   1. Direct unit tests against the two functions themselves (fast,
#      deterministic, no app/DB needed).
#   2. Live, full-app round trips per route family, proving the real
#      wiring in app/main.py's create_app() -- not just the functions in
#      isolation.
#   3. A source-level guard that fails if a future router is added to
#      create_app() without being classified into exactly one family
#      (or the explicit health-router exemption) -- this project's own
#      established plain-text/regex guard style (test_single_api_
#      process_guard.py), chosen because this installed FastAPI's own
#      route objects are lazy wrappers (`_IncludedRouter`) with no public
#      way to introspect "which dependency does this concrete route
#      carry" without depending on private internals that could change
#      between FastAPI versions.
import re
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException, Request

from app.config import Settings, get_settings
from app.host_binding import require_api_host, require_dashboard_host
from app.main import create_app
from tests.conftest import VALID_ENV, http_client, set_valid_env

BACKEND_DIR = Path(__file__).resolve().parent.parent

# --- direct unit tests ---


def _make_request(host: str) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [(b"host", host.encode())],
        "server": ("test", 80),
        "scheme": "http",
        "root_path": "",
        "app": FastAPI(),
    }
    return Request(scope)


def _settings(monkeypatch, **overrides) -> Settings:
    set_valid_env(monkeypatch, VALID_ENV, **overrides)
    get_settings.cache_clear()
    return get_settings()


def test_require_dashboard_host_is_a_noop_outside_production(monkeypatch):
    settings = _settings(monkeypatch, APP_ENV="development")
    require_dashboard_host(_make_request("evil.com"), settings)  # must not raise


def test_require_dashboard_host_accepts_the_configured_value(monkeypatch):
    settings = _settings(
        monkeypatch,
        APP_ENV="production",
        API_HOSTNAME="api.example.test",
        DASHBOARD_HOSTNAME="dashboard.example.test",
        QDRANT_API_KEY="zQ7mK2pR9xLaUeVbNcOd",
        QDRANT_READ_ONLY_API_KEY="aB3dE6fG9hJkLmNoPqRs",
        JWT_SIGNING_KEY="zQ7mK2pR9xLaUeVbNcOdTfGhIjKlMnOp",
        DB_CONNECTION_ENCRYPTION_KEY="aB3dE6fG9hJkLmNoPqRsTuVwXyZ01234",
        OPENAI_API_KEY="aB3dE6fG9hJkLmNoPqRs",
    )
    require_dashboard_host(_make_request("dashboard.example.test"), settings)


def test_require_dashboard_host_strips_a_port_suffix(monkeypatch):
    settings = _settings(
        monkeypatch,
        APP_ENV="production",
        API_HOSTNAME="api.example.test",
        DASHBOARD_HOSTNAME="dashboard.example.test",
        QDRANT_API_KEY="zQ7mK2pR9xLaUeVbNcOd",
        QDRANT_READ_ONLY_API_KEY="aB3dE6fG9hJkLmNoPqRs",
        JWT_SIGNING_KEY="zQ7mK2pR9xLaUeVbNcOdTfGhIjKlMnOp",
        DB_CONNECTION_ENCRYPTION_KEY="aB3dE6fG9hJkLmNoPqRsTuVwXyZ01234",
        OPENAI_API_KEY="aB3dE6fG9hJkLmNoPqRs",
    )
    require_dashboard_host(_make_request("dashboard.example.test:443"), settings)


def test_require_dashboard_host_rejects_the_api_hostname(monkeypatch):
    settings = _settings(
        monkeypatch,
        APP_ENV="production",
        API_HOSTNAME="api.example.test",
        DASHBOARD_HOSTNAME="dashboard.example.test",
        QDRANT_API_KEY="zQ7mK2pR9xLaUeVbNcOd",
        QDRANT_READ_ONLY_API_KEY="aB3dE6fG9hJkLmNoPqRs",
        JWT_SIGNING_KEY="zQ7mK2pR9xLaUeVbNcOdTfGhIjKlMnOp",
        DB_CONNECTION_ENCRYPTION_KEY="aB3dE6fG9hJkLmNoPqRsTuVwXyZ01234",
        OPENAI_API_KEY="aB3dE6fG9hJkLmNoPqRs",
    )
    with pytest.raises(HTTPException) as excinfo:
        require_dashboard_host(_make_request("api.example.test"), settings)
    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "Invalid host header"


def test_require_api_host_rejects_the_dashboard_hostname(monkeypatch):
    settings = _settings(
        monkeypatch,
        APP_ENV="production",
        API_HOSTNAME="api.example.test",
        DASHBOARD_HOSTNAME="dashboard.example.test",
        QDRANT_API_KEY="zQ7mK2pR9xLaUeVbNcOd",
        QDRANT_READ_ONLY_API_KEY="aB3dE6fG9hJkLmNoPqRs",
        JWT_SIGNING_KEY="zQ7mK2pR9xLaUeVbNcOdTfGhIjKlMnOp",
        DB_CONNECTION_ENCRYPTION_KEY="aB3dE6fG9hJkLmNoPqRsTuVwXyZ01234",
        OPENAI_API_KEY="aB3dE6fG9hJkLmNoPqRs",
    )
    with pytest.raises(HTTPException) as excinfo:
        require_api_host(_make_request("dashboard.example.test"), settings)
    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "Invalid host header"


def test_require_api_host_accepts_the_configured_value(monkeypatch):
    settings = _settings(
        monkeypatch,
        APP_ENV="production",
        API_HOSTNAME="api.example.test",
        DASHBOARD_HOSTNAME="dashboard.example.test",
        QDRANT_API_KEY="zQ7mK2pR9xLaUeVbNcOd",
        QDRANT_READ_ONLY_API_KEY="aB3dE6fG9hJkLmNoPqRs",
        JWT_SIGNING_KEY="zQ7mK2pR9xLaUeVbNcOdTfGhIjKlMnOp",
        DB_CONNECTION_ENCRYPTION_KEY="aB3dE6fG9hJkLmNoPqRsTuVwXyZ01234",
        OPENAI_API_KEY="aB3dE6fG9hJkLmNoPqRs",
    )
    require_api_host(_make_request("api.example.test"), settings)


# --- live, full-app round trips per route family ---
#
# A real GET through the real app, production mode, both real hostnames
# configured -- the same shared env shape test_main.py's own _HOST_
# VALIDATION_ENV uses. The "wrong host" side always returns 400 "Invalid
# host header" through TrustedHostMiddleware's own, coarser allow-list,
# so a DISTINCT, unroutable-but-not-blackholed DATABASE_URL (127.0.0.1:1
# -- nothing listens there, so a real DB attempt fails FAST with a
# genuine connection-refused, never a multi-second timeout) is what
# proves the CORRECT host actually reached real route logic: it gets
# PAST the host-family check and fails for an unrelated, expected reason
# (a 500 from the unreachable database), never the host-rejection's own
# specific 400 "Invalid host header" body.
_ROUTE_FAMILY_ENV = {
    **VALID_ENV,
    "APP_ENV": "production",
    "DATABASE_URL": "postgresql+psycopg://user:valid-env-db-password@127.0.0.1:1/widgetplatform",
    "QDRANT_API_KEY": "zQ7mK2pR9xLaUeVbNcOd",  # noqa: S105 (realistic-looking, not a real secret)
    "QDRANT_READ_ONLY_API_KEY": "aB3dE6fG9hJkLmNoPqRs",  # noqa: S105
    "JWT_SIGNING_KEY": "zQ7mK2pR9xLaUeVbNcOdTfGhIjKlMnOp",  # noqa: S105
    "DB_CONNECTION_ENCRYPTION_KEY": "aB3dE6fG9hJkLmNoPqRsTuVwXyZ01234",  # noqa: S105
    "OPENAI_API_KEY": "aB3dE6fG9hJkLmNoPqRs",  # noqa: S105 (realistic-looking, not a real secret)
    "API_HOSTNAME": "api.example.test",
    "DASHBOARD_HOSTNAME": "dashboard.example.test",
}


async def test_dashboard_family_route_rejects_the_api_hostname(monkeypatch):
    set_valid_env(monkeypatch, _ROUTE_FAMILY_ENV)
    app = create_app()
    async with await http_client(app) as client:
        response = await client.get(
            "/api/v1/dashboard/auth/csrf", headers={"Host": "api.example.test"}
        )
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid host header"}


async def test_dashboard_family_route_accepts_the_dashboard_hostname(monkeypatch):
    # No DB dependency at all (app/dashboard/routes.py's issue_csrf_
    # cookie) -- a genuine, full round trip, not just "didn't 400".
    set_valid_env(monkeypatch, _ROUTE_FAMILY_ENV)
    app = create_app()
    async with await http_client(app) as client:
        response = await client.get(
            "/api/v1/dashboard/auth/csrf", headers={"Host": "dashboard.example.test"}
        )
    assert response.status_code == 204


async def test_admin_family_route_rejects_the_api_hostname(monkeypatch):
    set_valid_env(monkeypatch, _ROUTE_FAMILY_ENV)
    app = create_app()
    async with await http_client(app) as client:
        response = await client.get(
            "/api/v1/admin/me", headers={"Host": "api.example.test"}
        )
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid host header"}


async def test_api_family_route_rejects_the_dashboard_hostname(monkeypatch):
    set_valid_env(monkeypatch, _ROUTE_FAMILY_ENV)
    app = create_app()
    async with await http_client(app) as client:
        response = await client.get(
            "/api/v1/widget-appearance",
            params={"site_key": "pk_live_whatever"},
            headers={"Host": "dashboard.example.test"},
        )
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid host header"}


async def test_api_family_route_accepts_the_api_hostname_and_reaches_real_logic(
    monkeypatch,
):
    # Proceeds PAST the host check into the real route, which then hits
    # the deliberately-unroutable DATABASE_URL above and raises a real
    # psycopg/SQLAlchemy connection error -- httpx's own ASGITransport
    # re-raises an unhandled server exception rather than turning it
    # into a 500 response (a debugging convenience of the test tool, not
    # production uvicorn's own behavior) -- catching the SPECIFIC
    # OperationalError here, rather than asserting a status code, is the
    # more direct proof either way: this is categorically NOT the host-
    # rejection path, which never touches the database at all.
    from sqlalchemy.exc import OperationalError

    set_valid_env(monkeypatch, _ROUTE_FAMILY_ENV)
    app = create_app()
    async with await http_client(app) as client:
        with pytest.raises(OperationalError, match="port 1 failed"):
            await client.get(
                "/api/v1/widget-appearance",
                params={"site_key": "pk_live_whatever"},
                headers={"Host": "api.example.test"},
            )


async def test_health_route_is_reachable_on_either_hostname(monkeypatch):
    # Deliberately unrestricted by either route-family dependency --
    # still gated by TrustedHostMiddleware itself, one layer out.
    set_valid_env(monkeypatch, _ROUTE_FAMILY_ENV)
    app = create_app()
    async with await http_client(app) as client:
        on_api = await client.get("/health", headers={"Host": "api.example.test"})
        on_dashboard = await client.get(
            "/health", headers={"Host": "dashboard.example.test"}
        )
    assert on_api.status_code == 200
    assert on_dashboard.status_code == 200


# --- source-level guard: every router create_app() registers must be
# classified into exactly one family, or explicitly exempted ---

DASHBOARD_FAMILY_ROUTERS: tuple[str, ...] = (
    "dashboard_router",
    "dashboard_tenant_router",
    "dashboard_source_router",
    "dashboard_escalation_router",
    "admin_router",
)
API_FAMILY_ROUTERS: tuple[str, ...] = (
    "auth_router",
    "chat_router",
    "handoff_router",
)
# health_router: deliberately unrestricted by either family (see this
# file's own live test above) -- the ONE router allowed to appear in
# neither tuple above.
UNRESTRICTED_ROUTERS: tuple[str, ...] = ("health_router",)


def _create_app_source() -> str:
    source = (BACKEND_DIR / "app" / "main.py").read_text()
    match = re.search(r"def create_app\(.*?\n(.*?)\n    return app", source, re.DOTALL)
    assert match is not None, "app/main.py's create_app() was not found at all"
    return match.group(1)


def _include_router_calls(source: str) -> dict[str, str]:
    # Router name -> the full text of its own include_router(...) call
    # (balanced on parens, since some calls span multiple lines).
    calls: dict[str, str] = {}
    for match in re.finditer(r"app\.include_router\(\s*(\w+)", source):
        name = match.group(1)
        start = match.start()
        depth = 0
        i = source.index("(", start)
        j = i
        for j in range(i, len(source)):
            if source[j] == "(":
                depth += 1
            elif source[j] == ")":
                depth -= 1
                if depth == 0:
                    break
        calls[name] = source[start : j + 1]
    return calls


def test_every_router_create_app_registers_is_classified_into_one_family():
    source = _create_app_source()
    calls = _include_router_calls(source)

    all_classified = set(DASHBOARD_FAMILY_ROUTERS) | set(API_FAMILY_ROUTERS) | set(
        UNRESTRICTED_ROUTERS
    )
    assert set(calls) == all_classified, (
        f"app/main.py's create_app() registers {set(calls)!r}, but this guard's own "
        f"classification covers {all_classified!r} -- a router was added or removed "
        "without updating this test's own DASHBOARD_FAMILY_ROUTERS/API_FAMILY_ROUTERS/"
        "UNRESTRICTED_ROUTERS tuples."
    )

    for name in DASHBOARD_FAMILY_ROUTERS:
        assert "require_dashboard_host" in calls[name], (
            f"{name}'s own include_router() call no longer passes "
            "dependencies=[Depends(require_dashboard_host)]"
        )
        assert "require_api_host" not in calls[name]

    for name in API_FAMILY_ROUTERS:
        assert "require_api_host" in calls[name], (
            f"{name}'s own include_router() call no longer passes "
            "dependencies=[Depends(require_api_host)]"
        )
        assert "require_dashboard_host" not in calls[name]

    for name in UNRESTRICTED_ROUTERS:
        assert "require_dashboard_host" not in calls[name]
        assert "require_api_host" not in calls[name]
