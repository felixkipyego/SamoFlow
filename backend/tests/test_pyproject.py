# This test only parses pyproject.toml with tomllib, so it needs no
# installed package. See test_app_skeleton.py for the import check and the
# deferred `pip install -e .` check run in Step 1.1.b's task report.
import tomllib
from pathlib import Path

PYPROJECT_PATH = Path(__file__).resolve().parent.parent / "pyproject.toml"

# The approved dependency sets (PROJECT_SPEC.md "Decisions made"). Any
# unapproved addition, removal or version drift fails this test.
APPROVED_RUNTIME_DEPENDENCIES = {
    "fastapi==0.141.1",
    "uvicorn==0.53.0",
    "pydantic-settings==2.15.0",
    "alembic==1.20.0",
    "sqlalchemy==2.0.54",
    "psycopg[binary]==3.3.6",
}
APPROVED_DEV_DEPENDENCIES = {
    "pytest==9.1.1",
    "pytest-asyncio==1.4.0",
    "httpx==0.28.1",
    "ruff==0.16.8",
}


def _load_pyproject() -> dict:
    with open(PYPROJECT_PATH, "rb") as f:
        return tomllib.load(f)


def test_package_name_is_widgetplatform():
    data = _load_pyproject()
    assert data["project"]["name"] == "widgetplatform"


def test_requires_python_is_312():
    data = _load_pyproject()
    assert data["project"]["requires-python"] == "==3.12.*"


def test_runtime_dependencies_are_exactly_approved():
    data = _load_pyproject()
    assert set(data["project"]["dependencies"]) == APPROVED_RUNTIME_DEPENDENCIES


def test_dev_dependencies_are_exactly_approved():
    data = _load_pyproject()
    dev = data["project"]["optional-dependencies"]["dev"]
    assert set(dev) == APPROVED_DEV_DEPENDENCIES


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
