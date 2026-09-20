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


def test_database_url_matches_postgres_service_vars():
    values = _parse_env_example()
    parts = urlsplit(values["DATABASE_URL"])

    assert parts.username == values["POSTGRES_USER"], (
        f"DATABASE_URL's user {parts.username!r} does not match "
        f"POSTGRES_USER {values['POSTGRES_USER']!r}"
    )
    # No f-string of the actual password values: pytest's assertion
    # rewriting would otherwise print both on failure.
    if parts.password != values["POSTGRES_PASSWORD"]:
        pytest.fail(
            "DATABASE_URL's password does not match POSTGRES_PASSWORD "
            "(values withheld; this message never prints them)."
        )
    assert parts.hostname == POSTGRES_SERVICE_NAME, (
        f"DATABASE_URL's host {parts.hostname!r} does not match the "
        f"postgres service name {POSTGRES_SERVICE_NAME!r}"
    )
    assert parts.path.lstrip("/") == values["POSTGRES_DB"], (
        f"DATABASE_URL's database {parts.path.lstrip('/')!r} does not "
        f"match POSTGRES_DB {values['POSTGRES_DB']!r}"
    )


def test_qdrant_url_host_matches_qdrant_service_name():
    values = _parse_env_example()
    parts = urlsplit(values["QDRANT_URL"])
    assert parts.hostname == QDRANT_SERVICE_NAME, (
        f"QDRANT_URL's host {parts.hostname!r} does not match the qdrant "
        f"service name {QDRANT_SERVICE_NAME!r}"
    )
