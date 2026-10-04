# backend/app/config.py
# Settings module (Task 1.1.c): validates the required environment
# variables at startup and exposes them through one cached Settings
# instance. Nothing here reads the environment or calls get_settings() at
# import time (module import must succeed with an empty environment).
# Task 1.3.a1 adds qdrant_api_key (required, never optional or defaulted --
# an unauthenticated Qdrant client would silently work today and silently
# stop working the moment auth is enabled on a real deployment). Task 1.4.a
# adds jwt_signing_key (required) and jwt_signing_key_previous (optional,
# for rotation via a future `kid` header -- PyJWT/the token module itself
# arrive in 1.4.b, this task only adds the secret plumbing). Task 2.1.a
# adds db_connection_encryption_key (required): the pgcrypto passphrase
# for db_connections.encrypted_credentials.
#
# ASSUMPTION: pydantic and pydantic-settings are not new dependencies here.
# pydantic-settings==2.15.0 is already an approved runtime dependency
# (Step 1.1.a); pydantic arrives transitively through it and fastapi.
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# SQLAlchemy 2.0 maps a plain "postgresql://" DSN to the psycopg2 driver,
# which this project does not install (Step 1.1.a installs psycopg[binary],
# i.e. psycopg 3). This scheme selects the psycopg (v3) driver instead.
# Public (no leading underscore): Task 1.1.h's test database guard imports
# this instead of duplicating the literal.
POSTGRES_SCHEME = "postgresql+psycopg"


def _require_no_whitespace_or_control_chars(raw: str, field_name: str, reason: str) -> None:
    # Shared by every secret field that needs this (duplication check after
    # 1.4.a/b; re-confirmed still accurate at the 2.2.d/e/f duplication
    # check, which deliberately rewords this away from naming call sites by
    # count/name -- that phrasing had already gone stale once by then):
    # rejects stray whitespace or control characters (e.g. from a
    # copy-paste error), each caller for its own reason -- `reason` keeps
    # the message truthful per field instead of one field borrowing
    # another's rationale. Message names only the requirement, never the
    # value under validation.
    if any(ch.isspace() or not ch.isprintable() for ch in raw):
        raise ValueError(
            f"{field_name} must not contain whitespace or control characters ({reason})."
        )


class Settings(BaseSettings):
    # frozen: instances are immutable after construction.
    # hide_input_in_errors: hides the raw offending value from str()/repr()
    # of a ValidationError, but not from its structured .errors() list
    # (verified against pydantic 2.13.5). get_settings() below is the
    # boundary that turns a ValidationError into an input-free SettingsError;
    # calling Settings() directly still risks a raw value in .errors(), so
    # startup code must always go through get_settings().
    model_config = SettingsConfigDict(frozen=True, hide_input_in_errors=True)

    app_env: Literal["development", "test", "production"]
    database_url: SecretStr
    qdrant_url: str
    qdrant_api_key: SecretStr
    # min_length uses pydantic's own built-in constraint (rule 11 -- a
    # well-known mechanism, not a hand-rolled length check); it applies to
    # jwt_signing_key_previous's SecretStr arm only, never to its None arm
    # (confirmed by hand against the installed pydantic==2.13.5).
    jwt_signing_key: SecretStr = Field(min_length=32)
    jwt_signing_key_previous: SecretStr | None = Field(default=None, min_length=32)
    api_host: str = Field(min_length=1)
    api_port: int = Field(ge=1, le=65535)
    # Task 1.5.b: two independent rate-limit policies for
    # POST /api/v1/session (docs/SPEC.md §9), each a (limit, window) pair.
    # Not secrets -- ordinary configurable integers, no SecretStr, no
    # guarded accessor, no allow-list. gt=0 is pydantic's own built-in
    # constraint (rule 11, same mechanism api_port already uses above),
    # not a hand-rolled positivity check -- it rejects 0 and negative
    # values for all four fields with no custom validator needed.
    session_rate_limit_per_site_key: int = Field(default=10, gt=0)
    session_rate_limit_per_site_key_window_seconds: float = Field(default=60, gt=0)
    session_rate_limit_per_ip: int = Field(default=30, gt=0)
    session_rate_limit_per_ip_window_seconds: float = Field(default=60, gt=0)
    # Task 2.1.a: the pgcrypto passphrase for db_connections.
    # encrypted_credentials (docs/SPEC.md §5.6: "Credentials are encrypted
    # at rest"). Required, same discipline as qdrant_api_key/
    # jwt_signing_key -- an unencrypted-at-rest credential would silently
    # work today and silently stay wrong on a real deployment if this were
    # optional. min_length=32 matches jwt_signing_key's own established
    # floor (rule 11 -- reuse an already-justified number, not a new one
    # invented here).
    db_connection_encryption_key: SecretStr = Field(min_length=32)
    # Task 2.1.c: the job queue's own retry policy. Not secrets -- same
    # treatment as the session-rate-limit fields above (plain configurable
    # numbers, gt=0 is pydantic's own built-in constraint, no custom
    # validator). job_max_attempts is read by enqueue() (IngestRepository)
    # to set each new job row's own max_attempts column at creation time --
    # mark_job_failed() then compares against that row's own persisted
    # value, not a fresh Settings read, so changing this setting later
    # never shifts the retry ceiling of an already-created job. jobs.
    # max_attempts' DB-level server_default=5 (app/ingest/models.py, from
    # 2.1.a) stays as an inert defensive floor for any insert that
    # bypasses enqueue(); it no longer governs behavior for jobs created
    # through the sanctioned path once enqueue() always sets it
    # explicitly.
    job_retry_base_seconds: float = Field(default=60, gt=0)
    job_max_attempts: int = Field(default=5, gt=0)
    # Task 2.2.f: the temporary admin-key mechanism (docs/SPEC.md §12:
    # "during Phases 2 to 5 the admin endpoints... are protected by a
    # single secret from the environment"). Required, same discipline as
    # every other secret here -- an unauthenticated admin surface would
    # silently work today and silently stay wrong on a real deployment if
    # this were optional. Explicitly temporary: superseded outright by
    # Step 6.1's real `platform_admin` role, never extended between now
    # and then (see PROJECT_SPEC.md's own Open marker and
    # app/admin/dependencies.py's header comment).
    admin_api_key: SecretStr = Field(min_length=32)
    # Task 2.1.d: how long worker.py's run() waits between poll attempts
    # when claim_next_job() finds nothing ready -- never a tight spin
    # loop. Same treatment as the fields directly above (plain
    # configurable number, gt=0 is pydantic's own built-in constraint).
    # 2 seconds: noticeably shorter than job_retry_base_seconds's own
    # 60-second floor (this is "is anything ready yet", not a retry
    # delay), but long enough that an idle worker doesn't burn CPU/DB
    # round trips -- retuned later with real load-test numbers if needed
    # (Phase 7), like every other estimate in this project.
    worker_poll_interval_seconds: float = Field(default=2, gt=0)

    @field_validator("database_url")
    @classmethod
    def _require_postgres_psycopg_scheme(cls, value: SecretStr) -> SecretStr:
        parts = urlsplit(value.get_secret_value())
        database_name = parts.path.lstrip("/")
        if parts.scheme != POSTGRES_SCHEME or not parts.hostname or not database_name:
            # Message names only the required scheme, never the value under
            # validation, so a bad DSN's password cannot leak here.
            raise ValueError(
                "DATABASE_URL must be a PostgreSQL URL using the "
                f"'{POSTGRES_SCHEME}' scheme, with a hostname and a "
                "database name (SQLAlchemy 2.0 defaults plain "
                "'postgresql://' to psycopg2, which is not installed)."
            )
        return value

    @field_validator("qdrant_url")
    @classmethod
    def _require_http_scheme(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("QDRANT_URL must be an http or https URL.")
        return value

    @field_validator("qdrant_api_key")
    @classmethod
    def _require_a_clean_header_value(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        # Message names only the requirement, never the value under
        # validation, so a bad key cannot leak here.
        if not raw:
            raise ValueError("QDRANT_API_KEY must not be empty.")
        # Duplication check after 1.4.a/b: delegates to the same helper
        # jwt_signing_key/jwt_signing_key_previous use below -- one
        # whitespace/control-char check shared by every secret field that
        # needs it, instead of a second copy of the same `any(...)` line.
        _require_no_whitespace_or_control_chars(
            raw,
            "QDRANT_API_KEY",
            "it is sent as an HTTP header value; a newline would allow header injection",
        )
        return value

    @field_validator("jwt_signing_key")
    @classmethod
    def _require_a_clean_signing_key(cls, value: SecretStr) -> SecretStr:
        _require_no_whitespace_or_control_chars(
            value.get_secret_value(), "JWT_SIGNING_KEY", "it is used as UTF-8 bytes to sign tokens"
        )
        return value

    @field_validator("db_connection_encryption_key")
    @classmethod
    def _require_a_clean_encryption_key(cls, value: SecretStr) -> SecretStr:
        _require_no_whitespace_or_control_chars(
            value.get_secret_value(),
            "DB_CONNECTION_ENCRYPTION_KEY",
            "it is used as a pgcrypto passphrase",
        )
        return value

    @field_validator("admin_api_key")
    @classmethod
    def _require_a_clean_admin_api_key(cls, value: SecretStr) -> SecretStr:
        _require_no_whitespace_or_control_chars(
            value.get_secret_value(),
            "ADMIN_API_KEY",
            "it is sent as an HTTP header value; a newline would allow header injection",
        )
        return value

    @field_validator("jwt_signing_key_previous", mode="before")
    @classmethod
    def _empty_previous_signing_key_means_none(cls, value: object) -> object:
        # An empty string (the common ".env.example placeholder when not
        # rotating" shape) means "no previous key" -- converted to None
        # here, before Field(min_length=32)'s own check would otherwise
        # reject "" as too short. Runs before type coercion, so `value` is
        # whatever pydantic-settings read from the environment (always a
        # str) -- anything else is passed through untouched and left to the
        # field's own type validation to reject.
        if value == "":
            return None
        return value

    @field_validator("jwt_signing_key_previous")
    @classmethod
    def _require_a_clean_previous_signing_key(
        cls, value: SecretStr | None
    ) -> SecretStr | None:
        if value is not None:
            _require_no_whitespace_or_control_chars(
                value.get_secret_value(),
                "JWT_SIGNING_KEY_PREVIOUS",
                "it is used as UTF-8 bytes to verify tokens",
            )
        return value

    @model_validator(mode="after")
    def _require_previous_signing_key_differs_from_current(self) -> "Settings":
        # Message names only the requirement, never either value, so a
        # match cannot leak which value the operator typed twice.
        if (
            self.jwt_signing_key_previous is not None
            and self.jwt_signing_key_previous.get_secret_value()
            == self.jwt_signing_key.get_secret_value()
        ):
            raise ValueError("JWT_SIGNING_KEY_PREVIOUS must differ from JWT_SIGNING_KEY.")
        return self

    def database_url_str(self) -> str:
        # The one explicit call that unwraps the secret. Never log this.
        return self.database_url.get_secret_value()

    def qdrant_api_key_str(self) -> str:
        # The one explicit call that unwraps the secret. Never log this.
        return self.qdrant_api_key.get_secret_value()

    def jwt_signing_key_str(self) -> str:
        # The one explicit call that unwraps the secret. Never log this.
        return self.jwt_signing_key.get_secret_value()

    def jwt_signing_key_previous_str(self) -> str | None:
        # The one explicit call that unwraps the secret. Never log this.
        if self.jwt_signing_key_previous is None:
            return None
        return self.jwt_signing_key_previous.get_secret_value()

    def db_connection_encryption_key_str(self) -> str:
        # The one explicit call that unwraps the secret. Never log this.
        return self.db_connection_encryption_key.get_secret_value()

    def admin_api_key_str(self) -> str:
        # The one explicit call that unwraps the secret. Never log this.
        return self.admin_api_key.get_secret_value()


class SettingsError(Exception):
    # Raised by get_settings() instead of pydantic's ValidationError. Its
    # message is a plain str captured from str(exc) before this exception is
    # raised, so it carries no reference to the original ValidationError
    # (no __cause__/__context__) and no raw input value.
    pass


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        message = str(exc)
    # Raised after leaving the except block on purpose: this is no longer
    # "while handling" exc, so Python does not implicitly chain __context__.
    raise SettingsError(message)
