# backend/tests/test_all_models_imports_guard.py
# Guard test (originally built at the duplication check after 2.1.a/b,
# item D1, scoped to alembic/env.py only -- repointed at the duplication
# check after 2.1.c/d/e, item D, once app/all_models.py became the one
# shared place both alembic/env.py and app/worker.py import for this): every
# backend/app/<pkg>/models.py must be imported somewhere in
# backend/app/all_models.py, or autogenerate silently sees zero changes for
# that package's tables (found live at Task 2.1.b) and/or a genuine
# standalone worker process can raise NoReferencedTableError at flush time
# (found live at Task 2.1.e) -- either way, a package's tables are
# effectively invisible to anything relying on Base.metadata being
# complete. This guard makes that structurally harder to repeat for the
# next domain package that adds models, the same spirit as
# test_qdrant_read_path_guard.py's own AST-based approach.
import ast
from pathlib import Path

from tests.conftest import BACKEND_DIR

APP_DIR = BACKEND_DIR / "app"
ALL_MODELS_PY = BACKEND_DIR / "app" / "all_models.py"


def _packages_with_models() -> set[str]:
    return {path.parent.name for path in APP_DIR.glob("*/models.py")}


def _packages_imported_in_all_models_py(all_models_py: Path = ALL_MODELS_PY) -> set[str]:
    tree = ast.parse(all_models_py.read_text(), filename=str(all_models_py))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app."):
            parts = node.module.split(".")
            if len(parts) == 2 and any(alias.name == "models" for alias in node.names):
                imported.add(parts[1])
    return imported


def test_packages_imported_in_all_models_py_detects_every_domain_import(tmp_path):
    fake_all_models_py = tmp_path / "all_models.py"
    fake_all_models_py.write_text(
        "from app.plans import models as plans_models\n"
        "from app.tenancy import models as tenancy_models\n"
    )
    assert _packages_imported_in_all_models_py(fake_all_models_py) == {"plans", "tenancy"}


def test_packages_imported_in_all_models_py_ignores_unrelated_imports(tmp_path):
    fake_all_models_py = tmp_path / "all_models.py"
    fake_all_models_py.write_text(
        "from app.config import get_settings\nfrom app.plans import models as plans_models\n"
    )
    assert _packages_imported_in_all_models_py(fake_all_models_py) == {"plans"}


def test_every_models_module_is_imported_into_all_models():
    missing = _packages_with_models() - _packages_imported_in_all_models_py()
    assert not missing, (
        f"backend/app/{sorted(missing)}/models.py exists but is not imported in "
        "backend/app/all_models.py -- anything relying on Base.metadata being "
        "complete (Alembic autogenerate, a standalone worker process's own "
        "flush-time FK resolution) will silently or loudly break for these "
        "tables (found live at Tasks 2.1.b and 2.1.e)"
    )
