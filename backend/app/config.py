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
# for db_connections.encrypted_credentials. Task 2.5.a adds openai_api_key
# (required): the dense-embedding primitive's own API credential.
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
    # Task 2.6.f: the stuck-job reaper's own staleness threshold (the
    # Step-2.1.e-owned marker, reassigned to Step 2.6 at 2.4.g's close-out)
    # -- how long a job may sit in "running" with no `updated_at` progress
    # before it's reclaimed. Same treatment as the two fields above (plain
    # configurable number, gt=0). 1800s (30 minutes): generous enough to
    # never false-positive on a genuinely long-running crawl at this
    # project's own current scale (the 500-page default `crawl_page_cap`,
    # 2.6.e, throttled at ~0.25s/request plus a real embedding call per
    # page realistically totals single-digit minutes, not 30), while still
    # bounding a genuinely crashed worker's own stuck row to "reclaimed
    # within half an hour" rather than forever -- retuned later with real
    # numbers, like every other estimate in this project.
    job_stuck_after_seconds: float = Field(default=1800, gt=0)
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
    # Task 2.5.a: the dense-embedding primitive's own API credential
    # (docs/SPEC.md §5.1: "an English embedding model such as
    # text-embedding-3-small"). Required, same discipline as qdrant_api_key/
    # admin_api_key -- an unauthenticated embedding call is not a real
    # failure mode (the API simply refuses it), but a missing key would
    # silently defer the failure from startup to the first real ingestion
    # job, which this project's own "fail loud at startup" pattern exists
    # to avoid. No min_length (matching qdrant_api_key's own treatment,
    # not jwt_signing_key's): this is an opaque, already-high-entropy
    # third-party-issued key, not a self-chosen secret needing an
    # artificial entropy floor.
    openai_api_key: SecretStr
    # Task 2.6.e: identifies the crawler on every robots.txt/sitemap/page
    # request it makes, matching the Step 2.6 decision entry's own
    # "Settings-configurable, not hardcoded" call -- a placeholder domain
    # (samoflow.app), since the real production domain isn't decided yet;
    # kept easy to change later rather than baked into a constant. Not a
    # secret (no SecretStr): a User-Agent string is sent in plaintext on
    # every outbound request by design, nothing to protect.
    crawl_user_agent: str = Field(default="SamoFlowBot/1.0 (+https://samoflow.app/bot)")
    # Task 2.6.e part 2: docs/SPEC.md §5.5 names "a delay" between
    # requests to the same host but gives no specific number (unlike the
    # fetch-limit numbers 2.3 owns, which §5.5 states exactly) -- same
    # "Settings-configurable, no spec number given" treatment as crawl_
    # user_agent above. 0.25s (250ms) matches ordinary polite-crawling
    # convention; retuned later with real numbers, like every other
    # estimate in this project (worker_poll_interval_seconds's own
    # precedent). The "2 concurrent requests per host" half of this same
    # §5.5 line IS spec-fixed (not described as configurable anywhere),
    # so it stays a plain module constant in app/ingest/crawl.py
    # (HostThrottle's own MAX_CONCURRENT_PER_HOST), not a Settings field.
    crawl_request_delay_seconds: float = Field(default=0.25, ge=0)
    # Task 2.6.e part 2: the "per-crawl safety cap" (docs/SPEC.md §5.5) --
    # deliberately NOT read from `plans.limits` here. Investigated live
    # before deciding, not assumed: `plans.limits` is confirmed (Task
    # 1.2.b's own header comment) to be "an unstructured JSONB blob...
    # until Phase 2/4 features define real limit fields," and `app/
    # plans/service.py`'s own `get_plan_limits()` is an explicit stub
    # (`raise NotImplementedError`, `TODO(4.3)`) -- real per-tenant plan-
    # limit reading is Task 4.3's own documented job, not this one's.
    # Building a real schema for `plans.limits` and a real "total
    # currently indexed pages" counting query here would preempt that
    # task's own design work and scope-creep well beyond "crawl wiring,
    # concurrency, and the heartbeat mechanism" (this task's own stated
    # scope). This Settings field is the smallest correct mechanism for
    # NOW (matching job_max_attempts's own identical-shape precedent) --
    # a single global safety ceiling, not yet per-plan. See the matching
    # Open marker: Task 4.3 should revisit this crawl handler's own page-
    # cap sourcing once real plan-limit reading exists. 500: a generous
    # but bounded safety default (this is a CEILING against a runaway
    # crawl, not a tight business limit) -- retuned later with real
    # numbers, like every other estimate in this project.
    crawl_page_cap: int = Field(default=500, gt=0)
    # Task 2.7.a: where the upload adapter's raw file bytes live on disk
    # (docs/SPEC.md §5.6: "originals stored privately") -- a plain
    # string, not pathlib.Path (matching every other path-like setting in
    # this project so far, e.g. database_url's own string treatment);
    # app/ingest/upload_storage.py turns it into a Path at the call site.
    # Mounted as a Docker volume shared between `api` and `worker`
    # (deploy/docker-compose.yml, Step 2.7 breakdown decision (b)) at the
    # same path this default names, so no override is needed in a real
    # deployment -- only tests override it (to a pytest tmp_path).
    # CONFIRMED at Task 2.7.f (2026-10-09): "/data/uploads" is the real,
    # accepted convention -- mirrored exactly in deploy/docker-compose.yml's
    # upload-storage volume mount, so the default needs no override in a
    # real deployment. (Resolves this field's own original ASSUMPTION,
    # recorded at Task 2.7.a -- see PROJECT_SPEC.md's Step 2.7 closure
    # summary.)
    upload_storage_path: str = Field(default="/data/uploads", min_length=1)
    # Task 2.7.a: the upload adapter's own per-file size ceiling
    # (docs/SPEC.md §5.4: "upload storage and file size... from the
    # plan") -- deliberately NOT sourced from `plans.limits` yet, same
    # already-established reason as `crawl_page_cap` directly above
    # (`plans.limits` is still an unstructured JSONB stub, Task 4.3's own
    # job; see the matching Open marker). 20MB (20 * 1024 * 1024): a
    # generous ceiling for a real PDF/DOCX/TXT/MD business document
    # (including a scanned, image-heavy PDF), while still bounding a
    # single upload job's worst-case parse time/memory -- distinct from,
    # and not to be confused with, `safe_fetch.py`'s own 5MB-per-page web-
    # fetch limit (2.3), which bounds a different thing (one HTML page
    # fetched over the network) for a different reason. Retuned later
    # with real numbers, like every other estimate in this project.
    upload_max_size_bytes: int = Field(default=20 * 1024 * 1024, gt=0)
    # Task 2.7.b: the per-member decompressed-size ceiling for a `.docx`
    # upload's own internal ZIP structure (app/ingest/upload_sniff.py's
    # sniff_file_type()) -- a zip-bomb defense, investigated live before
    # building (see the decision log entry): python-docx itself (via
    # `_ZipPkgReader.blob_for()` -> `zipfile.ZipFile.read()`) has ZERO
    # protection against a tiny-on-disk `.docx` whose own internal XML
    # part decompresses to gigabytes, confirmed live by constructing one
    # (a 200MB payload compressing to ~204KB, well under
    # `upload_max_size_bytes` above and so never caught by that check).
    # NOT sourced from `plans.limits` -- this isn't a tenant-facing
    # business limit at all (no docs/SPEC.md line names it), it is a pure
    # internal safety ceiling, same treatment as `job_stuck_after_seconds`
    # above (also Settings-configurable with no corresponding spec
    # number). 100MB: generous for any real Word document's own largest
    # single internal XML part (even a very large real document's own
    # word/document.xml is realistically a few MB at most), while still
    # meaningfully bounding a single decompression call's worst-case
    # memory use. Retuned later with real numbers, like every other
    # estimate in this project.
    docx_max_part_size_bytes: int = Field(default=100 * 1024 * 1024, gt=0)
    # Task 2.8.b: the database-sync adapter's own per-query row cap
    # (docs/SPEC.md §5.4: "maximum synced database rows... from the plan")
    # -- deliberately NOT sourced from `plans.limits` yet, same
    # already-established reason as `crawl_page_cap`/`upload_max_size_bytes`
    # above (`plans.limits` is still an unstructured JSONB stub, Task 4.3's
    # own job; see the matching Open marker). 10,000: generous for a real
    # reporting-style sync query, while still bounding a single job's
    # worst-case memory/transfer cost. Read ONCE by a real future caller
    # (2.8.e's own job handler, not yet built) and passed to app/ingest/
    # database_adapter.py's fetch_readonly_rows() as a plain parameter --
    # that primitive itself stays Settings-free, matching run_crawl()'s/
    # ingest_upload()'s own established precedent.
    db_sync_row_cap: int = Field(default=10_000, gt=0)
    # Task 2.8.b: the database-sync adapter's own per-query statement
    # timeout (docs/SPEC.md §5.6: "statement timeouts... apply") --
    # enforced via Postgres's own real `statement_timeout` mechanism
    # (fetch_readonly_rows(), confirmed live), not an application-level
    # asyncio.wait_for() race. NOT sourced from `plans.limits` -- this is
    # a pure internal safety ceiling, same treatment as `docx_max_part_
    # size_bytes` above (no corresponding per-tenant number in docs/
    # SPEC.md). 30s: generous for a real synchronous reporting query
    # against a tenant's own database, while still bounding a single
    # job's worst-case time spent waiting on one query.
    db_sync_statement_timeout_seconds: float = Field(default=30.0, gt=0)

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

    @field_validator("openai_api_key")
    @classmethod
    def _require_a_clean_openai_api_key(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        # Message names only the requirement, never the value under
        # validation, so a bad key cannot leak here.
        if not raw:
            raise ValueError("OPENAI_API_KEY must not be empty.")
        _require_no_whitespace_or_control_chars(
            raw,
            "OPENAI_API_KEY",
            "it is sent as an HTTP Authorization header value; a newline would allow "
            "header injection",
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

    def openai_api_key_str(self) -> str:
        # The one explicit call that unwraps the secret. Never log this.
        return self.openai_api_key.get_secret_value()


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
