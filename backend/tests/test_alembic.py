# backend/tests/test_alembic.py
# Tests for Task 1.1.h's Alembic skeleton (backend/alembic.ini, backend/alembic/).
#
# Three groups:
#   - tests that need no database: "alembic history" as a subprocess proves
#     alembic.ini and the versions/ script directory are valid (it does NOT
#     execute env.py — see that test's own docstring); py_compile proves
#     env.py itself is at least syntactically valid Python, which "history"
#     does not; unit tests for require_test_database() (tests/conftest.py)
#     cover all of its behaviours with monkeypatch; a subprocess run with an
#     empty environment proves env.py fails cleanly (no traceback) via the
#     same SettingsError handling as app/worker.py;
#   - a leak-proof test that a connection failure in this file's own
#     database-touching helpers never renders the password in a captured,
#     locals-inclusive traceback (Task 1.1.h refinement);
#   - one integration test that needs a real Postgres reachable at
#     TEST_DATABASE_URL: it resets that database's public schema, runs
#     "python -m alembic upgrade head" as a subprocess twice, and asserts
#     neither run's output leaks the database password. Task 1.2.c extends
#     it with sqlalchemy.inspect() assertions proving the real migration
#     (backend/alembic/versions/541870ecc5d9_*.py) produces the schema
#     app/tenancy/models.py and app/plans/models.py describe -- a different,
#     real gap from test_models.py's own metadata-only checks (1.2.b), which
#     can prove the ORM definitions are internally consistent but not that
#     a migration actually applies them to a real database.
import os
import py_compile
import subprocess
import sys
import traceback
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app import db
from app.config import get_settings
from app.ingest.repository import IngestRepository
from app.plans import models as plans_models  # noqa: F401 (registers "plans" on Base.metadata)
from app.tenancy.models import Tenant
from tests.conftest import (
    CASCADE_FK_COLUMNS,
    EXPECTED_PK_COLUMNS,
    EXPECTED_STATUS_CHECK_CONSTRAINTS,
    EXPECTED_TABLES,
    EXPECTED_UNIQUE_INDEXES,
    db_session,
    minimal_subprocess_env,
    require_test_database,
)

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Harmless values for the Settings fields alembic/env.py does not use
# (get_settings() still requires all of them to be present).
_HARMLESS_ENV = {
    "APP_ENV": "test",
    "QDRANT_URL": "http://203.0.113.1:6333",
    "QDRANT_API_KEY": "harmless-test-key",  # noqa: S105 (test fixture value, not a real secret)
    "JWT_SIGNING_KEY": "harmless-jwt-signing-key-32-chars-ok",  # noqa: S105
    "DB_CONNECTION_ENCRYPTION_KEY": "harmless-db-connection-key-32-chars-ok",  # noqa: S105
    "API_HOST": "127.0.0.1",
    "API_PORT": "8000",
}


def _alembic_subprocess_env(database_url):
    # Explicit and minimal: only the Settings variables (DATABASE_URL set to
    # the test URL, harmless values for the rest) plus PATH and HOME
    # inherited from the parent. Nothing else from the parent's environment
    # reaches the subprocess, so a stray PGPASSWORD/PGHOST/PGUSER (or
    # anything else) in the caller's shell can never affect the connection
    # alembic/env.py makes.
    env = {**_HARMLESS_ENV, "DATABASE_URL": database_url}
    env.update(minimal_subprocess_env())
    return env


def _run_alembic(*args, engine):
    # database_url (with the real password) is a local only in this frame.
    # subprocess.run() reports failure through the returncode/captured
    # output the caller asserts on, not by raising with this frame on the
    # traceback, so it never needs the leak-avoidance _reset_public_schema/
    # _table_names below get.
    database_url = engine.url.render_as_string(hide_password=False)
    return subprocess.run(  # noqa: S603 (args are fixed test-internal strings, not user input)
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=_alembic_subprocess_env(database_url),
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_alembic_history_runs_with_no_database():
    # Proves alembic.ini and the versions/ script directory are valid: a
    # bogus, never-dialed URL still lets "history" load and list revisions.
    # It does NOT prove env.py is even valid Python: alembic only calls
    # env.py's run_env() for "history" when the "revision_environment" ini
    # option is set or --indicate-current is passed (neither is true here),
    # so this command never imports or executes env.py at all. See
    # test_env_py_compiles_cleanly below for that check.
    bogus_engine = sa.create_engine(
        "postgresql+psycopg://user:pw@203.0.113.1:5432/widgetplatform_test",
        poolclass=sa.pool.NullPool,
    )
    result = _run_alembic("history", engine=bogus_engine)
    assert result.returncode == 0, result.stderr


def test_env_py_compiles_cleanly():
    # Catches a syntax error in env.py on the common local/no-database path,
    # which "alembic history" (above) cannot: it never loads env.py.
    py_compile.compile(str(BACKEND_DIR / "alembic" / "env.py"), doraise=True)


def test_upgrade_head_with_empty_environment_exits_cleanly_without_traceback():
    env = {name: os.environ[name] for name in ("PATH", "HOME") if name in os.environ}
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    for field in (
        "app_env",
        "database_url",
        "qdrant_url",
        "qdrant_api_key",
        "api_host",
        "api_port",
    ):
        assert field in result.stderr


def test_require_test_database_unset_and_not_ci_skips_with_docker_hint(monkeypatch):
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("CI", raising=False)
    with pytest.raises(pytest.skip.Exception) as exc_info:
        require_test_database()
    assert "docker compose" in str(exc_info.value)


def test_require_test_database_unset_and_ci_true_fails(monkeypatch):
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("CI", "true")
    with pytest.raises(pytest.fail.Exception):
        require_test_database()


@pytest.mark.parametrize(
    "bad_url",
    [
        "postgresql+psycopg://user:pw@localhost:5432/widgetplatform",  # wrong suffix
        "postgresql://user:pw@localhost:5432/widgetplatform_test",  # wrong scheme
        "mysql://user:pw@localhost:3306/widgetplatform_test",  # wrong scheme
    ],
)
def test_require_test_database_set_but_unsafe_fails_never_skips(monkeypatch, bad_url):
    monkeypatch.delenv("TEST_DATABASE_ALLOWED_HOSTS", raising=False)
    monkeypatch.setenv("TEST_DATABASE_URL", bad_url)
    monkeypatch.delenv("CI", raising=False)
    with pytest.raises(pytest.fail.Exception):
        require_test_database()


def test_require_test_database_set_and_safe_returns_the_url(monkeypatch):
    monkeypatch.delenv("TEST_DATABASE_ALLOWED_HOSTS", raising=False)
    good_url = "postgresql+psycopg://user:pw@localhost:5432/widgetplatform_test"
    monkeypatch.setenv("TEST_DATABASE_URL", good_url)
    assert require_test_database() == good_url


def test_require_test_database_127_0_0_1_host_passes(monkeypatch):
    monkeypatch.delenv("TEST_DATABASE_ALLOWED_HOSTS", raising=False)
    good_url = "postgresql+psycopg://user:pw@127.0.0.1:5432/widgetplatform_test"
    monkeypatch.setenv("TEST_DATABASE_URL", good_url)
    assert require_test_database() == good_url


def test_require_test_database_remote_host_fails_even_with_a_correctly_named_database(monkeypatch):
    # The earlier review's example: a "_test"-named database on a host
    # that is neither localhost nor explicitly allowed must still fail.
    monkeypatch.delenv("TEST_DATABASE_ALLOWED_HOSTS", raising=False)
    monkeypatch.setenv(
        "TEST_DATABASE_URL",
        "postgresql+psycopg://prod_user:prod_pw@prod-db.internal.example.com:5432/legacy_customer_test",
    )
    with pytest.raises(pytest.fail.Exception) as exc_info:
        require_test_database()
    assert "prod_pw" not in str(exc_info.value)


def test_require_test_database_host_listed_in_allowed_hosts_env_var_passes(monkeypatch):
    monkeypatch.setenv("TEST_DATABASE_ALLOWED_HOSTS", "ci-postgres, other-host")
    good_url = "postgresql+psycopg://user:pw@ci-postgres:5432/widgetplatform_test"
    monkeypatch.setenv("TEST_DATABASE_URL", good_url)
    assert require_test_database() == good_url


def test_require_test_database_host_not_in_allowed_hosts_env_var_still_fails(monkeypatch):
    monkeypatch.setenv("TEST_DATABASE_ALLOWED_HOSTS", "ci-postgres")
    monkeypatch.setenv(
        "TEST_DATABASE_URL",
        "postgresql+psycopg://user:pw@some-other-host:5432/widgetplatform_test",
    )
    with pytest.raises(pytest.fail.Exception):
        require_test_database()


def test_alembic_subprocess_env_excludes_stray_libpq_variables(monkeypatch):
    monkeypatch.setenv("PGPASSWORD", "should-not-be-inherited")
    monkeypatch.setenv("PGHOST", "should-not-be-inherited")
    monkeypatch.setenv("PGUSER", "should-not-be-inherited")
    env = _alembic_subprocess_env(
        "postgresql+psycopg://user:pw@localhost:5432/widgetplatform_test"
    )
    assert "PGPASSWORD" not in env
    assert "PGHOST" not in env
    assert "PGUSER" not in env


def test_alembic_subprocess_env_has_exactly_the_eight_settings_variables_plus_path_and_home(
    monkeypatch,
):
    monkeypatch.setenv("SOME_OTHER_STRAY_VAR", "should-not-be-inherited")
    env = _alembic_subprocess_env(
        "postgresql+psycopg://user:pw@localhost:5432/widgetplatform_test"
    )
    expected_keys = {
        "APP_ENV",
        "DATABASE_URL",
        "QDRANT_URL",
        "QDRANT_API_KEY",
        "JWT_SIGNING_KEY",
        "DB_CONNECTION_ENCRYPTION_KEY",
        "API_HOST",
        "API_PORT",
    }
    expected_keys |= {name for name in ("PATH", "HOME") if name in os.environ}
    assert set(env) == expected_keys


def _reset_public_schema(engine):
    try:
        with engine.connect() as connection:
            connection.execute(sa.text("DROP SCHEMA public CASCADE"))
            connection.execute(sa.text("CREATE SCHEMA public"))
            connection.commit()
    except SQLAlchemyError as exc:
        # engine.url is a sqlalchemy.engine.URL object: its own repr/str
        # masks the password, and .host/.database are plain safe strings, so
        # nothing here can render the raw connection string. "from None"
        # drops the original exception's traceback (and the raw conninfo/
        # kwargs locals inside psycopg's own connect() frames) instead of
        # chaining it, so pytest never has a reason to render those frames.
        raise RuntimeError(
            f"could not reset the public schema on "
            f"{engine.url.host!r}/{engine.url.database!r}: {type(exc).__name__}"
        ) from None


def _table_names(engine):
    try:
        return sa.inspect(engine).get_table_names(schema="public")
    except SQLAlchemyError as exc:
        raise RuntimeError(
            f"could not list tables on "
            f"{engine.url.host!r}/{engine.url.database!r}: {type(exc).__name__}"
        ) from None


# Module-level, and never assigned to a local inside the test below: a name
# resolved via global lookup is not part of a function frame's f_locals, so
# keeping it out of any local variable is what makes the assertion below a
# real proof rather than a check that happens to pass by accident.
_DISTINCTIVE_PASSWORD = "distinctive-pw-456"  # noqa: S105 (test fixture value)


def test_reset_schema_failure_never_reveals_the_password_in_a_rendered_traceback():
    # Proves the leak-avoidance above actually works: an unreachable host
    # with a distinctive password must not appear even in a traceback
    # rendered with capture_locals=True, which is strictly more revealing
    # than pytest's own default (non "-l") traceback display.
    bad_engine = sa.create_engine(
        f"postgresql+psycopg://widgetplatform:{_DISTINCTIVE_PASSWORD}@127.0.0.1:1/widgetplatform_test",
        poolclass=sa.pool.NullPool,
    )
    try:
        with pytest.raises(RuntimeError) as exc_info:
            _reset_public_schema(bad_engine)
        rendered = "".join(
            traceback.TracebackException.from_exception(
                exc_info.value, capture_locals=True
            ).format()
        )
    finally:
        bad_engine.dispose()
    assert _DISTINCTIVE_PASSWORD not in rendered


@pytest.fixture
def _test_engine():
    # database_url (with the real password) is a local only in this fixture
    # function's own frame. Generator-fixture teardown ("engine.dispose()"
    # below) runs after the test body's own exception, if any, has already
    # been captured and reported by pytest as a separate step — so this
    # frame is never part of a test failure's rendered traceback.
    database_url = require_test_database()
    engine = sa.create_engine(database_url, poolclass=sa.pool.NullPool)
    yield engine
    engine.dispose()


def test_upgrade_head_is_idempotent_and_never_prints_the_password(_test_engine):
    engine = _test_engine
    real_password = engine.url.password
    assert real_password, "TEST_DATABASE_URL must include a password"

    _reset_public_schema(engine)

    first = _run_alembic("upgrade", "head", engine=engine)
    assert first.returncode == 0, first.stdout + first.stderr

    # Table list includes the real migration's 5 tables (Task 1.2.c), plus
    # 2.1.b's own four ingestion tables -- was just ["alembic_version"]
    # before any migration existed (1.1.h).
    assert set(_table_names(engine)) == {"alembic_version", *EXPECTED_TABLES}

    inspector = sa.inspect(engine)

    for table_name, pk_column in EXPECTED_PK_COLUMNS.items():
        assert inspector.get_pk_constraint(table_name)["constrained_columns"] == [pk_column], (
            f"{table_name}'s primary key column does not match the model"
        )

    for (table_name, column_name), referred_table in CASCADE_FK_COLUMNS.items():
        foreign_keys = inspector.get_foreign_keys(table_name)
        (fk,) = [fk for fk in foreign_keys if fk["constrained_columns"] == [column_name]]
        assert fk["referred_table"] == referred_table
        assert fk["options"].get("ondelete") == "CASCADE", (
            f"{table_name}.{column_name} must cascade from {referred_table}"
        )

    for table_name, (constraint_name, allowed_values) in EXPECTED_STATUS_CHECK_CONSTRAINTS.items():
        check_constraints = inspector.get_check_constraints(table_name)
        (status_check,) = [c for c in check_constraints if c["name"] == constraint_name]
        for status_value in allowed_values:
            assert status_value in status_check["sqltext"]

    for table_name, (index_name, columns) in EXPECTED_UNIQUE_INDEXES.items():
        indexes = inspector.get_indexes(table_name)
        (index,) = [i for i in indexes if i["name"] == index_name]
        assert index["unique"] is True
        assert index["column_names"] == columns

    # Task 2.1.b: db_connections.encrypted_credentials needs pgcrypto --
    # the real migration (not just the test fixture's own workaround,
    # 2.1.a's own bug) must be the thing that enables it.
    with engine.connect() as connection:
        installed_extensions = {
            row[0] for row in connection.execute(sa.text("SELECT extname FROM pg_extension"))
        }
    assert "pgcrypto" in installed_extensions

    second = _run_alembic("upgrade", "head", engine=engine)
    assert second.returncode == 0, second.stdout + second.stderr

    for result in (first, second):
        assert real_password not in result.stdout
        assert real_password not in result.stderr


def test_tenant_id_cannot_be_null(_test_engine):
    # A null tenant_id would silently escape tenant-scoped filtering later --
    # this proves Postgres itself rejects it, not just that the model/
    # migration declare NOT NULL (see test_models.py's static backstop).
    engine = _test_engine
    _reset_public_schema(engine)
    migrated = _run_alembic("upgrade", "head", engine=engine)
    assert migrated.returncode == 0, migrated.stdout + migrated.stderr

    with engine.connect() as connection, pytest.raises(IntegrityError):
        connection.execute(
            sa.text(
                "INSERT INTO site_keys (key, tenant_id, environment, status) "
                "VALUES ('pk_live_null_tenant_test', NULL, 'production', 'draft')"
            )
        )
        connection.commit()


def test_site_keys_key_uniqueness_is_enforced_by_the_database(_test_engine):
    # A duplicate site key is a real security problem (two rows sharing one
    # live secret token) -- this proves the unique index actually rejects a
    # second insert, not just that it's declared in the model.
    engine = _test_engine
    _reset_public_schema(engine)
    migrated = _run_alembic("upgrade", "head", engine=engine)
    assert migrated.returncode == 0, migrated.stdout + migrated.stderr

    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO tenants (name, status) VALUES ('tenant one', 'active')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO site_keys (key, tenant_id, environment, status) "
                "SELECT 'pk_live_duplicate_test', id, 'production', 'draft' FROM tenants"
            )
        )
        connection.commit()

    with engine.connect() as connection, pytest.raises(IntegrityError):
        connection.execute(
            sa.text(
                "INSERT INTO site_keys (key, tenant_id, environment, status) "
                "SELECT 'pk_live_duplicate_test', id, 'production', 'draft' FROM tenants"
            )
        )
        connection.commit()


def test_tenant_status_outside_the_allowed_vocabulary_is_rejected(_test_engine):
    # Task 1.4.d: proves ck_tenants_status actually rejects a bad value at
    # the database, not just that it's declared in the model/migration.
    engine = _test_engine
    _reset_public_schema(engine)
    migrated = _run_alembic("upgrade", "head", engine=engine)
    assert migrated.returncode == 0, migrated.stdout + migrated.stderr

    with engine.connect() as connection, pytest.raises(IntegrityError):
        connection.execute(
            sa.text("INSERT INTO tenants (name, status) VALUES ('bad tenant', 'pending')")
        )
        connection.commit()


def test_visitor_secret_hash_uniqueness_is_scoped_to_one_site_key(_test_engine):
    # Task 1.4.d: proves ix_visitors_site_key_id_secret_hash rejects a
    # duplicate (site_key_id, secret_hash) pair, while the SAME secret_hash
    # under a DIFFERENT site_key_id is accepted -- the uniqueness is scoped
    # to one site key, not a global constraint on secret_hash alone.
    engine = _test_engine
    _reset_public_schema(engine)
    migrated = _run_alembic("upgrade", "head", engine=engine)
    assert migrated.returncode == 0, migrated.stdout + migrated.stderr

    with engine.connect() as connection:
        connection.execute(
            sa.text("INSERT INTO tenants (name, status) VALUES ('tenant one', 'active')")
        )
        connection.execute(
            sa.text(
                "INSERT INTO site_keys (key, tenant_id, environment, status) "
                "SELECT 'pk_live_visitor_test_a', id, 'production', 'live' FROM tenants "
                "WHERE name = 'tenant one'"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO site_keys (key, tenant_id, environment, status) "
                "SELECT 'pk_live_visitor_test_b', id, 'production', 'live' FROM tenants "
                "WHERE name = 'tenant one'"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO visitors (tenant_id, site_key_id, secret_hash) "
                "SELECT t.id, sk.id, 'shared-hash-value' FROM tenants t "
                "JOIN site_keys sk ON sk.key = 'pk_live_visitor_test_a' "
                "WHERE t.name = 'tenant one'"
            )
        )
        connection.commit()

    # Same secret_hash, same site_key_id: rejected.
    with engine.connect() as connection, pytest.raises(IntegrityError):
        connection.execute(
            sa.text(
                "INSERT INTO visitors (tenant_id, site_key_id, secret_hash) "
                "SELECT t.id, sk.id, 'shared-hash-value' FROM tenants t "
                "JOIN site_keys sk ON sk.key = 'pk_live_visitor_test_a' "
                "WHERE t.name = 'tenant one'"
            )
        )
        connection.commit()

    # Same secret_hash, DIFFERENT site_key_id: accepted.
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO visitors (tenant_id, site_key_id, secret_hash) "
                "SELECT t.id, sk.id, 'shared-hash-value' FROM tenants t "
                "JOIN site_keys sk ON sk.key = 'pk_live_visitor_test_b' "
                "WHERE t.name = 'tenant one'"
            )
        )
        connection.commit()


def test_downgrade_from_head_removes_the_visitor_index_and_tenant_status_check(_test_engine):
    # C1 (duplication check after 1.4.c/d/e): establishes the pattern -- no
    # other migration in this project has an equivalent live downgrade test
    # yet, and retrofitting the others is not in scope here. Proves
    # dcd1f5b27bb7's downgrade() actually removes both objects from a real
    # database, not just that upgrade() creates them (already proven by
    # test_upgrade_head_is_idempotent_and_never_prints_the_password above).
    #
    # Upgrades to dcd1f5b27bb7 explicitly, not "head", and downgrades "-1"
    # from THERE -- found live at Task 2.1.b: this test originally
    # upgraded to "head" and downgraded "-1", which broke the moment
    # 2.1.b's own migration extended the revision chain (head's "-1" then
    # landed one migration too early, still inside dcd1f5b27bb7's own
    # state, never reaching what this test actually checks). Targeting
    # the specific revision by name makes this test's own boundary
    # independent of however many later migrations exist on top.
    engine = _test_engine
    _reset_public_schema(engine)
    migrated = _run_alembic("upgrade", "dcd1f5b27bb7", engine=engine)
    assert migrated.returncode == 0, migrated.stdout + migrated.stderr

    downgraded = _run_alembic("downgrade", "-1", engine=engine)
    assert downgraded.returncode == 0, downgraded.stdout + downgraded.stderr

    inspector = sa.inspect(engine)
    visitor_index_names = {i["name"] for i in inspector.get_indexes("visitors")}
    assert "ix_visitors_site_key_id_secret_hash" not in visitor_index_names

    tenant_check_names = {c["name"] for c in inspector.get_check_constraints("tenants")}
    assert "ck_tenants_status" not in tenant_check_names

    restored = _run_alembic("upgrade", "head", engine=engine)
    assert restored.returncode == 0, restored.stdout + restored.stderr


async def test_downgrade_from_head_removes_ingestion_tables_but_keeps_pgcrypto(
    _test_engine, monkeypatch
):
    # Task 2.1.b: the same C1 pattern as the visitor-index/tenant-check
    # downgrade test above, extended with the two additional proofs this
    # migration's own downgrade() must get right: pgcrypto survives (its
    # own comment in the migration file explains why it is never
    # dropped), and a real credential round trip through the actual
    # application code path (app.ingest.repository.IngestRepository) --
    # not just the test fixture's own separate CREATE EXTENSION
    # workaround -- still works correctly once the real migration
    # restores the schema. This is the genuine, deployment-level proof
    # that 2.1.a's own bug (encryption silently depending on whichever
    # test happened to run first) is fixed for real, not just in tests.
    engine = _test_engine
    _reset_public_schema(engine)
    migrated = _run_alembic("upgrade", "head", engine=engine)
    assert migrated.returncode == 0, migrated.stdout + migrated.stderr

    downgraded = _run_alembic("downgrade", "-1", engine=engine)
    assert downgraded.returncode == 0, downgraded.stdout + downgraded.stderr

    inspector = sa.inspect(engine)
    remaining_tables = set(inspector.get_table_names())
    assert not remaining_tables & {"sources", "documents", "db_connections", "jobs"}

    with engine.connect() as connection:
        installed_extensions = {
            row[0] for row in connection.execute(sa.text("SELECT extname FROM pg_extension"))
        }
    assert "pgcrypto" in installed_extensions

    restored = _run_alembic("upgrade", "head", engine=engine)
    assert restored.returncode == 0, restored.stdout + restored.stderr

    database_url = engine.url.render_as_string(hide_password=False)
    for key, value in _HARMLESS_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    db.get_engine.cache_clear()
    db._session_factory.cache_clear()

    tenant_id = uuid.uuid4()
    async with db_session() as session:
        session.add(Tenant(id=tenant_id, name="Downgrade-Upgrade Proof Tenant", status="active"))
        await session.commit()

    credentials = {"username": "dbuser", "password": "post-migration-proof-password"}
    async with db_session() as session:
        repo = IngestRepository(tenant_id=tenant_id, session=session)
        db_connection = await repo.create_db_connection(
            host="proof-db.internal", credentials=credentials
        )
        await session.commit()
        db_connection_id = db_connection.id

    async with db_session() as session:
        repo = IngestRepository(tenant_id=tenant_id, session=session)
        decrypted = await repo.get_decrypted_credentials(db_connection_id)

    assert decrypted == credentials

    await db.get_engine().dispose()
    db.get_engine.cache_clear()
    db._session_factory.cache_clear()
