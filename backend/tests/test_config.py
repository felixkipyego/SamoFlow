# backend/tests/test_config.py
# Tests for Task 1.1.c's Settings module (backend/app/config.py).
import pytest
from pydantic import ValidationError

from app.config import Settings, SettingsError, get_settings
from tests.conftest import REQUIRED_VARS, TEST_PASSWORD, fresh_import, set_valid_env

VALID_ENV = {
    "APP_ENV": "development",
    "DATABASE_URL": "postgresql+psycopg://user:pw@localhost:5432/widgetplatform",
    "QDRANT_URL": "http://localhost:6333",
    "API_HOST": "127.0.0.1",
    "API_PORT": "8000",
}


def test_valid_environment_loads_with_correct_types_and_values(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    settings = Settings()
    assert settings.app_env == "development"
    assert settings.database_url_str() == VALID_ENV["DATABASE_URL"]
    assert settings.qdrant_url == "http://localhost:6333"
    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8000
    assert isinstance(settings.api_port, int)


@pytest.mark.parametrize("missing", REQUIRED_VARS)
def test_missing_required_variable_raises_and_names_field(monkeypatch, missing):
    set_valid_env(monkeypatch, VALID_ENV)
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
    set_valid_env(monkeypatch, VALID_ENV, **overrides)
    with pytest.raises(ValidationError):
        Settings()


def test_extra_unrelated_variables_are_ignored(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    monkeypatch.setenv("POSTGRES_PASSWORD", "irrelevant-to-settings")
    settings = Settings()  # must not raise
    assert not hasattr(settings, "postgres_password")


def test_repr_str_and_error_do_not_leak_password(monkeypatch):
    set_valid_env(
        monkeypatch,
        VALID_ENV,
        DATABASE_URL=f"postgresql+psycopg://user:{TEST_PASSWORD}@localhost/db",
    )
    settings = Settings()
    assert TEST_PASSWORD not in repr(settings)
    assert TEST_PASSWORD not in str(settings)

    set_valid_env(
        monkeypatch, VALID_ENV, DATABASE_URL=f"mysql://user:{TEST_PASSWORD}@localhost/db"
    )
    with pytest.raises(ValidationError) as exc_info:
        Settings()
    assert TEST_PASSWORD not in str(exc_info.value)


def test_get_settings_is_cached_and_resets_after_cache_clear(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    first = get_settings()
    second = get_settings()
    assert first is second
    get_settings.cache_clear()
    third = get_settings()
    assert third is not first


def test_settings_instance_is_frozen(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    settings = Settings()
    with pytest.raises(ValidationError):
        settings.api_port = 9999


def test_module_imports_cleanly_with_empty_environment(monkeypatch):
    for name in REQUIRED_VARS:
        monkeypatch.delenv(name, raising=False)
    fresh_import("app.config")  # must not raise despite empty env


def test_settings_env_file_is_never_configured():
    # Guarantees Settings is read only from process environment variables:
    # if env_file were ever set, Settings could silently pick up a stray
    # .env file instead of the orchestrator-provided environment.
    assert Settings.model_config.get("env_file") is None


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
    set_valid_env(
        monkeypatch, VALID_ENV, DATABASE_URL=f"mysql://user:{TEST_PASSWORD}@localhost/db"
    )
    with pytest.raises(SettingsError):
        get_settings()


def test_settings_error_chain_never_carries_the_password(monkeypatch):
    set_valid_env(
        monkeypatch, VALID_ENV, DATABASE_URL=f"mysql://user:{TEST_PASSWORD}@localhost/db"
    )
    with pytest.raises(SettingsError) as exc_info:
        get_settings()
    err = exc_info.value
    for link in _exception_chain(err):
        assert TEST_PASSWORD not in str(link)
        assert TEST_PASSWORD not in repr(link)
        assert TEST_PASSWORD not in str(link.args)
    assert err.__cause__ is None
    assert err.__context__ is None


def test_settings_error_message_still_names_the_field(monkeypatch):
    set_valid_env(
        monkeypatch, VALID_ENV, DATABASE_URL=f"mysql://user:{TEST_PASSWORD}@localhost/db"
    )
    with pytest.raises(SettingsError) as exc_info:
        get_settings()
    assert "database_url" in str(exc_info.value)


def test_database_url_without_a_database_name_rejected(monkeypatch):
    set_valid_env(
        monkeypatch,
        VALID_ENV,
        DATABASE_URL=f"postgresql+psycopg://user:{TEST_PASSWORD}@host",
    )
    with pytest.raises(ValidationError) as exc_info:
        Settings()
    assert TEST_PASSWORD not in str(exc_info.value)


def test_database_url_without_a_host_rejected(monkeypatch):
    set_valid_env(
        monkeypatch,
        VALID_ENV,
        DATABASE_URL=f"postgresql+psycopg://user:{TEST_PASSWORD}@/db",
    )
    with pytest.raises(ValidationError) as exc_info:
        Settings()
    assert TEST_PASSWORD not in str(exc_info.value)
