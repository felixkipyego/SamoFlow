# backend/tests/test_database_adapter_guard.py
# Task 2.8.b [SECURITY]: the structural half of "only one allowlisted
# read-only SELECT ever runs" -- import confinement for `asyncpg`, static
# and AST-based, adapted from test_qdrant_read_path_guard.py's own rule
# (a) (import confinement is the PRIMARY guard there too, for the
# identical reason: a file that cannot import the client library at all
# cannot call a method on one, side-stepping the type-inference problem a
# name-only scan would have).
#
# Deliberately simplified relative to that guard, not a lazy partial
# port: qdrant-client is used broadly across this codebase for both reads
# and writes, which is why that guard also needs rules (b)/(c)/(d) (a
# READ/WRITE/CONFIG method classification, a raw-transport-surface check,
# a no-snapshots rule). asyncpg has exactly ONE real module to confine
# (`app/ingest/database_adapter.py`) and no other legitimate caller
# anywhere in this codebase at all (the app's own internal database uses
# psycopg via SQLAlchemy, a separate, pre-existing stack) -- there is
# nothing for a second file to call a read-vs-write method on, since
# nothing else can ever hold a real asyncpg.Connection object in the
# first place. Import confinement alone is therefore already the
# complete guarantee this task needs: if asyncpg is never importable
# anywhere else, no other file can ever run ANY query (read or write)
# against a tenant's database connection through it.
#
# No relative-import forms to handle either (unlike app.qdrant, an
# INTERNAL dotted module reachable via "from . import qdrant"-style
# relative imports from inside the app package): asyncpg is an external,
# top-level third-party package, reachable only via "import asyncpg",
# "from asyncpg import X", or a dynamic import with a literal string
# argument -- the same three forms test_qdrant_read_path_guard.py already
# checks for qdrant_client itself.
import ast

from tests.conftest import BACKEND_DIR, called_name, iter_python_files

APP_DIR = BACKEND_DIR / "app"

# NOTE: adding a file here is a deliberate decision -- it means that file
# may import asyncpg and hold a real connection capable of running a SQL
# query against a tenant's own database. Review it by hand.
ASYNCPG_ACCESS_ALLOWLIST: frozenset[str] = frozenset({"app/ingest/database_adapter.py"})


def _import_violations(node: ast.AST, rel: str, has_access: bool) -> list[str]:
    if has_access:
        return []
    violations = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name == "asyncpg" or alias.name.startswith("asyncpg."):
                violations.append(
                    f"{rel}:{node.lineno}: imports {alias.name!r}; only "
                    f"{sorted(ASYNCPG_ACCESS_ALLOWLIST)} may import asyncpg "
                    "(rule a: import confinement)"
                )
    elif isinstance(node, ast.ImportFrom):
        module = node.module or ""
        if module == "asyncpg" or module.startswith("asyncpg."):
            for alias in node.names:
                violations.append(
                    f"{rel}:{node.lineno}: imports {alias.name!r} from {module!r}; "
                    f"only {sorted(ASYNCPG_ACCESS_ALLOWLIST)} may import asyncpg "
                    "(rule a: import confinement)"
                )
    return violations


def _dynamic_import_violations(node: ast.AST, rel: str, has_access: bool) -> list[str]:
    if not isinstance(node, ast.Call) or has_access:
        return []
    name = called_name(node)
    if name not in ("import_module", "__import__") or not node.args:
        return []
    first_arg = node.args[0]
    if not (isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str)):
        return []
    target = first_arg.value
    if target == "asyncpg" or target.startswith("asyncpg."):
        return [
            f"{rel}:{node.lineno}: dynamically imports {target!r} via "
            f"{name}(); only {sorted(ASYNCPG_ACCESS_ALLOWLIST)} may import "
            "asyncpg (rule a: import confinement)"
        ]
    return []


def scan_asyncpg_guard(root) -> list[str]:
    """Pure static AST scan, no IO beyond reading the given tree's own .py
    files -- matching scan_read_path_guard()'s own shape (test_qdrant_
    read_path_guard.py), including testability against a synthetic tmp_path
    tree rather than only the real backend/app tree.
    """
    violations: list[str] = []
    app_root = root / "app"
    if not app_root.is_dir():
        return violations
    for path in iter_python_files(app_root):
        rel = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(), filename=str(path))
        has_access = rel in ASYNCPG_ACCESS_ALLOWLIST
        for node in ast.walk(tree):
            violations.extend(_import_violations(node, rel, has_access))
            violations.extend(_dynamic_import_violations(node, rel, has_access))
    return violations


def test_asyncpg_guard_passes_against_the_real_backend_app():
    assert scan_asyncpg_guard(BACKEND_DIR) == []


# --- Guard unit tests on synthetic trees (no real database needed) -------


def _write(tmp_path, rel_path: str, content: str) -> None:
    full = tmp_path / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)


def test_fails_on_plain_import_of_asyncpg_outside_the_allowlist(tmp_path):
    _write(tmp_path, "app/other.py", "import asyncpg\n")
    violations = scan_asyncpg_guard(tmp_path)
    assert len(violations) == 1
    assert "app/other.py" in violations[0]
    assert "rule a" in violations[0]


def test_fails_on_from_asyncpg_import_outside_the_allowlist(tmp_path):
    _write(tmp_path, "app/other.py", "from asyncpg import connect\n")
    violations = scan_asyncpg_guard(tmp_path)
    assert len(violations) == 1
    assert "app/other.py" in violations[0]
    assert "rule a" in violations[0]


def test_fails_on_a_dynamic_import_of_asyncpg(tmp_path):
    _write(
        tmp_path,
        "app/other.py",
        "import importlib\n\n\ndef f():\n    return importlib.import_module('asyncpg')\n",
    )
    violations = scan_asyncpg_guard(tmp_path)
    assert len(violations) == 1
    assert "app/other.py" in violations[0]
    assert "rule a" in violations[0]


def test_passes_for_the_allowlisted_file_importing_asyncpg(tmp_path):
    for content in ("import asyncpg\n", "from asyncpg import connect\n"):
        _write(tmp_path, "app/ingest/database_adapter.py", content)
        assert scan_asyncpg_guard(tmp_path) == [], content


def test_passes_for_imports_of_other_app_modules_not_asyncpg(tmp_path):
    _write(
        tmp_path,
        "app/other.py",
        "from app.config import get_settings\nimport app.ingest.database_adapter\n",
    )
    assert scan_asyncpg_guard(tmp_path) == []
