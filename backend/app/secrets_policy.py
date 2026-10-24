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
#     DB_CONNECTION_ENCRYPTION_KEY, OPENAI_API_KEY, POSTGRES_PASSWORD);
#     "change-me-owner" (DATABASE_URL_MIGRATE's password,
#     POSTGRES_OWNER_PASSWORD) -- both covered by the "change-me" prefix.
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

# [SECURITY] 2026-10-24 (Felix-approved, D-db-password-min-length): the
# minimum length for all four database passwords (see their entries below).
DB_PASSWORD_MIN_LENGTH = 16


@dataclass(frozen=True)
class SecretClassSetting:
    # Settings attribute name (app/config.py), or None for a deploy-layer-
    # only secret (DEPLOY_SECRET_SETTINGS below) that no Settings field
    # reads at all.
    attr: str | None
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
    # embedded password, not a bare secret token -- DATABASE_URL and
    # DATABASE_URL_MIGRATE.
    is_dsn: bool = False


# Every SecretStr-typed field on Settings (app/config.py), found by
# introspecting Settings.model_fields directly (confirmed live: exactly
# these 10, via `typing.get_args(field.annotation)` containing SecretStr or
# the bare annotation being SecretStr) -- backend/tests/test_secrets_
# policy_guard.py fails if a new one is ever added here without a matching
# SecretClassSetting entry.
SECRET_CLASS_SETTINGS: tuple[SecretClassSetting, ...] = (
    # [SECURITY] 2026-10-24 (Felix-approved, D-db-password-min-length): the
    # four database passwords -- this DSN's, DATABASE_URL_MIGRATE's,
    # POSTGRES_PASSWORD and POSTGRES_OWNER_PASSWORD (DEPLOY_SECRET_SETTINGS
    # below) -- must be at least DB_PASSWORD_MIN_LENGTH characters. For a
    # DSN the check runs on the decoded embedded password, never the whole
    # string.
    SecretClassSetting(
        "database_url", "DATABASE_URL", DB_PASSWORD_MIN_LENGTH, optional=False, is_dsn=True
    ),
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
        "database_url_migrate",
        "DATABASE_URL_MIGRATE",
        DB_PASSWORD_MIN_LENGTH,
        optional=False,
        is_dsn=True,
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


# [SECURITY] L2 follow-up (advisor review of eedad4e, 2026-10-18): secrets
# .env carries that NO Settings field reads -- each is consumed directly
# by a container or ops script instead -- so the Settings-driven guard
# (backend/tests/test_secrets_policy_guard.py) could never see them, and
# after the role split POSTGRES_PASSWORD no longer duplicates any checked
# DSN: a production .env could carry the bootstrap superuser's own
# "change-me" and pass preflight.sh. Same weak-value rules, and (since
# 2026-10-24) the same DB_PASSWORD_MIN_LENGTH as the DSN passwords above.
# test_secrets_policy_guard.py's .env.example-driven guard now fails if a
# secret-looking .env.example variable is classified in neither tuple.
DEPLOY_SECRET_SETTINGS: tuple[SecretClassSetting, ...] = (
    # The BOOTSTRAP superuser (deploy/docker-compose.yml's postgres
    # service, and app/db_provisioning.py's own connection).
    SecretClassSetting(None, "POSTGRES_PASSWORD", DB_PASSWORD_MIN_LENGTH, optional=False),
    # The OWNER role's bare password, for ops/backup/docker-compose.yml's
    # PGPASSWORD -- must equal DATABASE_URL_MIGRATE's own embedded
    # password (check_credential_consistency() below).
    SecretClassSetting(None, "POSTGRES_OWNER_PASSWORD", DB_PASSWORD_MIN_LENGTH, optional=False),
    # The backup bucket's secret key (ops/backup/docker-compose.yml).
    # optional=True: preflight.sh gates the app deploy, and the backup
    # stack's own `${...:?}` interpolation already refuses to start
    # without it -- this entry only rejects a placeholder when it IS set.
    SecretClassSetting(None, "AWS_SECRET_ACCESS_KEY", None, optional=True),
)

# Everything a real .env must get right -- what check_env_secrets.py
# (preflight.sh) checks. Settings' own production gate checks only
# SECRET_CLASS_SETTINGS, the subset it actually reads.
ALL_SECRET_ENV_SETTINGS: tuple[SecretClassSetting, ...] = (
    SECRET_CLASS_SETTINGS + DEPLOY_SECRET_SETTINGS
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
    for setting in ALL_SECRET_ENV_SETTINGS:
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


def check_credential_consistency(raw_values: dict[str, str | None]) -> list[str]:
    # The one credential .env deliberately carries twice: the OWNER role's
    # username/password live both inside DATABASE_URL_MIGRATE (read by
    # migrate and provision-roles -- provision-roles SETS the role's real
    # password from it) and as bare POSTGRES_OWNER_USER/PASSWORD (read by
    # ops/backup/docker-compose.yml's PGUSER/PGPASSWORD, which plain YAML
    # interpolation cannot extract from a DSN). If they drift, the nightly
    # backup fails to authenticate. The APP role's password lives only in
    # DATABASE_URL and the bootstrap password only in POSTGRES_PASSWORD,
    # so neither has a second copy to drift from. Messages name variables
    # only, never values. A missing value is not this function's concern
    # (check_env_secrets.py's own presence check runs first).
    dsn = raw_values.get("DATABASE_URL_MIGRATE")
    if not dsn:
        return []
    parts = urlsplit(dsn)
    violations: list[str] = []
    bare_user = raw_values.get("POSTGRES_OWNER_USER")
    if bare_user and unquote(parts.username or "") != bare_user:
        violations.append(
            "POSTGRES_OWNER_USER does not match the username inside DATABASE_URL_MIGRATE "
            "-- both must name the same OWNER role."
        )
    bare_password = raw_values.get("POSTGRES_OWNER_PASSWORD")
    if bare_password and password_from_dsn(dsn) != bare_password:
        violations.append(
            "POSTGRES_OWNER_PASSWORD does not match the password inside "
            "DATABASE_URL_MIGRATE -- both must hold the same OWNER role password "
            "(nightly backups authenticate with POSTGRES_OWNER_PASSWORD)."
        )
    return violations
