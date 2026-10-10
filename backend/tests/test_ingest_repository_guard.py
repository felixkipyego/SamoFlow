# backend/tests/test_ingest_repository_guard.py
# Guard test (Task 2.1.a), a sibling to test_config_guard.py using the
# same shared check_call_allowlist() helper (tests/conftest.py,
# duplication check after 2.1.a/b), scoped to a different secret:
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
from tests.conftest import BACKEND_DIR, check_call_allowlist, iter_python_files

APP_DIR = BACKEND_DIR / "app"
REPOSITORY_FILE = APP_DIR / "ingest" / "repository.py"

# NOTE: adding a file here is a deliberate decision: it means that file
# receives a tenant's decrypted database credentials in plaintext. Review
# it by hand.
#
# Task 2.8.e [SECURITY]: the first real caller -- handle_ingest_db()
# (app/ingest/job_handlers.py) decrypts a db_connection's own credentials
# immediately before connect_safely() (2.8.a), exactly the real need this
# guard's own header comment anticipated.
ALLOWED_DECRYPTED_CREDENTIALS_CALLERS: tuple[str, ...] = ("app/ingest/job_handlers.py",)


def test_get_decrypted_credentials_guard():
    violations = check_call_allowlist(
        iter_python_files(APP_DIR),
        "get_decrypted_credentials",
        ALLOWED_DECRYPTED_CREDENTIALS_CALLERS,
        self_file=REPOSITORY_FILE,
    )
    assert not violations, "\n".join(violations)
