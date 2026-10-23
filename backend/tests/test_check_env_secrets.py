# backend/tests/test_check_env_secrets.py
# [SECURITY] L2 follow-up (advisor review of eedad4e, 2026-10-18): runs
# the REAL ops/deploy/check_env_secrets.py (preflight.sh's own secret
# check) as a subprocess against real .env files -- end to end, the same
# way preflight.sh invokes it, not by importing its internals. Before this
# file, the script had no test at all.
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_env_example import _parse_env_example

SCRIPT = Path(__file__).resolve().parents[2] / "ops" / "deploy" / "check_env_secrets.py"

# Non-placeholder values for every secret-class variable .env.example
# fills with "change-me..." -- the owner password appears twice, in the
# DSN and bare, exactly as a real .env must carry it.
_OWNER_PASSWORD = "owner-pw-for-check-env-secrets-tests"  # noqa: S105
_REALISTIC = {
    "POSTGRES_PASSWORD": "bootstrap-pw-for-check-env-secrets-tests",
    "DATABASE_URL_MIGRATE": (
        f"postgresql+psycopg://widgetplatform_owner:{_OWNER_PASSWORD}@postgres:5432/widgetplatform"
    ),
    "POSTGRES_OWNER_PASSWORD": _OWNER_PASSWORD,
    "DATABASE_URL": (
        "postgresql+psycopg://widgetplatform_app:app-pw-for-check-env-secrets-tests"
        "@postgres:5432/widgetplatform"
    ),
    "QDRANT_API_KEY": "qdrant-key-for-check-env-secrets-tests",
    "QDRANT_READ_ONLY_API_KEY": "qdrant-ro-key-for-check-env-secrets-tests",
    "JWT_SIGNING_KEY": "jwt-key-for-check-env-secrets-tests-32-chars",
    "DB_CONNECTION_ENCRYPTION_KEY": "dbconn-key-for-check-env-secrets-tests-32ch",
    "OPENAI_API_KEY": "openai-key-for-check-env-secrets-tests",
}


def _write_env(tmp_path: Path, **overrides: str | None) -> Path:
    values = {**_parse_env_example(), **_REALISTIC}
    for key, value in overrides.items():
        if value is None:
            values.pop(key, None)
        else:
            values[key] = value
    path = tmp_path / ".env"
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    return path


def _run(env_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 (fixed args: sys.executable + a path, not user input)
        [sys.executable, str(SCRIPT), str(env_path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_realistic_env_passes(tmp_path):
    result = _run(_write_env(tmp_path))
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("OK:")


def test_env_example_as_is_fails_naming_both_postgres_passwords(tmp_path):
    # The advisor's exact scenario: an operator copies .env.example to
    # .env. Before this fix, POSTGRES_PASSWORD's own "change-me" was never
    # checked by anything.
    path = tmp_path / ".env"
    path.write_text("".join(f"{k}={v}\n" for k, v in _parse_env_example().items()))
    result = _run(path)
    assert result.returncode == 1
    assert "POSTGRES_PASSWORD is set to a known placeholder" in result.stderr
    assert "POSTGRES_OWNER_PASSWORD is set to a known placeholder" in result.stderr


@pytest.mark.parametrize("env_var", ["POSTGRES_PASSWORD", "POSTGRES_OWNER_PASSWORD"])
@pytest.mark.parametrize("weak_value", ["change-me", "change-me-owner", "test-pw", "smoke-pw"])
def test_placeholder_or_weak_postgres_password_alone_fails(tmp_path, env_var, weak_value):
    overrides = {env_var: weak_value}
    if env_var == "POSTGRES_OWNER_PASSWORD":
        # Keep the two copies consistent, so ONLY the weak-value rule is
        # under test here.
        overrides["DATABASE_URL_MIGRATE"] = (
            f"postgresql+psycopg://widgetplatform_owner:{weak_value}@postgres:5432/widgetplatform"
        )
    result = _run(_write_env(tmp_path, **overrides))
    assert result.returncode == 1
    assert f"{env_var} is set to a known placeholder" in result.stderr
    assert weak_value not in result.stderr + result.stdout


@pytest.mark.parametrize("env_var", ["POSTGRES_PASSWORD", "POSTGRES_OWNER_PASSWORD"])
@pytest.mark.parametrize("missing", [None, ""])
def test_missing_or_empty_postgres_password_fails(tmp_path, env_var, missing):
    result = _run(_write_env(tmp_path, **{env_var: missing}))
    assert result.returncode == 1
    assert f"{env_var} is required but missing or empty" in result.stderr


def test_owner_password_mismatch_fails_naming_variables_never_values(tmp_path):
    other = "a-different-owner-pw-value"  # noqa: S105
    result = _run(_write_env(tmp_path, POSTGRES_OWNER_PASSWORD=other))
    assert result.returncode == 1
    assert "POSTGRES_OWNER_PASSWORD does not match the password inside" in result.stderr
    assert "DATABASE_URL_MIGRATE" in result.stderr
    output = result.stderr + result.stdout
    assert other not in output
    assert _OWNER_PASSWORD not in output


def test_owner_user_mismatch_fails(tmp_path):
    result = _run(_write_env(tmp_path, POSTGRES_OWNER_USER="widgetplatform_app"))
    assert result.returncode == 1
    assert "POSTGRES_OWNER_USER does not match the username inside" in result.stderr


def test_quoted_values_are_compared_after_unquoting(tmp_path):
    # Docker Compose strips a matched quote pair; so does this script --
    # a quoted bare password must still match the DSN's unquoted one.
    result = _run(_write_env(tmp_path, POSTGRES_OWNER_PASSWORD=f'"{_OWNER_PASSWORD}"'))
    assert result.returncode == 0, result.stderr


def test_unset_aws_secret_access_key_is_not_required_but_a_placeholder_fails(tmp_path):
    assert _run(_write_env(tmp_path, AWS_SECRET_ACCESS_KEY=None)).returncode == 0
    result = _run(_write_env(tmp_path, AWS_SECRET_ACCESS_KEY="change-me"))  # noqa: S106
    assert result.returncode == 1
    assert "AWS_SECRET_ACCESS_KEY" in result.stderr


# --- Adversarial pass (2026-10-18): the .env text must be read the way
# Docker Compose reads it, or the check verifies a different value than
# the containers get. Each rule below was confirmed live against `docker
# compose config` (Compose v5.1.4) before being encoded here.


def _append(path: Path, text: str) -> Path:
    path.write_text(path.read_text() + text)
    return path


def test_interpolated_placeholder_is_refused_not_passed(tmp_path):
    # Compose resolves POSTGRES_PASSWORD=${WEAK} to "change-me"; the old
    # raw-text parser saw "${WEAK}" and passed it.
    path = _append(_write_env(tmp_path), "WEAK=change-me\nPOSTGRES_PASSWORD=${WEAK}\n")
    result = _run(path)
    assert result.returncode == 1
    assert "POSTGRES_PASSWORD contains a '$'" in result.stderr
    assert "change-me" not in result.stderr


@pytest.mark.parametrize("line", ['POSTGRES_PASSWORD="${WEAK}"', "POSTGRES_PASSWORD=pa$word"])
def test_double_quoted_or_bare_dollar_in_a_secret_fails_closed(tmp_path, line):
    result = _run(_append(_write_env(tmp_path), f"{line}\n"))
    assert result.returncode == 1
    assert "POSTGRES_PASSWORD contains a '$'" in result.stderr


def test_single_quoted_dollar_is_literal_and_accepted(tmp_path):
    # Compose never interpolates inside single quotes.
    result = _run(_append(_write_env(tmp_path), "POSTGRES_PASSWORD='bootstrap-pw-with-$-sign'\n"))
    assert result.returncode == 0, result.stderr


def test_dollar_in_a_non_secret_variable_is_not_this_checks_concern(tmp_path):
    result = _run(_append(_write_env(tmp_path), "MONITORING_DISK_PATH=${HOME}\n"))
    assert result.returncode == 0, result.stderr


def test_export_prefix_does_not_hide_a_placeholder(tmp_path):
    # Compose accepts `export KEY=value`; the old parser read the key as
    # "export AWS_SECRET_ACCESS_KEY" and so never checked this optional one.
    path = _append(
        _write_env(tmp_path, AWS_SECRET_ACCESS_KEY=None), "export AWS_SECRET_ACCESS_KEY=change-me\n"
    )
    result = _run(path)
    assert result.returncode == 1
    assert "AWS_SECRET_ACCESS_KEY is set to a known placeholder" in result.stderr


def test_duplicate_key_last_one_wins_like_compose(tmp_path):
    path = _append(_write_env(tmp_path), "POSTGRES_PASSWORD=change-me\n")
    result = _run(path)
    assert result.returncode == 1
    assert "POSTGRES_PASSWORD is set to a known placeholder" in result.stderr


@pytest.mark.parametrize(
    "line",
    [
        f"POSTGRES_OWNER_PASSWORD={_OWNER_PASSWORD} # rotated 2026-10",
        f'POSTGRES_OWNER_PASSWORD="{_OWNER_PASSWORD}" # rotated 2026-10',
        f"POSTGRES_OWNER_PASSWORD='{_OWNER_PASSWORD}'   # rotated",
    ],
)
def test_inline_comment_is_not_part_of_the_value(tmp_path, line):
    # Compose strips " # ..." (and anything after a closing quote), so the
    # bare password still matches the DSN's.
    result = _run(_append(_write_env(tmp_path), f"{line}\n"))
    assert result.returncode == 0, result.stderr


def test_quoted_placeholder_with_trailing_comment_is_still_caught(tmp_path):
    # The old parser only unquoted a value whose LAST char was the quote,
    # so '"change-me" # x' kept its leading quote and dodged the prefix check.
    result = _run(_append(_write_env(tmp_path), 'POSTGRES_PASSWORD="change-me" # x\n'))
    assert result.returncode == 1
    assert "POSTGRES_PASSWORD is set to a known placeholder" in result.stderr


def test_percent_encoded_placeholder_inside_a_dsn_is_caught(tmp_path):
    dsn = "postgresql+psycopg://widgetplatform_app:change%2Dme@postgres:5432/widgetplatform"
    result = _run(_write_env(tmp_path, DATABASE_URL=dsn))
    assert result.returncode == 1
    assert "DATABASE_URL is set to a known placeholder" in result.stderr
