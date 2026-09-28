# backend/tests/test_config_guard.py
# Guard test (Task 1.1.c refinement; Task 1.1.h extends it to backend/alembic;
# Task 1.3.a1 extends it to qdrant_api_key_str(); Task 1.4.a extends it to
# jwt_signing_key_str()/jwt_signing_key_previous_str(), starting
# ALLOWED_JWT_KEY_CALLERS empty; Task 1.4.b adds its first real entry,
# app/auth/tokens.py): parses every .py file under backend/app and
# backend/alembic with ast (not text search, so comments/strings never
# trigger it) and enforces four rules from PROJECT_SPEC.md's decisions log:
#   - only app/config.py may construct Settings() directly;
#   - nothing calls a method named errors() (that leaks raw input, see
#     config.py's model_config comment);
#   - only files listed in ALLOWED_DATABASE_URL_CALLERS may call
#     database_url_str() outside app/config.py;
#   - only files listed in ALLOWED_QDRANT_API_KEY_CALLERS may call
#     qdrant_api_key_str() outside app/config.py;
#   - only files listed in ALLOWED_JWT_KEY_CALLERS may call
#     jwt_signing_key_str()/jwt_signing_key_previous_str() outside
#     app/config.py.
import ast

from tests.conftest import BACKEND_DIR, called_name, iter_python_files

APP_DIR = BACKEND_DIR / "app"
ALEMBIC_DIR = BACKEND_DIR / "alembic"
CONFIG_FILE = APP_DIR / "config.py"

# NOTE: adding a file here is a deliberate decision: it means that file
# receives the real database password. Review it by hand.
ALLOWED_DATABASE_URL_CALLERS: tuple[str, ...] = ("alembic/env.py", "app/db.py")

# NOTE: adding a file here is a deliberate decision: it means that file
# receives the real Qdrant API key. Review it by hand.
ALLOWED_QDRANT_API_KEY_CALLERS: tuple[str, ...] = ("app/qdrant.py",)

# NOTE: adding a file here is a deliberate decision: it means that file
# receives the real JWT signing key(s). Review it by hand.
ALLOWED_JWT_KEY_CALLERS: tuple[str, ...] = ("app/auth/tokens.py",)

# Duplication check after 1.3.a1/a2/b: both secret accessors are checked by
# the same single branch below instead of one copy-pasted elif per accessor
# -- a third one later needs one entry here, not a third branch. Task 1.4.a
# adds a fourth (and fifth) entry, both sharing ALLOWED_JWT_KEY_CALLERS,
# same reasoning as the task's own single shared allow-list decision.
_SECRET_ACCESSOR_ALLOW_LISTS: dict[str, tuple[str, ...]] = {
    "database_url_str": ALLOWED_DATABASE_URL_CALLERS,
    "qdrant_api_key_str": ALLOWED_QDRANT_API_KEY_CALLERS,
    "jwt_signing_key_str": ALLOWED_JWT_KEY_CALLERS,
    "jwt_signing_key_previous_str": ALLOWED_JWT_KEY_CALLERS,
}


def test_config_guard():
    violations = []
    for path in iter_python_files(APP_DIR, ALEMBIC_DIR):
        rel = path.relative_to(BACKEND_DIR).as_posix()
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = called_name(node)
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
                name in _SECRET_ACCESSOR_ALLOW_LISTS
                and path != CONFIG_FILE
                and rel not in _SECRET_ACCESSOR_ALLOW_LISTS[name]
            ):
                violations.append(
                    f"{path}:{node.lineno}: calls {name}() but {rel!r} is not in its allow-list"
                )
    assert not violations, "\n".join(violations)
