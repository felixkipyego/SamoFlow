# This test only parses pyproject.toml with tomllib, so it needs no
# installed package. See test_app_skeleton.py for the import check and the
# deferred `pip install -e .` check run in Step 1.1.b's task report.
#
# Task 1.1.o.b: requirements.lock/requirements-dev.lock (hash-pinned via
# `uv pip compile`) replace pyproject.toml's own dependency list as the
# thing actually installed (1.1.o.d/e will point the Dockerfile and CI at
# them). These tests are the replacement for the exact-dependency-set tests
# removed in 1.1.o.a: instead of duplicating the approved set here, they
# check pyproject.toml's own `==` pins are honoured by the lockfiles, and
# that the lockfiles are actually hash-pinned. Plain text parsing only (no
# new dependency, e.g. no `packaging`) -- lockfile package lines look like
# "name==version" optionally followed by " ; <environment marker>", with
# their "--hash=sha256:..." lines indented underneath.
import re
import tomllib
from pathlib import Path

PYPROJECT_PATH = Path(__file__).resolve().parent.parent / "pyproject.toml"
RUNTIME_LOCK_PATH = PYPROJECT_PATH.parent / "requirements.lock"
DEV_LOCK_PATH = PYPROJECT_PATH.parent / "requirements-dev.lock"

_PACKAGE_LINE = re.compile(r"^([A-Za-z0-9_.\-]+)==([A-Za-z0-9_.\-]+)")


def _load_pyproject() -> dict:
    with open(PYPROJECT_PATH, "rb") as f:
        return tomllib.load(f)


def _normalize(name: str) -> str:
    return name.lower().replace("_", "-")


def _pinned_versions(requirement_strings: list[str]) -> dict[str, str]:
    # requirement_strings look like "fastapi==0.141.1" or "psycopg[binary]==3.3.6".
    versions = {}
    for req in requirement_strings:
        name, _, version = req.partition("==")
        name = name.split("[", 1)[0]
        versions[_normalize(name)] = version
    return versions


def _lockfile_packages(path: Path) -> dict[str, dict]:
    # Maps normalized package name -> {"version": str, "hash_count": int}.
    # A new package entry is any non-indented, non-comment line matching
    # "name==version"; every indented line up to the next package entry
    # (continuation, "--hash=sha256:..." or "# via ...") belongs to it.
    packages: dict[str, dict] = {}
    current = None
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        if not line.startswith((" ", "\t")):
            match = _PACKAGE_LINE.match(line)
            assert match, f"{path}: could not parse package line: {line!r}"
            current = _normalize(match.group(1))
            packages[current] = {"version": match.group(2), "hash_count": 0}
            continue
        assert current is not None, f"{path}: continuation line before any package: {line!r}"
        packages[current]["hash_count"] += line.count("--hash=sha256:")
    return packages


def test_package_name_is_widgetplatform():
    data = _load_pyproject()
    assert data["project"]["name"] == "widgetplatform"


def test_requires_python_is_312():
    data = _load_pyproject()
    assert data["project"]["requires-python"] == "==3.12.*"


def test_build_backend_is_hatchling():
    data = _load_pyproject()
    assert data["build-system"]["build-backend"] == "hatchling.build"
    assert data["build-system"]["requires"] == ["hatchling==1.32.3"]


def test_pytest_asyncio_mode_is_auto():
    data = _load_pyproject()
    assert data["tool"]["pytest"]["asyncio_mode"] == "auto"


def test_ruff_select_and_test_file_ignore():
    data = _load_pyproject()
    lint = data["tool"]["ruff"]["lint"]
    assert set(lint["select"]) == {"E", "F", "I", "UP", "B", "S"}
    assert "S101" in lint["per-file-ignores"]["tests/*"]


def test_runtime_dependencies_match_requirements_lock():
    data = _load_pyproject()
    pinned = _pinned_versions(data["project"]["dependencies"])
    locked = _lockfile_packages(RUNTIME_LOCK_PATH)
    for name, version in pinned.items():
        assert name in locked, (
            f"{name} is pinned in pyproject.toml's dependencies but missing "
            "from requirements.lock"
        )
        assert locked[name]["version"] == version, (
            f"{name} is pinned to {version!r} in pyproject.toml but "
            f"{locked[name]['version']!r} in requirements.lock"
        )


def test_dev_dependencies_match_requirements_dev_lock():
    data = _load_pyproject()
    pinned = _pinned_versions(data["project"]["optional-dependencies"]["dev"])
    locked = _lockfile_packages(DEV_LOCK_PATH)
    for name, version in pinned.items():
        assert name in locked, (
            f"{name} is pinned in pyproject.toml's dev extra but missing "
            "from requirements-dev.lock"
        )
        assert locked[name]["version"] == version, (
            f"{name} is pinned to {version!r} in pyproject.toml's dev extra "
            f"but {locked[name]['version']!r} in requirements-dev.lock"
        )


def test_lockfiles_hash_pin_every_package():
    for path in (RUNTIME_LOCK_PATH, DEV_LOCK_PATH):
        packages = _lockfile_packages(path)
        assert packages, f"{path} has no package entries"
        for name, info in packages.items():
            assert info["hash_count"] >= 1, f"{path}: {name!r} has no --hash=sha256: line"


def test_dev_lock_is_a_superset_of_runtime_lock():
    runtime_packages = set(_lockfile_packages(RUNTIME_LOCK_PATH))
    dev_packages = set(_lockfile_packages(DEV_LOCK_PATH))
    missing = runtime_packages - dev_packages
    assert not missing, (
        "requirements-dev.lock is missing packages present in "
        f"requirements.lock: {sorted(missing)}"
    )


def test_lockfiles_agree_on_shared_package_versions():
    # The superset check above only compares package *names*; a package
    # present in both lockfiles could still be pinned to different versions
    # (e.g. a hand-edit or a bad merge), which neither that test nor the
    # pyproject.toml-vs-lockfile tests above would catch.
    runtime = _lockfile_packages(RUNTIME_LOCK_PATH)
    dev = _lockfile_packages(DEV_LOCK_PATH)
    for name in sorted(set(runtime) & set(dev)):
        assert runtime[name]["version"] == dev[name]["version"], (
            f"{name!r} is pinned to {runtime[name]['version']!r} in "
            f"requirements.lock but {dev[name]['version']!r} in "
            "requirements-dev.lock"
        )
