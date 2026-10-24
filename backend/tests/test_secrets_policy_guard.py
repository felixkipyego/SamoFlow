# backend/tests/test_secrets_policy_guard.py
# [SECURITY] L1 (out-of-band Phase 7 batch, 2026-10-17), Decision D
# (D-pilot-placeholder-secrets-gate): guards app/secrets_policy.py's own
# SECRET_CLASS_SETTINGS registry stays complete and accurate as Settings
# (app/config.py) evolves -- a new SecretStr field added there without a
# matching entry here must fail a test, not silently ship unclassified.
import re
import typing

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.secrets_policy import (
    ALL_SECRET_ENV_SETTINGS,
    DEPLOY_SECRET_SETTINGS,
    SECRET_CLASS_SETTINGS,
    check_credential_consistency,
    check_raw_secret_values,
    is_weak_value,
    password_from_dsn,
)
from tests.test_env_example import _parse_env_example


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
    placeholder = [v for v in violations if "known placeholder" in v]
    assert len(placeholder) == 1
    assert "DATABASE_URL" in placeholder[0]


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



# --- [SECURITY] L2 follow-up (advisor review of eedad4e, 2026-10-18) ---
#
# Why the guards above never caught POSTGRES_PASSWORD/POSTGRES_OWNER_
# PASSWORD: their universe is Settings' own SecretStr fields, and neither
# variable is a Settings field (deploy containers/scripts read them
# directly). Nothing ever looked at .env.example itself -- the file an
# operator actually fills in. The guard below starts from .env.example.

# A name that looks like a credential. Deliberately broad: a false
# positive costs one reasoned line in NOT_SECRET_CLASS_EXAMPLE_KEYS; a
# false negative is exactly the bug this guard exists to prevent. The one
# carve-out: "SITE_KEY" (the *_RATE_LIMIT_PER_SITE_KEY settings) -- a
# site key is the widget's PUBLIC identifier, embedded in every customer
# page's HTML, never a credential.
_SECRET_LOOKING_NAME = re.compile(r"PASSWORD|SECRET|TOKEN|(^|(?<!SITE)_)KEY(_|$)")

# .env.example variables whose NAME looks like a credential but which are
# not secret -- each with the reason. Adding to this list is a deliberate,
# reviewed decision, never a way to silence the guard.
NOT_SECRET_CLASS_EXAMPLE_KEYS: dict[str, str] = {
    "BACKUP_AGE_PUBLIC_KEY": "the PUBLIC (encrypting) half of the backup age keypair; "
    "the private half never lives in .env",
    "AWS_ACCESS_KEY_ID": "an access-key IDENTIFIER, not the secret half "
    "(AWS_SECRET_ACCESS_KEY, which is classified)",
}


def _unclassified_secret_looking_keys(
    example_values: dict[str, str], classified_env_vars: set[str]
) -> set[str]:
    found = set()
    for key, value in example_values.items():
        looks_secret = bool(_SECRET_LOOKING_NAME.search(key)) or (
            "://" in value and password_from_dsn(value) is not None
        )
        exempt = key in classified_env_vars or key in NOT_SECRET_CLASS_EXAMPLE_KEYS
        if looks_secret and not exempt:
            found.add(key)
    return found


def _all_classified_env_vars() -> set[str]:
    return {setting.env_var for setting in ALL_SECRET_ENV_SETTINGS}


def test_every_secret_looking_env_example_variable_is_classified():
    missing = _unclassified_secret_looking_keys(_parse_env_example(), _all_classified_env_vars())
    assert not missing, (
        f".env.example declares secret-looking variable(s) {sorted(missing)} that "
        "app/secrets_policy.py classifies nowhere -- add each to SECRET_CLASS_SETTINGS "
        "(a Settings field) or DEPLOY_SECRET_SETTINGS (read only by a container/script), "
        "or, if genuinely not a secret, to NOT_SECRET_CLASS_EXAMPLE_KEYS with the reason."
    )


def test_the_env_example_guard_would_have_caught_the_original_finding():
    # Non-vacuous: the exact pre-fix state (neither name classified) must
    # be flagged by the same detector the real guard above uses.
    pre_fix = _all_classified_env_vars() - {"POSTGRES_PASSWORD", "POSTGRES_OWNER_PASSWORD"}
    assert _unclassified_secret_looking_keys(_parse_env_example(), pre_fix) == {
        "POSTGRES_PASSWORD",
        "POSTGRES_OWNER_PASSWORD",
    }


def test_the_env_example_guard_flags_a_dsn_with_an_embedded_password_by_value():
    # A credential hiding inside a URL under an innocent-looking name.
    flagged = _unclassified_secret_looking_keys(
        {"REPLICA_URL": "postgresql+psycopg://u:pw@h:5432/db", "QDRANT_URL": "http://qdrant:6333"},
        set(),
    )
    assert flagged == {"REPLICA_URL"}


@pytest.mark.parametrize(
    ("name", "looks_secret"),
    [
        ("POSTGRES_PASSWORD", True),
        ("REDIS_PASSWORD", True),
        ("AWS_SECRET_ACCESS_KEY", True),
        ("GITHUB_TOKEN", True),
        ("KEY", True),
        ("PRIVATE_KEY", True),
        ("JWT_SIGNING_KEY_PREVIOUS", True),
        ("STRIPE_SITE_KEY_SECRET", True),  # the SITE_KEY carve-out never hides a SECRET
        ("SESSION_RATE_LIMIT_PER_SITE_KEY", False),
        ("MONKEY_COUNT", False),
        ("KEYRING_PATH", False),
        ("QDRANT_URL", False),
    ],
)
def test_secret_looking_name_detector(name, looks_secret):
    assert bool(_SECRET_LOOKING_NAME.search(name)) is looks_secret


def test_not_secret_class_exemptions_are_real_and_not_also_classified():
    example = _parse_env_example()
    for key in NOT_SECRET_CLASS_EXAMPLE_KEYS:
        assert key in example, f"stale exemption: {key} is no longer in .env.example"
        assert key not in _all_classified_env_vars(), f"{key} is both exempt and classified"


def test_every_deploy_secret_is_declared_in_env_example_and_is_not_a_settings_field():
    example = _parse_env_example()
    settings_env_vars = {name.upper() for name in Settings.model_fields}
    for setting in DEPLOY_SECRET_SETTINGS:
        assert setting.attr is None
        assert setting.env_var in example, f"stale entry: {setting.env_var} not in .env.example"
        assert setting.env_var not in settings_env_vars, (
            f"{setting.env_var} is a real Settings field -- classify it in "
            "SECRET_CLASS_SETTINGS instead, so Settings' own production gate covers it"
        )


@pytest.mark.parametrize("env_var", ["POSTGRES_PASSWORD", "POSTGRES_OWNER_PASSWORD"])
@pytest.mark.parametrize(
    "weak_value",
    [
        "change-me",  # .env.example's POSTGRES_PASSWORD
        "change-me-owner",  # .env.example's POSTGRES_OWNER_PASSWORD
        "CHANGE-ME",
        "  change-me  ",
        "test-postgres-password",
        "smoke-test-postgres",
        "placeholder-password",
    ],
)
def test_postgres_passwords_reject_placeholder_and_weak_values(env_var, weak_value):
    violations = check_raw_secret_values({env_var: weak_value})
    # A short placeholder also trips the 16-character minimum
    # (D-db-password-min-length) -- the placeholder finding itself must be
    # there exactly once either way.
    placeholder = [v for v in violations if "known placeholder" in v]
    assert len(placeholder) == 1
    assert all(env_var in v for v in violations)
    assert all(weak_value.strip() not in v for v in violations)


@pytest.mark.parametrize("env_var", ["POSTGRES_PASSWORD", "POSTGRES_OWNER_PASSWORD"])
def test_postgres_passwords_accept_a_realistic_value(env_var):
    assert check_raw_secret_values({env_var: "Xk3vQ9pL2mN8rT5wY7zA1bC4dE6fG0hJ"}) == []


def test_aws_secret_access_key_is_checked_only_when_set():
    assert check_raw_secret_values({"AWS_SECRET_ACCESS_KEY": None}) == []
    violations = check_raw_secret_values({"AWS_SECRET_ACCESS_KEY": "change-me"})
    assert len(violations) == 1 and "AWS_SECRET_ACCESS_KEY" in violations[0]


_OWNER_DSN = "postgresql+psycopg://widgetplatform_owner:owner-pw-for-consistency-tests@postgres:5432/widgetplatform"


def test_credential_consistency_accepts_matching_owner_credentials():
    assert (
        check_credential_consistency(
            {
                "DATABASE_URL_MIGRATE": _OWNER_DSN,
                "POSTGRES_OWNER_USER": "widgetplatform_owner",
                "POSTGRES_OWNER_PASSWORD": "owner-pw-for-consistency-tests",
            }
        )
        == []
    )


def test_credential_consistency_compares_the_decoded_dsn_password():
    # A password with URL-special characters is percent-encoded inside
    # the DSN but written raw in POSTGRES_OWNER_PASSWORD -- still a match.
    dsn = "postgresql+psycopg://widgetplatform_owner:p%40ss%2Fw0rd%3Axyz@postgres:5432/widgetplatform"
    assert (
        check_credential_consistency(
            {
                "DATABASE_URL_MIGRATE": dsn,
                "POSTGRES_OWNER_USER": "widgetplatform_owner",
                "POSTGRES_OWNER_PASSWORD": "p@ss/w0rd:xyz",
            }
        )
        == []
    )


def test_credential_consistency_rejects_a_mismatched_owner_password_naming_only_variables():
    distinctive = "Distinctive-Bare-Owner-Password-Marker"
    violations = check_credential_consistency(
        {
            "DATABASE_URL_MIGRATE": _OWNER_DSN,
            "POSTGRES_OWNER_USER": "widgetplatform_owner",
            "POSTGRES_OWNER_PASSWORD": distinctive,
        }
    )
    assert len(violations) == 1
    assert "POSTGRES_OWNER_PASSWORD" in violations[0]
    assert "DATABASE_URL_MIGRATE" in violations[0]
    assert distinctive not in violations[0]
    assert "owner-pw-for-consistency-tests" not in violations[0]


def test_credential_consistency_rejects_a_mismatched_owner_user():
    violations = check_credential_consistency(
        {
            "DATABASE_URL_MIGRATE": _OWNER_DSN,
            "POSTGRES_OWNER_USER": "some_other_role",
            "POSTGRES_OWNER_PASSWORD": "owner-pw-for-consistency-tests",
        }
    )
    assert len(violations) == 1 and "POSTGRES_OWNER_USER" in violations[0]


def test_credential_consistency_rejects_a_dsn_with_no_password_at_all():
    violations = check_credential_consistency(
        {
            "DATABASE_URL_MIGRATE": "postgresql+psycopg://widgetplatform_owner@postgres:5432/db",
            "POSTGRES_OWNER_PASSWORD": "owner-pw-for-consistency-tests",
        }
    )
    assert len(violations) == 1 and "POSTGRES_OWNER_PASSWORD" in violations[0]


def test_credential_consistency_is_case_sensitive():
    violations = check_credential_consistency(
        {
            "DATABASE_URL_MIGRATE": _OWNER_DSN,
            "POSTGRES_OWNER_PASSWORD": "OWNER-PW-FOR-CONSISTENCY-TESTS",
        }
    )
    assert len(violations) == 1


@pytest.mark.parametrize(
    "raw_values",
    [{}, {"DATABASE_URL_MIGRATE": None}, {"DATABASE_URL_MIGRATE": _OWNER_DSN}],
)
def test_credential_consistency_leaves_missing_values_to_the_presence_check(raw_values):
    assert check_credential_consistency(raw_values) == []


# --- [SECURITY] 2026-10-24 (Felix-approved, D-db-password-min-length) ---
# All four database passwords need >= 16 characters; 15 is rejected, 16
# passes, and the message names the variable, never the value. For a DSN
# the length is the DECODED embedded password's, not the whole string's.

from app.secrets_policy import DB_PASSWORD_MIN_LENGTH  # noqa: E402

_DB_PASSWORD_VARS = (
    "POSTGRES_PASSWORD",
    "POSTGRES_OWNER_PASSWORD",
    "DATABASE_URL",
    "DATABASE_URL_MIGRATE",
)


def _as_value(env_var: str, password: str) -> str:
    if env_var.startswith("DATABASE_URL"):
        return f"postgresql+psycopg://some_role:{password}@postgres:5432/widgetplatform"
    return password


def test_db_password_minimum_is_16():
    assert DB_PASSWORD_MIN_LENGTH == 16
    by_var = {s.env_var: s.min_length for s in ALL_SECRET_ENV_SETTINGS}
    assert all(by_var[v] == 16 for v in _DB_PASSWORD_VARS)


@pytest.mark.parametrize("env_var", _DB_PASSWORD_VARS)
def test_a_15_character_db_password_is_rejected_naming_only_the_variable(env_var):
    candidate = "abcdefghijklmn5"  # 15 characters, not placeholder-prefixed
    assert len(candidate) == 15
    violations = check_raw_secret_values({env_var: _as_value(env_var, candidate)})
    assert violations == [
        f"{env_var} is shorter than its required minimum (16 characters)."
    ]
    assert candidate not in violations[0]


@pytest.mark.parametrize("env_var", _DB_PASSWORD_VARS)
def test_a_16_character_db_password_passes(env_var):
    candidate = "abcdefghijklmno6"  # 16 characters
    assert len(candidate) == 16
    assert check_raw_secret_values({env_var: _as_value(env_var, candidate)}) == []


@pytest.mark.parametrize("env_var", ["DATABASE_URL", "DATABASE_URL_MIGRATE"])
def test_dsn_password_length_is_measured_after_percent_decoding(env_var):
    # "%40" is ONE character ("@") once decoded: 13 + 1 + 1 = 15 decoded
    # characters, even though the raw text is 17 long -- must be rejected.
    raw = "abcdefghijklm%40z"
    assert len(raw) == 17
    violations = check_raw_secret_values({env_var: _as_value(env_var, raw)})
    assert violations and "shorter than its required minimum" in violations[0]
    # 16 decoded characters passes.
    assert check_raw_secret_values({env_var: _as_value(env_var, "abcdefghijklmn%40z")}) == []
