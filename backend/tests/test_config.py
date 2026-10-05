# backend/tests/test_config.py
# Tests for Task 1.1.c's Settings module (backend/app/config.py).
import pytest
from pydantic import ValidationError

from app.config import Settings, SettingsError, get_settings
from tests.conftest import (
    ALL_SETTINGS_VARS,
    REQUIRED_VARS,
    TEST_PASSWORD,
    VALID_ENV,
    assert_secret_not_in_exception_chain,
    fresh_import,
    set_valid_env,
)


def test_valid_environment_loads_with_correct_types_and_values(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    settings = Settings()
    assert settings.app_env == "development"
    assert settings.database_url_str() == VALID_ENV["DATABASE_URL"]
    assert settings.qdrant_url == "http://localhost:6333"
    assert settings.qdrant_api_key_str() == VALID_ENV["QDRANT_API_KEY"]
    assert settings.jwt_signing_key_str() == VALID_ENV["JWT_SIGNING_KEY"]
    assert settings.jwt_signing_key_previous_str() is None
    assert settings.admin_api_key_str() == VALID_ENV["ADMIN_API_KEY"]
    assert settings.openai_api_key_str() == VALID_ENV["OPENAI_API_KEY"]
    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8000
    assert isinstance(settings.api_port, int)
    assert settings.session_rate_limit_per_site_key == 10
    assert isinstance(settings.session_rate_limit_per_site_key, int)
    assert settings.session_rate_limit_per_site_key_window_seconds == 60
    assert settings.session_rate_limit_per_ip == 30
    assert settings.session_rate_limit_per_ip_window_seconds == 60


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
        {"QDRANT_API_KEY": ""},
        {"QDRANT_API_KEY": "has a space"},
        {"QDRANT_API_KEY": "has\ttab"},
        {"QDRANT_API_KEY": "has\nnewline"},
        {"JWT_SIGNING_KEY": ""},
        {"JWT_SIGNING_KEY": "short"},
        {"JWT_SIGNING_KEY": "a" * 31},  # one character short of the minimum
        {"JWT_SIGNING_KEY": "a" * 20 + " " + "a" * 20},
        {"JWT_SIGNING_KEY": "a" * 20 + "\t" + "a" * 20},
        {"JWT_SIGNING_KEY": "a" * 20 + "\n" + "a" * 20},
        {"JWT_SIGNING_KEY_PREVIOUS": "a" * 31},
        {"JWT_SIGNING_KEY_PREVIOUS": "b" * 20 + " " + "b" * 20},
        {"DB_CONNECTION_ENCRYPTION_KEY": ""},
        {"DB_CONNECTION_ENCRYPTION_KEY": "short"},
        {"DB_CONNECTION_ENCRYPTION_KEY": "a" * 31},  # one character short of the minimum
        {"DB_CONNECTION_ENCRYPTION_KEY": "a" * 20 + " " + "a" * 20},
        {"DB_CONNECTION_ENCRYPTION_KEY": "a" * 20 + "\t" + "a" * 20},
        {"DB_CONNECTION_ENCRYPTION_KEY": "a" * 20 + "\n" + "a" * 20},
        {"ADMIN_API_KEY": ""},
        {"ADMIN_API_KEY": "short"},
        {"ADMIN_API_KEY": "a" * 31},  # one character short of the minimum
        {"ADMIN_API_KEY": "a" * 20 + " " + "a" * 20},
        {"ADMIN_API_KEY": "a" * 20 + "\t" + "a" * 20},
        {"ADMIN_API_KEY": "a" * 20 + "\n" + "a" * 20},
        {"OPENAI_API_KEY": ""},
        {"OPENAI_API_KEY": "has a space"},
        {"OPENAI_API_KEY": "has\ttab"},
        {"OPENAI_API_KEY": "has\nnewline"},
        {"API_PORT": "abc"},
        {"API_PORT": "0"},
        {"API_PORT": "70000"},
        {"API_HOST": ""},
        # Task 1.5.b: non-integer value for a limit field, non-numeric
        # value for a window field -- pydantic's own type coercion, no
        # custom parsing.
        {"SESSION_RATE_LIMIT_PER_SITE_KEY": "10.5"},
        {"SESSION_RATE_LIMIT_PER_SITE_KEY": "abc"},
        {"SESSION_RATE_LIMIT_PER_SITE_KEY_WINDOW_SECONDS": "abc"},
        {"SESSION_RATE_LIMIT_PER_IP": "10.5"},
        {"SESSION_RATE_LIMIT_PER_IP": "abc"},
        {"SESSION_RATE_LIMIT_PER_IP_WINDOW_SECONDS": "abc"},
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
    for name in ALL_SETTINGS_VARS:
        monkeypatch.delenv(name, raising=False)
    fresh_import("app.config")  # must not raise despite empty env


def test_settings_env_file_is_never_configured():
    # Guarantees Settings is read only from process environment variables:
    # if env_file were ever set, Settings could silently pick up a stray
    # .env file instead of the orchestrator-provided environment.
    assert Settings.model_config.get("env_file") is None


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
    assert_secret_not_in_exception_chain(err, TEST_PASSWORD)
    assert err.__cause__ is None
    assert err.__context__ is None


def test_get_settings_raises_settings_error_on_malformed_qdrant_api_key(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV, QDRANT_API_KEY="distinctive-key 123")
    with pytest.raises(SettingsError):
        get_settings()


def test_settings_error_chain_never_carries_the_qdrant_api_key(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV, QDRANT_API_KEY="distinctive-key 123")
    with pytest.raises(SettingsError) as exc_info:
        get_settings()
    err = exc_info.value
    assert_secret_not_in_exception_chain(err, "distinctive-key")
    assert err.__cause__ is None
    assert err.__context__ is None


def test_get_settings_raises_settings_error_on_malformed_openai_api_key(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV, OPENAI_API_KEY="distinctive-key 123")
    with pytest.raises(SettingsError):
        get_settings()


def test_settings_error_chain_never_carries_the_openai_api_key(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV, OPENAI_API_KEY="distinctive-key 123")
    with pytest.raises(SettingsError) as exc_info:
        get_settings()
    err = exc_info.value
    assert_secret_not_in_exception_chain(err, "distinctive-key")
    assert err.__cause__ is None
    assert err.__context__ is None


@pytest.mark.parametrize(
    ("value", "distinctive_substring"),
    [
        ("distinctive-jwt 123", "distinctive-jwt"),
        ("distinctive-short", "distinctive-short"),
    ],
)
def test_get_settings_raises_settings_error_on_malformed_jwt_signing_key(
    monkeypatch, value, distinctive_substring
):
    set_valid_env(monkeypatch, VALID_ENV, JWT_SIGNING_KEY=value)
    with pytest.raises(SettingsError):
        get_settings()


@pytest.mark.parametrize(
    ("value", "distinctive_substring"),
    [
        ("distinctive-jwt 123", "distinctive-jwt"),
        ("distinctive-short", "distinctive-short"),
    ],
)
def test_settings_error_chain_never_carries_the_jwt_signing_key(
    monkeypatch, value, distinctive_substring
):
    set_valid_env(monkeypatch, VALID_ENV, JWT_SIGNING_KEY=value)
    with pytest.raises(SettingsError) as exc_info:
        get_settings()
    err = exc_info.value
    assert_secret_not_in_exception_chain(err, distinctive_substring)
    assert err.__cause__ is None
    assert err.__context__ is None


@pytest.mark.parametrize(
    ("value", "distinctive_substring"),
    [
        ("distinctive-dbkey 123", "distinctive-dbkey"),
        ("distinctive-short", "distinctive-short"),
    ],
)
def test_get_settings_raises_settings_error_on_malformed_db_connection_encryption_key(
    monkeypatch, value, distinctive_substring
):
    set_valid_env(monkeypatch, VALID_ENV, DB_CONNECTION_ENCRYPTION_KEY=value)
    with pytest.raises(SettingsError):
        get_settings()


@pytest.mark.parametrize(
    ("value", "distinctive_substring"),
    [
        ("distinctive-dbkey 123", "distinctive-dbkey"),
        ("distinctive-short", "distinctive-short"),
    ],
)
def test_settings_error_chain_never_carries_the_db_connection_encryption_key(
    monkeypatch, value, distinctive_substring
):
    set_valid_env(monkeypatch, VALID_ENV, DB_CONNECTION_ENCRYPTION_KEY=value)
    with pytest.raises(SettingsError) as exc_info:
        get_settings()
    err = exc_info.value
    assert_secret_not_in_exception_chain(err, distinctive_substring)
    assert err.__cause__ is None
    assert err.__context__ is None


@pytest.mark.parametrize(
    ("value", "distinctive_substring"),
    [
        ("distinctive-adminkey 123", "distinctive-adminkey"),
        ("distinctive-short", "distinctive-short"),
    ],
)
def test_get_settings_raises_settings_error_on_malformed_admin_api_key(
    monkeypatch, value, distinctive_substring
):
    set_valid_env(monkeypatch, VALID_ENV, ADMIN_API_KEY=value)
    with pytest.raises(SettingsError):
        get_settings()


@pytest.mark.parametrize(
    ("value", "distinctive_substring"),
    [
        ("distinctive-adminkey 123", "distinctive-adminkey"),
        ("distinctive-short", "distinctive-short"),
    ],
)
def test_settings_error_chain_never_carries_the_admin_api_key(
    monkeypatch, value, distinctive_substring
):
    set_valid_env(monkeypatch, VALID_ENV, ADMIN_API_KEY=value)
    with pytest.raises(SettingsError) as exc_info:
        get_settings()
    err = exc_info.value
    assert_secret_not_in_exception_chain(err, distinctive_substring)
    assert err.__cause__ is None
    assert err.__context__ is None


def test_jwt_signing_key_previous_unset_defaults_to_none(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    monkeypatch.delenv("JWT_SIGNING_KEY_PREVIOUS", raising=False)
    settings = Settings()
    assert settings.jwt_signing_key_previous is None
    assert settings.jwt_signing_key_previous_str() is None


def test_jwt_signing_key_previous_empty_string_becomes_none(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV, JWT_SIGNING_KEY_PREVIOUS="")
    settings = Settings()
    assert settings.jwt_signing_key_previous is None
    assert settings.jwt_signing_key_previous_str() is None


def test_jwt_signing_key_previous_valid_value_is_accepted(monkeypatch):
    previous = "previous-jwt-signing-key-32-chars-ok"
    set_valid_env(monkeypatch, VALID_ENV, JWT_SIGNING_KEY_PREVIOUS=previous)
    settings = Settings()
    assert settings.jwt_signing_key_previous_str() == previous


def test_jwt_signing_key_previous_identical_to_current_is_rejected(monkeypatch):
    current = VALID_ENV["JWT_SIGNING_KEY"]
    set_valid_env(monkeypatch, VALID_ENV, JWT_SIGNING_KEY_PREVIOUS=current)
    with pytest.raises(ValidationError) as exc_info:
        Settings()
    assert current not in str(exc_info.value)


def test_repr_and_str_never_leak_jwt_signing_keys(monkeypatch):
    previous = "previous-jwt-signing-key-32-chars-ok"
    set_valid_env(monkeypatch, VALID_ENV, JWT_SIGNING_KEY_PREVIOUS=previous)
    settings = Settings()
    assert VALID_ENV["JWT_SIGNING_KEY"] not in repr(settings)
    assert VALID_ENV["JWT_SIGNING_KEY"] not in str(settings)
    assert previous not in repr(settings)
    assert previous not in str(settings)


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


# Task 1.5.b: the four rate-limit Settings fields, each an env var name
# derived the same way REQUIRED_VARS/ALL_SETTINGS_VARS are (field_name.upper()).
RATE_LIMIT_FIELDS = (
    ("SESSION_RATE_LIMIT_PER_SITE_KEY", "session_rate_limit_per_site_key"),
    (
        "SESSION_RATE_LIMIT_PER_SITE_KEY_WINDOW_SECONDS",
        "session_rate_limit_per_site_key_window_seconds",
    ),
    ("SESSION_RATE_LIMIT_PER_IP", "session_rate_limit_per_ip"),
    ("SESSION_RATE_LIMIT_PER_IP_WINDOW_SECONDS", "session_rate_limit_per_ip_window_seconds"),
)


@pytest.mark.parametrize(("env_var", "attr"), RATE_LIMIT_FIELDS)
def test_rate_limit_field_overridable_via_its_own_env_var(monkeypatch, env_var, attr):
    set_valid_env(monkeypatch, VALID_ENV, **{env_var: "7"})
    settings = Settings()
    assert getattr(settings, attr) == 7


@pytest.mark.parametrize(("env_var", "attr"), RATE_LIMIT_FIELDS)
@pytest.mark.parametrize("bad_value", ["0", "-1"])
def test_rate_limit_field_rejects_zero_and_negative(monkeypatch, env_var, attr, bad_value):
    set_valid_env(monkeypatch, VALID_ENV, **{env_var: bad_value})
    with pytest.raises(ValidationError) as exc_info:
        Settings()
    assert env_var.lower() in str(exc_info.value)
