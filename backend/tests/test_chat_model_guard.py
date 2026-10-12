# backend/tests/test_chat_model_guard.py
# Task 3.4.a [SECURITY]: Q3's own guard -- matches test_qdrant_read_path_
# guard.py's/test_agent_entrypoint_guard.py's/test_config_guard.py's own
# established style and NOTE-comment allow-list convention (the seventh
# such AST guard in this codebase). Two rules, both from app/agent/
# chat_model.py's own header comment:
#
#   (a) Construction confinement: only app/agent/chat_model.py may call
#       init_chat_model()/ChatOpenAI()/ChatAnthropic().
#   (b) Explicit api_key required, even inside the allowed file: every one
#       of those calls must carry an api_key= keyword argument. Confirmed
#       live (chat_model.py's own header comment) that omitting it
#       silently falls back to the provider's own raw environment
#       variable read, and that variable IS present in this project's
#       real environment (Settings requires OPENAI_API_KEY) -- so a
#       RUNTIME check could never distinguish "passed explicitly" from
#       "silently fell back to the env var"; only a static AST check
#       catches an omitted kwarg reliably, regardless of environment state.
import ast
from pathlib import Path

from tests.conftest import BACKEND_DIR, called_name, check_call_allowlist, iter_python_files

_CONSTRUCTION_NAMES = ("init_chat_model", "ChatOpenAI", "ChatAnthropic")

# NOTE: adding a file here is a deliberate decision -- it means that file
# may construct a real chat model. Review it by hand.
ALLOWED_CHAT_MODEL_CONSTRUCTION_CALLERS: tuple[str, ...] = ("app/agent/chat_model.py",)


def _missing_api_key_violations(path: Path, rel: str) -> list[str]:
    if rel not in ALLOWED_CHAT_MODEL_CONSTRUCTION_CALLERS:
        return []
    tree = ast.parse(path.read_text(), filename=str(path))
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and called_name(node) in _CONSTRUCTION_NAMES:
            if not any(kw.arg == "api_key" for kw in node.keywords):
                violations.append(
                    f"{path}:{node.lineno}: calls {called_name(node)}() with no "
                    "explicit api_key= keyword -- would silently fall back to the "
                    "provider's own raw environment variable"
                )
    return violations


def scan_chat_model_guard(root: Path) -> list[str]:
    """Pure static AST scan, no IO beyond reading the given tree's own .py
    files -- `root` is the directory ALLOWED_CHAT_MODEL_CONSTRUCTION_
    CALLERS' paths are relative to (BACKEND_DIR in production; a tmp_path
    synthetic tree in tests below), matching every other guard's own
    established shape.
    """
    app_root = root / "app"
    if not app_root.is_dir():
        return []
    paths = list(iter_python_files(app_root))
    violations = []
    for name in _CONSTRUCTION_NAMES:
        violations.extend(
            check_call_allowlist(paths, name, ALLOWED_CHAT_MODEL_CONSTRUCTION_CALLERS, root=root)
        )
    for path in paths:
        rel = path.relative_to(root).as_posix()
        violations.extend(_missing_api_key_violations(path, rel))
    return violations


def test_chat_model_guard_passes_against_the_real_backend_app():
    assert scan_chat_model_guard(BACKEND_DIR) == []


# --- Rule (a): construction confinement -------------------------------------


def test_fails_on_construction_outside_the_allowlist(tmp_path):
    body = "\n".join(
        f"m_{i} = {name}('x', api_key='k')" for i, name in enumerate(_CONSTRUCTION_NAMES)
    )
    full = tmp_path / "app" / "somewhere_else.py"
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(body + "\n")
    violations = scan_chat_model_guard(tmp_path)
    assert len(violations) == len(_CONSTRUCTION_NAMES)


def test_passes_for_construction_with_explicit_api_key_inside_the_allowed_file(tmp_path):
    full = tmp_path / "app" / "agent" / "chat_model.py"
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text("m = init_chat_model('x', api_key='k')\n")
    assert scan_chat_model_guard(tmp_path) == []


# --- Rule (b): explicit api_key required, even inside the allowed file -----


def test_fails_on_construction_inside_the_allowed_file_with_no_explicit_api_key(tmp_path):
    full = tmp_path / "app" / "agent" / "chat_model.py"
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text("m = init_chat_model('x')\n")
    violations = scan_chat_model_guard(tmp_path)
    assert len(violations) == 1
    assert "no explicit api_key" in violations[0]


def test_an_unrelated_call_is_never_flagged(tmp_path):
    full = tmp_path / "app" / "somewhere_else.py"
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text("m = some_other_call('x')\n")
    assert scan_chat_model_guard(tmp_path) == []
