# backend/tests/conftest.py
# Shared fixtures and helpers for backend/tests. Consolidates what was
# duplicated across test_config.py, test_main.py, test_worker.py and
# test_env_example.py (duplication check after Task 1.1.f): the required
# env-var list, the autouse env-isolation fixture, the valid-environment
# setter, the fresh-import helper and the shared test password.
import importlib
import sys

import pytest

from app.config import Settings, get_settings

REQUIRED_VARS = [name.upper() for name in Settings.model_fields]

# Shared wherever a test needs a password value that must never leak into
# logs, repr/str output or exception messages.
TEST_PASSWORD = "sup3r-secret-pw"  # noqa: S105 (test fixture value, not a real secret)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    # Every test starts with none of the required variables present, and
    # with no cached Settings instance left over from another test.
    for name in REQUIRED_VARS:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def set_valid_env(monkeypatch, valid_env, **overrides):
    for key, value in {**valid_env, **overrides}.items():
        monkeypatch.setenv(key, value)


def fresh_import(module_name):
    # A fresh import into a new module object (not importlib.reload, which
    # mutates the module dict a test file's own `from x import y` binding
    # still points into, breaking later isinstance/pytest.raises checks).
    # Must not raise despite an empty environment.
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)
