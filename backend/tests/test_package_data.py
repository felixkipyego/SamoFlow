# backend/tests/test_package_data.py
# The config-path bug (found live, 2026-10-16): app/agent/escalation_
# phrases.py, greeting_phrases.py and rewrite_trigger.py each loaded
# their own yaml data file via Path(__file__).resolve().parents[N] --
# correct only when `app` is imported from the source checkout, not
# once installed as a real, non-editable package (`pip install --no-deps
# .`, exactly what backend/Dockerfile's builder stage runs), where
# __file__ resolves under site-packages instead and parents[N] lands on
# site-packages itself. A same-session follow-up fix swapped that for
# Path.cwd(), which happens to work inside the built image (its WORKDIR
# is always /app) but is just as wrong in principle -- it silently
# depends on the caller's current directory, not on anything about the
# package itself. The real fix moved the three yaml files INSIDE the
# `app` package (app/agent/phrases/, declared as wheel-build `include`
# data in pyproject.toml) and loads them via `importlib.resources`,
# which resolves a package's own data file through Python's real import
# machinery -- correct for an editable install, a non-editable install
# and the built image alike.
#
# This test reproduces the exact non-editable-install scenario the bug
# only showed up in: builds a real wheel, installs it into a disposable
# venv with no hatchling/build toolchain installed afterward (so no
# accidental editable-install fallback), and proves all three phrase
# files still load from there. Needs network (pip resolves pyyaml/
# pydantic from PyPI) and is slow (a real venv + a real pip install) --
# both accepted costs for a production-critical bug with no cheaper way
# to catch a regression, matching PROJECT_SPEC.md's own "scale rigor to
# risk" rule for a real, previously-shipped defect.
import subprocess
import sys
import venv
from pathlib import Path

import pytest

from tests.conftest import minimal_subprocess_env

BACKEND_DIR = Path(__file__).resolve().parent.parent

_LOAD_CHECK_SCRIPT = """
import app
assert "site-packages" in app.__file__, f"not a non-editable install: {app.__file__}"

from app.agent.escalation_phrases import escalation_phrase_category
from app.agent.greeting_phrases import is_greeting_or_thanks
from app.agent.rewrite_trigger import needs_join

assert escalation_phrase_category("I want to speak to a human please") == "asks_for_person"
assert is_greeting_or_thanks("hi") is True
assert is_greeting_or_thanks("thanks, but how do I return this?") is False
assert needs_join("what about it") is True
print("OK")
"""


@pytest.mark.timeout(180)
def test_phrase_yaml_files_load_from_a_non_editable_install(tmp_path):
    venv_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True).create(venv_dir)
    venv_python = venv_dir / "bin" / "python3"
    env = minimal_subprocess_env()

    # pydantic needs its own compiled pydantic-core dependency -- installed
    # WITH deps (unlike the local backend wheel below, whose dependencies
    # already come from the hash-pinned lockfile elsewhere, matching the
    # Makefile's/Dockerfile's own `--no-deps .` convention exactly).
    deps_install = subprocess.run(  # noqa: S603 (fixed, test-internal argument list, not user input)
        [str(venv_python), "-m", "pip", "install", "--quiet", "pyyaml", "pydantic"],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=150,
    )
    assert deps_install.returncode == 0, (
        f"installing pyyaml/pydantic failed:\n"
        f"stdout={deps_install.stdout}\nstderr={deps_install.stderr}"
    )

    install = subprocess.run(  # noqa: S603
        [str(venv_python), "-m", "pip", "install", "--quiet", "--no-deps", str(BACKEND_DIR)],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=150,
    )
    assert install.returncode == 0, (
        f"non-editable install failed:\nstdout={install.stdout}\nstderr={install.stderr}"
    )

    # cwd=tmp_path, deliberately NOT BACKEND_DIR: `python -c` prepends ''
    # (the current directory) to sys.path, and BACKEND_DIR's own literal
    # `app/` source directory would shadow the just-installed site-
    # packages copy, silently testing the wrong thing (found live while
    # writing this test -- the real Docker image's own WORKDIR has no
    # such sibling `app/` directory, so this is a test-isolation
    # concern, not a production one).
    check = subprocess.run(  # noqa: S603
        [str(venv_python), "-c", _LOAD_CHECK_SCRIPT],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=30,
    )
    assert check.returncode == 0, (
        f"phrase files failed to load from the non-editable install:\n"
        f"stdout={check.stdout}\nstderr={check.stderr}"
    )
    assert check.stdout.strip() == "OK"


@pytest.mark.timeout(60)
def test_built_wheel_contains_all_three_phrase_yaml_files(tmp_path):
    """Narrower and faster than the full install test above -- builds
    the wheel only, and inspects its file listing directly, so a future
    pyproject.toml edit that silently drops the `include` entry (back to
    hatchling's own default file selection, which has no reason to know
    these three yaml files are meant to ship) fails here first.
    """
    out_dir = tmp_path / "dist"
    env = minimal_subprocess_env()
    build = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", str(out_dir), str(BACKEND_DIR)],
        capture_output=True,
        text=True,
        env=env,
        timeout=50,
    )
    assert build.returncode == 0, (
        f"wheel build failed:\nstdout={build.stdout}\nstderr={build.stderr}"
    )

    wheels = list(out_dir.glob("widgetplatform-*.whl"))
    assert len(wheels) == 1, f"expected exactly one built wheel, found {wheels}"

    import zipfile

    with zipfile.ZipFile(wheels[0]) as zf:
        names = set(zf.namelist())
    for filename in (
        "escalation_phrases.yaml",
        "greeting_phrases.yaml",
        "rewrite_trigger_phrases.yaml",
    ):
        yaml_names = sorted(n for n in names if n.endswith(".yaml"))
        assert f"app/agent/phrases/{filename}" in names, (
            f"{filename} missing from the built wheel: {yaml_names}"
        )
