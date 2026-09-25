# backend/tests/test_ci_guard.py
# Task 1.1.m: static checks on .github/workflows/ci.yml, so a future edit
# cannot silently widen permissions, switch to the target-repo-context
# trigger, reference a secret, use a floating/mutable action reference, run
# on a "latest" runner, drop a job timeout, stop running lint/tests, stop
# building/smoke-testing the image, or let a checkout step persist
# credentials. Deliberately no YAML parser (no new dependency): line-based
# text checks only, the same style test_makefile_guard.py and
# test_hardening_guard.py already use. _check() takes the file text as a
# plain string, not a path, so each rule can be proved able to fail on an
# edited copy of the text without ever touching the real file.
import re

from tests.conftest import REPO_ROOT, is_comment_or_blank, read_lines

CI_YML_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"

_USES_LINE = re.compile(r"^\s*uses:\s*(\S+)")
_CHECKOUT_USES = re.compile(r"^\s*uses:\s*actions/checkout@")
_STEP_HEADER = re.compile(r"^\s*- name:")
_RUN_LINE = re.compile(r"^\s*run:\s*(.*)$")
_WRITE_PERMISSION = re.compile(r"^\s*[\w-]+:\s*write\b")
_RUNS_ON = re.compile(r"^\s*runs-on:\s*(\S+)")
_TIMEOUT = re.compile(r"^\s*timeout-minutes:\s*\d+")
_JOB_HEADER = re.compile(r"^  (\w[\w-]*):\s*$")


def _checkout_step_blocks(lines):
    # Each block starts at a "uses: actions/checkout@..." line and runs
    # until the next step header ("- name: ..."), or EOF.
    blocks = []
    for i, line in enumerate(lines):
        if not _CHECKOUT_USES.match(line):
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

    return violations


def _checkout_persist_credentials_violations(text: str) -> list[str]:
    lines = text.splitlines()
    violations = []
    for block in _checkout_step_blocks(lines):
        if not any("persist-credentials: false" in line for line in block):
            violations.append(
                "an actions/checkout step does not set persist-credentials: "
                "false (needed so a job running an untrusted fork PR's code "
                f"cannot push back with this repo's token): {block[0].strip()!r}"
            )
    return violations


def _image_job_build_and_smoke_violations(text: str) -> list[str]:
    jobs = _jobs(text.splitlines())
    image_commands = _run_commands(jobs.get("image", []))
    violations = []
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


def test_ci_workflow():
    violations = _check("\n".join(read_lines(CI_YML_PATH)))
    assert not violations, ".github/workflows/ci.yml violations:\n" + "\n".join(violations)


def test_checkout_steps_never_persist_credentials():
    violations = _checkout_persist_credentials_violations("\n".join(read_lines(CI_YML_PATH)))
    assert not violations, ".github/workflows/ci.yml violations:\n" + "\n".join(violations)


def test_image_job_builds_and_smoke_tests_the_image():
    violations = _image_job_build_and_smoke_violations("\n".join(read_lines(CI_YML_PATH)))
    assert not violations, ".github/workflows/ci.yml violations:\n" + "\n".join(violations)
