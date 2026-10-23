#!/usr/bin/env python3
# ops/deploy/check_env_secrets.py -- [SECURITY] L1 (out-of-band Phase 7
# batch, 2026-10-17), PART B: the one Python half of ops/deploy/
# preflight.sh's own checks, reusing backend/app/secrets_policy.py
# directly -- one source of truth for "which values are secret-class"
# and "what counts as weak", never a second, bash-side copy of the same
# policy. Deliberately standalone (no pydantic/pydantic-settings import,
# no `app.config` import): secrets_policy.py itself has zero third-party
# imports, so this script runs with the bare system python3 already on
# any Ubuntu server, before any Docker image has necessarily been built
# yet (a fresh server's very first deploy has no image to run this
# inside).
#
# Parses a real .env file's raw KEY=VALUE text (the exact format Docker
# Compose's own --env-file reads) -- never imports or executes it, so a
# malformed or hostile line cannot do anything beyond fail this parse.
# Prints nothing but PASS/FAIL lines and variable NAMES; never a value.
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.secrets_policy import (  # noqa: E402
    ALL_SECRET_ENV_SETTINGS,
    check_credential_consistency,
    check_raw_secret_values,
)


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # .env values are not shell-quoted by this project's own
        # .env.example (confirmed by reading it directly) -- a leading/
        # trailing matched quote pair is stripped the same way Docker
        # Compose's own --env-file parser does, so a quoted placeholder
        # is still recognized as the same placeholder.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_env_secrets.py <path-to-.env>", file=sys.stderr)
        return 2
    env_path = Path(argv[1])
    if not env_path.is_file():
        print(f"FAIL: {env_path} not found", file=sys.stderr)
        return 1

    raw = parse_env_file(env_path)

    missing = [
        setting.env_var
        for setting in ALL_SECRET_ENV_SETTINGS
        if not setting.optional and not raw.get(setting.env_var)
    ]
    if missing:
        for name in missing:
            print(f"FAIL: {name} is required but missing or empty in {env_path}", file=sys.stderr)
        return 1

    # [SECURITY] L2 follow-up (2026-10-18): ALL_SECRET_ENV_SETTINGS also
    # covers the deploy-layer secrets no Settings field reads
    # (POSTGRES_PASSWORD, POSTGRES_OWNER_PASSWORD, ...), and the OWNER
    # credential's two copies must agree.
    raw_or_none = {k: (v or None) for k, v in raw.items()}
    violations = check_raw_secret_values(raw_or_none) + check_credential_consistency(
        raw_or_none
    )
    if violations:
        for v in violations:
            print(f"FAIL: {v}", file=sys.stderr)
        return 1

    print(
        "OK: every secret-class variable is present, not a known placeholder/weak "
        "value, and the OWNER credential's two copies agree"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
