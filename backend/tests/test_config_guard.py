# backend/tests/test_config_guard.py
# Guard test (Task 1.1.c refinement): parses every .py file under
# backend/app with ast (not text search, so comments/strings never trigger
# it) and enforces three rules from PROJECT_SPEC.md's decisions log:
#   - only app/config.py may construct Settings() directly;
#   - nothing calls a method named errors() (that leaks raw input, see
#     config.py's model_config comment);
#   - only files listed in ALLOWED_DATABASE_URL_CALLERS may call
#     database_url_str() outside app/config.py.
import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "app"
CONFIG_FILE = APP_DIR / "config.py"

# NOTE: adding a file here is a deliberate decision: it means that file
# receives the real database password. Review it by hand.
ALLOWED_DATABASE_URL_CALLERS: tuple[str, ...] = ()


def _called_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _app_python_files():
    for path in sorted(APP_DIR.rglob("*.py")):
        if "__pycache__" not in path.parts:
            yield path


def test_config_guard():
    violations = []
    for path in _app_python_files():
        rel = path.relative_to(APP_DIR.parent).as_posix()
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _called_name(node)
            if name == "Settings" and path != CONFIG_FILE:
                violations.append(
                    f"{path}:{node.lineno}: constructs Settings() directly; "
                    "only app/config.py may do this, everything else must "
                    "call get_settings()"
                )
            elif name == "errors":
                violations.append(
                    f"{path}:{node.lineno}: calls a method named errors(); "
                    "log str(exc) instead, never exc.errors()"
                )
            elif (
                name == "database_url_str"
                and path != CONFIG_FILE
                and rel not in ALLOWED_DATABASE_URL_CALLERS
            ):
                violations.append(
                    f"{path}:{node.lineno}: calls database_url_str() but "
                    f"{rel!r} is not in ALLOWED_DATABASE_URL_CALLERS"
                )
    assert not violations, "\n".join(violations)
