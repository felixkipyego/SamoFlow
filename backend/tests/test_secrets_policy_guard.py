# backend/tests/test_secrets_policy_guard.py
# [SECURITY] L1 (out-of-band Phase 7 batch, 2026-10-17), Decision D
# (D-pilot-placeholder-secrets-gate): guards app/secrets_policy.py's own
# SECRET_CLASS_SETTINGS registry stays complete and accurate as Settings
# (app/config.py) evolves -- a new SecretStr field added there without a
# matching entry here must fail a test, not silently ship unclassified.
import typing

from pydantic import SecretStr

from app.config import Settings
from app.secrets_policy import (
    SECRET_CLASS_SETTINGS,
    check_raw_secret_values,
    is_weak_value,
    password_from_dsn,
)


def _settings_secretstr_field_names() -> set[str]:
    names = set()
    for name, field in Settings.model_fields.items():
        annotation = field.annotation
        if annotation is SecretStr or SecretStr in typing.get_args(annotation):
            names.add(name)
    return names


def test_every_secretstr_field_on_settings_is_classified():
    # The core guard: a new secret-class Settings field with no matching
    # SecretClassSetting entry must fail this test, not ship unclassified.
    real = _settings_secretstr_field_names()
    classified = {setting.attr for setting in SECRET_CLASS_SETTINGS}
    missing = real - classified
    assert not missing, (
        f"Settings has SecretStr field(s) {sorted(missing)} with no matching "
        "SecretClassSetting entry in app/secrets_policy.py -- classify it "
        "(min_length, optional, is_dsn) before shipping."
    )


def test_no_stale_classified_entries():
    # The reverse check: every classified entry must still correspond to a
    # real SecretStr field on Settings (catches a renamed/removed field
    # left behind in the registry).
    real = _settings_secretstr_field_names()
    classified = {setting.attr for setting in SECRET_CLASS_SETTINGS}
    stale = classified - real
    assert not stale, (
        f"app/secrets_policy.py classifies {sorted(stale)}, which no longer "
        "exist as SecretStr fields on Settings -- remove the stale entry."
    )


def test_env_var_names_match_attr_upper():
    # pydantic-settings matches an env var to a field name case-
    # insensitively by default (confirmed: Settings.model_config sets
    # neither case_sensitive nor an alias) -- every classified entry's own
    # env_var must therefore be attr.upper(), so a future rename of either
    # side cannot silently desync them.
    for setting in SECRET_CLASS_SETTINGS:
        assert setting.env_var == setting.attr.upper(), (
            f"{setting.attr!r}'s env_var {setting.env_var!r} is not "
            f"{setting.attr.upper()!r}"
        )


def test_is_weak_value_catches_every_real_value_used_in_the_repo():
    # Non-vacuous: the exact real values found by grep (see app/
    # secrets_policy.py's own header comment for each one's source file).
    real_weak_values = [
        "change-me",
        "change-me-change-me-change-me-change-me",
        "test-qdrant-key",
        "test-jwt-signing-key-at-least-32-chars",
        "smoke-test-openai-key",
        "smoke-test-jwt-signing-key-32-chars-ok",
        "placeholder-qdrant-key",
        "placeholder-jwt-signing-key-at-least-32-chars",
        # Case-insensitivity: an operator could plausibly type either case.
        "CHANGE-ME",
        "Test-Qdrant-Key",
    ]
    for value in real_weak_values:
        assert is_weak_value(value), f"{value!r} should be flagged as weak"


def test_is_weak_value_does_not_flag_a_realistic_looking_secret():
    # The adversarial complement: a real-shaped random token must NOT be
    # flagged, or this gate would block every real deployment.
    realistic_values = [
        "zQ7mK2pR9xLaUeVbNcOdTfGhIjKlMnOp",
        "sk-proj-aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789",
        "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6",
    ]
    for value in realistic_values:
        assert not is_weak_value(value), f"{value!r} should NOT be flagged as weak"


def test_password_from_dsn_extracts_only_the_password():
    assert password_from_dsn("postgresql+psycopg://user:change-me@host:5432/db") == "change-me"
    assert password_from_dsn("postgresql+psycopg://user:zQ7mK2pR9x@host:5432/db") == "zQ7mK2pR9x"
    # URL-encoded special characters in the password must be decoded back
    # to the real value before the weak-value check runs against it.
    assert password_from_dsn("postgresql+psycopg://user:p%40ss@host:5432/db") == "p@ss"


def test_password_from_dsn_returns_none_when_no_password_present():
    assert password_from_dsn("postgresql+psycopg://host:5432/db") is None


def test_check_raw_secret_values_catches_a_weak_dsn_password_even_though_the_whole_dsn_is_fine():
    # The real reason DATABASE_URL needs is_dsn=True, not a bare
    # is_weak_value() call on the whole string: "postgresql+psycopg://..."
    # never starts with any weak prefix, so checking the whole DSN would
    # never catch a weak PASSWORD -- confirmed here, not assumed.
    violations = check_raw_secret_values(
        {"DATABASE_URL": "postgresql+psycopg://user:change-me@host:5432/db"}
    )
    assert len(violations) == 1
    assert "DATABASE_URL" in violations[0]


def test_check_raw_secret_values_ignores_none_optional_values():
    violations = check_raw_secret_values({"ANTHROPIC_API_KEY": None, "SMTP_PASSWORD": None})
    assert violations == []


def test_check_raw_secret_values_catches_a_too_short_signing_key():
    violations = check_raw_secret_values({"JWT_SIGNING_KEY": "short-key"})
    assert any("JWT_SIGNING_KEY" in v and "minimum" in v for v in violations)


def test_check_raw_secret_values_message_never_contains_the_raw_value():
    distinctive = "change-me-distinctive-marker-value-should-never-leak"
    violations = check_raw_secret_values({"OPENAI_API_KEY": distinctive})
    assert violations
    for v in violations:
        assert distinctive not in v


def test_check_raw_secret_values_accepts_a_fully_realistic_env():
    from tests.conftest import PRODUCTION_REALISTIC_SECRET_OVERRIDES

    violations = check_raw_secret_values(dict(PRODUCTION_REALISTIC_SECRET_OVERRIDES))
    assert violations == []
