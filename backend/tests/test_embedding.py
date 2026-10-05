# backend/tests/test_embedding.py
# Task 2.5.a: tests for embed_dense() (backend/app/ingest/embedding.py).
# Offline, no test database, no network, NO REAL OPENAI API CALLS --
# confirmed live once (during this task's own build, not committed
# here) that the real integration works; the committed suite stubs the
# client's own embeddings.create() method instead, matching this
# project's own "verify live once, stub for the committed suite"
# discipline for a real external paid API. Settings-level leak-test
# coverage for OPENAI_API_KEY itself lives in test_config.py, alongside
# every other secret's own identical pattern -- not duplicated here.
import httpx2
import pytest
from openai import RateLimitError

from app.ingest import embedding
from app.ingest.embedding import EMBEDDING_MODEL, embed_dense


class _FakeEmbeddingItem:
    def __init__(self, vector: list[float]) -> None:
        self.embedding = vector


class _FakeEmbeddingResponse:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.data = [_FakeEmbeddingItem(v) for v in vectors]


class _FakeEmbeddingsResource:
    def __init__(self, vectors: list[list[float]] | None = None, error: Exception | None = None):
        self._vectors = vectors
        self._error = error
        self.calls: list[dict] = []

    async def create(self, *, model, input):  # noqa: A002 (matches the real SDK's own param name)
        self.calls.append({"model": model, "input": input})
        if self._error is not None:
            raise self._error
        return _FakeEmbeddingResponse(self._vectors)


class _FakeClient:
    def __init__(self, embeddings_resource: _FakeEmbeddingsResource) -> None:
        self.embeddings = embeddings_resource


def _patch_client(monkeypatch, fake_client: _FakeClient) -> None:
    # _get_client() is an @lru_cache singleton -- replacing the function
    # object itself (not calling it) bypasses the cache entirely, rather
    # than needing a cache_clear() dance.
    monkeypatch.setattr(embedding, "_get_client", lambda: fake_client)


def _fake_rate_limit_error() -> RateLimitError:
    request = httpx2.Request("POST", "https://api.openai.com/v1/embeddings")
    response = httpx2.Response(429, request=request)
    return RateLimitError("rate limited", response=response, body=None)


async def test_embed_dense_returns_expected_shape_from_a_stubbed_successful_response(
    monkeypatch,
):
    fake_resource = _FakeEmbeddingsResource(vectors=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])
    _patch_client(monkeypatch, _FakeClient(fake_resource))

    result = await embed_dense(["first text", "second text"])

    assert result == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    assert len(fake_resource.calls) == 1
    assert fake_resource.calls[0]["model"] == EMBEDDING_MODEL
    assert fake_resource.calls[0]["input"] == ["first text", "second text"]


async def test_embed_dense_propagates_api_errors_for_the_job_failure_path_to_catch(monkeypatch):
    # Task 2.5.a, point 6(b): a real rate-limit error, left uncaught and
    # unwrapped by embed_dense() itself -- Step 2.1's own worker.py already
    # catches any raised Exception from a job handler and calls mark_job_
    # failed() with its own backoff (confirmed by reading app/worker.py
    # directly at this task's own research stage); embed_dense() does not
    # need its own try/except to make that composition work.
    fake_resource = _FakeEmbeddingsResource(error=_fake_rate_limit_error())
    _patch_client(monkeypatch, _FakeClient(fake_resource))

    with pytest.raises(RateLimitError):
        await embed_dense(["some text"])


async def test_embed_dense_with_empty_input_makes_no_api_call(monkeypatch):
    fake_resource = _FakeEmbeddingsResource(vectors=[])
    _patch_client(monkeypatch, _FakeClient(fake_resource))

    result = await embed_dense([])

    assert result == []
    assert fake_resource.calls == []
