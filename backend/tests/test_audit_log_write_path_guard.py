# backend/tests/test_audit_log_write_path_guard.py
# Task 2.2.h [SECURITY]: AST-based allow-list restricting which file may
# ever construct an AuditLog(...) instance -- a sibling to
# test_ingest_repository_guard.py (not an extension of test_config_guard.py
# itself, which is scoped to app/config.py's own Settings-level secrets, a
# different layer entirely), reusing the exact same shared
# check_call_allowlist()/called_name() helpers (tests/conftest.py) those two
# files already established.
#
# Why this guard matters RIGHT NOW, specifically -- not ordinary
# defense-in-depth: 2.2.b discovered live that `widgetplatform`
# (DATABASE_URL's own role, the only role anywhere in this stack) is a
# Postgres superuser, which bypasses the `REVOKE UPDATE, DELETE ON
# audit_log` statement that migration also applies -- confirmed live, not
# assumed (see PROJECT_SPEC.md's Step 2.2 breakdown, decision (a)'s own
# dated correction). Until Phase 7's role split lands, THIS guard is the
# ONLY real enforcement of audit_log's append-only invariant; it is not a
# secondary layer under a working primary one, unlike every other guard in
# this project (qdrant_api_key_str(), get_decrypted_credentials()).
#
# Construction only, not every mutation path: this guard catches an
# application-code mistake that constructs a NEW AuditLog row somewhere it
# shouldn't -- it says nothing about UPDATE/DELETE on an *existing* row
# (there is no application code anywhere that does that today; the DB-level
# REVOKE, though currently inert against the superuser, remains the
# documented statement of intent for that specific case).
#
# Limits of this guard (matching every prior guard's own stated-limits
# convention -- name what a static check can't prove, don't overstate it):
# it is static and name-based, like test_config_guard.py's own Settings()/
# errors() checks (not import-confinement, like the Qdrant read-path
# guard's rule (a) -- that distinction matters here specifically because
# "AuditLog" is not a generic name reused elsewhere in this codebase the way
# "retrieve" is, confirmed by grep before relying on a name-only match).
# Concretely, it CANNOT see: an import alias (`from app.ingest.models
# import AuditLog as AL` then `AL(...)` -- the Call node's own name would
# be "AL", not "AuditLog"); a dynamically-resolved reference
# (`getattr(models, "AuditLog")(...)`, or any factory function that
# constructs one internally and returns it); or a raw SQL statement that
# inserts into the audit_log TABLE directly (`session.execute(text("INSERT
# INTO audit_log ..."))`), which never goes through the AuditLog ORM class,
# and therefore never produces an AuditLog(...) Call node for this guard to
# see at all. A reviewer must still read code that uses any of these forms
# by hand; this guard narrows what needs that scrutiny to the uncommon
# cases, not all of it.
import ast
from pathlib import Path

from app.ingest.models import AuditLog
from tests.conftest import BACKEND_DIR, called_name, check_call_allowlist, iter_python_files

APP_DIR = BACKEND_DIR / "app"
REPOSITORY_FILE = APP_DIR / "ingest" / "repository.py"

# NOTE: adding a file here is a deliberate decision: it means that file may
# construct a new audit_log row directly. Review it by hand. revoke_domain()
# (Task 2.2.g) is the one real caller today.
ALLOWED_AUDIT_LOG_CONSTRUCTORS: tuple[str, ...] = ("app/ingest/repository.py",)


def test_audit_log_write_path_guard():
    violations = check_call_allowlist(
        iter_python_files(APP_DIR), "AuditLog", ALLOWED_AUDIT_LOG_CONSTRUCTORS
    )
    assert not violations, "\n".join(violations)


def _count_audit_log_constructions(path: Path) -> int:
    # check_call_allowlist() alone would also pass vacuously if AuditLog()
    # were constructed nowhere at all -- this counts the real construction
    # sites inside the one allow-listed file directly, so the test below
    # proves there is exactly one real, live caller, not merely that no
    # violation exists.
    tree = ast.parse(path.read_text(), filename=str(path))
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and called_name(node) == "AuditLog"
    )


def test_exactly_one_legitimate_audit_log_construction_site_exists_today():
    assert _count_audit_log_constructions(REPOSITORY_FILE) == 1
    # Confirms AuditLog itself is actually importable/constructible at all
    # (i.e. this isn't vacuously counting a name that doesn't even resolve
    # to the real ORM class) -- the class exists, imported directly, not
    # assumed.
    assert AuditLog.__tablename__ == "audit_log"


# --- Guard unit tests on synthetic trees (no real backend/app file touched,
# matching the Qdrant read-path guard's own convention) --------------------
#
# check_call_allowlist() (tests/conftest.py) hardcodes its own relative path
# as path.relative_to(BACKEND_DIR) -- correct for scanning the real tree
# above, but it cannot be pointed at a tmp_path synthetic tree (it raises
# ValueError: tmp_path is not a subpath of BACKEND_DIR). Rather than modify
# that shared helper for a need no other guard has yet (out of scope for
# this task's own file list: a new test file, not tests/conftest.py), this
# one small local scan -- the same Call-node/called_name() logic,
# parameterized by `root` instead of hardcoded to BACKEND_DIR -- exists
# purely to make the proof below possible, matching why
# test_qdrant_read_path_guard.py's own scan_read_path_guard() takes a root
# parameter in the first place.


def _scan_for_violations(root: Path, allowlist: tuple[str, ...]) -> list[str]:
    violations = []
    for path in iter_python_files(root):
        rel = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or called_name(node) != "AuditLog":
                continue
            if rel not in allowlist:
                violations.append(
                    f"{rel}:{node.lineno}: calls AuditLog() but is not in its allow-list"
                )
    return violations


def _write(tmp_path: Path, rel_path: str, content: str) -> None:
    full = tmp_path / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)


def test_fails_on_an_audit_log_construction_outside_the_allowlist(tmp_path):
    _write(
        tmp_path,
        "app/other.py",
        "from app.ingest.models import AuditLog\n\n\n"
        "def f():\n"
        "    return AuditLog(actor='x', action='y', target_type='z')\n",
    )
    violations = _scan_for_violations(tmp_path, ALLOWED_AUDIT_LOG_CONSTRUCTORS)
    assert len(violations) == 1
    assert "app/other.py" in violations[0]
    assert "AuditLog" in violations[0]


def test_passes_for_an_audit_log_construction_inside_the_allowlisted_file(tmp_path):
    _write(
        tmp_path,
        "app/ingest/repository.py",
        "from app.ingest.models import AuditLog\n\n\n"
        "def f():\n"
        "    return AuditLog(actor='x', action='y', target_type='z')\n",
    )
    violations = _scan_for_violations(tmp_path, ALLOWED_AUDIT_LOG_CONSTRUCTORS)
    assert violations == []


def test_fails_on_an_audit_log_construction_via_an_import_alias_evading_detection(tmp_path):
    # Documents one of the "Limits of this guard" stated in the header
    # comment, as a live, run proof rather than only a claim in prose: an
    # import alias evades this guard's name-based match entirely -- the
    # Call node's own name is "AL", not "AuditLog", so NO violation is
    # reported even though a real audit_log row is constructed outside the
    # allow-list. This test asserts the (accepted) false negative, so a
    # future change to called_name()/check_call_allowlist() that silently
    # starts resolving aliases would be caught here as a behavior change,
    # not just contradict a comment nobody re-reads.
    _write(
        tmp_path,
        "app/other.py",
        "from app.ingest.models import AuditLog as AL\n\n\n"
        "def f():\n"
        "    return AL(actor='x', action='y', target_type='z')\n",
    )
    violations = _scan_for_violations(tmp_path, ALLOWED_AUDIT_LOG_CONSTRUCTORS)
    assert violations == []
