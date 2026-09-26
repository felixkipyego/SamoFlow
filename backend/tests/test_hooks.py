# backend/tests/test_hooks.py
# Step 1.1c: hooks.py lists TODO/ASSUMPTION/UNCERTAIN/NOTE markers (see
# PROJECT_SPEC.md §3, "make hooks"). Run as a subprocess with cwd=REPO_ROOT
# (unlike evals/run.py, hooks.py's own correctness depends on the
# repo-root-relative backend/ and evals/ paths it scans) and a restricted
# environment (PATH/HOME only, matching test_evals_placeholder.py's pattern),
# so a future real implementation cannot pass this suite by accident while
# still depending on ambient state.
import os
import subprocess
import sys

from tests.conftest import REPO_ROOT

_HOOKS_PY = REPO_ROOT / "hooks.py"


def _subprocess_env():
    env = {}
    for name in ("PATH", "HOME"):
        if name in os.environ:
            env[name] = os.environ[name]
    return env


def test_hooks_py_exists():
    assert _HOOKS_PY.is_file(), (
        f"hooks.py not found at {_HOOKS_PY}. If it moved, update the path "
        "derivation in this test (hooks.py relative to REPO_ROOT)."
    )


def test_hooks_py_runs_and_finds_known_markers():
    result = subprocess.run(  # noqa: S603 (fixed args: sys.executable + a path, not user input)
        [sys.executable, str(_HOOKS_PY)],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=REPO_ROOT,
        env=_subprocess_env(),
    )

    assert result.returncode == 0, (
        f"hooks.py must never fail because markers exist -- a visibility "
        f"tool, not a gate. stderr: {result.stderr!r}"
    )
    assert result.stderr == ""

    first_line = result.stdout.splitlines()[0]
    count = int(first_line.split()[0])
    assert count > 0, f"expected at least one marker, got: {first_line!r}"

    # Two markers known to already exist in the codebase (not the full list,
    # so this test does not become brittle as markers are added/resolved).
    assert "worker.py" in result.stdout and "TODO(2.1)" in result.stdout
    assert "alembic/env.py" in result.stdout and "TODO(1.2)" in result.stdout
