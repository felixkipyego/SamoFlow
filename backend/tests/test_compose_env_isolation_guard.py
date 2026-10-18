# backend/tests/test_compose_env_isolation_guard.py
# [SECURITY] L2 follow-up (advisor review of commit c260f23, 2026-10-18):
# a REAL finding, not speculative -- deploy/docker-compose.yml's shared
# x-app-env anchor used to hand DATABASE_URL_MIGRATE (the OWNER role's
# own password) to api AND worker, not just migrate. A compromised api
# or worker process could read it straight out of its own environment
# and reconnect as the schema owner -- DDL, owner rights on audit_log,
# the original (now real) REVOKE bypassed -- largely defeating the role
# split this whole batch exists to build. "Unused, harmless" does not
# apply to a credential.
#
# Proven here via the REAL `docker compose config` merge (never a hand-
# rolled YAML re-parse, which could silently drift from what Compose
# itself actually resolves) -- for the base file alone AND the base +
# prod-overlay combination, since the overlay could in principle
# reintroduce a forbidden key even though, confirmed by reading it
# directly, docker-compose.prod.yml never touches `environment:` for any
# service at all today.
import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Every ${VAR:?...}/${VAR:-...} deploy/docker-compose.yml and
# docker-compose.prod.yml need to resolve at all -- harmless placeholders:
# `docker compose config` only ever resolves YAML, it never starts a
# container or makes a real connection with any of these.
_CONFIG_ENV = {
    "APP_ENV": "development",
    "DATABASE_URL": "postgresql+psycopg://widgetplatform_app:x@postgres:5432/widgetplatform",
    "DATABASE_URL_MIGRATE": "postgresql+psycopg://widgetplatform_owner:x@postgres:5432/widgetplatform",  # noqa: S105
    "POSTGRES_USER": "widgetplatform",
    "POSTGRES_PASSWORD": "x",  # noqa: S105
    "POSTGRES_DB": "widgetplatform",
    "POSTGRES_OWNER_USER": "widgetplatform_owner",
    "POSTGRES_OWNER_PASSWORD": "x",  # noqa: S105
    "QDRANT_URL": "http://qdrant:6333",
    "QDRANT_API_KEY": "x",  # noqa: S105
    "QDRANT_READ_ONLY_API_KEY": "x",  # noqa: S105
    "JWT_SIGNING_KEY": "x",  # noqa: S105
    "DB_CONNECTION_ENCRYPTION_KEY": "x",  # noqa: S105
    "OPENAI_API_KEY": "x",  # noqa: S105
    "API_HOST": "0.0.0.0",  # noqa: S104 (docker compose config never binds anything)
    "API_PORT": "8000",
    "DASHBOARD_HOSTNAME": "dashboard.example",
    "API_HOSTNAME": "api.example",
    "WIDGET_HOSTNAME": "widget.example",
    "CADDY_ACME_EMAIL": "ops@example.com",
}

# The credentials a compromised api/worker process must never be able to
# read out of its own environment: the OWNER role's DSN, and both
# cluster-admin (BOOTSTRAP) and schema-owner bare passwords.
_FORBIDDEN_ENV_KEYS: tuple[str, ...] = (
    "DATABASE_URL_MIGRATE",
    "POSTGRES_PASSWORD",
    "POSTGRES_OWNER_PASSWORD",
)

_BASE = ("deploy/docker-compose.yml",)
_BASE_PLUS_PROD = ("deploy/docker-compose.yml", "deploy/docker-compose.prod.yml")


def _resolved_services(*compose_files: str) -> dict:
    # --profile tools: migrate/provision-roles are profile-gated and
    # otherwise excluded entirely from `config`'s own output.
    args = ["docker", "compose"]
    for f in compose_files:
        args += ["-f", f]
    args += ["--profile", "tools", "config", "--format", "json"]
    result = subprocess.run(  # noqa: S603 (fixed args, no user input)
        args,
        cwd=REPO_ROOT,
        env={**os.environ, **_CONFIG_ENV},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"docker compose config failed: {result.stderr}"
    return json.loads(result.stdout)["services"]


@pytest.mark.parametrize("compose_files", [_BASE, _BASE_PLUS_PROD], ids=["base", "base+prod"])
def test_api_and_worker_never_receive_owner_or_bootstrap_credentials(compose_files):
    services = _resolved_services(*compose_files)
    for service_name in ("api", "worker"):
        env = services[service_name].get("environment", {})
        for forbidden in _FORBIDDEN_ENV_KEYS:
            assert forbidden not in env, (
                f"{service_name!r}'s resolved environment ({compose_files!r}) "
                f"contains {forbidden!r} -- api/worker must never receive the "
                "OWNER or BOOTSTRAP role's own credentials"
            )


@pytest.mark.parametrize("compose_files", [_BASE, _BASE_PLUS_PROD], ids=["base", "base+prod"])
def test_migrate_still_genuinely_receives_database_url_migrate(compose_files):
    # The guard above must not pass by accident (e.g. the variable
    # dropped from every service, not just api/worker) -- migrate itself
    # still needs it for real DDL (alembic, the checkpointer's setup()),
    # proving the split MOVED the credential rather than deleting it.
    services = _resolved_services(*compose_files)
    env = services["migrate"].get("environment", {})
    assert "DATABASE_URL_MIGRATE" in env
    assert "POSTGRES_PASSWORD" not in env
    assert "POSTGRES_OWNER_PASSWORD" not in env


def test_provision_roles_has_bootstrap_and_owner_credentials_by_design():
    # The one service that SHOULD have both -- it is the provisioning
    # tool itself, run explicitly by an operator, never network-facing
    # and never chained into migrate/api/worker. Asserted explicitly so
    # a future reader sees this is a deliberate exception, not a gap the
    # two tests above failed to catch.
    services = _resolved_services(*_BASE)
    env = services["provision-roles"].get("environment", {})
    assert "DATABASE_URL_MIGRATE" in env
    assert "POSTGRES_PASSWORD" in env
