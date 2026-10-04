# backend/tests/test_admin_dependencies.py
# Task 2.2.f: tests for the temporary admin-key dependency
# (backend/app/admin/dependencies.py). Not wired into any real endpoint yet
# (2.2.g's own job) -- exercised through a small throwaway FastAPI app
# defined here, the same httpx.AsyncClient + ASGITransport pattern
# test_dependencies.py already established for app/auth/dependencies.py.
#
# Fully offline, no real database needed: require_admin_key() itself
# performs no I/O at all (confirmed by reading it directly), and
# get_current_visitor()'s own failure path exercised below (test (e)'s
# "reverse" check) fails at JWT decode, before ever reaching a database
# query -- confirmed by reading that function directly too, not assumed.
import uuid

from fastapi import Depends, FastAPI

from app.admin.dependencies import AdminKeyVerified, require_admin_key
from app.auth.dependencies import get_current_visitor
from app.auth.tokens import encode_session_token
from tests.conftest import VALID_ENV, http_client, set_valid_env

ADMIN_KEY = VALID_ENV["ADMIN_API_KEY"]
DISTINCTIVE_FAKE_ADMIN_KEY = "distinctive-fake-admin-key-should-never-leak-98765"  # noqa: S105
ORIGIN = "https://widget.example"


def _build_test_app() -> FastAPI:
    app = FastAPI()

    @app.get("/admin-probe")
    async def admin_probe(_marker: AdminKeyVerified = Depends(require_admin_key)):  # noqa: B008
        return {"ok": True}

    @app.get("/whoami")
    async def whoami(identity=Depends(get_current_visitor)):  # noqa: B008
        return {"tenant_id": str(identity.tenant_id)}

    return app


async def test_missing_header_returns_401_distinct_from_get_current_visitors_own_failure(
    monkeypatch,
):
    set_valid_env(monkeypatch, VALID_ENV)
    app = _build_test_app()
    async with await http_client(app) as client:
        admin_response = await client.get("/admin-probe")
        visitor_response = await client.get("/whoami")

    assert admin_response.status_code == 401
    assert visitor_response.status_code == 401
    # Structurally distinct, not coincidentally the same: different detail
    # text, proving these are genuinely separate code paths, not one
    # failure shape reused under two names.
    assert admin_response.json() != visitor_response.json()
    assert admin_response.json()["detail"] == "admin key could not be verified"
    assert visitor_response.json()["detail"] == "session could not be verified"


async def test_wrong_key_returns_401_with_the_same_distinct_shape(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    app = _build_test_app()
    async with await http_client(app) as client:
        response = await client.get("/admin-probe", headers={"X-Admin-Key": "totally-wrong-key"})

    assert response.status_code == 401
    # Identical shape to the missing-header case (no oracle: "missing" and
    # "wrong" must not be distinguishable from the outside).
    assert response.json()["detail"] == "admin key could not be verified"


async def test_correct_key_succeeds_and_returns_the_marker(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    app = _build_test_app()
    async with await http_client(app) as client:
        response = await client.get("/admin-probe", headers={"X-Admin-Key": ADMIN_KEY})

    assert response.status_code == 200
    assert response.json() == {"ok": True}


async def test_wrong_key_never_appears_in_the_401_response(monkeypatch):
    # Leak test matching this project's established discipline: a
    # distinctive fake admin key that fails to match must never appear in
    # any response body or header.
    set_valid_env(monkeypatch, VALID_ENV)
    app = _build_test_app()
    async with await http_client(app) as client:
        response = await client.get(
            "/admin-probe", headers={"X-Admin-Key": DISTINCTIVE_FAKE_ADMIN_KEY}
        )

    assert response.status_code == 401
    assert DISTINCTIVE_FAKE_ADMIN_KEY not in response.text
    assert DISTINCTIVE_FAKE_ADMIN_KEY not in str(response.headers)


async def test_a_real_widget_jwt_does_not_satisfy_require_admin_key(monkeypatch):
    # Genuinely separate, non-overlapping mechanisms, not just separately
    # named: a real, validly-signed widget JWT, presented as the admin
    # key, must fail exactly like any other wrong value -- require_admin_key()
    # only ever reads X-Admin-Key, never Authorization, so it never even
    # looks at where a real JWT would normally arrive.
    set_valid_env(monkeypatch, VALID_ENV)
    token = encode_session_token(tenant_id=uuid.uuid4(), vid=uuid.uuid4(), origin=ORIGIN)
    app = _build_test_app()
    async with await http_client(app) as client:
        response = await client.get("/admin-probe", headers={"X-Admin-Key": token})

    assert response.status_code == 401
    assert response.json()["detail"] == "admin key could not be verified"


async def test_a_correct_admin_key_does_not_satisfy_get_current_visitor(monkeypatch):
    # The reverse direction: the correct admin key, presented as a Bearer
    # token, must fail get_current_visitor() exactly like any other
    # malformed token -- it is not a valid JWT, so decode_session_token()
    # rejects it before ever reaching a database query.
    set_valid_env(monkeypatch, VALID_ENV)
    app = _build_test_app()
    async with await http_client(app) as client:
        response = await client.get(
            "/whoami", headers={"Authorization": f"Bearer {ADMIN_KEY}", "Origin": ORIGIN}
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "session could not be verified"
