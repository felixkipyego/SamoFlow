# backend/tests/test_alembic_env_imports_guard.py
# Guard test (duplication check after 2.1.a/b, item D1): every
# backend/app/<pkg>/models.py must be imported somewhere in
# backend/alembic/env.py, or autogenerate silently sees zero changes for
# that package's tables -- found live at Task 2.1.b (a first
# `alembic revision --autogenerate` run produced a completely empty
# upgrade()/downgrade() pair, no error, no warning, caught only by
# manually reading the generated file). This guard makes that structurally
# harder to repeat for the next domain package that adds models, the same
# spirit as test_qdrant_read_path_guard.py's own AST-based approach.
import ast
from pathlib import Path

from tests.conftest import BACKEND_DIR

APP_DIR = BACKEND_DIR / "app"
ENV_PY = BACKEND_DIR / "alembic" / "env.py"


def _packages_with_models() -> set[str]:
    return {path.parent.name for path in APP_DIR.glob("*/models.py")}


def _packages_imported_in_env_py(env_py: Path = ENV_PY) -> set[str]:
    tree = ast.parse(env_py.read_text(), filename=str(env_py))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app."):
            parts = node.module.split(".")
            if len(parts) == 2 and any(alias.name == "models" for alias in node.names):
                imported.add(parts[1])
    return imported


def test_packages_imported_in_env_py_detects_every_domain_import(tmp_path):
    fake_env_py = tmp_path / "env.py"
    fake_env_py.write_text(
        "from app.plans import models as plans_models\n"
        "from app.tenancy import models as tenancy_models\n"
    )
    assert _packages_imported_in_env_py(fake_env_py) == {"plans", "tenancy"}


def test_packages_imported_in_env_py_ignores_unrelated_imports(tmp_path):
    fake_env_py = tmp_path / "env.py"
    fake_env_py.write_text(
        "from app.config import get_settings\nfrom app.plans import models as plans_models\n"
    )
    assert _packages_imported_in_env_py(fake_env_py) == {"plans"}


def test_every_models_module_is_imported_for_alembic_autogenerate():
    missing = _packages_with_models() - _packages_imported_in_env_py()
    assert not missing, (
        f"backend/app/{sorted(missing)}/models.py exists but is not imported in "
        "backend/alembic/env.py -- autogenerate will silently see zero changes "
        "for these tables (found live at Task 2.1.b)"
    )
