# backend/app/secrets_policy.py
# [SECURITY] L1 (out-of-band Phase 7 batch, 2026-10-17), Decision D
# (D-pilot-placeholder-secrets-gate): the ONE place that both app/config.py's
# own production startup gate and ops/deploy/preflight.sh (via its own small
# Python invocation of this module) read from -- which Settings fields are
# secret-class, what counts as a known-weak/placeholder value, and what
# minimum length (if any) applies. One source of truth, never two
# independently-maintained copies of the same policy.
#
# WEAK_VALUE_PREFIXES derived by grepping this repo for every literal
# secret-class value that appears in a file an operator could plausibly
# copy from while standing up a real deployment -- not pytest-fixture-only
# naming creativity (e.g. "harmless-"/"distinctive-"/"a-previous-",
# scattered across backend/tests for test-to-test distinctiveness, which
# never appears in any file an operator would reference while deploying):
#   - .env.example itself: "change-me" (DATABASE_URL's password,
#     QDRANT_API_KEY, QDRANT_READ_ONLY_API_KEY, JWT_SIGNING_KEY,
#     DB_CONNECTION_ENCRYPTION_KEY, OPENAI_API_KEY).
#   - deploy/smoke-image.sh: "smoke-test-..." for every secret-class var.
#   - backend/tests/conftest.py's own VALID_ENV (the base fixture every
#     other test environment in this project starts from): "test-...".
#   - backend/scripts/generate_openapi_schema.py's own _MINIMAL_ENV:
#     "placeholder-...".
# Confirmed live by grepping the real repo for each secret-class ENV VAR
# name before writing this list down, not guessed.
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

WEAK_VALUE_PREFIXES: tuple[str, ...] = ("change-me", "test-", "smoke-", "placeholder-")


@dataclass(frozen=True)
class SecretClassSetting:
    # Settings attribute name (app/config.py).
    attr: str
    # The environment variable name -- pydantic-settings matches this to
    # `attr` case-insensitively by default (confirmed against the installed
    # pydantic-settings: no case_sensitive/alias config exists on Settings'
    # own model_config), so this is always attr.upper() today; spelled out
    # explicitly anyway so a future field-renaming cannot silently desync
    # this table from the real env var an operator actually sets.
    env_var: str
    # The stated minimum length, or None if no minimum is stated anywhere in
    # this project (an opaque, already-high-entropy third-party-issued key
    # like OPENAI_API_KEY has no artificial length floor to check against --
    # see app/config.py's own field-level comments for why).
    min_length: int | None
    # True if the REAL VALUE this env var must hold in a genuine
    # deployment's own .env is allowed to be absent -- this is what
    # check_env_secrets.py's own "missing" check (and preflight.sh,
    # through it) enforces against the operator's .env FILE. Deliberately
    # a SEPARATE question from whether the matching Settings field has a
    # Python-level default (e.g. database_url_migrate below is Optional
    # on Settings -- api/worker's own process must start without it --
    # but .env itself must always carry a real value, since migrate/
    # provision-roles genuinely need one; optional=False here is correct
    # for exactly that reason, not stale).
    optional: bool
    # True if this setting's real value is a connection-string DSN with an
    # embedded password, not a bare secret token -- only DATABASE_URL today.
    is_dsn: bool = False


# Every SecretStr-typed field on Settings (app/config.py), found by
# introspecting Settings.model_fields directly (confirmed live: exactly
# these 10, via `typing.get_args(field.annotation)` containing SecretStr or
# the bare annotation being SecretStr) -- backend/tests/test_secrets_
# policy_guard.py fails if a new one is ever added here without a matching
# SecretClassSetting entry.
SECRET_CLASS_SETTINGS: tuple[SecretClassSetting, ...] = (
    SecretClassSetting("database_url", "DATABASE_URL", None, optional=False, is_dsn=True),
    # [SECURITY] L2 (Phase 7 role split, 2026-10-18): the OWNER/MIGRATOR
    # role's own DSN (app/config.py's database_url_migrate) -- covers that
    # role's own password the identical way DATABASE_URL above already
    # covers the APP role's. optional=False here (advisor review,
    # 2026-10-18): the .env FILE must always carry a real value (migrate/
    # provision-roles need one), even though the Settings FIELD itself is
    # Optional so api/worker's own Settings() construction succeeds
    # without it -- see this dataclass's own `optional` field comment for
    # why those are different questions.
    SecretClassSetting(
        "database_url_migrate", "DATABASE_URL_MIGRATE", None, optional=False, is_dsn=True
    ),
    SecretClassSetting("qdrant_api_key", "QDRANT_API_KEY", None, optional=False),
    SecretClassSetting(
        "qdrant_read_only_api_key", "QDRANT_READ_ONLY_API_KEY", None, optional=False
    ),
    # min_length=32: app/config.py's own Field(min_length=32), already a
    # universal (every-environment) pydantic constraint -- this module does
    # not re-enforce it, only documents it so a reader sees the real,
    # stated minimum for every secret-class setting in one place.
    SecretClassSetting("jwt_signing_key", "JWT_SIGNING_KEY", 32, optional=False),
    SecretClassSetting("jwt_signing_key_previous", "JWT_SIGNING_KEY_PREVIOUS", 32, optional=True),
    SecretClassSetting(
        "db_connection_encryption_key", "DB_CONNECTION_ENCRYPTION_KEY", 32, optional=False
    ),
    SecretClassSetting("openai_api_key", "OPENAI_API_KEY", None, optional=False),
    SecretClassSetting("anthropic_api_key", "ANTHROPIC_API_KEY", None, optional=True),
    SecretClassSetting("smtp_password", "SMTP_PASSWORD", None, optional=True),
)


def password_from_dsn(dsn: str) -> str | None:
    # The one place either caller (the Settings validator, or preflight.sh's
    # own raw-.env-text check) extracts a DSN's embedded password -- never
    # checks the whole DSN string against WEAK_VALUE_PREFIXES, which would
    # never match ("postgresql+psycopg://..." does not start with any of
    # them) and would silently never catch a weak password either.
    parts = urlsplit(dsn)
    if parts.password is None:
        return None
    return unquote(parts.password)


def is_weak_value(raw: str) -> bool:
    lowered = raw.strip().lower()
    return any(lowered.startswith(prefix) for prefix in WEAK_VALUE_PREFIXES)


def check_raw_secret_values(raw_values: dict[str, str | None]) -> list[str]:
    # raw_values maps each SecretClassSetting.env_var to its real, unwrapped
    # string value (or None if unset). Returns one violation message per
    # problem found, naming ONLY the variable name, never the value --
    # callers must never format a raw secret value into one of these
    # messages. A missing REQUIRED value is not this function's own
    # concern: app/config.py's existing field validators already reject an
    # empty/missing required SecretStr field in every environment, before
    # this check ever runs; preflight.sh checks "required variable present
    # at all" as its own separate rule (ops/deploy/preflight.sh).
    violations: list[str] = []
    for setting in SECRET_CLASS_SETTINGS:
        raw = raw_values.get(setting.env_var)
        if raw is None:
            continue
        check_value = password_from_dsn(raw) if setting.is_dsn else raw
        if not check_value:
            continue
        if is_weak_value(check_value):
            violations.append(
                f"{setting.env_var} is set to a known placeholder/test value -- "
                "replace it with a real, randomly-generated secret before "
                "APP_ENV=production."
            )
        if setting.min_length is not None and len(check_value) < setting.min_length:
            violations.append(
                f"{setting.env_var} is shorter than its required minimum "
                f"({setting.min_length} characters)."
            )
    return violations
