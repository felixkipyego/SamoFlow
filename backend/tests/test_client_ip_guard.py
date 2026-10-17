# backend/tests/test_client_ip_guard.py
# [SECURITY] L1 PART C (#7) (out-of-band Phase 7 batch, 2026-10-17): the
# task's own "every rate limiter, the audit log and usage events must use
# this one helper -- grep for and remove any other IP derivation" --
# confirmed live (grep across app/) that app/chat/routes.py,
# app/auth/routes.py and app/dashboard/routes.py's own 5 call sites were
# the ONLY places `request.client.host` was read, and that no file
# anywhere derived an IP from X-Forwarded-For/X-Real-IP before this task
# (audit_log/usage_events capture no IP at all today -- app/ingest/
# models.py's AuditLog has no ip_address column -- so there was nothing
# to migrate there). A plain substring scan (this project's own
# established "good enough for a guard whose only job is 'this must stay
# absent'" precedent, test_single_api_process_guard.py), not a full AST
# walk: the point is literal absence of these strings outside the one
# file that is allowed to contain them.
from pathlib import Path

from tests.conftest import iter_python_files

BACKEND_DIR = Path(__file__).resolve().parent.parent
_ALLOWED_FILE = "app/client_ip.py"
_FORBIDDEN_NEEDLES: tuple[str, ...] = (
    "request.client.host",
    "x-forwarded-for",
    "X-Forwarded-For",
    "x-real-ip",
    "X-Real-IP",
)


def test_only_client_ip_module_derives_a_request_ip():
    violations = []
    for path in iter_python_files(BACKEND_DIR / "app"):
        rel = path.relative_to(BACKEND_DIR).as_posix()
        if rel == _ALLOWED_FILE:
            continue
        text = path.read_text()
        for needle in _FORBIDDEN_NEEDLES:
            if needle in text:
                violations.append(f"{rel} contains {needle!r}")
    assert violations == [], (
        "app/client_ip.py's get_client_ip() must be the ONE place a request's "
        "visitor IP is derived -- found: " + "; ".join(violations)
    )
