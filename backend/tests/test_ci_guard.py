# backend/tests/test_ci_guard.py
# Task 1.1.m, generalized in 1.1.o.f: static checks on every workflow file
# under .github/workflows/, so a future edit cannot silently widen
# permissions, switch to the target-repo-context trigger, reference a
# secret, use a floating/mutable action reference, run on a "latest"
# runner, drop a job timeout, or let a checkout step persist credentials --
# in any workflow file, not just ci.yml. ci.yml and security.yml each also
# get their own specific checks (ci.yml: install/lock-check/lint/test-all
# order, the image job's build+smoke steps; security.yml: the paths filter,
# the schedule trigger, the audit and image-scan steps). Deliberately no
# YAML parser (no new dependency): line-based text checks only, the same
# style test_makefile_guard.py and test_hardening_guard.py already use.
# Every _*_violations() function takes the file text as a plain string, not
# a path, so each rule can be proved able to fail on an edited copy of the
# text without ever touching the real file.
import re

from tests.conftest import REPO_ROOT, is_comment_or_blank, read_lines

WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
CI_YML_PATH = WORKFLOWS_DIR / "ci.yml"
SECURITY_YML_PATH = WORKFLOWS_DIR / "security.yml"

_USES_LINE = re.compile(r"^\s*uses:\s*(\S+)")
_CHECKOUT_USES = re.compile(r"^\s*uses:\s*actions/checkout@")
_ANCHORE_USES = re.compile(r"^\s*uses:\s*anchore/scan-action@")
_STEP_HEADER = re.compile(r"^\s*- name:")
_RUN_LINE = re.compile(r"^\s*run:\s*(.*)$")
_WRITE_PERMISSION = re.compile(r"^\s*[\w-]+:\s*write\b")
_RUNS_ON = re.compile(r"^\s*runs-on:\s*(\S+)")
_TIMEOUT = re.compile(r"^\s*timeout-minutes:\s*\d+")
_JOB_HEADER = re.compile(r"^  (\w[\w-]*):\s*$")

_EXPECTED_SECURITY_PATHS = (
    "backend/pyproject.toml",
    "backend/requirements.lock",
    "backend/requirements-dev.lock",
    "backend/Dockerfile",
    "deploy/docker-compose.yml",
    ".grype.yaml",
    ".github/workflows/security.yml",
)


def _all_workflow_files():
    return sorted(WORKFLOWS_DIR.glob("*.yml")) + sorted(WORKFLOWS_DIR.glob("*.yaml"))


def _step_blocks(lines, uses_pattern):
    # Each block starts at a line matching uses_pattern (e.g. a specific
    # action's "uses:" line) and runs until the next step header
    # ("- name: ..."), or EOF.
    blocks = []
    for i, line in enumerate(lines):
        if not uses_pattern.match(line):
            continue
        block = [line]
        for later in lines[i + 1 :]:
            if _STEP_HEADER.match(later):
                break
            block.append(later)
        blocks.append(block)
    return blocks


def _run_commands(job_lines):
    # Restricted to actual "run:" command lines (comments excluded), so a
    # step's explanatory comment mentioning a command in prose cannot mask
    # its real command being removed or changed.
    return [
        run_match.group(1)
        for line in job_lines
        if not is_comment_or_blank(line)
        for run_match in [_RUN_LINE.match(line)]
        if run_match
    ]


def _paths_filter_entries(lines):
    # Collects every path listed under any "paths:" key (there may be more
    # than one, e.g. separate push/pull_request triggers), so a path can be
    # checked as actually configured *there* -- not just present anywhere
    # else in the file (e.g. a step's "config:" input or a comment).
    entries = []
    for i, line in enumerate(lines):
        if line.strip() != "paths:":
            continue
        indent = len(line) - len(line.lstrip(" "))
        for later in lines[i + 1 :]:
            if later.strip() == "":
                continue
            later_indent = len(later) - len(later.lstrip(" "))
            if later_indent <= indent:
                break
            stripped = later.strip()
            if stripped.startswith("- "):
                entries.append(stripped[2:].strip())
    return entries


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


def _generic_workflow_violations(text: str) -> list[str]:
    # Rules every workflow file under .github/workflows/ must follow,
    # regardless of what it does.
    lines = text.splitlines()
    violations = []

    if "permissions:" not in text:
        violations.append("no top-level 'permissions:' block")
    elif "contents: read" not in text:
        violations.append("'permissions:' block does not grant 'contents: read'")
    for line in lines:
        if is_comment_or_blank(line):
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

    for block in _step_blocks(lines, _CHECKOUT_USES):
        if not any("persist-credentials: false" in line for line in block):
            violations.append(
                "an actions/checkout step does not set persist-credentials: "
                "false (needed so a job running an untrusted fork PR's code "
                f"cannot push back with this repo's token): {block[0].strip()!r}"
            )

    return violations


def _ci_specific_violations(text: str) -> list[str]:
    jobs = _jobs(text.splitlines())
    violations = []

    # Task 1.1.o.e: install and lockfile-drift-check must run, and in the
    # right relative order -- install first (it puts uv on PATH, which
    # lock-check needs), then lock-check before lint/test-all (fail fast on
    # drift before spending time on anything else).
    test_commands = _run_commands(jobs.get("test", []))
    required_order = ["make install", "make lock-check", "make lint", "make test-all"]
    indexes = {}
    for required in required_order:
        matches = [i for i, cmd in enumerate(test_commands) if required in cmd]
        if not matches:
            violations.append(f"job 'test' does not run {required!r}")
        else:
            indexes[required] = matches[0]
    if len(indexes) == len(required_order):
        ordered = [indexes[r] for r in required_order]
        if ordered != sorted(ordered):
            violations.append(
                "job 'test' does not run "
                f"{required_order} in that relative order: got {indexes}"
            )

    image_commands = _run_commands(jobs.get("image", []))
    if not any("docker build" in cmd for cmd in image_commands):
        violations.append(
            "job 'image' does not run a 'docker build' command (needed "
            "before the smoke test can run against the built image)"
        )
    if not any("smoke-image.sh" in cmd for cmd in image_commands):
        violations.append(
            "job 'image' does not run deploy/smoke-image.sh (this is what "
            "catches non-root, missing-server-header and password-leak "
            "regressions in the built image)"
        )

    return violations


def _security_specific_violations(text: str) -> list[str]:
    lines = text.splitlines()
    violations = []

    if "paths:" not in text:
        violations.append("no 'paths:' filter found (push/pull_request should be scoped)")
    else:
        paths_entries = _paths_filter_entries(lines)
        for path in _EXPECTED_SECURITY_PATHS:
            if path not in paths_entries:
                violations.append(f"paths filter is missing {path!r}")

    if "schedule:" not in text or "cron:" not in text:
        violations.append("no 'schedule:'/'cron:' trigger found (this workflow must run weekly)")

    jobs = _jobs(lines)

    audit_commands = _run_commands(jobs.get("python-audit", []))
    if not any("make audit" in cmd for cmd in audit_commands):
        violations.append("job 'python-audit' does not run 'make audit'")

    image_scan_commands = _run_commands(jobs.get("image-scan", []))
    if not any("docker build" in cmd for cmd in image_scan_commands):
        violations.append("job 'image-scan' does not run a 'docker build' command")

    scan_blocks = _step_blocks(jobs.get("image-scan", []), _ANCHORE_USES)
    if not scan_blocks:
        violations.append("job 'image-scan' does not run anchore/scan-action")
    else:
        block_text = "\n".join(scan_blocks[0])
        if "fail-build: true" not in block_text:
            violations.append("anchore/scan-action step does not set fail-build: true")
        if "severity-cutoff: high" not in block_text:
            violations.append("anchore/scan-action step does not set severity-cutoff: high")
        # An explicit, absolute config: input is required -- grype's own
        # auto-detection of .grype.yaml (relative to its process's working
        # directory) was proven unreliable in a real run; see
        # PROJECT_SPEC.md, Step 1.1.o.f follow-up. A relative path would
        # carry the same risk, so this also requires the absolute
        # ${{ github.workspace }} prefix, not just any "config:" line.
        if "config: ${{ github.workspace }}/.grype.yaml" not in block_text:
            violations.append(
                "anchore/scan-action step does not set an absolute "
                "config: ${{ github.workspace }}/.grype.yaml input"
            )

    return violations


def test_every_workflow_file_passes_generic_checks():
    for path in _all_workflow_files():
        violations = _generic_workflow_violations("\n".join(read_lines(path)))
        assert not violations, f"{path} violations:\n" + "\n".join(violations)


def test_ci_workflow():
    violations = _ci_specific_violations("\n".join(read_lines(CI_YML_PATH)))
    assert not violations, ".github/workflows/ci.yml violations:\n" + "\n".join(violations)


def test_security_workflow():
    violations = _security_specific_violations("\n".join(read_lines(SECURITY_YML_PATH)))
    assert not violations, ".github/workflows/security.yml violations:\n" + "\n".join(violations)
