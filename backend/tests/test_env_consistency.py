# backend/tests/test_env_consistency.py
# Task 1.1.i: cross-checks .env.example's DATABASE_URL and QDRANT_URL
# against POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_DB and the service host
# names deploy/docker-compose.yml uses (resolves the TODO(1.1.i) note).
# NOTE: reuses test_env_example.py's parser instead of duplicating it
# (rule 4).
from urllib.parse import urlsplit

import pytest

from tests.test_env_example import _parse_env_example

# deploy/docker-compose.yml's service names: DATABASE_URL/QDRANT_URL must
# name these, not "localhost" or an IP, since api/worker only reach
# postgres/qdrant by Compose's internal DNS (service name resolution).
POSTGRES_SERVICE_NAME = "postgres"
QDRANT_SERVICE_NAME = "qdrant"


def test_database_url_matches_postgres_service_host_and_db():
    # [SECURITY] L2 (Phase 7 role split, 2026-10-18): DATABASE_URL is now
    # the APP role's own DSN, never POSTGRES_USER/PASSWORD (the BOOTSTRAP
    # superuser) -- only host/database still must line up with the same
    # postgres service and database POSTGRES_DB names.
    values = _parse_env_example()
    parts = urlsplit(values["DATABASE_URL"])

    assert parts.hostname == POSTGRES_SERVICE_NAME, (
        f"DATABASE_URL's host {parts.hostname!r} does not match the "
        f"postgres service name {POSTGRES_SERVICE_NAME!r}"
    )
    assert parts.path.lstrip("/") == values["POSTGRES_DB"], (
        f"DATABASE_URL's database {parts.path.lstrip('/')!r} does not "
        f"match POSTGRES_DB {values['POSTGRES_DB']!r}"
    )


def test_database_url_migrate_matches_postgres_owner_vars():
    # The OWNER/MIGRATOR role's own DSN must embed the SAME username/
    # password as the bare POSTGRES_OWNER_USER/PASSWORD ops/backup/
    # docker-compose.yml reads directly (a DSN cannot supply plain YAML
    # interpolation with a bare username/password).
    values = _parse_env_example()
    parts = urlsplit(values["DATABASE_URL_MIGRATE"])

    assert parts.username == values["POSTGRES_OWNER_USER"], (
        f"DATABASE_URL_MIGRATE's user {parts.username!r} does not match "
        f"POSTGRES_OWNER_USER {values['POSTGRES_OWNER_USER']!r}"
    )
    # No f-string of the actual password values: pytest's assertion
    # rewriting would otherwise print both on failure.
    if parts.password != values["POSTGRES_OWNER_PASSWORD"]:
        pytest.fail(
            "DATABASE_URL_MIGRATE's password does not match "
            "POSTGRES_OWNER_PASSWORD (values withheld; this message never "
            "prints them)."
        )
    assert parts.hostname == POSTGRES_SERVICE_NAME, (
        f"DATABASE_URL_MIGRATE's host {parts.hostname!r} does not match "
        f"the postgres service name {POSTGRES_SERVICE_NAME!r}"
    )
    assert parts.path.lstrip("/") == values["POSTGRES_DB"], (
        f"DATABASE_URL_MIGRATE's database {parts.path.lstrip('/')!r} does "
        f"not match POSTGRES_DB {values['POSTGRES_DB']!r}"
    )


def test_three_postgres_roles_are_genuinely_distinct():
    # [SECURITY] L2 regression guard: the whole point of the role split is
    # three DIFFERENT usernames -- a future edit that accidentally points
    # DATABASE_URL (APP) or DATABASE_URL_MIGRATE (OWNER) back at
    # POSTGRES_USER (BOOTSTRAP) would silently undo the split while every
    # other test here kept passing.
    values = _parse_env_example()
    app_user = urlsplit(values["DATABASE_URL"]).username
    owner_user = values["POSTGRES_OWNER_USER"]
    bootstrap_user = values["POSTGRES_USER"]
    assert len({app_user, owner_user, bootstrap_user}) == 3, (
        f"the three Postgres roles must be distinct usernames, got "
        f"app={app_user!r} owner={owner_user!r} bootstrap={bootstrap_user!r}"
    )


def test_qdrant_url_host_matches_qdrant_service_name():
    values = _parse_env_example()
    parts = urlsplit(values["QDRANT_URL"])
    assert parts.hostname == QDRANT_SERVICE_NAME, (
        f"QDRANT_URL's host {parts.hostname!r} does not match the qdrant "
        f"service name {QDRANT_SERVICE_NAME!r}"
    )
