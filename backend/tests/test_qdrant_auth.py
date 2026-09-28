# backend/tests/test_qdrant_auth.py
# Task 1.3.a2: the automated proof that Qdrant's own API-key authentication
# (enabled in 1.3.a1) actually rejects unauthenticated/wrongly-authenticated
# requests, against the real, disposable test-qdrant Compose service.
#
# Two groups:
#   - unit tests for conftest.py's require_test_qdrant(), in the same style
#     as test_alembic.py's require_test_database() tests: unset locally
#     skips with the make command, unset in CI fails, a half-configured
#     environment (URL set, key not) fails rather than skips, and a
#     non-local host fails without ever printing the key.
#   - the live proof: no key / wrong key / right key against a real
#     endpoint, and a write attempt without the key, confirmed to have not
#     merely been rejected but to have genuinely not created anything.
import uuid

import httpx
import pytest

from tests.conftest import require_test_qdrant


def test_require_test_qdrant_unset_and_not_ci_skips_with_make_hint(monkeypatch):
    monkeypatch.delenv("TEST_QDRANT_URL", raising=False)
    monkeypatch.delenv("CI", raising=False)
    with pytest.raises(pytest.skip.Exception) as exc_info:
        require_test_qdrant()
    assert "make test-qdrant" in str(exc_info.value)


def test_require_test_qdrant_unset_and_ci_true_fails(monkeypatch):
    monkeypatch.delenv("TEST_QDRANT_URL", raising=False)
    monkeypatch.setenv("CI", "true")
    with pytest.raises(pytest.fail.Exception):
        require_test_qdrant()


def test_require_test_qdrant_url_set_but_key_unset_fails_never_skips(monkeypatch):
    monkeypatch.setenv("TEST_QDRANT_URL", "http://127.0.0.1:56333")
    monkeypatch.delenv("TEST_QDRANT_API_KEY", raising=False)
    monkeypatch.delenv("CI", raising=False)
    with pytest.raises(pytest.fail.Exception):
        require_test_qdrant()


def test_require_test_qdrant_remote_host_fails_without_printing_the_key(monkeypatch):
    monkeypatch.setenv("TEST_QDRANT_URL", "http://prod-qdrant.internal.example.com:6333")
    monkeypatch.setenv("TEST_QDRANT_API_KEY", "distinctive-remote-key")
    with pytest.raises(pytest.fail.Exception) as exc_info:
        require_test_qdrant()
    assert "distinctive-remote-key" not in str(exc_info.value)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
def test_require_test_qdrant_set_and_safe_returns_url_and_key(monkeypatch, host):
    monkeypatch.setenv("TEST_QDRANT_URL", f"http://{host}:56333")
    monkeypatch.setenv("TEST_QDRANT_API_KEY", "a-test-key")
    assert require_test_qdrant() == (f"http://{host}:56333", "a-test-key")


def test_no_key_is_rejected():
    url, _key = require_test_qdrant()
    response = httpx.get(f"{url}/collections", timeout=5)
    assert response.status_code == 401, response.text


def test_wrong_key_is_rejected():
    url, _key = require_test_qdrant()
    response = httpx.get(f"{url}/collections", headers={"api-key": "wrong-key"}, timeout=5)
    assert response.status_code == 401, response.text


def test_right_key_succeeds():
    url, key = require_test_qdrant()
    response = httpx.get(f"{url}/collections", headers={"api-key": key}, timeout=5)
    assert response.status_code == 200, response.text


def test_write_without_the_key_is_rejected_and_nothing_is_created():
    url, key = require_test_qdrant()
    scratch_name = f"scratch-{uuid.uuid4()}"
    minimal_body = {"vectors": {"size": 4, "distance": "Cosine"}}

    try:
        create_response = httpx.put(
            f"{url}/collections/{scratch_name}", json=minimal_body, timeout=5
        )
        assert create_response.status_code == 401, create_response.text

        # Not just "the write was rejected" -- prove the collection was
        # genuinely never created, using the right key so a 404 here can
        # only mean "does not exist", not "you're not allowed to see it".
        check_response = httpx.get(
            f"{url}/collections/{scratch_name}", headers={"api-key": key}, timeout=5
        )
        assert check_response.status_code == 404, check_response.text
    finally:
        httpx.delete(f"{url}/collections/{scratch_name}", headers={"api-key": key}, timeout=5)
