# backend/scripts/_shared.py
# Duplication check after 3.6-deferral/3.7.a/the-answer-prompt-fix
# (2026-10-13, due at n=3 per rule 9): point_test_env_at_env_vars() had
# been copy-pasted, byte-for-byte identical in substance, into THREE
# separate scripts (manual_research_gate_signal.py, the original;
# manual_research_followup_rewrite.py; manual_research_answer_prompt_
# referent.py) -- past this project's own established 3rd-occurrence
# extraction threshold (e.g. _fake_is_unsafe_except_loopback(), flagged
# at exactly 3 occurrences in PROJECT_SPEC.md's own Open markers).
# manual_research_followup_rewrite.py's own copy claimed, in a comment,
# that manual_research_gate_signal.py's own header explained "why
# importing it wholesale, rather than just its data, would be the wrong
# call" -- checked directly: no such reasoning exists anywhere in that
# file. Extracted for real here; the stale claim does not survive this
# change.
#
# Security-relevant, not merely convenience: every caller of this
# function is a script that seeds REAL data (including real, paid
# embedding calls) into whatever Qdrant instance TEST_QDRANT_URL names --
# the host allow-list below is what stops such a script from ever being
# pointed at a non-local, non-disposable instance by accident. Three
# independently-maintained copies of a safety check is a real drift risk
# (a future fix to one copy silently leaving the other two on the old,
# less-safe behavior), not just duplicated text.
import os
from urllib.parse import urlsplit

_ALLOWED_TEST_HOSTS = frozenset({"localhost", "127.0.0.1"})


def point_test_env_at_env_vars() -> None:
    """Points the plain QDRANT_URL/QDRANT_API_KEY/QDRANT_READ_ONLY_API_KEY
    env vars (that every real `app.*` module reads) at the TEST_QDRANT_*
    values already in the process environment -- so importing `app.*`
    after calling this transparently talks to the disposable test
    instance `make test-qdrant` started, never production Qdrant.

    MUST be called before any `app.*` import in the caller -- Settings
    and every Qdrant-touching module read these plain env vars at import
    or first-use time.
    """
    required = ("TEST_QDRANT_URL", "TEST_QDRANT_API_KEY", "TEST_QDRANT_READ_ONLY_API_KEY")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise SystemExit(
            f"Missing required env var(s): {', '.join(missing)}. Start test-qdrant first "
            "(`make test-qdrant`) and export its URL/keys -- see the calling script's own "
            "header."
        )
    url = os.environ["TEST_QDRANT_URL"]
    host = urlsplit(url).hostname
    if host not in _ALLOWED_TEST_HOSTS:
        raise SystemExit(
            f"TEST_QDRANT_URL host {host!r} is not in the allowed test-host list "
            f"{sorted(_ALLOWED_TEST_HOSTS)} -- refusing to run against a non-local Qdrant "
            "instance (these scripts seed and query real data, including real OpenAI "
            "embeddings, and must never point at anything but a disposable local instance)."
        )
    os.environ["QDRANT_URL"] = url
    os.environ["QDRANT_API_KEY"] = os.environ["TEST_QDRANT_API_KEY"]
    os.environ["QDRANT_READ_ONLY_API_KEY"] = os.environ["TEST_QDRANT_READ_ONLY_API_KEY"]
