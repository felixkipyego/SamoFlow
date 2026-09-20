# backend/tests/test_hardening_guard.py
# Duplication/simplification review after 1.1.g/h/i, item C: a plain-text
# regression guard for the container-hardening decisions in
# backend/Dockerfile and deploy/docker-compose.yml (non-root USER, no
# ":latest" tag, no .env copied into the image; the x-app-hardening anchor's
# settings and that api/worker/migrate all merge it; every published port
# bound to 127.0.0.1). These were verified by hand in 1.1.g/1.1.i's task
# reports with no automated check behind them -- a later edit could revert
# any of them silently. Deliberately no YAML or Dockerfile parser (no new
# dependency, no Docker needed at test time): line-based text checks only,
# the same style test_env_example.py already uses for .env.example.
#
# The checking logic (_check_dockerfile/_check_compose) takes the file text
# as a plain string, not a path, so it can be exercised on an edited copy of
# the text without ever touching the real files -- see this module's own
# review notes for how each rule was proved able to fail.
import re
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
DOCKERFILE_PATH = BACKEND_DIR / "Dockerfile"
COMPOSE_PATH = REPO_ROOT / "deploy" / "docker-compose.yml"

_APP_SERVICES = ("api", "worker", "migrate")


def _block_after(lines, header_index):
    # Every line strictly more indented than the header, stopping at the
    # first non-blank line that is not (blank lines inside the block, e.g.
    # inside a YAML mapping, do not end it).
    header_indent = len(lines[header_index]) - len(lines[header_index].lstrip(" "))
    block = []
    for line in lines[header_index + 1 :]:
        if line.strip() == "":
            block.append(line)
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent <= header_indent:
            break
        block.append(line)
    return block


def _check_dockerfile(text: str) -> list[str]:
    lines = text.splitlines()
    violations = []

    user_directives = [
        line.strip().split(None, 1)[1]
        for line in lines
        if re.match(r"^USER\s+\S+", line.strip())
    ]
    if not user_directives:
        violations.append("no USER directive found (the final stage must set USER 10001)")
    elif user_directives[-1] != "10001":
        violations.append(
            f"final USER directive is {user_directives[-1]!r}, expected '10001'"
        )

    if ":latest" in text:
        violations.append("references a ':latest' tag (base image must be pinned)")

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("COPY") and ".env" in stripped:
            violations.append(f"copies .env into the image: {stripped!r}")

    return violations


def _find_line(lines, predicate):
    for i, line in enumerate(lines):
        if predicate(line):
            return i
    return None


def _check_compose(text: str) -> list[str]:
    lines = text.splitlines()
    violations = []

    hardening_index = _find_line(
        lines, lambda line: line.strip().startswith("x-app-hardening:")
    )
    if hardening_index is None:
        violations.append("x-app-hardening anchor not found")
    else:
        block_text = "\n".join(_block_after(lines, hardening_index))
        required = [
            ("read_only: true", "read_only: true"),
            ("cap_drop:", "a cap_drop: key"),
            ("- ALL", "cap_drop: [ALL]"),
            ("no-new-privileges:true", "no-new-privileges:true"),
            ("mem_limit:", "a mem_limit"),
            ("pids_limit:", "a pids_limit"),
        ]
        for substring, label in required:
            if substring not in block_text:
                violations.append(f"x-app-hardening block is missing {label}")

    for service in _APP_SERVICES:
        service_index = _find_line(lines, lambda line, s=service: line == f"  {s}:")
        if service_index is None:
            violations.append(f"service {service!r} not found")
            continue
        service_block = "\n".join(_block_after(lines, service_index))
        if "*app-hardening" not in service_block:
            violations.append(f"service {service!r} does not merge x-app-hardening")

    for i, line in enumerate(lines):
        if line.strip() != "ports:":
            continue
        for entry in _block_after(lines, i):
            stripped = entry.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not (stripped.startswith('- "127.0.0.1:') or stripped.startswith("- '127.0.0.1:")):
                violations.append(f"published port entry not bound to 127.0.0.1: {stripped!r}")

    return violations


def test_dockerfile_hardening():
    violations = _check_dockerfile(DOCKERFILE_PATH.read_text())
    assert not violations, "backend/Dockerfile hardening violations:\n" + "\n".join(violations)


def test_compose_hardening():
    violations = _check_compose(COMPOSE_PATH.read_text())
    assert not violations, "deploy/docker-compose.yml hardening violations:\n" + "\n".join(
        violations
    )
