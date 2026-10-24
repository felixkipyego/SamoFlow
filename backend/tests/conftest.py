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
# test_db.py). Duplication check after 1.3.c/d/e adds: BACKEND_DIR (was
# independently re-derived in test_config_guard.py, test_qdrant.py and
# test_qdrant_read_path_guard.py); iter_python_files() and called_name()
# (the same file-walk and ast.Call-name-extraction logic, each spelled
# slightly differently in test_config_guard.py and
# test_qdrant_read_path_guard.py -- the two files' actual rule-checking
# logic stays separate, since it differs enough not to share); and
# live_qdrant_collection() (the require_test_qdrant() -> build_qdrant_client()
# -> unique name -> try/finally(delete-if-exists, close) scaffold repeated
# at ~15 call sites across test_qdrant_collection.py and
# test_qdrant_isolation.py, mirroring db_session()'s own shape below).
# Duplication check after 1.5.a-e adds _FakeClock (byte-identical in
# test_ratelimit.py, test_dependencies.py and test_session.py -- the same
# ~10-line injectable-clock test double, three times). Duplication check
# after 2.3.a/b/c adds assert_no_socket_connections() (the socket.socket.
# connect-raises-if-called proof, duplicated twice in test_safe_fetch.py)
# and local_http_server() (the HTTPServer-on-an-ephemeral-port +
# background-thread + shutdown/join lifecycle from test_safe_fetch.py's
# own _slow_large_server, generalized to accept any handler class) --
# both anticipating 2.3.d's own already-approved need for the identical
# proof shapes against a redirect-issuing handler.
import ast
import asyncio
import http.server
import importlib
import ipaddress
import json
import os
import socket
import sys
import threading
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import aclosing, asynccontextmanager, contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from qdrant_client import AsyncQdrantClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

# all_models (imported below, for its side effect, like db/qdrant) registers
# every domain's tables on Base.metadata -- needed so _seeded_tenant/
# _fetch_job further down (duplication check after 2.1.c/d/e, items A1/A2)
# work correctly on their own, not merely by accident because some other
# already-collected test module happened to import app.plans.models first
# (the exact fragility app/all_models.py exists to remove -- see its own
# and app/worker.py's header comments).
from app import all_models, db, qdrant  # noqa: F401
from app.agent import chat_model, checkpointer
from app.config import POSTGRES_SCHEME, Settings, get_settings
from app.ingest import embedding
from app.ingest.ip_safety import is_unsafe_destination_ip
from app.ingest.models import Job
from app.ingest.repository import IngestRepository
from app.tenancy.models import Tenant

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = Path(__file__).resolve().parent.parent

# Duplication check after 2.4.b/c/d: test_extract_html.py, test_extract_pdf.py
# and test_chunking.py each independently defined the identical "FIXTURES_DIR
# + a one-line read helper" pattern -- the 3rd occurrence (test_chunking.py's
# own _extract()) crossed this project's own extraction threshold.
_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def read_html_fixture(name: str) -> str:
    return (_FIXTURES_DIR / "html" / name).read_text()


def read_pdf_fixture(name: str) -> bytes:
    return (_FIXTURES_DIR / "pdf" / name).read_bytes()


def read_docx_fixture(name: str) -> bytes:
    return (_FIXTURES_DIR / "docx" / name).read_bytes()


def read_text_fixture(name: str) -> str:
    return (_FIXTURES_DIR / "text" / name).read_text()


def read_markdown_fixture(name: str) -> str:
    return (_FIXTURES_DIR / "markdown" / name).read_text()


# Task 1.4.a: jwt_signing_key_previous is the first Settings field with a
# default (optional), so REQUIRED_VARS must now actually filter rather than
# list every field -- field.is_required() is pydantic's own built-in
# "has no default" check (rule 11), confirmed against the installed
# pydantic==2.13.5. REQUIRED_VARS feeds "missing this var must raise" tests
# and the .env.example required-set check, both of which must NOT treat an
# optional field as required. ALL_SETTINGS_VARS is the superset (required +
# optional) for anywhere the actual intent is "no Settings-related env var
# at all" -- the autouse isolation fixture below, and test_worker.py's own
# subprocess-env builder.
REQUIRED_VARS = [
    name.upper() for name, field in Settings.model_fields.items() if field.is_required()
]
OPTIONAL_VARS = [
    name.upper() for name, field in Settings.model_fields.items() if not field.is_required()
]
ALL_SETTINGS_VARS = REQUIRED_VARS + OPTIONAL_VARS


class _FakeClock:
    # Injectable time source (Task 1.4.h design point 3): starts at an
    # arbitrary fixed point and only ever moves when a test tells it to --
    # never real time, so TTL/window expiry is deterministic, not
    # timing-flaky. Shared by test_ratelimit.py (1.5.a), test_dependencies.py
    # (1.4.h) and test_session.py (1.5.c) -- duplication check after 1.5.a-e
    # confirmed all three were byte-identical (same default start=1000.0,
    # same __call__/advance semantics) before moving this here.
    def __init__(self, start: float = 1_000.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def is_comment_or_blank(line: str) -> bool:
    stripped = line.strip()
    return stripped == "" or stripped.startswith("#")


def read_lines(path: Path) -> list[str]:
    assert path.is_file(), f"{path} not found"
    return path.read_text().splitlines()


def write_guard_tree_file(tmp_path: Path, rel_path: str, content: str) -> None:
    # Duplication check after 2.8.a/b/c: this exact 3-line body existed as
    # three separate, byte-identical local copies -- test_qdrant_read_
    # path_guard.py (1.3.e, the original), test_audit_log_write_path_
    # guard.py (a second, previously-unflagged occurrence), and test_
    # database_adapter_guard.py (2.8.b, the confirmed 3rd) -- every
    # AST-guard test's own synthetic-tree unit tests write one or more
    # small .py files into a tmp_path tree before scanning it, and all
    # three needed the identical "create parent dirs, write text" two-
    # liner to do it. One shared helper instead of three copies.
    full = tmp_path / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)


def iter_python_files(*roots: Path):
    # Shared by test_config_guard.py and test_qdrant_read_path_guard.py
    # (duplication check after 1.3.c/d/e): both walked their own scan
    # root(s) the same way (sorted rglob("*.py"), skipping __pycache__),
    # just parameterized differently -- this is the one part of their AST
    # scans that was genuinely identical; the rule-checking logic each file
    # applies afterward stays separate.
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path


def called_name(node: ast.Call) -> str | None:
    # Shared by test_config_guard.py and test_qdrant_read_path_guard.py: the
    # name a Call node invokes, whether written as a bare name (`f(...)`) or
    # an attribute access (`x.f(...)`) -- None for anything else (e.g. a
    # call through a subscript or a lambda result).
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def check_call_allowlist(
    paths,
    target_name: str,
    allowlist: tuple[str, ...],
    self_file: Path | None = None,
    root: Path = BACKEND_DIR,
) -> list[str]:
    # Shared by test_config_guard.py and test_ingest_repository_guard.py
    # (duplication check after 2.1.a/b): both walked an AST, matched a
    # Call node's name via called_name() above, and reported the same
    # "calls X() but this file is not in its allow-list" shape -- this is
    # that one shared shape, parameterized by which name and which list.
    # Each guard's own OTHER rules (test_config_guard.py's Settings()/
    # errors() checks) stay separate, since they differ.
    #
    # `root` (duplication check after 2.2.g/h): defaults to BACKEND_DIR,
    # preserving every existing caller's exact prior behavior unchanged --
    # added so test_audit_log_write_path_guard.py's own synthetic-tree
    # unit tests could call this directly against a tmp_path root instead
    # of maintaining a second, near-identical local scan function just to
    # relativize paths against something other than the real backend tree.
    violations = []
    for path in paths:
        if path == self_file:
            continue
        rel = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and called_name(node) == target_name:
                if rel not in allowlist:
                    violations.append(
                        f"{path}:{node.lineno}: calls {target_name}() but {rel!r} "
                        "is not in its allow-list"
                    )
    return violations


@asynccontextmanager
async def spawn_module_subprocess(
    module: str, env: dict[str, str]
) -> AsyncIterator[asyncio.subprocess.Process]:
    # Scoped duplication check after 2.1.g: shared by test_worker.py's
    # `python -m app.worker` tests and test_qdrant.py's `python -m app.qdrant`
    # tests -- both spawned a real subprocess to exercise a CLI entrypoint's
    # own main(), with an identical asyncio.create_subprocess_exec(...) call
    # (differing only in which app.<module> to run) and an identical
    # kill-if-still-running cleanup block repeated at every call site.
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        f"app.{module}",
        cwd=BACKEND_DIR,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        yield process
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


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
    # 2026-10-24 (D-db-password-min-length): >= 16 characters, so production-
    # mode tests built on VALID_ENV pass the database-password minimum.
    "DATABASE_URL": "postgresql+psycopg://user:valid-env-db-password@localhost:5432/widgetplatform",
    # [SECURITY] L2 follow-up (advisor review, 2026-10-18): deliberately
    # NOT included -- database_url_migrate is Optional on Settings
    # (app/config.py), and api/worker-style code (what the vast majority
    # of tests using this base dict exercise) must construct Settings
    # and run correctly WITHOUT it. Leaving it out here is itself a live
    # proof of that: any test that silently needed it would fail loudly
    # the moment it ran. reset_checkpoint_env()/reset_test_database()
    # below add a REAL owner-role value as an explicit override wherever
    # a test genuinely drives migrate-level code (run_checkpointer_
    # setup(), real alembic subprocess runs).
    "QDRANT_URL": "http://localhost:6333",
    "QDRANT_API_KEY": "test-qdrant-key",  # noqa: S105 (test fixture value, not a real secret)
    "QDRANT_READ_ONLY_API_KEY": "test-qdrant-readonly-key",  # noqa: S105 (test fixture value, not a real secret)
    "JWT_SIGNING_KEY": "test-jwt-signing-key-at-least-32-chars",  # noqa: S105
    "DB_CONNECTION_ENCRYPTION_KEY": "test-db-connection-key-at-least-32-chars",  # noqa: S105
    "OPENAI_API_KEY": "test-openai-key",  # noqa: S105 (test fixture value, not a real secret)
    "API_HOST": "127.0.0.1",
    "API_PORT": "8000",
}

# Always allowed: a database on the machine running the tests. Additional
# hosts (e.g. a CI Postgres service container's name) are opted in via
# TEST_DATABASE_ALLOWED_HOSTS, never hard-coded here.
_DEFAULT_ALLOWED_TEST_HOSTS = frozenset({"localhost", "127.0.0.1"})

# [SECURITY] L2 (Phase 7 role split, 2026-10-18): `make test-db` (not a
# bare `docker compose up`) is the real local command now -- it also runs
# the one-time provisioning step (app/db_provisioning.py) that creates
# widgetplatform_owner/widgetplatform_app against test-db, which TEST_
# DATABASE_URL/TEST_DATABASE_URL_APP below need to already exist.
_LOCAL_DOCKER_RUN_HINT = (
    "TEST_DATABASE_URL/TEST_DATABASE_URL_APP are not set. Start the local "
    "test database with:\n  make test-db\n"
    "then set:\n"
    "  TEST_DATABASE_URL=postgresql+psycopg://widgetplatform_owner:"
    "change-me-owner@127.0.0.1:55432/widgetplatform_test\n"
    "  TEST_DATABASE_URL_APP=postgresql+psycopg://widgetplatform_app:"
    "change-me-app@127.0.0.1:55432/widgetplatform_test"
)

_LOCAL_QDRANT_RUN_HINT = (
    "TEST_QDRANT_URL is not set. Start the local test Qdrant with:\n  make test-qdrant"
)

# Shared wherever a test needs a password value that must never leak into
# logs, repr/str output or exception messages.
TEST_PASSWORD = "sup3r-secret-pw"  # noqa: S105 (test fixture value, not a real secret)

# [SECURITY] L1 (out-of-band Phase 7 batch, 2026-10-17): VALID_ENV's own
# secret-class values are deliberately "test-"-prefixed (see its own
# header comment) -- exactly one of the 4 known-weak prefixes app/
# secrets_policy.py's new production startup gate now rejects. Any test
# that combines APP_ENV=production with real Settings validation (not
# Settings.model_construct(), which bypasses every validator including
# this new one) must override these with realistic-LOOKING values instead
# -- never a real secret, matching VALID_ENV's own "chosen only to be
# valid, not to resemble anything real" precedent, just avoiding the 4
# prefixes this gate checks for. Deliberately does NOT include
# DATABASE_URL: every VALID_ENV variant's own password ("valid-env-db-
# password", 21 characters since 2026-10-24) is already not weak-prefixed
# and meets the 16-character minimum, so overriding it here would only risk silently
# replacing a test's own deliberately-chosen host (e.g. test_main.py's
# own TEST-NET-3 address, proving /health never contacts anything) for no
# real benefit -- a test that specifically needs a weak DATABASE_URL
# password builds that DSN inline, scoped to itself.
#
# [SECURITY] L1 PART C (2026-10-17): also carries API_HOSTNAME -- not a
# secret-class value, but the SAME shape ("VALID_ENV has none; production
# genuinely requires one") as every entry above, via a DIFFERENT
# validator (_require_api_hostname_in_production, app/config.py). Every
# site below already combines this dict with APP_ENV=production, so one
# dict covering "everything production additionally needs beyond
# VALID_ENV" is simpler than two dicts every caller would have to
# remember to spread together (rule 11).
PRODUCTION_REALISTIC_SECRET_OVERRIDES = {
    "QDRANT_API_KEY": "zQ7mK2pR9xLaUeVbNcOd",  # noqa: S105 (realistic-looking, not a real secret)
    "QDRANT_READ_ONLY_API_KEY": "aB3dE6fG9hJkLmNoPqRs",  # noqa: S105
    "JWT_SIGNING_KEY": "zQ7mK2pR9xLaUeVbNcOdTfGhIjKlMnOp",  # noqa: S105
    "DB_CONNECTION_ENCRYPTION_KEY": "aB3dE6fG9hJkLmNoPqRsTuVwXyZ01234",  # noqa: S105
    "OPENAI_API_KEY": "aB3dE6fG9hJkLmNoPqRs",  # noqa: S105 (realistic-looking, not a real secret)
    # "test", not a prettier placeholder like "api.example.test": every
    # existing production-mode test that also sends a real request
    # (http_client() above, AsyncClient(base_url="http://test")) sends
    # Host: test, and app/main.py's TrustedHostMiddleware (production
    # only) would otherwise 400 every one of those pre-existing tests
    # the moment this dict's own APP_ENV=production took effect. A test
    # that specifically exercises Host validation (test_main.py) uses
    # its own distinct hostname instead of this shared dict.
    "API_HOSTNAME": "test",
    # [SECURITY] L1 PART C follow-up (advisor review, 2026-10-17):
    # DASHBOARD_HOSTNAME became required in production the same moment
    # API_HOSTNAME did (Settings._require_dashboard_hostname_in_
    # production) -- same "test" value as API_HOSTNAME above is fine
    # here: nothing in this shared dict's own callers sends a dashboard-
    # family request, so there is no real host to distinguish.
    "DASHBOARD_HOSTNAME": "test",
}


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    # Every test starts with none of the Settings-related variables present
    # (required or optional -- Task 1.4.a: a value one test sets for the
    # optional JWT_SIGNING_KEY_PREVIOUS must never leak into the next), and
    # with no cached Settings instance left over from another test.
    for name in ALL_SETTINGS_VARS:
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


def _validate_test_database_url(var_name: str, url: str) -> str:
    # Shared by require_test_database() and require_test_database_app()
    # below (duplication check, L2 Phase 7 role split, 2026-10-18): the
    # exact same three checks, parameterized only by which env var's name
    # appears in the failure messages.
    #   - set but not a "..._test"-named postgresql+psycopg database: fail
    #     (never skip) — a malformed URL must not be treated as "no database
    #     configured";
    #   - set, correctly named, but on a host that isn't localhost/127.0.0.1
    #     or explicitly opted in via TEST_DATABASE_ALLOWED_HOSTS: fail — the
    #     "_test" name alone doesn't prove the host is safe to drop schemas on.
    parts = urlsplit(url)
    database_name = parts.path.lstrip("/")
    if parts.scheme != POSTGRES_SCHEME or not database_name.endswith("_test"):
        pytest.fail(
            f"{var_name} must use the "
            f"'{POSTGRES_SCHEME}' scheme and name a database ending in "
            "'_test' (this test drops and recreates its public schema, so "
            "it must never be able to point at a real database)."
        )

    allowed_hosts = _allowed_test_hosts()
    if (parts.hostname or "") not in allowed_hosts:
        pytest.fail(
            f"{var_name}'s host {parts.hostname!r} is not allowed. "
            "Only 'localhost' and '127.0.0.1' are allowed by default; to "
            "allow another host (e.g. a CI Postgres service container's "
            "name), list it in TEST_DATABASE_ALLOWED_HOSTS "
            "(comma-separated)."
        )
    return url


def require_test_database() -> str:
    # Guards destructive integration-test setup (DROP SCHEMA, etc.) so it can
    # never run against a real database:
    #   - unset locally (CI != "true"): skip, with the docker command to
    #     start a local test database;
    #   - unset in CI (CI == "true"): fail, since CI must run this test, not
    #     silently skip it;
    #   - see _validate_test_database_url() above for the rest.
    #
    # [SECURITY] L2 (Phase 7 role split, 2026-10-18): this is the
    # OWNER/MIGRATOR role's own test connection -- genuine schema DDL
    # (DROP SCHEMA, alembic upgrade head, the checkpointer's setup())
    # needs this, not the APP role require_test_database_app() returns.
    url = _require_env_var(
        "TEST_DATABASE_URL",
        "TEST_DATABASE_URL is not set and CI=true: this test must "
        "fail, not skip, when CI is expected to provide a test "
        "database. Check the CI workflow's Postgres service and env.",
        _LOCAL_DOCKER_RUN_HINT,
    )
    return _validate_test_database_url("TEST_DATABASE_URL", url)


def require_test_database_app() -> str:
    # [SECURITY] L2 (Phase 7 role split, 2026-10-18): the APP role's own
    # test connection -- least privilege, no DDL. Application code under
    # test (get_engine(), open_checkpointer_pool()) must connect this way,
    # mirroring the real stack's own api/worker runtime connections, so a
    # missing grant fails a test locally instead of only in production.
    url = _require_env_var(
        "TEST_DATABASE_URL_APP",
        "TEST_DATABASE_URL_APP is not set and CI=true: this test must "
        "fail, not skip, when CI is expected to provide a test "
        "database. Check the CI workflow's Postgres service and env.",
        _LOCAL_DOCKER_RUN_HINT,
    )
    return _validate_test_database_url("TEST_DATABASE_URL_APP", url)


async def reset_checkpoint_env(monkeypatch, **extra_overrides) -> str:
    # Duplication check after 3.2.b/c/d (2026-11-02), finding A.1: this
    # "point DATABASE_URL at the real test-db, then drop the four
    # checkpoint tables for a deterministic starting state" preamble
    # existed as three separate inline copies inside test_checkpointer.py's
    # own live tests, plus a fourth, reordered variant assembled inside
    # test_agent_entrypoint.py's own _fresh_graph() helper. AsyncPostgres-
    # Saver.setup() owns its own raw-SQL schema (checkpoints/
    # checkpoint_blobs/checkpoint_writes/checkpoint_migrations) outside
    # app/db.py's Base.metadata, so reset_test_database()'s own drop_all/
    # create_all never touches these -- every live checkpointer-adjacent
    # test needs this same reset, not that one.
    #
    # `**extra_overrides` (Task 3.3.c): threaded into the SAME set_valid_env()
    # call as DATABASE_URL, matching _prepare_env()'s own established
    # "one set_valid_env() call carrying every override together" discipline
    # (test_job_handlers.py) -- found live while building test_agent_graph_
    # retrieve.py: that file needs BOTH this reset AND set_real_qdrant_env()'s
    # own overrides active at once (the real retrieve node calls
    # get_qdrant_read_client() internally), and calling set_real_qdrant_env()
    # as a SEPARATE second set_valid_env() call after this one re-applies
    # VALID_ENV's own fake DATABASE_URL wholesale, silently clobbering the
    # real one just set here -- confirmed live (a real connection-refused
    # failure against the fake default port) before adding this parameter.
    # Every existing caller passes none, so this is purely additive -- no
    # existing caller's behavior changes.
    # [SECURITY] L2 (Phase 7 role split, 2026-10-18): DATABASE_URL (what
    # open_checkpointer_pool() connects with, via database_url_str()) is
    # now the APP role, least privilege, matching the real api process;
    # DATABASE_URL_MIGRATE (database_url_migrate_str(), what
    # run_checkpointer_setup() connects with) is the OWNER role, which
    # actually has CREATE on the schema. The conninfo this function
    # returns stays the OWNER one -- every existing caller uses it for
    # genuine DDL (a manual AsyncPostgresSaver(conn).setup() call, or a
    # raw SELECT against checkpoint_migrations), never for the
    # application-level pool.
    owner_url = require_test_database()
    app_url = require_test_database_app()
    set_valid_env(
        monkeypatch,
        VALID_ENV,
        DATABASE_URL=app_url,
        DATABASE_URL_MIGRATE=owner_url,
        **extra_overrides,
    )
    conninfo = checkpointer.checkpointer_conninfo(owner_url)
    async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
        await conn.execute(
            "DROP TABLE IF EXISTS checkpoint_writes, checkpoint_blobs, "
            "checkpoints, checkpoint_migrations CASCADE"
        )
    return conninfo


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


def require_test_qdrant_read_only_key() -> str:
    # Task 3.1.b: deliberately a SEPARATE, small helper, not a widened
    # require_test_qdrant() return tuple -- that function already has many
    # existing call sites across this test suite destructuring exactly
    # `(url, key)`; only the handful of NEW tests exercising the read-only
    # key need this value, so forcing every existing call site to adopt a
    # 3-tuple for a value they don't use would be a real, unnecessary blast
    # radius (rule 11). Same fail-not-skip discipline as the key check
    # inside require_test_qdrant() above: call require_test_qdrant() first
    # for the skip/CI/host checks, THEN this for the one additional value.
    key = os.environ.get("TEST_QDRANT_READ_ONLY_API_KEY")
    if not key:
        pytest.fail(
            "TEST_QDRANT_URL is set but TEST_QDRANT_READ_ONLY_API_KEY is not: "
            'a half-configured test Qdrant is a real misconfiguration, not '
            '"no Qdrant configured".'
        )
    return key


def set_real_qdrant_env(monkeypatch) -> None:
    # Phase-3.1 duplication check (2026-11-01): existed as two separate,
    # byte-identical local copies -- test_retrieval_service.py's and
    # test_retrieval_isolation.py's own `_set_real_qdrant_env()` -- past
    # this project's own established extraction threshold given both were
    # true duplicates, not merely similar. Any live test calling
    # retrieve() needs this: that function calls get_settings() and
    # get_qdrant_read_client() internally, so a full valid environment,
    # pointed at the real test-qdrant service, is required.
    url, key = require_test_qdrant()
    read_only_key = require_test_qdrant_read_only_key()
    set_valid_env(
        monkeypatch,
        VALID_ENV,
        QDRANT_URL=url,
        QDRANT_API_KEY=key,
        QDRANT_READ_ONLY_API_KEY=read_only_key,
    )


@pytest.fixture(scope="module")
def close_the_shared_qdrant_read_client_once():
    # Phase-3.1 duplication check (2026-11-01): existed as two separate,
    # byte-identical fixtures -- test_retrieval_service.py's and
    # test_retrieval_isolation.py's own `_close_the_shared_read_client_
    # once()` -- plus a third, half-identical occurrence inside
    # test_qdrant.py's own combined fixture (which also closes
    # get_qdrant_client(), a genuinely different concern that stays
    # local there, not merged into this one). Deliberately NOT
    # `autouse=True` here: unlike a per-file autouse fixture, marking it
    # autouse in conftest.py would apply it to every test module in the
    # whole suite, including the hundreds that never touch
    # get_qdrant_read_client() at all. Each module that needs this opts
    # in explicitly via `pytestmark = pytest.mark.usefixtures(
    # "close_the_shared_qdrant_read_client_once")` -- module-scoped
    # either way, so one real instance per module, not per test.
    yield
    if qdrant.get_qdrant_read_client.cache_info().currsize:
        asyncio.run(qdrant.get_qdrant_read_client().close())
    qdrant.get_qdrant_read_client.cache_clear()


@pytest.fixture
async def close_any_checkpointer_pool_left_open():
    # Duplication check after 3.2.b/c/d (2026-11-02), finding A.2: existed
    # as two separate, byte-identical fixtures -- test_checkpointer.py's
    # and test_agent_entrypoint.py's own `_close_any_pool_left_open()`.
    # checkpointer._checkpointer_pool is a plain module global (Task 3.2.b
    # decision 1 -- deliberately not an @lru_cache singleton), so this
    # needs the same close-once discipline this project's lru_cache
    # singletons get from their own fixtures, just FUNCTION-scoped (the
    # default, unlike close_the_shared_qdrant_read_client_once's own
    # module scope immediately above) -- each test here opens its own
    # pool against its own env, not one shared instance per module.
    # Deliberately NOT `autouse=True`, matching that same fixture's own
    # reasoning: opt in explicitly via `pytestmark = pytest.mark.
    # usefixtures("close_any_checkpointer_pool_left_open")`.
    yield
    if checkpointer._checkpointer_pool is not None:
        await checkpointer.close_checkpointer_pool()


@pytest.fixture
def close_chat_model_cache_between_tests():
    # Task 3.4.b: get_chat_model()'s own cached instance (app/agent/
    # chat_model.py) is NOT safe to reuse across repeated, separately-
    # closed event loops once a real HTTP keep-alive connection has been
    # established through it -- confirmed live, not assumed (see that
    # module's own header comment for the full finding). Function-scoped
    # (unlike close_the_shared_qdrant_read_client_once's own module
    # scope immediately above): construction is cheap (~0.65ms, confirmed
    # at Task 3.4.a), so there's no real resource worth preserving across
    # tests the way a Qdrant client's real connection pool is -- clearing
    # before AND after means a test using this fixture never inherits a
    # stale instance from an earlier test, and never leaks its own into a
    # later one. Deliberately NOT `autouse=True`, matching every other
    # such fixture's own reasoning: opt in explicitly via `pytestmark =
    # pytest.mark.usefixtures("close_chat_model_cache_between_tests")`.
    chat_model.get_chat_model.cache_clear()
    yield
    chat_model.get_chat_model.cache_clear()


def _exception_chain(exc):
    # Walks exc and every exception reachable via __cause__/__context__, so
    # a leak hidden anywhere in the chain (not just on exc itself) is found.
    # Shared by test_config.py (database_url/qdrant_api_key/jwt_signing_key
    # leak tests) and test_tokens.py's leak test (duplication check after
    # 1.4.a/b) via assert_secret_not_in_exception_chain() below.
    seen = []
    current = exc
    while current is not None and current not in seen:
        seen.append(current)
        current = current.__cause__ or current.__context__
    return seen


def assert_secret_not_in_exception_chain(exc, *secrets):
    # Shared by test_config.py's three leak tests and test_tokens.py's leak
    # test (duplication check after 1.4.a/b): every one of them walked
    # _exception_chain() and asserted each secret absent from str/repr/args
    # of every link, spelled out identically at each call site. Does not
    # cover test_qdrant.py's two leak tests, which separately check a
    # rendered traceback -- a surface this helper doesn't touch.
    for link in _exception_chain(exc):
        for secret in secrets:
            assert secret not in str(link)
            assert secret not in repr(link)
            assert secret not in str(link.args)


def fresh_import(module_name):
    # A fresh import into a new module object (not importlib.reload, which
    # mutates the module dict a test file's own `from x import y` binding
    # still points into, breaking later isinstance/pytest.raises checks).
    # Must not raise despite an empty environment.
    #
    # [SECURITY-adjacent, a REAL bug found live, not assumed]: both
    # existing callers (test_config.py's own test_module_imports_
    # cleanly_with_empty_environment, test_main.py's own test_importing_
    # app_main_succeeds_with_empty_environment) discard this function's
    # own return value -- neither needs `sys.modules[module_name]` to
    # stay pointed at the fresh object afterward. Before this fix, it
    # DID stay pointed at it, PERMANENTLY, for the rest of the pytest
    # session -- confirmed live with a real, reproducible full-suite
    # failure: `scripts/retrieval_eval.py`'s own main() does a
    # deliberate, function-LOCAL `from app.config import get_settings`
    # (required by its own "must stay importable with no env at all"
    # design) -- after test_config.py's own fresh-import test ran,
    # EVERY later function-local `from app.config import ...` anywhere
    # in the suite bound to this now-orphaned module instead of the
    # original, including its own SEPARATE get_settings() -- @lru_cache
    # singleton, never cleared by this project's own autouse `_isolated_
    # env` fixture, which only ever clears the ORIGINAL module's
    # function (bound at conftest.py's own top-level import, well
    # before any fresh_import() call could run). Whichever test
    # happened to be the FIRST later caller to populate that orphaned
    # cache -- with whatever env was active at that moment -- silently
    # fixed its own wrong Settings value in place for the REST of the
    # session. Live proof, not reasoned about: a direct `Settings()`
    # construction and `os.environ["QDRANT_URL"]` both read correctly
    # at the point of failure; only the orphaned module's own
    # `get_settings()` read stale.
    #
    # Fixed by restoring `sys.modules[module_name]` to whatever it was
    # before this call, immediately after the fresh import completes --
    # the fresh module object is still returned (and still a genuinely
    # different object from the original, still proving "a clean
    # import succeeds" -- this function's only real contract), but it
    # no longer lingers as the live entry in `sys.modules` for anything
    # else in the session to stumble into later.
    previous = sys.modules.get(module_name)
    sys.modules.pop(module_name, None)
    try:
        return importlib.import_module(module_name)
    finally:
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous


# Shared "expected schema" facts for Task 1.2.b's/1.2.c's tests (moved here
# in the duplication check after 1.2.a/b/c): test_models.py checks these
# against SQLAlchemy's Python metadata (no database), test_alembic.py checks
# the same facts against a live database via sa.inspect() after a real
# migration -- two different, real proofs, but the same expected data, so a
# schema change only needs updating it once.
EXPECTED_TABLES = {
    "tenants",
    "site_keys",
    "visitors",
    "conversations",
    "plans",
    "sources",
    "documents",
    "db_connections",
    "jobs",
    "verified_domains",
    "audit_log",
    "usage_events",
    "email_log",
    "escalations",
    # Step 6.1: "user" (singular, fastapi-users' own convention, kept
    # deliberately -- see app/dashboard/models.py's own header comment)
    # plus this project's own memberships/dashboard_sessions.
    "user",
    "memberships",
    "dashboard_sessions",
}

EXPECTED_PK_COLUMNS = {
    "tenants": "id",
    "site_keys": "id",
    "visitors": "vid",
    "conversations": "cid",
    "plans": "id",
    "sources": "id",
    "documents": "id",
    "db_connections": "id",
    "jobs": "id",
    "verified_domains": "id",
    "audit_log": "id",
    "usage_events": "id",
    "email_log": "id",
    "escalations": "id",
    # Step 6.1: "memberships" is this project's own table (a real
    # gen_random_uuid() PK, matching every table above). "user" and
    # "dashboard_sessions" are deliberately NOT listed here -- see
    # test_models.py's own test_dashboard_pk_shapes_deviate_deliberately
    # for why (fastapi-users' own User.id uses a client-side Python
    # default, not a server-side one; DashboardSession's own PK is its
    # token column, not a UUID at all).
    "memberships": "id",
}

# tenant_id columns cascade from tenants (PROJECT_SPEC.md decision: DB-level
# ON DELETE CASCADE from tenants down to site_keys/visitors/conversations).
# Task 2.1.a extends this with the four ingestion tables' own tenant_id
# columns, plus documents.source_id and jobs.source_id (both cascade from
# sources, not tenants -- the dict's value names the actual referenced
# table for each entry, not always "tenants").
CASCADE_FK_COLUMNS = {
    ("site_keys", "tenant_id"): "tenants",
    ("visitors", "tenant_id"): "tenants",
    ("conversations", "tenant_id"): "tenants",
    ("sources", "tenant_id"): "tenants",
    ("documents", "tenant_id"): "tenants",
    ("db_connections", "tenant_id"): "tenants",
    ("jobs", "tenant_id"): "tenants",
    ("documents", "source_id"): "sources",
    ("jobs", "source_id"): "sources",
    # Task 2.2.a: audit_log has no tenant_id column at all (deliberately
    # not tenant-scoped -- see app/ingest/models.py's AuditLog class), so
    # it adds no entry here.
    ("verified_domains", "tenant_id"): "tenants",
    # Task 4.3: usage_events/email_log both follow the identical,
    # already-established tenant_id-cascades-from-tenants rule -- a
    # deleted tenant's own usage/billing history has no reason to
    # survive the tenant row it describes.
    ("usage_events", "tenant_id"): "tenants",
    ("email_log", "tenant_id"): "tenants",
    # Step 4.4: escalations.tenant_id -- the identical already-established
    # rule (docs/SPEC.md §7: "tenant deletion cascades").
    ("escalations", "tenant_id"): "tenants",
    # Step 6.1: memberships cascades from BOTH its parents -- deleting
    # either the user or the tenant removes the membership row linking
    # them; dashboard_sessions cascades from the user (a deleted account
    # cannot be left with live sessions).
    ("memberships", "tenant_id"): "tenants",
    ("memberships", "user_id"): "user",
    ("dashboard_sessions", "user_id"): "user",
}

# Step 4.4: escalations.conversation_id is the FIRST column in this
# project with ondelete="SET NULL" -- neither CASCADE (deleting the
# conversation must not silently shorten the escalation's own LONGER,
# separately-configured retention window) nor the NO_ACTION default
# (which would make the conversation-retention sweep's own DELETE fail
# outright while any escalation still referenced it). See app/handoff/
# models.py's own `conversation_id` column comment for the full
# reasoning.
SET_NULL_FK_COLUMNS = {
    ("escalations", "conversation_id"): "conversations",
}

# site_key_id (visitors) and vid (conversations) are a separate edge the
# cascade decision never covered, and no site-key/visitor-deletion feature
# exists yet to require cascading them too, so they stay at the default (no
# ondelete -- NO ACTION), not cascade.
NO_ACTION_FK_COLUMNS = {
    ("visitors", "site_key_id"),
    ("conversations", "vid"),
    # Task 4.3: usage_events.vid/conversation_id -- the identical
    # "no cascade feature exists yet" reasoning as the two entries
    # above, not a new decision.
    ("usage_events", "vid"),
    ("usage_events", "conversation_id"),
    # Step 4.4: escalations.vid -- the identical "no cascade feature
    # exists yet" reasoning as the entries above.
    ("escalations", "vid"),
}

# Closed-vocabulary CHECK constraints, keyed by table (Task 1.4.d adds
# tenants; site_keys' own was already checked ad hoc before this -- moved
# here so both share one dict-driven test instead of two near-identical
# ones, the same convention as CASCADE_FK_COLUMNS above). Each table maps
# to a LIST of (constraint_name, allowed_values) pairs, not a single pair
# directly -- generalized at Task 2.2.b, when verified_domains became the
# first table needing two independent closed vocabularies (method, status)
# at once; every pre-existing table here still has exactly one, now as a
# one-item list.
EXPECTED_STATUS_CHECK_CONSTRAINTS = {
    "site_keys": [("ck_site_keys_status", frozenset({"draft", "live", "suspended"}))],
    "tenants": [("ck_tenants_status", frozenset({"active", "suspended"}))],
    # Task 2.1.a: sources.type is docs/SPEC.md §5.1's own closed, spec-fixed
    # four-value adapter vocabulary; jobs.status is this project's own
    # closed job-lifecycle vocabulary (see app/ingest/models.py's Job class
    # for the full reasoning, including why there is no fifth "retrying"
    # or "cancelled" state).
    "sources": [("ck_sources_type", frozenset({"urls", "crawl", "upload", "database"}))],
    "jobs": [("ck_jobs_status", frozenset({"pending", "running", "succeeded", "failed"}))],
    # Task 2.3.e: file/meta_tag restored alongside dns, now that Step 2.3's
    # own SSRF guard exists for them to reuse (schema only -- the
    # verification LOGIC for either method is still unbuilt).
    "verified_domains": [
        ("ck_verified_domains_method", frozenset({"dns", "file", "meta_tag"})),
        ("ck_verified_domains_status", frozenset({"pending", "verified", "revoked"})),
    ],
    # Task 2.4.a: pending (fetched, not yet extracted) / extracted (text/
    # chunks exist) / failed (extraction raised) -- see app/ingest/
    # models.py's Document class for the full vocabulary reasoning.
    "documents": [("ck_documents_status", frozenset({"pending", "extracted", "failed"}))],
    # Task 4.3: email_log's own two independent closed vocabularies
    # (status, threshold) -- see app/plans/models.py's EmailLog class
    # for the full reasoning.
    "email_log": [
        ("ck_email_log_status", frozenset({"sent", "failed", "skipped_not_configured"})),
        ("ck_email_log_threshold", frozenset({"80", "100"})),
    ],
    # Step 6.1: v1 is single-owner only (§11.3 kickoff decision #3) -- a
    # one-value closed vocabulary, same treatment as every one above.
    "memberships": [("ck_memberships_role", frozenset({"owner"}))],
}

# Composite unique indexes, keyed by table -- same (name, value) shape as
# EXPECTED_STATUS_CHECK_CONSTRAINTS above (duplication check after
# 1.4.c/d/e: originally a one-off flat dict, reshaped to match once a
# sibling constant of the same kind existed to be consistent with). Task
# 1.4.d's entry supports 1.4.e's "find a visitor by their secret within one
# site key" lookup -- see app/tenancy/models.py's Visitor.__table_args__
# for why it is scoped to (site_key_id, secret_hash) together, not
# secret_hash alone.
EXPECTED_UNIQUE_INDEXES = {
    "visitors": ("ix_visitors_site_key_id_secret_hash", ["site_key_id", "secret_hash"]),
}


@pytest.fixture
async def reset_test_database(monkeypatch) -> AsyncIterator[str]:
    # Shared across test_tenancy_repository.py (1.2.d), test_session.py
    # (1.4.g) and test_dependencies.py (1.4.h) (duplication check after
    # 1.4.f/g/h): all three repeated this exact require_test_database() ->
    # set_valid_env() -> engine cache_clear() -> drop_all/create_all ->
    # ... -> dispose()/cache_clear() scaffold, each layering its own seed
    # data on top. This fixture does only the shared part -- a fresh, empty
    # real-database schema, with DATABASE_URL pointed at it -- and yields
    # the database_url a caller might still need; each test file's own
    # fixture depends on this one and adds its own seed rows.
    # [SECURITY] L2 (Phase 7 role split, 2026-10-18): schema setup (DDL)
    # runs as the OWNER role; DATABASE_URL (what application code under
    # test actually connects with, via get_engine()) is pointed at the
    # APP role instead -- so every test using this fixture genuinely
    # exercises least privilege, the same way the real api/worker
    # processes do. Tables created below (create_all, as OWNER) inherit
    # the APP role's grants automatically via ALTER DEFAULT PRIVILEGES
    # (ops/deploy/provision_db_roles.py), which test-db's own provisioning
    # step (Makefile's test-db target) has already set up before this
    # fixture ever runs.
    owner_url = require_test_database()
    app_url = require_test_database_app()
    set_valid_env(monkeypatch, VALID_ENV, DATABASE_URL=app_url, DATABASE_URL_MIGRATE=owner_url)
    db.get_engine.cache_clear()
    db._session_factory.cache_clear()

    sync_engine = sa.create_engine(owner_url, poolclass=sa.pool.NullPool)
    with sync_engine.begin() as connection:
        # Task 2.1.a: db_connections.encrypted_credentials needs pgcrypto's
        # pgp_sym_encrypt/pgp_sym_decrypt. Extensions are database-level,
        # not touched by drop_all/create_all below (those only affect
        # tables), so a test-db container that has never had this test
        # run against it needs this explicitly -- confirmed live: dropping
        # the extension and re-running test_ingest_repository.py fails
        # with "function pgp_sym_decrypt(...) does not exist" without
        # this. 2.1.b's own real migration must do the same
        # (CREATE EXTENSION IF NOT EXISTS pgcrypto), not just this test
        # fixture -- see PROJECT_SPEC.md's Step 2.1.a decision entry.
        connection.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    db.Base.metadata.drop_all(sync_engine)
    db.Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    # Yields the APP url, not the OWNER one: every existing caller uses
    # this to build a subprocess env (seed_cli, worker) or a second
    # set_valid_env() override, matching DATABASE_URL's own real-runtime
    # meaning (see this fixture's own header comment above) -- a caller
    # that genuinely needs schema-DDL access uses require_test_database()
    # directly, same as this fixture does.
    yield app_url

    await db.get_engine().dispose()
    db.get_engine.cache_clear()
    db._session_factory.cache_clear()


async def http_client(app) -> AsyncClient:
    # Shared across test_main.py, test_session.py (1.4.g) and
    # test_dependencies.py (1.4.h) (duplication check after 1.4.f/g/h): the
    # same two-line httpx.AsyncClient/ASGITransport construction, repeated
    # byte-identically in all three.
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def assert_no_socket_connections(monkeypatch) -> None:
    # Duplication check after 2.3.a/b/c: test_safe_fetch.py had this exact
    # shape -- monkeypatch socket.socket.connect to raise loudly if ever
    # called -- at two separate call sites (zero-connection-attempt proofs
    # for a validation-rejected host and a literal unsafe IP). 2.3.d's own
    # already-approved redirect-chain hostile test ("caught at that
    # specific hop") needs the identical proof shape again: confirming the
    # unsafe hop's own target was never connected to. Patches the lowest
    # practical level (beneath httpx/httpcore entirely), so the proof is
    # implementation-agnostic -- it would catch a regression regardless of
    # which library eventually handles the connection.
    def _fail_if_connected(self, *args, **kwargs):
        raise AssertionError(
            "socket.socket.connect() was called -- a real connection was attempted"
        )

    monkeypatch.setattr(socket.socket, "connect", _fail_if_connected)


def assert_rejected_by_integrity_error(engine, sql: str, params: dict | None = None) -> None:
    # Duplication check after the Step 2.4 planning task and 2.4.a:
    # test_alembic.py had this exact "connect, pytest.raises(IntegrityError),
    # execute, commit" shape at 8 separate call sites (site_keys/tenants/
    # visitors/verified_domains/documents constraint-rejection proofs) --
    # well past this project's own established 3rd-occurrence threshold.
    with engine.connect() as connection, pytest.raises(IntegrityError):
        connection.execute(sa.text(sql), params)
        connection.commit()


@contextmanager
def local_http_server(handler_cls: type[http.server.BaseHTTPRequestHandler]) -> Iterator[int]:
    # Duplication check after 2.3.a/b/c: test_safe_fetch.py's own
    # _slow_large_server fixture had this exact HTTPServer-on-an-
    # ephemeral-port + background-thread + shutdown/join lifecycle inline,
    # specific only in its choice of handler class -- 2.3.d's own
    # redirect-chain tests need the identical lifecycle with a DIFFERENT
    # handler (one issuing 3xx responses, not a slow large body), so only
    # this generic shape is shared; each caller still defines its own
    # handler_cls, matching this project's own precedent of sharing the
    # repeated scaffold while keeping each caller's own differing logic
    # separate (e.g. db_session() above, check_call_allowlist()).
    server = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        thread.join()


class _IPv6HTTPServer(http.server.HTTPServer):
    address_family = socket.AF_INET6


@contextmanager
def local_ipv6_http_server(
    handler_cls: type[http.server.BaseHTTPRequestHandler],
) -> Iterator[int]:
    # Duplication check after 2.6.d/2.6.e/2.6.f: this exact IPv6-bound
    # variant of local_http_server() above existed as two separate,
    # byte-identical local copies -- test_web_adapter.py (2.6.d's own
    # C1 redirect-to-unverified-domain fix, the original) and
    # test_job_handlers.py (2.6.e, a confirmed second occurrence) --
    # extracted here ahead of a plausible third call site in Step 2.7,
    # rather than waiting for one. "::1" is a real, independently
    # bindable IPv6 loopback address on both Linux and macOS with zero
    # special setup, confirmed live at 2.6.d: a second IPv4 loopback
    # alias fails there with "Can't assign requested address" -- this is
    # the only portable way to stand up a second, genuinely different
    # "host" for a test.
    server = _IPv6HTTPServer(("::1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        thread.join()


def fake_is_unsafe_except_loopback(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    # Duplication check after 2.6.a/b/c: this exact two-line function
    # existed as three separate, byte-identical local copies --
    # test_safe_fetch.py (2.3.d, the original), test_domain_verification.py
    # (2.6.a, already flagged there as a second occurrence), and
    # test_web_adapter.py (2.6.c, the confirmed 3rd) -- past this
    # project's own established extraction threshold. 127.0.0.1 is
    # categorically unsafe by design (ip_safety.py), so it is treated as
    # safe FOR TEST PURPOSES ONLY here, letting a real local HTTP server
    # be used against SSRF-guarded primitives; every other address still
    # goes through the real, unmodified is_unsafe_destination_ip().
    if str(ip) == "127.0.0.1":
        return False
    return is_unsafe_destination_ip(ip)


def scripted_handler(routes: dict):
    # Duplication check after 2.6.a/b/c: the simple `path -> (status_code,
    # body_bytes)` handler factory existed as two separate, byte-identical
    # local copies -- test_domain_verification.py (2.6.a) and
    # test_web_adapter.py (2.6.c) -- past this project's own established
    # extraction threshold. test_safe_fetch.py's own _scripted_handler
    # stays local and separate: it supports a third element (custom
    # response headers, needed for its own redirect-chain Location-header
    # tests), a genuinely different shape, not duplication of this one.
    # routes is read fresh on EVERY request, so a caller can mutate the
    # dict after the server has already started (used by callers that
    # change what the SAME running server returns on a later request).
    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            status, body = routes.get(self.path, (404, b"not found"))
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return _Handler


def tracking_handler(routes: dict, hits: list, delay_seconds: float = 0):
    # Phase-2 close-out duplication check (2.10's own rule-9 phase-boundary
    # pass): a local variant of scripted_handler() above that ALSO records
    # every requested path -- needed by callers proving a specific path
    # was NEVER requested, not merely absent from the final result.
    # Existed as two separate copies before this extraction --
    # test_job_handlers.py's own `_tracking_handler()` (2.6.d/2.6.e/2.6.f,
    # itself already collapsed once from a third, delay-only variant) and
    # test_ingest_repository.py's own `_recording_handler()` (2.10.b) --
    # the second one a strictly-simpler subset of the first (`routes={}`,
    # no delay), written without cross-referencing it. Extracted here,
    # matching scripted_handler()'s own precedent of living in conftest.py
    # rather than staying duplicated per test file. `delay_seconds` gives
    # a caller proving mid-job progress (e.g. heartbeat writes during a
    # busy crawl) real, non-trivial elapsed time between requests; a
    # caller with no use for it (the hits-only, zero-network-activity
    # proof shape) passes `routes={}` and leaves it at its default 0.
    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            if delay_seconds:
                time.sleep(delay_seconds)
            status, body = routes.get(self.path, (404, b"not found"))
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return _Handler


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


async def assert_db_connection_credential_round_trip(
    tenant_id: uuid.UUID, credentials: dict[str, str], host: str = "round-trip-proof.internal"
) -> None:
    # Shared by test_ingest_repository.py and test_alembic.py (duplication
    # check after 2.1.a/b): both had a near-identical 12-13 line
    # seed-create-read-assert block proving a credential written through
    # IngestRepository.create_db_connection() reads back correctly,
    # unchanged, through get_decrypted_credentials() -- one shared helper
    # instead of two copies. Neither caller asserts on `host` itself, so
    # it stays an optional, defaulted parameter rather than something
    # every call site must repeat.
    async with db_session() as session:
        repo = IngestRepository(tenant_id=tenant_id, session=session)
        db_connection = await repo.create_db_connection(host=host, credentials=credentials)
        await session.commit()
        db_connection_id = db_connection.id

    async with db_session() as session:
        repo = IngestRepository(tenant_id=tenant_id, session=session)
        decrypted = await repo.get_decrypted_credentials(db_connection_id)

    assert decrypted == credentials


async def _verify_domain_directly(domain_id: uuid.UUID) -> None:
    # Duplication check after 2.9.a/b/c: "claim a domain, then force it to
    # 'verified' via a raw UPDATE, bypassing the real DNS/file/meta_tag
    # check" had grown to FIVE separate copies with no shared helper --
    # test_revocation_preserves_indexed_content.py, test_web_adapter.py,
    # and three inside test_job_handlers.py alone (two named helpers,
    # _seed_tenant_source_and_verified_domain()/_seed_tenant_crawl_source_
    # and_verified_domain(), plus a THIRD, inline copy the 2.9.c refresh-
    # now test added) -- each one's own comment already said "matching
    # the established precedent," so the duplication was known, just never
    # extracted, well past this project's own 3rd-occurrence threshold.
    #
    # Deliberately holds ONLY the claim-then-verify dance, decoupled from
    # any source/job creation: the 2.9.c call site needed exactly this,
    # and nothing more -- its own source is created and its own job is
    # triggered via request_refresh_now() afterward, not auto-enqueued the
    # way _seed_tenant_source_and_verified_domain()'s own bundled shape
    # would (which is the real reason that call site wrote an inline copy
    # in the first place rather than reusing an existing helper). Callers
    # that also want a source/job created still build their own bundled
    # helper around this one (see test_job_handlers.py's own two, still
    # bundled on purpose, each for its own adapter-specific config shape).
    async with db_session() as session:
        await session.execute(
            sa.text(
                "UPDATE verified_domains SET status = 'verified', verified_at = now() "
                "WHERE id = :id"
            ),
            {"id": domain_id},
        )
        await session.commit()


@pytest.fixture
async def _seeded_tenant(reset_test_database) -> AsyncIterator[uuid.UUID]:
    # Shared by test_queue.py (2.1.c) and test_worker.py (2.1.d/e)
    # (duplication check after 2.1.c/d/e, item A1): both had a
    # byte-identical one-tenant seed fixture, differing only in the
    # tenant's own `name` string (which neither file's tests ever assert
    # on) -- one shared fixture instead of two copies.
    tenant_id = uuid.uuid4()
    async with db_session() as session:
        session.add(Tenant(id=tenant_id, name="Seeded Tenant", status="active"))
        await session.commit()
    yield tenant_id


async def _fetch_job(job_id: uuid.UUID) -> Job:
    # Shared by test_queue.py and test_worker.py (duplication check after
    # 2.1.c/d/e, item A2): test_worker.py already had this as its own
    # local helper; test_queue.py repeated the same one-line
    # select-and-scalar_one() query inline seven times instead. One
    # shared helper, used by both.
    async with db_session() as session:
        return (await session.execute(sa.select(Job).where(Job.id == job_id))).scalar_one()


@asynccontextmanager
async def live_qdrant_collection(
    prefix: str = "probe",
) -> AsyncIterator[tuple[AsyncQdrantClient, str]]:
    # Shared across test_qdrant_collection.py and test_qdrant_isolation.py
    # (duplication check after 1.3.c/d/e): both files repeated this exact
    # require_test_qdrant() -> build_qdrant_client() -> unique
    # f"{prefix}_{uuid.uuid4()}" name -> try/finally(delete-if-exists,
    # close()) scaffold at ~15 call sites -- mirrors db_session()'s shape
    # above. Teardown always runs, including when the caller's own block
    # raises (contextlib guarantees this, same as a plain try/finally).
    url, key = require_test_qdrant()
    client = qdrant.build_qdrant_client(url, key)
    name = f"{prefix}_{uuid.uuid4()}"
    try:
        yield client, name
    finally:
        if await client.collection_exists(name):
            await client.delete_collection(name)
        await client.close()


@pytest.fixture
async def live_test_services(
    reset_test_database,
) -> AsyncIterator[tuple[str, AsyncQdrantClient, str]]:
    # Task 2.6.c: the second real caller the two-service watch-item marker
    # anticipated (recorded at the duplication check after 2.5.d/e/f) --
    # test_web_adapter.py needs BOTH real services together for most of
    # its own tests, matching test_revocation_preserves_indexed_content.py's
    # (2.5.f) own inline combination of reset_test_database + the identical
    # qdrant-collection lifecycle live_qdrant_collection() already has.
    # Extracted now, per that marker's own stated trigger ("if a second
    # real caller appears, extract a shared fixture then") -- NOT
    # retrofitting test_revocation_preserves_indexed_content.py's own
    # already-completed inline version in this task (rule 2); flagged here
    # as a candidate for the next duplication check instead.
    database_url = reset_test_database
    url, key = require_test_qdrant()
    client = qdrant.build_qdrant_client(url, key)
    name = f"probe_{uuid.uuid4()}"
    try:
        yield database_url, client, name
    finally:
        if await client.collection_exists(name):
            await client.delete_collection(name)
        await client.close()


class _FakeEmbeddingItem:
    def __init__(self, vector: list[float]) -> None:
        self.embedding = vector


class _FakeEmbeddingResponse:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.data = [_FakeEmbeddingItem(v) for v in vectors]


class _FakeEmbeddingsResource:
    async def create(self, *, model, input):  # noqa: A002 -- matches the real SDK's own param name
        return _FakeEmbeddingResponse([[0.0] * qdrant.DENSE_VECTOR_SIZE for _ in input])


class _FakeClient:
    def __init__(self) -> None:
        self.embeddings = _FakeEmbeddingsResource()


def patch_embed_dense(monkeypatch) -> None:
    # Shared across test_qdrant_writer.py (2.5.d/e) and test_revocation_
    # preserves_indexed_content.py (2.5.f) (duplication check after 2.5.d/
    # e/f): both files had their own byte-identical copy of this exact
    # stub. Monkeypatches embedding._get_client() directly (same mechanism
    # as test_embedding.py's own _patch_client(), not a shared import of
    # that file's own private helpers -- this one is simpler, with no
    # error-simulation support, since no caller of this shared version
    # needs that; test_qdrant_writer.py's own _patch_embed_dense_to_raise()
    # stays local there, it has exactly one caller). Returns a
    # deterministic fake dense vector -- the point-count/idempotency/
    # isolation properties every caller of this helper proves depend on
    # build_point_id()'s determinism and Qdrant's own upsert semantics,
    # never on the actual dense vector VALUES (2.5.a's own established
    # "verify live once, never a real OpenAI call in the committed suite"
    # rule), so a fixed stub exercises everything that matters.
    monkeypatch.setattr(embedding, "_get_client", lambda: _FakeClient())


class FakeAnswerToolChatModel(BaseChatModel):
    """Task 3.4.b: a configurable fake streaming the real `Answer` tool's
    own `tool_call_chunks` shape (`app/agent/graph.py`), for testing
    `_answer_node`'s own incremental-parsing logic with zero real API
    calls. `arg_chunks` is a list of raw JSON-fragment strings (in order)
    making up the eventual accumulated `args` string -- build one with
    `build_fake_answer_arg_chunks()` below rather than hand-splitting
    JSON. `fail_after` (if not `None`) raises a plain `RuntimeError` after
    that many chunks have already been yielded (`0` means "fail before
    any chunk at all" -- the case `.with_fallbacks()` is designed to
    catch; anything higher simulates a genuine mid-stream failure, the
    case it is confirmed live NOT to catch).

    Shared by `test_agent_graph_answer.py` and `test_agent_graph_
    retrieve.py` (both Task 3.4.b) -- both need a model that streams real
    `tool_call_chunks` matching the real `Answer` schema's own shape,
    not a generic text-only fake.
    """

    arg_chunks: list[str]
    fail_after: int | None = None
    # Task 4.3: when set, mirrors the REAL OpenAI streaming shape
    # confirmed live before building `_answer_node`'s own accumulator
    # (a one-off probe script against the real API, documented in
    # PROJECT_SPEC.md's Step 4.3 decision-log entry, never committed as
    # a paid-per-run test): the resolved model id and usage_metadata do
    # NOT arrive on the same chunk as the tool-call data, nor on a
    # "last" chunk with any tool_call_chunks of its own -- they arrive
    # on one or more EXTRA, separate chunks after every tool_call_chunks
    # fragment has already been yielded, each with empty tool_call_
    # chunks. `None` (the default) reproduces the OLDER shape every
    # pre-Task-4.3 test using this fake already relies on (no extra
    # chunk at all) -- existing call sites are unaffected.
    resolved_model_name: str | None = None
    usage: dict | None = None

    @property
    def _llm_type(self) -> str:
        return "fake-answer-tool"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise NotImplementedError("only streaming is exercised by this fake")

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        for index, fragment in enumerate(self.arg_chunks):
            if self.fail_after is not None and index >= self.fail_after:
                raise RuntimeError("FakeAnswerToolChatModel: scripted failure")
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "name": "Answer" if index == 0 else None,
                            "args": fragment,
                            "id": "call_1" if index == 0 else None,
                            "index": 0,
                        }
                    ],
                )
            )
        if self.resolved_model_name is not None:
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    response_metadata={"model_name": self.resolved_model_name},
                )
            )
        if self.usage is not None:
            yield ChatGenerationChunk(
                message=AIMessageChunk(content="", usage_metadata=self.usage)
            )

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        # The real _answer_node always calls .bind_tools([Answer],
        # tool_choice="Answer") on whatever get_chat_model() returns --
        # this fake just returns itself, already configured to stream the
        # Answer tool's own shape regardless of what's passed in.
        return self


def build_fake_answer_arg_chunks(
    *, can_answer: bool, used_chunk_ids: list[str], answer: str, split_every: int = 3
) -> list[str]:
    # Field order matches the real Answer schema exactly (app/agent/
    # graph.py) -- can_answer, answer, used_chunk_ids -- REORDERED at
    # Task 3.4.g (was can_answer/used_chunk_ids/answer) for measured
    # latency reasons, see that schema's own docstring. This helper's own
    # keyword-argument NAMES are unchanged (every existing caller passes
    # them by keyword, not position), only the payload dict's own
    # iteration order below, since that order is itself load-bearing
    # (confirmed live against real OpenAI) and every test relying on this
    # helper depends on it matching the real schema.
    payload = {"can_answer": can_answer, "answer": answer, "used_chunk_ids": used_chunk_ids}
    full = json.dumps(payload)
    return [full[i : i + split_every] for i in range(0, len(full), split_every)]
