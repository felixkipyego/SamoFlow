# backend/app/qdrant.py
# Task 1.3.b: async Qdrant client plumbing (no collection schema yet --
# that's 1.3.c). Nothing here reads the environment or calls get_settings()
# at import time (module import must succeed with an empty environment,
# matching app/db.py's/app/config.py's own rule): get_qdrant_client() is
# @lru_cache-wrapped, same lazy-singleton pattern as db.py's get_engine(),
# so the client is built once, on first real use, not at import.
#
# Construction does not block: verified by reading AsyncQdrantRemote.__init__
# directly (installed qdrant-client==1.19.1). check_compatibility=True (the
# default, kept and passed explicitly here) does perform a network request,
# but via `Thread(target=self._check_compatibility, ..., daemon=True).start()`
# -- a background OS thread, not the event loop -- so it never blocks async
# code, and it only ever emits a UserWarning (client/server version
# mismatch, or server unreachable), never raises. The engineering rule
# ("no blocking calls in handlers") is not actually at risk here, so no
# custom async compatibility check was built; the live test instead calls
# the library's own public qdrant_client.common.version_check functions
# directly (get_server_version/is_compatible) for a deterministic assertion,
# reusing the library's own mechanism rather than a parallel custom one
# (rule 11).
#
# Constructing a client with an api_key over a plain "http://" URL emits a
# second UserWarning ("Api key is used with an insecure connection."),
# confirmed by reading the same source (async_qdrant_remote.py). This is not
# new information -- plain HTTP inside the Docker network is a known,
# already-tracked Phase 7 concern (PROJECT_SPEC.md's open markers) -- so it
# is filtered here, narrowly (exact message, scoped to this one
# construction via warnings.catch_warnings(), never a global filter) rather
# than left to print on every client construction.
import warnings
from functools import lru_cache

from qdrant_client import AsyncQdrantClient

from app.config import get_settings

# Exact text confirmed against the installed qdrant-client==1.19.1
# (async_qdrant_remote.py); filtered by message, not blanket-suppressed, so
# an unrelated UserWarning is never accidentally swallowed.
_INSECURE_API_KEY_WARNING = "Api key is used with an insecure connection."


def build_qdrant_client(url: str, api_key: str) -> AsyncQdrantClient:
    # Pure factory: takes its arguments directly, never reads Settings
    # itself, so it can be unit-tested with fake values and no environment.
    # REST only (prefer_grpc=False): matches the approved 1.3 design and
    # this project's existing plain-HTTP style elsewhere; avoids a second
    # wire protocol for no current benefit. The key is passed via the
    # client's own api_key argument only -- never logged, never interpolated
    # into a message anywhere in this module.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message=_INSECURE_API_KEY_WARNING, category=UserWarning
        )
        return AsyncQdrantClient(
            url=url,
            api_key=api_key,
            prefer_grpc=False,
            check_compatibility=True,
        )


@lru_cache
def get_qdrant_client() -> AsyncQdrantClient:
    settings = get_settings()
    return build_qdrant_client(settings.qdrant_url, settings.qdrant_api_key_str())
