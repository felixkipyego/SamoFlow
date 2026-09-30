# backend/tests/test_config_guard.py
# Guard test (Task 1.1.c refinement; Task 1.1.h extends it to backend/alembic;
# Task 1.3.a1 extends it to qdrant_api_key_str(); Task 1.4.a extends it to
# jwt_signing_key_str()/jwt_signing_key_previous_str(), starting
# ALLOWED_JWT_KEY_CALLERS empty; Task 1.4.b adds its first real entry,
# app/auth/tokens.py; Task 2.1.a extends it to
# db_connection_encryption_key_str(), starting
# ALLOWED_DB_CONNECTION_ENCRYPTION_KEY_CALLERS with its one real caller,
# app/ingest/repository.py; duplication check after 2.1.a/b moves the
# secret-accessor allow-list check itself into a shared
# check_call_allowlist() helper, tests/conftest.py, reused by
# test_ingest_repository_guard.py -- the Settings()/errors() checks below
# stay this file's own, since they differ): parses every .py file under backend/app and
# backend/alembic with ast (not text search, so comments/strings never
# trigger it) and enforces five rules from PROJECT_SPEC.md's decisions log:
#   - only app/config.py may construct Settings() directly;
#   - nothing calls a method named errors() (that leaks raw input, see
#     config.py's model_config comment);
#   - only files listed in ALLOWED_DATABASE_URL_CALLERS may call
#     database_url_str() outside app/config.py;
#   - only files listed in ALLOWED_QDRANT_API_KEY_CALLERS may call
#     qdrant_api_key_str() outside app/config.py;
#   - only files listed in ALLOWED_JWT_KEY_CALLERS may call
#     jwt_signing_key_str()/jwt_signing_key_previous_str() outside
#     app/config.py;
#   - only files listed in ALLOWED_DB_CONNECTION_ENCRYPTION_KEY_CALLERS may
#     call db_connection_encryption_key_str() outside app/config.py.
import ast

from tests.conftest import BACKEND_DIR, called_name, check_call_allowlist, iter_python_files

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

# NOTE: adding a file here is a deliberate decision: it means that file
# receives the real pgcrypto passphrase for db_connections' credentials.
# Review it by hand.
ALLOWED_DB_CONNECTION_ENCRYPTION_KEY_CALLERS: tuple[str, ...] = ("app/ingest/repository.py",)

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
    "db_connection_encryption_key_str": ALLOWED_DB_CONNECTION_ENCRYPTION_KEY_CALLERS,
}


def test_config_guard():
    violations = []
    for path in iter_python_files(APP_DIR, ALEMBIC_DIR):
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

    # Duplication check after 2.1.a/b: the allow-list-checking shape below
    # (shared with test_ingest_repository_guard.py) is now
    # check_call_allowlist() (tests/conftest.py) -- one call per secret
    # accessor, self_file=CONFIG_FILE matching the "and path != CONFIG_FILE"
    # exemption every accessor above already had.
    for target_name, allowlist in _SECRET_ACCESSOR_ALLOW_LISTS.items():
        violations.extend(
            check_call_allowlist(
                iter_python_files(APP_DIR, ALEMBIC_DIR),
                target_name,
                allowlist,
                self_file=CONFIG_FILE,
            )
        )

    assert not violations, "\n".join(violations)
