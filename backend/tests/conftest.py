# backend/tests/conftest.py
# Shared fixtures and helpers for backend/tests. Consolidates what was
# duplicated across test_config.py, test_main.py, test_worker.py and
# test_env_example.py (duplication check after Task 1.1.f): the required
# env-var list, the autouse env-isolation fixture, the valid-environment
# setter, the fresh-import helper and the shared test password. Task 1.1.h
# adds require_test_database(), the guard that keeps destructive
# integration-test setup off a real database. Duplication check after
# 1.1.g/h/i also moves VALID_ENV here (was byte-identical in test_config.py
# and test_worker.py) and points the local-test-db hint at deploy/
# docker-compose.yml's test-db profile, the canonical way to start one
# since Task 1.1.i (rather than a hand-typed "docker run"). Duplication
# check after 1.1.k/j/l moves REPO_ROOT here (byte-identical
# Path(__file__).resolve().parents[2] in test_evals_placeholder.py,
# test_makefile_guard.py and test_layout_placeholders.py). Duplication check
# after 1.1.m/n/o.a adds is_comment_or_blank() and read_lines(), each
# repeated (with minor variations) across test_ci_guard.py,
# test_ignore_files_guard.py and test_hardening_guard.py. Duplication check
# after 1.1.o.h/1.1b/1.1c adds minimal_subprocess_env() (was byte-identical
# in test_evals_placeholder.py and test_hooks.py, and re-listed inline in
# test_alembic.py's _alembic_subprocess_env()). Duplication check after
# 1.2.d/e/f adds db_session() (the async-generator-driving shape was
# repeated ~14 times across test_tenancy_repository.py, plus twice in
# test_db.py).
import importlib
import os
import sys
from collections.abc import AsyncIterator
from contextlib import aclosing, asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import db
from app.config import POSTGRES_SCHEME, Settings, get_settings

REPO_ROOT = Path(__file__).resolve().parents[2]

REQUIRED_VARS = [name.upper() for name in Settings.model_fields]


def is_comment_or_blank(line: str) -> bool:
    stripped = line.strip()
    return stripped == "" or stripped.startswith("#")


def read_lines(path: Path) -> list[str]:
    assert path.is_file(), f"{path} not found"
    return path.read_text().splitlines()


def minimal_subprocess_env() -> dict[str, str]:
    # Shared by test_evals_placeholder.py, test_hooks.py, and built on top of
    # (with its own additional vars) by test_alembic.py's
    # _alembic_subprocess_env(): PATH and HOME only, so a subprocess under
    # test never inherits anything else from the caller's shell.
    env = {}
    for name in ("PATH", "HOME"):
        if name in os.environ:
            env[name] = os.environ[name]
    return env

# Shared by test_config.py and test_worker.py: a minimal environment that
# passes every Settings validator, with values chosen only to be valid, not
# to resemble anything real. test_main.py keeps its own (TEST-NET-3
# addresses, to prove /health never contacts anything).
VALID_ENV = {
    "APP_ENV": "development",
    "DATABASE_URL": "postgresql+psycopg://user:pw@localhost:5432/widgetplatform",
    "QDRANT_URL": "http://localhost:6333",
    "QDRANT_API_KEY": "test-qdrant-key",  # noqa: S105 (test fixture value, not a real secret)
    "API_HOST": "127.0.0.1",
    "API_PORT": "8000",
}

# Always allowed: a database on the machine running the tests. Additional
# hosts (e.g. a CI Postgres service container's name) are opted in via
# TEST_DATABASE_ALLOWED_HOSTS, never hard-coded here.
_DEFAULT_ALLOWED_TEST_HOSTS = frozenset({"localhost", "127.0.0.1"})

_LOCAL_DOCKER_RUN_HINT = (
    "TEST_DATABASE_URL is not set. Start the local test database with:\n"
    "  docker compose --env-file .env -f deploy/docker-compose.yml "
    "--profile test up -d test-db\n"
    "then set TEST_DATABASE_URL=postgresql+psycopg://widgetplatform:"
    "change-me@127.0.0.1:55432/widgetplatform_test"
)

_LOCAL_QDRANT_RUN_HINT = (
    "TEST_QDRANT_URL is not set. Start the local test Qdrant with:\n  make test-qdrant"
)

# Shared wherever a test needs a password value that must never leak into
# logs, repr/str output or exception messages.
TEST_PASSWORD = "sup3r-secret-pw"  # noqa: S105 (test fixture value, not a real secret)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    # Every test starts with none of the required variables present, and
    # with no cached Settings instance left over from another test.
    for name in REQUIRED_VARS:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def set_valid_env(monkeypatch, valid_env, **overrides):
    for key, value in {**valid_env, **overrides}.items():
        monkeypatch.setenv(key, value)


def _allowed_test_hosts() -> frozenset[str]:
    extra = os.environ.get("TEST_DATABASE_ALLOWED_HOSTS", "")
    extra_hosts = {h.strip().lower() for h in extra.split(",") if h.strip()}
    return _DEFAULT_ALLOWED_TEST_HOSTS | extra_hosts


def _require_env_var(var_name: str, ci_fail_message: str, skip_hint: str) -> str:
    # Shared by require_test_database() and require_test_qdrant() (duplication
    # check after 1.3.a1/a2/b): only this "unset -> skip locally / fail in
    # CI" first step was byte-identical in shape; everything each function
    # checks once it has a non-empty value stays in that function, since it
    # differs (URL scheme/suffix vs. a second required key, different
    # allowed-hosts checks).
    value = os.environ.get(var_name)
    if not value:
        if os.environ.get("CI") == "true":
            pytest.fail(ci_fail_message)
        pytest.skip(skip_hint)
    return value


def require_test_database() -> str:
    # Guards destructive integration-test setup (DROP SCHEMA, etc.) so it can
    # never run against a real database:
    #   - unset locally (CI != "true"): skip, with the docker command to
    #     start a local test database;
    #   - unset in CI (CI == "true"): fail, since CI must run this test, not
    #     silently skip it;
    #   - set but not a "..._test"-named postgresql+psycopg database: fail
    #     (never skip) — a malformed URL must not be treated as "no database
    #     configured";
    #   - set, correctly named, but on a host that isn't localhost/127.0.0.1
    #     or explicitly opted in via TEST_DATABASE_ALLOWED_HOSTS: fail — the
    #     "_test" name alone doesn't prove the host is safe to drop schemas on.
    url = _require_env_var(
        "TEST_DATABASE_URL",
        "TEST_DATABASE_URL is not set and CI=true: this test must "
        "fail, not skip, when CI is expected to provide a test "
        "database. Check the CI workflow's Postgres service and env.",
        _LOCAL_DOCKER_RUN_HINT,
    )

    parts = urlsplit(url)
    database_name = parts.path.lstrip("/")
    if parts.scheme != POSTGRES_SCHEME or not database_name.endswith("_test"):
        pytest.fail(
            "TEST_DATABASE_URL must use the "
            f"'{POSTGRES_SCHEME}' scheme and name a database ending in "
            "'_test' (this test drops and recreates its public schema, so "
            "it must never be able to point at a real database)."
        )

    allowed_hosts = _allowed_test_hosts()
    if (parts.hostname or "") not in allowed_hosts:
        pytest.fail(
            f"TEST_DATABASE_URL's host {parts.hostname!r} is not allowed. "
            "Only 'localhost' and '127.0.0.1' are allowed by default; to "
            "allow another host (e.g. a CI Postgres service container's "
            "name), list it in TEST_DATABASE_ALLOWED_HOSTS "
            "(comma-separated)."
        )
    return url


def require_test_qdrant() -> tuple[str, str]:
    # Same shape as require_test_database() above, for the same reason (the
    # live authentication proof in test_qdrant_auth.py must never run
    # against anything other than the disposable test-qdrant service):
    #   - unset locally (CI != "true"): skip, with the make command to start
    #     a local test Qdrant;
    #   - unset in CI (CI == "true"): fail, since CI must run this test, not
    #     silently skip it;
    #   - URL set but TEST_QDRANT_API_KEY unset: fail, never skip -- a
    #     half-configured environment is a real misconfiguration, not "no
    #     Qdrant configured";
    #   - set, but on a host that isn't localhost/127.0.0.1: fail, without
    #     printing the key.
    url = _require_env_var(
        "TEST_QDRANT_URL",
        "TEST_QDRANT_URL is not set and CI=true: this test must "
        "fail, not skip, when CI is expected to provide a test "
        "Qdrant. Check the CI workflow's Qdrant service and env.",
        _LOCAL_QDRANT_RUN_HINT,
    )

    key = os.environ.get("TEST_QDRANT_API_KEY")
    if not key:
        pytest.fail(
            "TEST_QDRANT_URL is set but TEST_QDRANT_API_KEY is not: a "
            "half-configured test Qdrant is a real misconfiguration, not "
            '"no Qdrant configured".'
        )

    parts = urlsplit(url)
    if (parts.hostname or "") not in _DEFAULT_ALLOWED_TEST_HOSTS:
        pytest.fail(
            f"TEST_QDRANT_URL's host {parts.hostname!r} is not allowed. "
            "Only 'localhost' and '127.0.0.1' are allowed."
        )
    return url, key


def fresh_import(module_name):
    # A fresh import into a new module object (not importlib.reload, which
    # mutates the module dict a test file's own `from x import y` binding
    # still points into, breaking later isinstance/pytest.raises checks).
    # Must not raise despite an empty environment.
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


# Shared "expected schema" facts for Task 1.2.b's/1.2.c's tests (moved here
# in the duplication check after 1.2.a/b/c): test_models.py checks these
# against SQLAlchemy's Python metadata (no database), test_alembic.py checks
# the same facts against a live database via sa.inspect() after a real
# migration -- two different, real proofs, but the same expected data, so a
# schema change only needs updating it once.
EXPECTED_TABLES = {"tenants", "site_keys", "visitors", "conversations", "plans"}

EXPECTED_PK_COLUMNS = {
    "tenants": "id",
    "site_keys": "id",
    "visitors": "vid",
    "conversations": "cid",
    "plans": "id",
}

# tenant_id columns cascade from tenants (PROJECT_SPEC.md decision: DB-level
# ON DELETE CASCADE from tenants down to site_keys/visitors/conversations).
CASCADE_FK_COLUMNS = {
    ("site_keys", "tenant_id"): "tenants",
    ("visitors", "tenant_id"): "tenants",
    ("conversations", "tenant_id"): "tenants",
}

# site_key_id (visitors) and vid (conversations) are a separate edge the
# cascade decision never covered, and no site-key/visitor-deletion feature
# exists yet to require cascading them too, so they stay at the default (no
# ondelete -- NO ACTION), not cascade.
NO_ACTION_FK_COLUMNS = {
    ("visitors", "site_key_id"),
    ("conversations", "vid"),
}


@asynccontextmanager
async def db_session() -> AsyncIterator[AsyncSession]:
    # Shared across test_db.py and test_tenancy_repository.py (duplication
    # check after 1.2.d/e/f): db.get_db_session() is itself a "dependency
    # with yield" generator, meant to be driven by FastAPI's own Depends()
    # machinery later; outside of that, the standard way to drive one is
    # contextlib.aclosing() + anext(), which every test here was repeating
    # inline. Wrapping it once as an @asynccontextmanager gives every call
    # site the plain "async with db_session() as session:" shape instead.
    async with aclosing(db.get_db_session()) as session_gen:
        yield await anext(session_gen)
