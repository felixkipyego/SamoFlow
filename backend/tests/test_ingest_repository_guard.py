# backend/tests/test_ingest_repository_guard.py
# Guard test (Task 2.1.a), a sibling to test_config_guard.py using the
# exact same AST-based allow-list mechanism (iter_python_files/
# called_name from conftest.py), scoped to a different secret:
# app.ingest.repository.IngestRepository.get_decrypted_credentials() --
# the one sanctioned path that ever turns a db_connections row's
# encrypted credential back into plaintext. A new sibling file, not an
# extension of test_config_guard.py itself: that file's own scope is
# Settings-level secrets (app/config.py); this one is a database-row-level
# secret, a different layer, so it gets its own small, separately-scoped
# guard rather than blurring test_config_guard.py's own stated purpose.
#
# Starts empty: no real caller exists yet -- Step 2.8's own database-sync
# adapter is the eventual, not-yet-built, first legitimate caller.
import ast

from tests.conftest import BACKEND_DIR, called_name, iter_python_files

APP_DIR = BACKEND_DIR / "app"
REPOSITORY_FILE = APP_DIR / "ingest" / "repository.py"

# NOTE: adding a file here is a deliberate decision: it means that file
# receives a tenant's decrypted database credentials in plaintext. Review
# it by hand.
ALLOWED_DECRYPTED_CREDENTIALS_CALLERS: tuple[str, ...] = ()


def test_get_decrypted_credentials_guard():
    violations = []
    for path in iter_python_files(APP_DIR):
        if path == REPOSITORY_FILE:
            continue
        rel = path.relative_to(BACKEND_DIR).as_posix()
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and called_name(node) == "get_decrypted_credentials":
                if rel not in ALLOWED_DECRYPTED_CREDENTIALS_CALLERS:
                    violations.append(
                        f"{path}:{node.lineno}: calls get_decrypted_credentials() but "
                        f"{rel!r} is not in ALLOWED_DECRYPTED_CREDENTIALS_CALLERS"
                    )
    assert not violations, "\n".join(violations)
