# backend/tests/test_ci_guard.py
# Task 1.1.m: static checks on .github/workflows/ci.yml, so a future edit
# cannot silently widen permissions, switch to the target-repo-context
# trigger, reference a secret, use a floating/mutable action reference, run
# on a "latest" runner, drop a job timeout, or stop running lint/tests.
# Deliberately no YAML parser (no new dependency): line-based text checks
# only, the same style test_makefile_guard.py and test_hardening_guard.py
# already use. _check() takes the file text as a plain string, not a path,
# so each rule can be proved able to fail on an edited copy of the text
# without ever touching the real file.
import re

from tests.conftest import REPO_ROOT

CI_YML_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"

_USES_LINE = re.compile(r"^\s*uses:\s*(\S+)")
_RUN_LINE = re.compile(r"^\s*run:\s*(.*)$")
_WRITE_PERMISSION = re.compile(r"^\s*[\w-]+:\s*write\b")
_RUNS_ON = re.compile(r"^\s*runs-on:\s*(\S+)")
_TIMEOUT = re.compile(r"^\s*timeout-minutes:\s*\d+")
_JOB_HEADER = re.compile(r"^  (\w[\w-]*):\s*$")


def _lines():
    assert CI_YML_PATH.is_file(), (
        f"ci.yml not found at {CI_YML_PATH}. If it moved, update the path "
        "derivation in this test."
    )
    return CI_YML_PATH.read_text().splitlines()


def _jobs(lines):
    # Maps each job name (a "  <job>:" header nested directly under the
    # top-level "jobs:" key) to its block of lines, up to the next job or
    # EOF. Scoped to start only after "jobs:" so "on:"'s own nested keys
    # (push, pull_request) are never mistaken for jobs.
    try:
        start = next(i for i, line in enumerate(lines) if line.rstrip() == "jobs:")
    except StopIteration:
        return {}
    jobs = {}
    current = None
    for line in lines[start + 1 :]:
        match = _JOB_HEADER.match(line)
        if match:
            current = match.group(1)
            jobs[current] = []
            continue
        if current is not None:
            jobs[current].append(line)
    return jobs


def _check(text: str) -> list[str]:
    lines = text.splitlines()
    violations = []

    if "permissions:" not in text:
        violations.append("no top-level 'permissions:' block")
    elif "contents: read" not in text:
        violations.append("'permissions:' block does not grant 'contents: read'")
    for line in lines:
        if line.strip().startswith("#"):
            continue
        if _WRITE_PERMISSION.match(line):
            violations.append(f"a permission grants write access: {line.strip()!r}")

    if "pull_request_target" in text:
        violations.append("workflow triggers on the target-repo-context event")

    if "secrets." in text:
        violations.append("workflow references a secret")

    for line in lines:
        match = _USES_LINE.match(line)
        if not match:
            continue
        ref = match.group(1)
        sha = ref.rsplit("@", 1)[1] if "@" in ref else ""
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            violations.append(f"'uses:' is not pinned to a 40-hex-character SHA: {line.strip()!r}")

    for line in lines:
        match = _RUNS_ON.match(line)
        if match and "latest" in match.group(1).lower():
            violations.append(f"runner is not pinned (uses 'latest'): {line.strip()!r}")

    jobs = _jobs(lines)
    if not jobs:
        violations.append("no jobs found (job-header parsing may be broken)")
    for name, job_lines in jobs.items():
        if not any(_TIMEOUT.match(line) for line in job_lines):
            violations.append(f"job {name!r} has no timeout-minutes")

    # Restricted to actual "run:" command lines (comments excluded), so a
    # step's explanatory comment mentioning "make test-all" in prose cannot
    # mask its command being removed or changed.
    run_commands = [
        run_match.group(1)
        for line in jobs.get("test", [])
        if not line.strip().startswith("#")
        for run_match in [_RUN_LINE.match(line)]
        if run_match
    ]
    if not any("make lint" in cmd for cmd in run_commands):
        violations.append("job 'test' does not run 'make lint'")
    if not any("make test-all" in cmd for cmd in run_commands):
        violations.append("job 'test' does not run 'make test-all'")

    return violations


def test_ci_workflow():
    violations = _check(CI_YML_PATH.read_text())
    assert not violations, ".github/workflows/ci.yml violations:\n" + "\n".join(violations)
