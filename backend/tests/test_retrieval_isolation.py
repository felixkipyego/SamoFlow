# backend/tests/test_retrieval_isolation.py
# Task 3.1.d [SECURITY]: re-runs Task 1.3.d's own isolation case set
# THROUGH retrieve() itself (app/retrieval/service.py), not query_points()
# directly -- 1.3.d only proved the underlying Qdrant filter/index
# mechanism is leak-proof; this proves retrieve() actually wires it up
# correctly, including the RRF fusion step 1.3.d's own tests never
# exercised through a real caller. Matches the already-recorded [SECURITY]
# marker's own explicit requirement (Step 1.3 breakdown decision log).
#
# Query-side embeddings are PINNED, not real: retrieve() takes query TEXT,
# not a raw vector, but every scenario here needs the exact same
# hand-crafted dense/sparse vectors 1.3.d's own tests already used (for
# the identical reason 1.3.d built them that way -- deterministic,
# engineered adversarial overlap). embedding._get_client()/_get_sparse_
# model() are patched to always return test_qdrant_isolation.py's own
# QUERY_DENSE/QUERY_SPARSE constants, regardless of the query text passed
# in -- every test below calls retrieve() with exactly one query per
# scenario, so no per-text branching is needed. Reuses that file's own
# seed data/constants directly (cross-test-file import, matching this
# project's own established precedent: test_job_handlers.py importing
# test_upload_adapter.py's _docx_with_invalid_xml_content; test_qdrant.py
# importing test_qdrant_collection.py's _create_bare_collection) rather
# than re-deriving the identical adversarial vectors a second time.
import uuid

import numpy as np
import pytest
from qdrant_client.http.models import PointStruct

from app import qdrant
from app.ingest import embedding
from app.ingest.qdrant_writer import HIDDEN_FIELD
from app.retrieval.service import retrieve
from tests.conftest import live_qdrant_collection, set_real_qdrant_env
from tests.test_qdrant_isolation import (
    QUERY_DENSE,
    QUERY_SPARSE,
    _mixed,
    _near_miss,
    _seed_two_tenants,
)

pytestmark = pytest.mark.usefixtures("close_the_shared_qdrant_read_client_once")

_QUERY_TEXT = "irrelevant -- embeddings are pinned below, not derived from this text"


class _FixedEmbeddingsResource:
    async def create(self, *, model, input):  # noqa: A002 (matches the real SDK's own param name)
        return _FixedEmbeddingResponse([QUERY_DENSE for _ in input])


class _FixedEmbeddingResponse:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.data = [_FixedEmbeddingItem(v) for v in vectors]


class _FixedEmbeddingItem:
    def __init__(self, embedding_vector: list[float]) -> None:
        self.embedding = embedding_vector


class _FixedDenseClient:
    def __init__(self) -> None:
        self.embeddings = _FixedEmbeddingsResource()


class _FixedSparseModel:
    def query_embed(self, texts, **kwargs):
        for _ in texts if not isinstance(texts, str) else [texts]:
            yield _FixedSparseEmbedding()


class _FixedSparseEmbedding:
    # .tolist() is called on both attributes by embed_sparse_query() --
    # matching the real SparseEmbedding's own numpy-array-typed fields,
    # not plain lists (QUERY_SPARSE.indices/.values ARE plain lists,
    # qdrant_client's own SparseVector pydantic model shape).
    indices = np.array(QUERY_SPARSE.indices)
    values = np.array(QUERY_SPARSE.values)


def _pin_query_embeddings(monkeypatch) -> None:
    # Dense: embed_dense() calls embedding._get_client().embeddings.create()
    # -- same interception POINT patch_embed_dense() already uses
    # (tests/conftest.py), just with a controllable, non-constant vector
    # here instead of an all-zero one, since these tests need real,
    # engineered dense similarity, not a degenerate one.
    monkeypatch.setattr(embedding, "_get_client", lambda: _FixedDenseClient())
    # Sparse: embed_sparse_query() calls embedding._get_sparse_model().
    # query_embed() -- patched the identical way, same reasoning.
    monkeypatch.setattr(embedding, "_get_sparse_model", lambda: _FixedSparseModel())


def _result_ids(points) -> set[str]:
    return {str(p.id) for p in points}


async def test_dense_only_leak_attempt_is_blocked_through_retrieve(monkeypatch):
    # Mirrors test_hybrid_dense_leak_is_caught_independently_of_the_sparse_
    # prefetch (1.3.d) -- a point with ONLY a dense vector (no sparse at
    # all, invisible to the sparse prefetch entirely), identical to the
    # pinned query vector, for BOTH tenants. If retrieve()'s own dense
    # Prefetch filter were ever missing, tenant B's point -- an exact
    # dense match -- would surface for tenant A's query.
    _pin_query_embeddings(monkeypatch)
    set_real_qdrant_env(monkeypatch)
    async with live_qdrant_collection("retrieve_isolation_dense_only") as (client, name):
        await qdrant.ensure_collection(client, name)
        tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
        a_point, b_point = uuid.uuid4(), uuid.uuid4()
        await client.upsert(
            collection_name=name,
            points=[
                PointStruct(
                    id=str(a_point),
                    vector={"dense": QUERY_DENSE},
                    payload={"client_id": str(tenant_a)},
                ),
                PointStruct(
                    id=str(b_point),
                    vector={"dense": QUERY_DENSE},
                    payload={"client_id": str(tenant_b)},
                ),
            ],
            wait=True,
        )

        results = (await retrieve(tenant_a, _QUERY_TEXT, 1000, collection_name=name)).points

        assert _result_ids(results) == {str(a_point)}
        assert all(p.payload.get("client_id") == str(tenant_a) for p in results)


async def test_sparse_only_leak_attempt_is_blocked_through_retrieve(monkeypatch):
    # Mirrors test_hybrid_sparse_leak_is_caught_independently_of_the_
    # dense_prefetch (1.3.d) -- a point with ONLY a sparse vector,
    # identical to the pinned query vector, for BOTH tenants. If
    # retrieve()'s own sparse Prefetch filter were ever missing, tenant
    # B's point -- an exact sparse/vocabulary match -- would surface for
    # tenant A's query, via real vocabulary overlap rather than dense
    # similarity.
    _pin_query_embeddings(monkeypatch)
    set_real_qdrant_env(monkeypatch)
    async with live_qdrant_collection("retrieve_isolation_sparse_only") as (client, name):
        await qdrant.ensure_collection(client, name)
        tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
        a_point, b_point = uuid.uuid4(), uuid.uuid4()
        await client.upsert(
            collection_name=name,
            points=[
                PointStruct(
                    id=str(a_point),
                    vector={"sparse": QUERY_SPARSE},
                    payload={"client_id": str(tenant_a)},
                ),
                PointStruct(
                    id=str(b_point),
                    vector={"sparse": QUERY_SPARSE},
                    payload={"client_id": str(tenant_b)},
                ),
            ],
            wait=True,
        )

        results = (await retrieve(tenant_a, _QUERY_TEXT, 1000, collection_name=name)).points

        assert _result_ids(results) == {str(a_point)}
        assert all(p.payload.get("client_id") == str(tenant_a) for p in results)


async def test_hybrid_fusion_leak_attempt_is_blocked_through_retrieve(monkeypatch):
    # Reuses test_qdrant_isolation.py's own full _seed_two_tenants()
    # scenario -- tenant B's b_identical is a PERFECT dense match (would
    # rank at the very top of an unfiltered dense query, per 1.3.d's own
    # control test) and b_sparse_only is a real sparse/vocabulary match;
    # tenant A's own points are each weaker on one channel or the other.
    # This is the case that genuinely exercises the both-prefetches-plus-
    # outer-filter claim through a real hybrid retrieve() call and real
    # RRF fusion -- not one sub-query in isolation (the two tests above),
    # and not just the underlying Qdrant mechanism (1.3.d's own
    # test_hybrid_query_filtered_to_tenant_a_returns_exactly_tenant_a, at
    # the query_points() level, not through retrieve()).
    _pin_query_embeddings(monkeypatch)
    set_real_qdrant_env(monkeypatch)
    async with live_qdrant_collection("retrieve_isolation_hybrid") as (client, name):
        ids = await _seed_two_tenants(client, name)

        results = (await retrieve(ids.tenant_a, _QUERY_TEXT, 1000, collection_name=name)).points

        assert _result_ids(results) == ids.a_ids
        assert all(p.payload.get("client_id") == str(ids.tenant_a) for p in results)
        # The orphan point (payload={}, no client_id at all -- 1.3.d's own
        # stand-in for a point with no tenant of record, the identical
        # shape a real 2.9.f-style reconcile gap would leave behind) must
        # never surface for ANY tenant's query, confirmed explicitly here,
        # not merely implied by the exact-set-equality check above.
        assert str(ids.orphan) not in _result_ids(results)


async def test_retrieve_for_the_other_tenant_also_returns_only_their_own_results(monkeypatch):
    # Mirrors test_all_three_query_types_filtered_to_tenant_b_return_
    # exactly_tenant_b (1.3.d), the hybrid case specifically -- retrieve()
    # only ever does hybrid, so this is the one relevant sub-case:
    # isolation is proven for BOTH tenants, not just the one asked about
    # above, and the orphan point stays absent here too.
    _pin_query_embeddings(monkeypatch)
    set_real_qdrant_env(monkeypatch)
    async with live_qdrant_collection("retrieve_isolation_hybrid_b") as (client, name):
        ids = await _seed_two_tenants(client, name)

        results = (await retrieve(ids.tenant_b, _QUERY_TEXT, 1000, collection_name=name)).points

        assert _result_ids(results) == ids.b_ids
        assert all(p.payload.get("client_id") == str(ids.tenant_b) for p in results)
        assert str(ids.orphan) not in _result_ids(results)


async def test_retrieve_a_tenant_with_no_points_gets_an_empty_result(monkeypatch):
    # Mirrors test_a_tenant_with_no_points_gets_an_empty_result_from_
    # every_query_type (1.3.d) -- a real tenant_id (tenant_c, seeded as a
    # real UUID by _seed_two_tenants() but given no points of its own)
    # must return an empty list, not an error.
    _pin_query_embeddings(monkeypatch)
    set_real_qdrant_env(monkeypatch)
    async with live_qdrant_collection("retrieve_isolation_empty") as (client, name):
        ids = await _seed_two_tenants(client, name)

        results = (await retrieve(ids.tenant_c, _QUERY_TEXT, 1000, collection_name=name)).points

        assert results == []


async def test_retrieve_a_near_miss_client_id_matches_nothing(monkeypatch):
    # Mirrors test_a_near_miss_client_id_matches_nothing (1.3.d) -- a
    # tenant_id lexically one bit different from a real, seeded tenant
    # (tenant_a) must match nothing, proving the match is exact, never a
    # prefix/substring/fuzzy one.
    _pin_query_embeddings(monkeypatch)
    set_real_qdrant_env(monkeypatch)
    async with live_qdrant_collection("retrieve_isolation_near_miss") as (client, name):
        ids = await _seed_two_tenants(client, name)

        retrieval = await retrieve(
            _near_miss(ids.tenant_a), _QUERY_TEXT, 1000, collection_name=name
        )
        results = retrieval.points

        assert results == []


async def test_retrieve_never_returns_a_hidden_points_even_for_its_own_tenant(monkeypatch):
    # Task 3.1.d's own new case, specific to retrieve() -- neither 1.3.d
    # nor 2.9.e's own tests exercise this through a real hybrid retrieve()
    # call. A soft-hidden (disabled-source) point for the SAME tenant
    # making the query must never appear in that tenant's own results,
    # even though it is an otherwise-perfect dense+sparse match (the
    # pinned query vectors) and belongs to the requesting tenant itself --
    # confirming the HIDDEN_FIELD must_not exclusion applies independently
    # of tenant isolation, not as a side effect of it.
    _pin_query_embeddings(monkeypatch)
    set_real_qdrant_env(monkeypatch)
    async with live_qdrant_collection("retrieve_isolation_hidden") as (client, name):
        await qdrant.ensure_collection(client, name)
        tenant_a = uuid.uuid4()
        visible_point, hidden_point = uuid.uuid4(), uuid.uuid4()
        await client.upsert(
            collection_name=name,
            points=[
                PointStruct(
                    id=str(visible_point),
                    vector={"dense": QUERY_DENSE, "sparse": QUERY_SPARSE},
                    payload={"client_id": str(tenant_a)},
                ),
                PointStruct(
                    id=str(hidden_point),
                    vector={"dense": QUERY_DENSE, "sparse": QUERY_SPARSE},
                    payload={"client_id": str(tenant_a), HIDDEN_FIELD: True},
                ),
            ],
            wait=True,
        )

        results = (await retrieve(tenant_a, _QUERY_TEXT, 1000, collection_name=name)).points

        assert _result_ids(results) == {str(visible_point)}


# --- Task 3.4.d [SECURITY]: best_dense_score isolation ----------------------
# A tenant's OWN gate score (retrieve()'s new RetrievalResult.
# best_dense_score) must never be influenced by another tenant's content, or
# by this tenant's own soft-hidden points -- otherwise the fallback-vs-answer
# outcome becomes a real side channel: a visitor could learn "another tenant
# has content closely matching this topic" purely from whether THEIR OWN turn
# was gated or answered, without ever seeing that other tenant's data
# directly. `_mixed({0: 1.0, 1: 1.0})` gives a known, exact, non-trivial
# cosine against QUERY_DENSE (a pure one-hot at index 0): dot product = 1.0,
# norm = sqrt(2), cosine = 1/sqrt(2) -- a real, non-degenerate "weak but
# genuine" match, never coincidentally equal to a perfect match's 1.0.

_KNOWN_WEAK_COSINE = 1.0 / (2.0**0.5)


async def test_best_dense_score_unaffected_by_another_tenants_perfect_dense_match(
    monkeypatch,
):
    _pin_query_embeddings(monkeypatch)
    set_real_qdrant_env(monkeypatch)
    async with live_qdrant_collection("retrieve_isolation_gate_cross_tenant") as (client, name):
        await qdrant.ensure_collection(client, name)
        tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
        a_point, b_point = uuid.uuid4(), uuid.uuid4()
        await client.upsert(
            collection_name=name,
            points=[
                PointStruct(
                    id=str(a_point),
                    vector={"dense": _mixed({0: 1.0, 1: 1.0})},
                    payload={"client_id": str(tenant_a)},
                ),
                PointStruct(
                    id=str(b_point),
                    # Tenant B's own PERFECT dense match for the pinned
                    # query -- if the dense-only request's own filter were
                    # ever dropped, THIS point's 1.0 score would leak into
                    # tenant A's own gate decision.
                    vector={"dense": QUERY_DENSE},
                    payload={"client_id": str(tenant_b)},
                ),
            ],
            wait=True,
        )

        result = await retrieve(tenant_a, _QUERY_TEXT, 1000, collection_name=name)

        assert result.best_dense_score == pytest.approx(_KNOWN_WEAK_COSINE)


async def test_best_dense_score_unaffected_by_a_hidden_point_for_the_same_tenant(
    monkeypatch,
):
    _pin_query_embeddings(monkeypatch)
    set_real_qdrant_env(monkeypatch)
    async with live_qdrant_collection("retrieve_isolation_gate_hidden") as (client, name):
        await qdrant.ensure_collection(client, name)
        tenant_a = uuid.uuid4()
        visible_point, hidden_point = uuid.uuid4(), uuid.uuid4()
        await client.upsert(
            collection_name=name,
            points=[
                PointStruct(
                    id=str(visible_point),
                    vector={"dense": _mixed({0: 1.0, 1: 1.0})},
                    payload={"client_id": str(tenant_a)},
                ),
                PointStruct(
                    id=str(hidden_point),
                    # A soft-hidden point belonging to the SAME tenant,
                    # with a PERFECT dense match -- the gate score must
                    # not be raised by content the HIDDEN_FIELD exclusion
                    # has otherwise taken out of circulation.
                    vector={"dense": QUERY_DENSE},
                    payload={"client_id": str(tenant_a), HIDDEN_FIELD: True},
                ),
            ],
            wait=True,
        )

        result = await retrieve(tenant_a, _QUERY_TEXT, 1000, collection_name=name)

        assert result.best_dense_score == pytest.approx(_KNOWN_WEAK_COSINE)
