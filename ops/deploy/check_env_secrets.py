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

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.secrets_policy import (  # noqa: E402
    ALL_SECRET_ENV_SETTINGS,
    check_credential_consistency,
    check_raw_secret_values,
)


def parse_env_file(path: Path) -> tuple[dict[str, str], set[str]]:
    # Returns (values, interpolated): values as Docker Compose's own
    # --env-file parser reads them, plus the keys whose value contains a
    # "$" that Compose would INTERPOLATE -- for those, the text in the file
    # is not the value the containers get.
    #
    # [SECURITY] L2 follow-up adversarial pass (2026-10-18), each rule
    # confirmed live against `docker compose config` (Compose v5.1.4), not
    # assumed: the earlier parser read raw text only, so
    #   WEAK=change-me / POSTGRES_PASSWORD=${WEAK}
    # resolved to "change-me" in the container while this check saw the
    # literal "${WEAK}" and passed it; an `export ` prefix hid the key
    # entirely; and an inline " # comment" stayed glued to the value.
    # Compose's rules, mirrored here:
    #   - an optional leading `export ` is ignored;
    #   - a single-quoted value is literal (no interpolation) and ends at
    #     the closing quote; a double-quoted value ends at its closing
    #     quote but IS interpolated; anything after the closing quote is
    #     ignored;
    #   - an unquoted value ends at the first " #" (inline comment) and IS
    #     interpolated ("pa$word" becomes "pa").
    values: dict[str, str] = {}
    interpolated: set[str] = set()
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        quote = value[:1]
        closing = value.find(quote, 1) if quote in ("'", '"') else -1
        if closing != -1:
            value = value[1:closing]
            may_interpolate = quote == '"'
        else:
            value = re.split(r"\s#", value, maxsplit=1)[0].rstrip()
            may_interpolate = True
        if may_interpolate and "$" in value:
            interpolated.add(key)
        else:
            interpolated.discard(key)
        values[key] = value
    return values, interpolated


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_env_secrets.py <path-to-.env>", file=sys.stderr)
        return 2
    env_path = Path(argv[1])
    if not env_path.is_file():
        print(f"FAIL: {env_path} not found", file=sys.stderr)
        return 1

    raw, interpolated = parse_env_file(env_path)

    # Fail closed: a secret-class value Compose would interpolate cannot
    # be verified from the file's text -- the real value is whatever the
    # "${...}" expands to. A literal "$" inside a real password must be
    # single-quoted anyway, or Compose would silently mangle it.
    dynamic = sorted(
        setting.env_var for setting in ALL_SECRET_ENV_SETTINGS if setting.env_var in interpolated
    )
    if dynamic:
        for name in dynamic:
            print(
                f"FAIL: {name} contains a '$' that Docker Compose would interpolate, so "
                "its real value cannot be checked -- write the literal value, "
                "single-quoted if it contains '$'",
                file=sys.stderr,
            )
        return 1

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
