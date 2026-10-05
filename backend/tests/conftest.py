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
import os
import socket
import sys
import threading
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import aclosing, asynccontextmanager, contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
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
from app.config import POSTGRES_SCHEME, Settings, get_settings
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
    "DATABASE_URL": "postgresql+psycopg://user:pw@localhost:5432/widgetplatform",
    "QDRANT_URL": "http://localhost:6333",
    "QDRANT_API_KEY": "test-qdrant-key",  # noqa: S105 (test fixture value, not a real secret)
    "JWT_SIGNING_KEY": "test-jwt-signing-key-at-least-32-chars",  # noqa: S105
    "DB_CONNECTION_ENCRYPTION_KEY": "test-db-connection-key-at-least-32-chars",  # noqa: S105
    "ADMIN_API_KEY": "test-admin-key-at-least-32-characters-long",  # noqa: S105
    "OPENAI_API_KEY": "test-openai-key",  # noqa: S105 (test fixture value, not a real secret)
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
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


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
}

# site_key_id (visitors) and vid (conversations) are a separate edge the
# cascade decision never covered, and no site-key/visitor-deletion feature
# exists yet to require cascading them too, so they stay at the default (no
# ondelete -- NO ACTION), not cascade.
NO_ACTION_FK_COLUMNS = {
    ("visitors", "site_key_id"),
    ("conversations", "vid"),
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
    database_url = require_test_database()
    set_valid_env(monkeypatch, VALID_ENV, DATABASE_URL=database_url)
    db.get_engine.cache_clear()
    db._session_factory.cache_clear()

    sync_engine = sa.create_engine(database_url, poolclass=sa.pool.NullPool)
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

    yield database_url

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
