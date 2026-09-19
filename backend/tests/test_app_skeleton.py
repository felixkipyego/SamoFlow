# backend/tests/test_app_skeleton.py
# Task 1.1.b: every package under backend/app must import without raising.
# Each subpackage is an empty placeholder for now (see NOTE comments in
# their __init__.py files and PROJECT_SPEC.md's component table for which
# step fills each one in).
import importlib
import os

# NOTE: this list duplicates docs/SPEC.md section 21; update both together.
SUBPACKAGES = [
    "app",
    "app.auth",
    "app.tenancy",
    "app.plans",
    "app.chat",
    "app.agent",
    "app.retrieval",
    "app.ingest",
    "app.handoff",
    "app.notifications",
    "app.mcp",
    "app.admin",
    "app.telemetry",
]


def test_app_and_every_subpackage_import_cleanly():
    for module_name in SUBPACKAGES:
        module = importlib.import_module(module_name)
        # A namespace package (PEP 420, created when __init__.py is missing)
        # has no __file__, so this catches an accidentally deleted __init__.py.
        assert module.__file__ is not None, (
            f"{module_name} has no __file__: it imported as a namespace "
            f"package, meaning its __init__.py is missing"
        )
        assert os.path.basename(module.__file__) == "__init__.py", (
            f"{module_name} did not import from an __init__.py: "
            f"{module.__file__}"
        )
