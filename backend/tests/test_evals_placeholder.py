# backend/tests/test_evals_placeholder.py
# Task 1.1.k: evals/run.py is a placeholder for the golden-set runner
# (task 3.7). These tests run it as a subprocess, isolated from this
# process's cwd and environment, so a future real runner cannot pass this
# suite by accident while still depending on ambient state.
import subprocess
import sys
import tempfile

from tests.conftest import REPO_ROOT, minimal_subprocess_env

_RUN_PY = REPO_ROOT / "evals" / "run.py"


def test_run_py_exists():
    assert _RUN_PY.is_file(), (
        f"evals/run.py not found at {_RUN_PY}. If it moved, update the "
        "path derivation in this test (evals/run.py relative to REPO_ROOT)."
    )


def test_run_py_prints_placeholder_and_exits_zero():
    with tempfile.TemporaryDirectory() as tmp_dir:
        result = subprocess.run(  # noqa: S603 (fixed args: sys.executable + a path, not user input)
            [sys.executable, str(_RUN_PY)],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=tmp_dir,
            env=minimal_subprocess_env(),
        )

    assert result.returncode == 0
    assert result.stdout == "no evals defined yet\n"
    assert result.stderr == ""
