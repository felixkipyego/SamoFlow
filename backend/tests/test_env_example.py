# backend/tests/test_env_example.py
# Tests for Task 1.1.d's .env.example: it must declare exactly the
# variables Settings (Task 1.1.c) requires, each documented and safe to
# commit, and loadable as-is into a working Settings instance.
from pathlib import Path
from urllib.parse import urlsplit

from app.config import Settings, get_settings
from tests.conftest import REQUIRED_VARS

# NOTE: adding a key here is a deliberate decision; add it in the task that needs it
# Task 1.1.i: docker-compose.yml's postgres service takes these three
# directly (not via Settings), so .env.example must declare them too.
# Task 1.4.a: JWT_SIGNING_KEY_PREVIOUS is an optional Settings field (has a
# default, so REQUIRED_VARS excludes it), but .env.example still declares it
# (empty) so an operator seeing the file knows the variable exists. Task
# 3.4.a: ANTHROPIC_API_KEY is the identical shape (optional, empty default).
EXTRA_EXAMPLE_KEYS: tuple[str, ...] = (
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    # [SECURITY] L2 follow-up (advisor review, 2026-10-18): Settings.
    # database_url_migrate is now Optional (SecretStr | None, no
    # unconditional default REQUIRED everywhere) -- api/worker's own
    # Settings() construction must succeed without it (deploy/docker-
    # compose.yml's x-app-env no longer carries it at all), so
    # REQUIRED_VARS (derived from field.is_required()) no longer
    # includes it. .env.example still declares a REAL value here (never
    # left empty): migrate/provision-roles genuinely need one, and
    # ops/deploy/check_env_secrets.py's own SECRET_CLASS_SETTINGS entry
    # (app/secrets_policy.py) still enforces that on the real server.
    "DATABASE_URL_MIGRATE",
    "JWT_SIGNING_KEY_PREVIOUS",
    "ANTHROPIC_API_KEY",
    # Step 4.5: PHOENIX_COLLECTOR_ENDPOINT is the identical shape --
    # optional (has a default, so REQUIRED_VARS excludes it), but
    # .env.example still declares it (empty) so an operator sees it.
    "PHOENIX_COLLECTOR_ENDPOINT",
    # Step 7.3 prep (ops track): SMTP_* (Task 4.3, backend/app/
    # notifications/service.py) and UPLOAD_MAX_SIZE_BYTES (Task 2.7.a)
    # are the identical "optional Settings field, has a default, but
    # .env.example still declares it" shape as the entries above -- not
    # new variable NAMES, just newly documented ones. UPLOAD_MAX_SIZE_
    # BYTES is declared now specifically because deploy/Caddyfile's own
    # request_body limit reads this SAME variable (one source of truth
    # for the upload cap, never a second Caddy-only number).
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "SMTP_FROM_ADDRESS",
    "UPLOAD_MAX_SIZE_BYTES",
    # Step 6.2: the original Task 1.5.c §9 marker's own "document these
    # four optional rate-limit fields in .env.example" -- optional
    # (each has a default), declared non-empty (their real default
    # values) so an operator sees both the variable and what it
    # defaults to, matching the identical shape already established.
    "SESSION_RATE_LIMIT_PER_SITE_KEY",
    "SESSION_RATE_LIMIT_PER_SITE_KEY_WINDOW_SECONDS",
    "SESSION_RATE_LIMIT_PER_IP",
    "SESSION_RATE_LIMIT_PER_IP_WINDOW_SECONDS",
    # Step 6.2: the install-snippet screen's own platform-wide base URL --
    # optional (defaults to None), declared empty like ANTHROPIC_API_KEY.
    "PUBLIC_API_BASE_URL",
    # [SECURITY] Step 7-follow-up, Part C: GET /api/v1/widget-appearance's
    # own rate limits -- the identical "optional Settings field, has a
    # default, declared non-empty with its real default" shape as the
    # SESSION_RATE_LIMIT_* entries above.
    "WIDGET_APPEARANCE_RATE_LIMIT_PER_SITE_KEY",
    "WIDGET_APPEARANCE_RATE_LIMIT_PER_SITE_KEY_WINDOW_SECONDS",
    "WIDGET_APPEARANCE_RATE_LIMIT_PER_IP",
    "WIDGET_APPEARANCE_RATE_LIMIT_PER_IP_WINDOW_SECONDS",
    # [SECURITY] batch follow-up (2026-10-16): POST /chat/stop's own new
    # rate limits -- same shape as the two blocks above.
    "CHAT_STOP_RATE_LIMIT_PER_VISITOR",
    "CHAT_STOP_RATE_LIMIT_PER_VISITOR_WINDOW_SECONDS",
    "CHAT_STOP_RATE_LIMIT_PER_IP",
    "CHAT_STOP_RATE_LIMIT_PER_IP_WINDOW_SECONDS",
    # [SECURITY] L1 PART C (#7) (out-of-band Phase 7 batch, 2026-10-17):
    # API_HOSTNAME is now ALSO a real (optional) Settings field --
    # app/main.py's TrustedHostMiddleware reads it, in production, as
    # well as deploy/Caddyfile's own {$API_HOSTNAME} site block -- so it
    # moves here from OPS_ONLY_EXAMPLE_KEYS below, which this file's own
    # header comment says is only for names with NO Settings field.
    # TRUSTED_PROXY_HOSTNAME is a new, backend-only field (declared with
    # its real, non-empty default "caddy", matching the SESSION_RATE_
    # LIMIT_* entries' own established shape).
    "API_HOSTNAME",
    "TRUSTED_PROXY_HOSTNAME",
    # [SECURITY] L1 PART C follow-up (advisor review, 2026-10-17):
    # DASHBOARD_HOSTNAME is now ALSO a real (optional) Settings field,
    # the SAME shape as API_HOSTNAME's own move above -- moves here from
    # OPS_ONLY_EXAMPLE_KEYS below for the identical reason (a real
    # production-breaking bug: the dashboard SPA's own /api/* calls
    # arrive at the api service with Host = this value, and app/main.py's
    # TrustedHostMiddleware needed to know it).
    "DASHBOARD_HOSTNAME",
)

# Step 7.3 prep (ops track): genuinely new deploy/ops-layer configuration
# with NO backend Settings field at all -- Caddy hostnames, backup/S3
# credentials, monitoring thresholds. Kept in a SEPARATELY named tuple
# from EXTRA_EXAMPLE_KEYS above on purpose: every EXTRA_EXAMPLE_KEYS
# entry corresponds to a real (optional) Settings field sharing that
# exact env var name; every OPS_ONLY_EXAMPLE_KEYS entry does not, and
# never should -- a name landing in the wrong tuple would hide a real
# "this duplicates a Settings field under a new name" mistake. See
# docs/deploy/RUNBOOK.md for what each one configures.
OPS_ONLY_EXAMPLE_KEYS: tuple[str, ...] = (
    # [SECURITY] L2 (Phase 7 role split, 2026-10-18): bare OWNER-role
    # credentials -- no Settings field reads these directly (the backend
    # reads DATABASE_URL_MIGRATE, a real Settings field, instead); only
    # ops/backup/docker-compose.yml's PGUSER/PGPASSWORD need the bare
    # username/password, which a DSN cannot supply to plain YAML
    # interpolation.
    "POSTGRES_OWNER_USER",
    "POSTGRES_OWNER_PASSWORD",
    "WIDGET_HOSTNAME",
    "CADDY_ACME_EMAIL",
    "DASHBOARD_DIST_DIR",
    "WIDGET_DIST_DIR",
    "BACKUP_AGE_PUBLIC_KEY",
    "QDRANT_COLLECTION_NAME",
    "S3_BACKUP_BUCKET",
    "S3_BACKUP_ENDPOINT_URL",
    "S3_BACKUP_RETENTION_DAYS",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_DEFAULT_REGION",
    "MONITORING_DISK_PATH",
    "MONITORING_DISK_THRESHOLD_PERCENT",
    "MONITORING_MEMORY_THRESHOLD_PERCENT",
    "MONITORING_ALERT_EMAIL",
)

HEADER_WARNING = "# Copy to .env and edit. Never commit .env."

# Derived from this test file's own location, not the current working
# directory, so the test passes regardless of where pytest is invoked from.
ENV_EXAMPLE_PATH = Path(__file__).resolve().parent.parent.parent / ".env.example"


def _lines():
    assert ENV_EXAMPLE_PATH.exists(), f".env.example not found at {ENV_EXAMPLE_PATH}"
    return ENV_EXAMPLE_PATH.read_text().splitlines()


def _classify_lines(lines):
    # Classifies every line as "blank", "comment" or "variable". Any
    # non-blank, non-comment line must contain "=" to be a variable line;
    # anything else is a parse error naming the exact offending line, not a
    # silently-wrong classification.
    classified = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            classified.append((i, "blank"))
        elif stripped.startswith("#"):
            classified.append((i, "comment"))
        else:
            assert "=" in line, (
                f"line {i + 1} ({line!r}) is not blank or a comment but has "
                "no '=': cannot be parsed as KEY=VALUE"
            )
            classified.append((i, "variable"))
    return classified


def _variable_line_indexes(lines):
    return [i for i, kind in _classify_lines(lines) if kind == "variable"]


def _parse_env_example():
    # Plain parse: skip blank lines and comment lines, split each other
    # line at the first "=". Returns key -> value for the last occurrence
    # of each key (duplicate-key detection is a separate test). Rejects an
    # inline " #" comment on a value line, since a naive loader (this one
    # included) would otherwise fold the comment into the value.
    lines = _lines()
    values = {}
    for i in _variable_line_indexes(lines):
        line = lines[i]
        key, _, value = line.partition("=")
        key = key.strip()
        assert " #" not in value, (
            f"{key}'s value ({value!r}) contains an inline comment ('  #'); "
            "a plain loader would fold this into the value -- put the "
            "comment on its own line above the variable instead"
        )
        values[key] = value
    return values


def test_no_settings_field_has_a_custom_alias():
    # REQUIRED_VARS maps each field to an env var name by plain
    # field_name.upper(). A custom alias would silently break that mapping
    # without any of the other tests here noticing.
    for name, field in Settings.model_fields.items():
        assert field.alias is None, (
            f"Settings field {name!r} has alias={field.alias!r}; this file's "
            "REQUIRED_VARS assumes env var names are exactly "
            "field_name.upper(), which a custom alias would break"
        )
        assert field.validation_alias is None, (
            f"Settings field {name!r} has validation_alias="
            f"{field.validation_alias!r}; this file's REQUIRED_VARS assumes "
            "env var names are exactly field_name.upper(), which a custom "
            "validation_alias would break"
        )
        assert field.serialization_alias is None, (
            f"Settings field {name!r} has serialization_alias="
            f"{field.serialization_alias!r}; this file's REQUIRED_VARS "
            "assumes env var names are exactly field_name.upper(), which a "
            "custom serialization_alias would break"
        )


def test_variable_names_match_settings_fields_plus_extras():
    required = set(REQUIRED_VARS) | set(EXTRA_EXAMPLE_KEYS) | set(OPS_ONLY_EXAMPLE_KEYS)
    lines = _lines()
    found = {lines[i].partition("=")[0].strip() for i in _variable_line_indexes(lines)}
    assert found == required


def test_every_variable_line_has_a_comment_line_directly_above_it():
    lines = _lines()
    for i in _variable_line_indexes(lines):
        assert i > 0, f"line {i} ({lines[i]!r}) has no line above it"
        above = lines[i - 1].strip()
        assert above.startswith("#"), (
            f"line {i} ({lines[i]!r}) is not directly preceded by a comment "
            f"(previous line was {lines[i - 1]!r})"
        )


def test_each_variable_appears_exactly_once():
    lines = _lines()
    keys = [lines[i].partition("=")[0].strip() for i in _variable_line_indexes(lines)]
    for key in set(keys):
        assert keys.count(key) == 1, f"{key} appears {keys.count(key)} times"


def test_parsed_values_load_into_a_working_settings_instance(monkeypatch):
    values = _parse_env_example()
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    settings = get_settings()
    assert settings.app_env == "development"
    assert settings.qdrant_url == "http://qdrant:6333"
    assert settings.api_host == "0.0.0.0"  # noqa: S104 (the example's own placeholder, not a live bind)
    assert settings.api_port == 8000
    assert isinstance(settings.api_port, int)


def test_database_url_password_placeholder_is_exactly_change_me():
    values = _parse_env_example()
    password = urlsplit(values["DATABASE_URL"]).password
    assert password == "change-me"  # noqa: S105 (asserting the placeholder value, not a real secret)


def test_jwt_signing_key_placeholder_is_at_least_32_characters_and_starts_with_change_me():
    values = _parse_env_example()
    placeholder = values["JWT_SIGNING_KEY"]
    assert len(placeholder) >= 32
    assert placeholder.startswith("change-me")


def test_jwt_signing_key_previous_placeholder_is_empty():
    values = _parse_env_example()
    assert values["JWT_SIGNING_KEY_PREVIOUS"] == ""


def test_anthropic_api_key_placeholder_is_empty():
    values = _parse_env_example()
    assert values["ANTHROPIC_API_KEY"] == ""


def test_db_connection_encryption_key_placeholder_is_32_chars_and_starts_with_change_me():
    values = _parse_env_example()
    placeholder = values["DB_CONNECTION_ENCRYPTION_KEY"]
    assert len(placeholder) >= 32
    assert placeholder.startswith("change-me")


def test_first_non_blank_line_is_the_header_warning():
    lines = _lines()
    first_non_blank = next(line for line in lines if line.strip())
    assert first_non_blank == HEADER_WARNING, (
        f"first non-blank line is {first_non_blank!r}, expected {HEADER_WARNING!r}"
    )


def test_env_example_path_is_independent_of_current_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert ENV_EXAMPLE_PATH.is_absolute()
    assert ENV_EXAMPLE_PATH.exists()
