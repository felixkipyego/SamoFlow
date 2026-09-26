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
# test_alembic.py's _alembic_subprocess_env()).
import importlib
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

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
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        if os.environ.get("CI") == "true":
            pytest.fail(
                "TEST_DATABASE_URL is not set and CI=true: this test must "
                "fail, not skip, when CI is expected to provide a test "
                "database. Check the CI workflow's Postgres service and env."
            )
        pytest.skip(_LOCAL_DOCKER_RUN_HINT)

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


def fresh_import(module_name):
    # A fresh import into a new module object (not importlib.reload, which
    # mutates the module dict a test file's own `from x import y` binding
    # still points into, breaking later isinstance/pytest.raises checks).
    # Must not raise despite an empty environment.
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)
