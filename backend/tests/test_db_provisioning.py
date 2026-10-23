# backend/tests/test_db_provisioning.py
# [SECURITY] L2 follow-up (advisor review of eedad4e, 2026-10-18):
# app/db_provisioning.py's own production gate -- under APP_ENV=production
# it must refuse a placeholder/weak bootstrap, owner or app password
# BEFORE making any connection, and name only the variable. No database
# needed: provision() is replaced with a recorder, so "refused" provably
# means "never connected". The real provisioning run against a live
# Postgres is covered by the test-db stack's own provision-roles-test
# service and deploy/smoke-image.sh.
import pytest

from app import db_provisioning

_REALISTIC_ENV = {
    "PGHOST": "postgres",
    "POSTGRES_USER": "widgetplatform",
    "POSTGRES_DB": "widgetplatform",
    "POSTGRES_PASSWORD": "bootstrap-pw-for-provisioning-tests",
    "DATABASE_URL_MIGRATE": (
        "postgresql+psycopg://widgetplatform_owner:owner-pw-for-provisioning-tests"
        "@postgres:5432/widgetplatform"
    ),
    "DATABASE_URL": (
        "postgresql+psycopg://widgetplatform_app:app-pw-for-provisioning-tests"
        "@postgres:5432/widgetplatform"
    ),
}


@pytest.fixture
def provision_calls(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(db_provisioning, "provision", lambda **kwargs: calls.append(kwargs))
    for name in ("APP_ENV", *_REALISTIC_ENV):
        monkeypatch.delenv(name, raising=False)
    return calls


def _set_env(monkeypatch, **overrides):
    for name, value in {**_REALISTIC_ENV, **overrides}.items():
        monkeypatch.setenv(name, value)


def test_production_with_realistic_passwords_provisions(monkeypatch, provision_calls):
    _set_env(monkeypatch, APP_ENV="production")
    db_provisioning.main()
    assert len(provision_calls) == 1
    assert provision_calls[0]["owner_role"] == "widgetplatform_owner"


@pytest.mark.parametrize(
    ("env_var", "weak_value"),
    [
        ("POSTGRES_PASSWORD", "change-me"),
        ("POSTGRES_PASSWORD", "Change-Me-Please"),
        ("POSTGRES_PASSWORD", "test-bootstrap"),
        (
            "DATABASE_URL_MIGRATE",
            "postgresql+psycopg://widgetplatform_owner:change-me-owner@postgres:5432/widgetplatform",
        ),
        (
            "DATABASE_URL",
            "postgresql+psycopg://widgetplatform_app:smoke-app@postgres:5432/widgetplatform",
        ),
    ],
)
def test_production_refuses_a_weak_password_before_connecting(
    monkeypatch, capsys, provision_calls, env_var, weak_value
):
    _set_env(monkeypatch, APP_ENV="production", **{env_var: weak_value})
    with pytest.raises(SystemExit) as exc:
        db_provisioning.main()
    assert exc.value.code == 1
    assert provision_calls == []
    err = capsys.readouterr().err
    assert env_var in err
    assert weak_value not in err
    # Never the raw password either, for the DSN cases.
    for secret in ("change-me", "smoke-app", "test-bootstrap", "Change-Me-Please"):
        assert secret not in err


def test_development_and_test_accept_placeholder_passwords(monkeypatch, provision_calls):
    # The disposable test-db stack (provision-roles-test, smoke-image.sh)
    # provisions with "change-me" values on purpose.
    for app_env in ("development", "test"):
        _set_env(monkeypatch, APP_ENV=app_env, POSTGRES_PASSWORD="change-me")  # noqa: S106
        db_provisioning.main()
    assert len(provision_calls) == 2


@pytest.mark.parametrize("app_env", [None, ""])
def test_missing_app_env_fails_closed(monkeypatch, capsys, provision_calls, app_env):
    _set_env(monkeypatch, POSTGRES_PASSWORD="change-me")  # noqa: S106
    if app_env is not None:
        monkeypatch.setenv("APP_ENV", app_env)
    with pytest.raises(SystemExit):
        db_provisioning.main()
    assert provision_calls == []
    assert "APP_ENV must be set" in capsys.readouterr().err


@pytest.mark.parametrize("app_env", ["Production", "PRODUCTION", "prod", " production", "staging"])
def test_unknown_app_env_fails_closed_instead_of_skipping_the_gate(
    monkeypatch, capsys, provision_calls, app_env
):
    # Settings.app_env is a strict Literal, so api/worker/migrate would
    # refuse these too -- but only after provision-roles had already set
    # the weak passwords. Fail here, first.
    _set_env(monkeypatch, APP_ENV=app_env, POSTGRES_PASSWORD="change-me")  # noqa: S106
    with pytest.raises(SystemExit):
        db_provisioning.main()
    assert provision_calls == []
    assert "APP_ENV must be one of" in capsys.readouterr().err


def test_app_envs_match_settings_literal():
    import typing

    from app.config import Settings

    assert db_provisioning.APP_ENVS == typing.get_args(Settings.model_fields["app_env"].annotation)

