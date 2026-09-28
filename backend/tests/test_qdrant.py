# backend/tests/test_qdrant.py
# Tests for Task 1.3.b's async Qdrant client plumbing (backend/app/qdrant.py).
#
# Two groups:
#   - structural tests (no database or Qdrant needed): importing the module
#     has no side effects, the factory configures REST (not gRPC), and
#     get_qdrant_client() is a cached singleton like db.py's get_engine().
#   - the live proof, against the real test-qdrant service: the whole
#     Settings-to-header path works end to end, a wrong key fails
#     authentication, the installed client version is compatible with the
#     pinned server (using qdrant_client's own public compatibility
#     functions -- see app/qdrant.py's header comment for why no custom
#     compatibility check was built), and -- verified directly, not
#     assumed -- the cached client survives across two sequential tests'
#     different pytest-asyncio event loops. This is NOT the same trap
#     db.py's engine has: a scratch two-test experiment (same cached
#     AsyncQdrantClient, two separate pytest-asyncio function-scoped event
#     loops) passed cleanly with no errors, so unlike test_db.py's
#     _fresh_engine fixture, these live tests do not clear the cache
#     between each other -- only once, after the whole module, below.
import asyncio
import importlib.metadata
import re
import subprocess
import sys
import traceback
import warnings

import pytest
from qdrant_client import AsyncQdrantClient
from qdrant_client.common.version_check import get_server_version, is_compatible
from qdrant_client.http.exceptions import UnexpectedResponse

from app import qdrant
from tests.conftest import (
    BACKEND_DIR,
    VALID_ENV,
    minimal_subprocess_env,
    require_test_qdrant,
    set_valid_env,
)


def test_importing_qdrant_module_has_no_side_effects():
    # Subprocess with no environment at all: proves the module can be
    # imported before Settings could possibly validate, i.e. get_settings()
    # is never called at import time, and no client is constructed either.
    result = subprocess.run(  # noqa: S603 (fixed args, not user input)
        [sys.executable, "-c", "import app.qdrant"],
        cwd=BACKEND_DIR,
        env=minimal_subprocess_env(),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


def test_the_insecure_api_key_warning_filter_is_an_exact_match_not_a_regex():
    # B1 (duplication check after 1.3.a1/a2/b): warnings.filterwarnings'
    # message argument is matched as a regex, not a literal string, so this
    # proves -- using the exact filter arguments build_qdrant_client() itself
    # uses -- that an unrelated UserWarning raised in the same
    # catch_warnings() scope is never accidentally swallowed too.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        warnings.filterwarnings(
            "ignore",
            message=re.escape(qdrant._INSECURE_API_KEY_WARNING),
            category=UserWarning,
        )
        warnings.warn(qdrant._INSECURE_API_KEY_WARNING, UserWarning, stacklevel=1)
        warnings.warn("some unrelated warning", UserWarning, stacklevel=1)

    messages = [str(w.message) for w in caught]
    assert messages == ["some unrelated warning"]


async def test_build_qdrant_client_is_configured_for_rest_not_grpc():
    # 127.0.0.1:1 (an immediate connection refused, not a timeout) so the
    # background compatibility-check thread AsyncQdrantClient.__init__
    # starts (see app/qdrant.py's header comment) fails fast and quietly.
    client = qdrant.build_qdrant_client("http://127.0.0.1:1", "fake-key")
    try:
        assert isinstance(client, AsyncQdrantClient)
        # No public attribute exposes this; _prefer_grpc is the only place
        # it is recorded, confirmed by reading the installed library source.
        assert client._client._prefer_grpc is False
    finally:
        await client.close()


async def test_get_qdrant_client_is_a_cached_singleton(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV, QDRANT_URL="http://127.0.0.1:1")
    qdrant.get_qdrant_client.cache_clear()
    try:
        first = qdrant.get_qdrant_client()
        second = qdrant.get_qdrant_client()
        assert first is second
        qdrant.get_qdrant_client.cache_clear()
        third = qdrant.get_qdrant_client()
        assert third is not first
        await third.close()
    finally:
        await first.close()
        qdrant.get_qdrant_client.cache_clear()


@pytest.fixture(scope="module", autouse=True)
def _close_the_shared_live_client_once():
    # Deliberately module-scoped, not per-test: the two tests below share
    # one cached get_qdrant_client() instance across their own separate
    # event loops (see this file's header comment for the proof that this
    # is safe for this client), so it is closed and the cache cleared once,
    # here, after both have run -- not per-test, which would defeat the
    # point of what is being proved.
    yield
    if qdrant.get_qdrant_client.cache_info().currsize:
        asyncio.run(qdrant.get_qdrant_client().close())
    qdrant.get_qdrant_client.cache_clear()


async def test_get_qdrant_client_lists_collections_with_the_right_key(monkeypatch):
    url, key = require_test_qdrant()
    set_valid_env(monkeypatch, VALID_ENV, QDRANT_URL=url, QDRANT_API_KEY=key)
    client = qdrant.get_qdrant_client()
    # Proves the whole Settings -> build_qdrant_client -> api-key-header
    # path works end to end against the real service, not just that
    # construction succeeds.
    result = await client.get_collections()
    assert result.collections is not None


async def test_get_qdrant_client_again_reuses_the_same_client_a_different_loop(monkeypatch):
    # Same cache entry as the test above, deliberately not cleared in
    # between -- a fresh pytest-asyncio event loop for this test, the
    # classic "async client across event loops" trap, proven not to apply
    # to this client (see this file's header comment).
    url, key = require_test_qdrant()
    set_valid_env(monkeypatch, VALID_ENV, QDRANT_URL=url, QDRANT_API_KEY=key)
    client = qdrant.get_qdrant_client()
    result = await client.get_collections()
    assert result.collections is not None


async def test_wrong_key_fails_authentication():
    url, _key = require_test_qdrant()
    wrong_key = "wrong-key"  # noqa: S105 (test fixture value, not a real secret)
    client = qdrant.build_qdrant_client(url, wrong_key)
    try:
        with pytest.raises(UnexpectedResponse) as exc_info:
            await client.get_collections()
        assert exc_info.value.status_code == 401
        # C1 (duplication check after 1.3.a1/a2/b): the wrong key itself must
        # never appear in this exception's public surfaces -- repr/str, or
        # a full traceback -- even though it was rejected, not accepted.
        exc = exc_info.value
        assert wrong_key not in repr(exc)
        assert wrong_key not in str(exc)
        rendered_traceback = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        assert wrong_key not in rendered_traceback
    finally:
        await client.close()


def test_client_repr_and_str_never_contain_the_api_key():
    # C1: repr()/str() of the client object itself, for a client built with
    # a real-shaped key, must never contain that key -- checked offline
    # (fake unreachable URL, no live Qdrant needed) since this is a property
    # of the client object's own __repr__/__str__, not of a live connection.
    # Deliberately asserts only on these two public surfaces, not on the
    # client's private headers dict, which holds the key by design as the
    # auth header.
    distinctive_key = "distinctive-test-key-should-not-leak"  # noqa: S105
    client = qdrant.build_qdrant_client("http://127.0.0.1:1", distinctive_key)
    try:
        assert distinctive_key not in repr(client)
        assert distinctive_key not in str(client)
    finally:
        asyncio.run(client.close())


def test_client_library_version_is_compatible_with_the_pinned_server():
    url, key = require_test_qdrant()
    client_version = importlib.metadata.version("qdrant-client")
    server_version = get_server_version(url, {"api-key": key}, None, 5)
    assert server_version is not None
    assert is_compatible(client_version, server_version), (
        f"client {client_version} vs server {server_version}"
    )
