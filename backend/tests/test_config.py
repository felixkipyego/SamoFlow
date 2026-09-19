# backend/tests/test_config.py
# Tests for Task 1.1.c's Settings module (backend/app/config.py).
import importlib
import sys

import pytest
from pydantic import ValidationError

from app.config import Settings, SettingsError, get_settings

REQUIRED_VARS = ["APP_ENV", "DATABASE_URL", "QDRANT_URL", "API_HOST", "API_PORT"]

VALID_ENV = {
    "APP_ENV": "development",
    "DATABASE_URL": "postgresql+psycopg://user:pw@localhost:5432/widgetplatform",
    "QDRANT_URL": "http://localhost:6333",
    "API_HOST": "127.0.0.1",
    "API_PORT": "8000",
}


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    # Every test starts with none of the five real variables present, and
    # with no cached Settings instance left over from another test.
    for name in REQUIRED_VARS:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _set_valid_env(monkeypatch, **overrides):
    for key, value in {**VALID_ENV, **overrides}.items():
        monkeypatch.setenv(key, value)


def test_valid_environment_loads_with_correct_types_and_values(monkeypatch):
    _set_valid_env(monkeypatch)
    settings = Settings()
    assert settings.app_env == "development"
    assert settings.database_url_str() == VALID_ENV["DATABASE_URL"]
    assert settings.qdrant_url == "http://localhost:6333"
    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8000
    assert isinstance(settings.api_port, int)


@pytest.mark.parametrize("missing", REQUIRED_VARS)
def test_missing_required_variable_raises_and_names_field(monkeypatch, missing):
    _set_valid_env(monkeypatch)
    monkeypatch.delenv(missing, raising=False)
    with pytest.raises(ValidationError) as exc_info:
        Settings()
    assert missing.lower() in str(exc_info.value)


@pytest.mark.parametrize(
    "overrides",
    [
        {"APP_ENV": "staging"},
        {"DATABASE_URL": "mysql://user:pw@localhost/db"},
        {"DATABASE_URL": "postgresql://user:pw@localhost/db"},
        {"DATABASE_URL": "not-a-url"},
        {"QDRANT_URL": "ftp://localhost:6333"},
        {"QDRANT_URL": "not-a-url"},
        {"API_PORT": "abc"},
        {"API_PORT": "0"},
        {"API_PORT": "70000"},
        {"API_HOST": ""},
    ],
)
def test_malformed_value_rejected(monkeypatch, overrides):
    _set_valid_env(monkeypatch, **overrides)
    with pytest.raises(ValidationError):
        Settings()


def test_extra_unrelated_variables_are_ignored(monkeypatch):
    _set_valid_env(monkeypatch)
    monkeypatch.setenv("POSTGRES_PASSWORD", "irrelevant-to-settings")
    settings = Settings()  # must not raise
    assert not hasattr(settings, "postgres_password")


def test_repr_str_and_error_do_not_leak_password(monkeypatch):
    password = "sup3r-secret-pw"  # noqa: S105 (test fixture value, not a real secret)
    _set_valid_env(
        monkeypatch, DATABASE_URL=f"postgresql+psycopg://user:{password}@localhost/db"
    )
    settings = Settings()
    assert password not in repr(settings)
    assert password not in str(settings)

    _set_valid_env(monkeypatch, DATABASE_URL=f"mysql://user:{password}@localhost/db")
    with pytest.raises(ValidationError) as exc_info:
        Settings()
    assert password not in str(exc_info.value)


def test_get_settings_is_cached_and_resets_after_cache_clear(monkeypatch):
    _set_valid_env(monkeypatch)
    first = get_settings()
    second = get_settings()
    assert first is second
    get_settings.cache_clear()
    third = get_settings()
    assert third is not first


def test_settings_instance_is_frozen(monkeypatch):
    _set_valid_env(monkeypatch)
    settings = Settings()
    with pytest.raises(ValidationError):
        settings.api_port = 9999


def test_module_imports_cleanly_with_empty_environment(monkeypatch):
    for name in REQUIRED_VARS:
        monkeypatch.delenv(name, raising=False)
    # A fresh import (not importlib.reload, which mutates the module dict
    # this file's own `from app.config import ...` names still point into,
    # breaking later isinstance/pytest.raises checks against SettingsError)
    # into a new module object, leaving the one already imported untouched.
    sys.modules.pop("app.config", None)
    importlib.import_module("app.config")  # must not raise despite empty env


def _exception_chain(exc):
    # Walks exc and every exception reachable via __cause__/__context__, so
    # a leak hidden anywhere in the chain (not just on exc itself) is found.
    seen = []
    current = exc
    while current is not None and current not in seen:
        seen.append(current)
        current = current.__cause__ or current.__context__
    return seen


def test_get_settings_raises_settings_error_on_malformed_database_url(monkeypatch):
    password = "sup3r-secret-pw"  # noqa: S105 (test fixture value, not a real secret)
    _set_valid_env(monkeypatch, DATABASE_URL=f"mysql://user:{password}@localhost/db")
    with pytest.raises(SettingsError):
        get_settings()


def test_settings_error_chain_never_carries_the_password(monkeypatch):
    password = "sup3r-secret-pw"  # noqa: S105 (test fixture value, not a real secret)
    _set_valid_env(monkeypatch, DATABASE_URL=f"mysql://user:{password}@localhost/db")
    with pytest.raises(SettingsError) as exc_info:
        get_settings()
    err = exc_info.value
    for link in _exception_chain(err):
        assert password not in str(link)
        assert password not in repr(link)
        assert password not in str(link.args)
    assert err.__cause__ is None
    assert err.__context__ is None


def test_settings_error_message_still_names_the_field(monkeypatch):
    password = "sup3r-secret-pw"  # noqa: S105 (test fixture value, not a real secret)
    _set_valid_env(monkeypatch, DATABASE_URL=f"mysql://user:{password}@localhost/db")
    with pytest.raises(SettingsError) as exc_info:
        get_settings()
    assert "database_url" in str(exc_info.value)


def test_get_settings_loads_a_valid_environment(monkeypatch):
    _set_valid_env(monkeypatch)
    settings = get_settings()
    assert settings.app_env == "development"
    assert settings.api_port == 8000


def test_database_url_without_a_database_name_rejected(monkeypatch):
    password = "sup3r-secret-pw"  # noqa: S105 (test fixture value, not a real secret)
    _set_valid_env(
        monkeypatch, DATABASE_URL=f"postgresql+psycopg://user:{password}@host"
    )
    with pytest.raises(ValidationError) as exc_info:
        Settings()
    assert password not in str(exc_info.value)


def test_database_url_without_a_host_rejected(monkeypatch):
    password = "sup3r-secret-pw"  # noqa: S105 (test fixture value, not a real secret)
    _set_valid_env(
        monkeypatch, DATABASE_URL=f"postgresql+psycopg://user:{password}@/db"
    )
    with pytest.raises(ValidationError) as exc_info:
        Settings()
    assert password not in str(exc_info.value)


def test_database_url_with_host_and_database_name_accepted(monkeypatch):
    _set_valid_env(
        monkeypatch,
        DATABASE_URL="postgresql+psycopg://user:pw@postgres:5432/widgetplatform",
    )
    settings = Settings()
    assert settings.database_url_str() == (
        "postgresql+psycopg://user:pw@postgres:5432/widgetplatform"
    )
