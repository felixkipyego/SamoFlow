# backend/tests/test_app_skeleton.py
# Task 1.1.b: every package under backend/app must import without raising.
# Task 1.1b (whole-application skeleton, distinct from subtask 1.1.b): each
# subpackage also gained one stub module with a single NotImplementedError
# entry point; see PROJECT_SPEC.md's component table (and the TODO(<step>)
# comment in each stub) for which step fills each one in.
import importlib
import os

import pytest

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

# (stub module, entry-point callable) for each subpackage's Step 1.1b stub.
STUB_ENTRY_POINTS = [
    ("app.auth.routes", "create_session"),
    ("app.tenancy.repository", "get_tenant_scoped_repository"),
    ("app.plans.service", "get_plan_limits"),
    ("app.chat.routes", "stream_chat_response"),
    ("app.agent.graph", "build_graph"),
    ("app.retrieval.service", "retrieve"),
    ("app.ingest.service", "enqueue_ingestion_job"),
    ("app.handoff.routes", "submit_escalation"),
    ("app.notifications.service", "send_email"),
    ("app.mcp.service", "search_knowledge_base"),
    ("app.admin.routes", "list_platform_tenants"),
    ("app.telemetry.service", "record_trace"),
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


def test_every_subpackage_stub_raises_not_implemented():
    # Proves each stub is a real placeholder (not silently working code):
    # importing the module must have no side effects, and calling its entry
    # point must raise, not return.
    for module_name, attr_name in STUB_ENTRY_POINTS:
        module = importlib.import_module(module_name)
        entry_point = getattr(module, attr_name)
        with pytest.raises(NotImplementedError):
            entry_point()
