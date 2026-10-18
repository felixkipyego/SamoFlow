# backend/app/db_provisioning.py
# [SECURITY] L2 (Phase 7 role split, 2026-10-18): one-time, idempotent
# provisioning for the three-role Postgres model -- see docs/deploy/
# RUNBOOK.md for the operator-facing walkthrough (fresh volume and
# existing-volume commands) and PROJECT_SPEC.md for the design rationale.
#
# Three roles:
#   - BOOTSTRAP: the Postgres container's own superuser (POSTGRES_USER).
#     Used ONLY here, for one-time provisioning -- never by api, worker
#     or migrate at runtime.
#   - OWNER/MIGRATOR: owns the schema and every table, runs Alembic
#     (alembic/env.py) and the checkpointer's own setup() (app/agent/
#     checkpointer.py). Not a superuser, no CREATEROLE.
#   - APP (api + worker): no superuser, no DDL, no CREATE on the schema,
#     owns nothing. DML only -- audit_log's own narrower SELECT/INSERT-
#     only grant is deliberately NOT special-cased in this module (see
#     grant_app_role_privileges()'s own docstring): a dedicated Alembic
#     migration narrows it back down, the one place "audit_log is
#     special" is expressed, mirroring exactly where the OLD, inert
#     REVOKE already lived (backend/alembic/versions/610897dd7071_*.py).
#
# Deliberately synchronous (plain psycopg, not asyncio/app.cli.run_cli):
# this is a one-shot admin script, run once per environment via `docker
# compose run --rm provision-roles`, never part of the long-running api/
# worker event loop -- forcing asyncio here would buy nothing.
#
# Deliberately does NOT import app.config/get_settings: provisioning must
# run successfully using only the bootstrap/owner/app credentials it
# actually needs, never requiring every OTHER secret (OPENAI_API_KEY,
# JWT_SIGNING_KEY, ...) Settings() would otherwise demand just to
# construct -- the same "no heavy Settings import" discipline ops/deploy/
# check_env_secrets.py already established for a pre-Docker-image script.
from __future__ import annotations

import logging
import os
import sys
from urllib.parse import unquote, urlsplit

import psycopg
from psycopg import sql

logger = logging.getLogger(__name__)

# The two role names this entire L2 batch agrees on -- used here, and
# imported directly by backend/tests/test_alembic.py's own
# _reset_public_schema() (which must re-apply grant_app_role_privileges()
# after every DROP SCHEMA public CASCADE; see that function's own
# docstring for why) and by backend/tests/test_db_provisioning_guard.py's
# live grant-correctness guard, so the real name is never hand-copied a
# second time anywhere.
OWNER_ROLE = "widgetplatform_owner"
APP_ROLE = "widgetplatform_app"

# Every DML privilege APP gets on an ordinary table, by default, via
# ALTER DEFAULT PRIVILEGES below -- audit_log's own narrower grant is
# applied afterward, by a dedicated Alembic migration (see this module's
# own header comment).
_APP_TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE")


def _role_exists(conn: psycopg.Connection, role_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM pg_roles WHERE rolname = %s", (role_name,)
    ).fetchone()
    return row is not None


def _create_or_update_role(conn: psycopg.Connection, role_name: str, password: str) -> None:
    # Idempotent: CREATE on a fresh volume, ALTER (refreshing the
    # password/attributes) when the role already exists -- safe to rerun
    # against Felix's existing local demo volume with no data loss
    # (the per-table/sequence ALTER ... OWNER TO loop in provision()
    # below, not role creation, is what touches existing objects).
    # sql.Literal(password), not a %s bind parameter: confirmed live --
    # Postgres's own CREATE/ALTER ROLE grammar requires PASSWORD's own
    # value as a literal string constant (Sconst), not a query parameter
    # ($1) -- psycopg sends a parameterized PASSWORD as a real bind
    # parameter regardless of placeholder style, which Postgres then
    # rejects with a syntax error ("syntax error at or near '$1'").
    # sql.Literal() safely quotes the value INTO the statement text
    # itself (the correct, injection-safe mechanism for exactly this
    # case), never string-formatted by hand.
    if _role_exists(conn, role_name):
        conn.execute(
            sql.SQL(
                "ALTER ROLE {} WITH LOGIN PASSWORD {} NOSUPERUSER NOCREATEROLE NOCREATEDB"
            ).format(sql.Identifier(role_name), sql.Literal(password))
        )
        logger.info("role %s already exists; password/attributes refreshed", role_name)
    else:
        conn.execute(
            sql.SQL(
                "CREATE ROLE {} WITH LOGIN PASSWORD {} NOSUPERUSER NOCREATEROLE NOCREATEDB"
            ).format(sql.Identifier(role_name), sql.Literal(password))
        )
        logger.info("role %s created", role_name)


def _existing_tables(conn: psycopg.Connection) -> list[str]:
    return [
        row[0]
        for row in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()
    ]


def _existing_sequences(conn: psycopg.Connection) -> list[str]:
    return [
        row[0]
        for row in conn.execute(
            "SELECT sequencename FROM pg_sequences WHERE schemaname = 'public'"
        ).fetchall()
    ]


def grant_app_role_privileges(conn: psycopg.Connection, owner_role: str, app_role: str) -> None:
    """The part of provisioning that must be RE-RUN whenever schema public
    is dropped and recreated. `ALTER DEFAULT PRIVILEGES ... IN SCHEMA
    public` entries are keyed by the schema's own OID, not its name
    (confirmed against Postgres's own pg_default_acl catalog) -- a fresh
    `CREATE SCHEMA public` after a `DROP SCHEMA public CASCADE` gets a
    NEW oid, silently orphaning any default-privilege entries set up
    against the old one. Exposed as its own function specifically so
    backend/tests/test_alembic.py's own _reset_public_schema() can call
    it again after recreating the schema -- the identical "must stay
    correct across a schema-level reset" requirement a real fresh-volume
    provisioning run never has to worry about (CREATE SCHEMA public only
    ever happens once, at initdb time, on a real deployment).

    Also grants APP's own DML directly on every table/sequence that
    ALREADY exists (an existing volume may have objects older than this
    run; ALTER DEFAULT PRIVILEGES only covers FUTURE `CREATE TABLE`
    statements) -- harmless no-op loops on a fresh, empty schema.
    """
    conn.execute(sql.SQL("GRANT CREATE, USAGE ON SCHEMA public TO {}").format(
        sql.Identifier(owner_role)
    ))
    conn.execute(
        sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(app_role))
    )

    table_privileges = sql.SQL(", ").join(sql.SQL(p) for p in _APP_TABLE_PRIVILEGES)
    conn.execute(
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT {} ON TABLES TO {}"
        ).format(sql.Identifier(owner_role), table_privileges, sql.Identifier(app_role))
    )
    conn.execute(
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
            "GRANT USAGE, SELECT ON SEQUENCES TO {}"
        ).format(sql.Identifier(owner_role), sql.Identifier(app_role))
    )

    for table_name in _existing_tables(conn):
        conn.execute(
            sql.SQL("GRANT {} ON {} TO {}").format(
                table_privileges, sql.Identifier(table_name), sql.Identifier(app_role)
            )
        )

    for sequence_name in _existing_sequences(conn):
        conn.execute(
            sql.SQL("GRANT USAGE, SELECT ON SEQUENCE {} TO {}").format(
                sql.Identifier(sequence_name), sql.Identifier(app_role)
            )
        )


def provision(
    *,
    bootstrap_host: str,
    bootstrap_port: int,
    bootstrap_dbname: str,
    bootstrap_user: str,
    bootstrap_password: str,
    owner_role: str,
    owner_password: str,
    app_role: str,
    app_password: str,
) -> None:
    # A real, found-live hazard, not a speculative one: `_create_or_
    # update_role()` ALTERs an already-existing role to NOSUPERUSER
    # NOCREATEROLE NOCREATEDB -- if DATABASE_URL/DATABASE_URL_MIGRATE
    # ever named the SAME username as BOOTSTRAP (an operator's copy-
    # paste mistake while editing .env, e.g. forgetting to change
    # DATABASE_URL's own username away from POSTGRES_USER's), this
    # would silently strip BOOTSTRAP's own superuser status on this
    # exact run -- and BOOTSTRAP is the only role this script itself
    # connects as, so every FUTURE run would then also fail to connect
    # at all. Fail loudly before touching anything, instead.
    if owner_role == bootstrap_user or app_role == bootstrap_user:
        raise RuntimeError(
            "DATABASE_URL_MIGRATE's or DATABASE_URL's username must not be "
            "the same as POSTGRES_USER (the bootstrap superuser) -- this "
            "would strip the bootstrap role's own superuser status."
        )
    if owner_role == app_role:
        raise RuntimeError(
            "DATABASE_URL_MIGRATE's and DATABASE_URL's usernames must be "
            "different roles (owner vs. app)."
        )
    with psycopg.connect(
        host=bootstrap_host,
        port=bootstrap_port,
        dbname=bootstrap_dbname,
        user=bootstrap_user,
        password=bootstrap_password,
        autocommit=True,
    ) as conn:
        _create_or_update_role(conn, owner_role, owner_password)
        _create_or_update_role(conn, app_role, app_password)

        # [SECURITY] L2: OWNER "owns the schema" literally, not just via a
        # GRANT -- confirmed live: `public` starts out owned by the
        # pg_database_owner pseudo-role (Postgres 15+'s own default,
        # inherited through whichever role owns the DATABASE itself,
        # BOOTSTRAP here), and DROP SCHEMA requires real ownership, not
        # merely the CREATE privilege the GRANT below provides -- without
        # this, OWNER could create tables inside public but could never
        # run `DROP SCHEMA public CASCADE` itself (backend/tests/
        # test_alembic.py's own _reset_public_schema() does exactly this,
        # as OWNER, between every live migration test). Idempotent, and
        # only needs to run once ever: once OWNER owns the schema, any
        # DROP+CREATE SCHEMA public OWNER later runs as itself keeps
        # OWNER as the new schema's owner automatically (CREATE SCHEMA's
        # own default owner is the executing role).
        conn.execute(
            sql.SQL("ALTER SCHEMA public OWNER TO {}").format(sql.Identifier(owner_role))
        )
        # CREATE SCHEMA/CREATE TABLE both ALSO require CREATE on the
        # DATABASE itself (confirmed live: PUBLIC has no such privilege
        # by default on a modern Postgres, the identical "not granted to
        # PUBLIC by default" change this project already relies on at
        # the schema level, app/ingest/database_adapter.py's own read-
        # only-probe design) -- without this, OWNER owning schema public
        # is not enough on its own to let it `CREATE SCHEMA public` back
        # after a DROP (test_alembic.py's own _reset_public_schema()).
        conn.execute(
            sql.SQL("GRANT CREATE ON DATABASE {} TO {}").format(
                sql.Identifier(bootstrap_dbname), sql.Identifier(owner_role)
            )
        )

        # Created here, as BOOTSTRAP (a real superuser, so this always
        # succeeds regardless of whether pgcrypto happens to be a
        # "trusted" extension on this image) -- guarantees it exists
        # before OWNER's own redundant `CREATE EXTENSION IF NOT EXISTS
        # pgcrypto` (the already-shipped ef16e0a4ce7c migration) ever
        # runs, so that statement is always a true no-op at migrate time
        # rather than depending on OWNER's own extension-create rights.
        conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

        # No-op on a fresh volume (no tables exist yet); on Felix's
        # existing local demo volume, this moves ownership of every table
        # the OLD single shared role created to OWNER, with zero data
        # loss -- ALTER TABLE/SEQUENCE OWNER TO only ever changes a
        # catalog ownership column (pg_class.relowner), never touches row
        # data.
        #
        # Deliberately table-by-table (ALTER TABLE/SEQUENCE OWNER TO),
        # NOT a blanket `REASSIGN OWNED BY bootstrap_user TO owner_role`
        # -- confirmed live, not assumed: BOOTSTRAP (the Postgres
        # container's own POSTGRES_USER) also owns template0/template1/
        # the `postgres` database itself (initdb's own doing, not this
        # script's), and REASSIGN OWNED BY, run as a superuser, also
        # attempts to reassign those CLUSTER-SHARED objects -- which
        # Postgres unconditionally refuses ("cannot reassign ownership of
        # objects owned by role ... because they are required by the
        # database system"), aborting the whole provisioning run before
        # it ever reaches a single real table. BOOTSTRAP keeping those
        # system objects is correct and desired anyway; only TABLE/
        # SEQUENCE ownership inside the application's own schema needs
        # to move.
        for table_name in _existing_tables(conn):
            conn.execute(
                sql.SQL("ALTER TABLE {} OWNER TO {}").format(
                    sql.Identifier(table_name), sql.Identifier(owner_role)
                )
            )
        for sequence_name in _existing_sequences(conn):
            conn.execute(
                sql.SQL("ALTER SEQUENCE {} OWNER TO {}").format(
                    sql.Identifier(sequence_name), sql.Identifier(owner_role)
                )
            )

        grant_app_role_privileges(conn, owner_role, app_role)

    logger.info(
        "provisioning complete: %s (owner) and %s (app) are ready", owner_role, app_role
    )


def _bare_credentials(dsn: str, env_var: str) -> tuple[str, str]:
    parts = urlsplit(dsn)
    if not parts.username or parts.password is None:
        raise RuntimeError(f"{env_var} is missing a username or password")
    return parts.username, unquote(parts.password)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} must be set")
    return value


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        owner_user, owner_password = _bare_credentials(
            _require_env("DATABASE_URL_MIGRATE"), "DATABASE_URL_MIGRATE"
        )
        app_user, app_password = _bare_credentials(
            _require_env("DATABASE_URL"), "DATABASE_URL"
        )
        provision(
            bootstrap_host=_require_env("PGHOST"),
            bootstrap_port=int(os.environ.get("PGPORT", "5432")),
            bootstrap_dbname=_require_env("POSTGRES_DB"),
            bootstrap_user=_require_env("POSTGRES_USER"),
            bootstrap_password=_require_env("POSTGRES_PASSWORD"),
            owner_role=owner_user,
            owner_password=owner_password,
            app_role=app_user,
            app_password=app_password,
        )
    except (RuntimeError, psycopg.Error) as exc:
        # A clean one-line message, no traceback -- every value interpolated
        # above is a role/variable NAME, never a password (passwords are
        # always passed as bind parameters, never string-formatted into a
        # message), so printing str(exc) directly is safe.
        print(str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
