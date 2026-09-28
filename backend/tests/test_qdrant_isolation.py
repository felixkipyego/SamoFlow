# backend/tests/test_qdrant_isolation.py
# Tests for Task 1.3.d: the isolation proof at the Qdrant filter/index level
# -- the second tenant-isolation boundary after Postgres (PROJECT_SPEC.md's
# engineering rules: "the client_id filter is applied inside every
# sub-query"). Deliberately not retrieve() (that's Step 3.1's job) -- this
# proves the underlying Qdrant mechanism (tenant_filter() plus a filtered
# Prefetch in both sub-queries of a hybrid search) is actually leak-proof,
# using the client's query_points() directly.
#
# Two groups:
#   - offline tests for tenant_filter() itself: no Qdrant needed.
#   - live tests against the real test-qdrant service, each on its own
#     uuid-named collection (created via 1.3.c's ensure_collection()),
#     always deleted in a finally block. Two tenants (A, B) are seeded with
#     deliberately leak-prone data (a point in B identical to the query
#     vector, an identical duplicate of it in A, a sparse-only match in each
#     tenant, an orphan point with no client_id at all) so that an unfiltered
#     query provably would leak, and every filtered query is checked by
#     exact set-equality of returned point ids, never just "B is absent".
import uuid
from dataclasses import dataclass

import pytest
from qdrant_client.http.models import (
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchValue,
    PointStruct,
    Prefetch,
    SparseVector,
)

from app import qdrant
from tests.conftest import require_test_qdrant

DIM = qdrant.DENSE_VECTOR_SIZE

# Larger than the number of points any test here seeds (at most 9), so a
# truncated result can never masquerade as a correctly filtered one.
_QUERY_LIMIT = 1000

EMPTY_SPARSE = SparseVector(indices=[], values=[])
QUERY_SPARSE = SparseVector(indices=[10, 20, 30], values=[1.0, 1.0, 1.0])


def _onehot(index: int, value: float = 1.0) -> list[float]:
    vector = [0.0] * DIM
    vector[index] = value
    return vector


def _mixed(values: dict[int, float]) -> list[float]:
    # A vector with a component at index 0 (some overlap with the query
    # axis, giving a weaker but genuinely non-zero cosine score) plus a
    # component at another index (so it is not simply a scaled copy of the
    # query vector either).
    vector = [0.0] * DIM
    for index, value in values.items():
        vector[index] = value
    return vector


QUERY_DENSE = _onehot(0)


def _near_miss(client_id: uuid.UUID) -> uuid.UUID:
    # Flips exactly one bit (and so, for almost every input, exactly one hex
    # digit) of a real UUID -- still a well-formed uuid.UUID, deliberately
    # not "a different random UUID", to prove the match is exact, not a
    # prefix/substring/fuzzy match.
    return uuid.UUID(int=client_id.int ^ 1)


# --- Offline tests: tenant_filter() itself -----------------------------------


def test_tenant_filter_is_a_single_must_condition_on_client_id():
    client_id = uuid.uuid4()
    result = qdrant.tenant_filter(client_id)
    assert isinstance(result, Filter)
    assert result.should is None
    assert result.must_not is None
    assert result.must is not None
    assert len(result.must) == 1
    condition = result.must[0]
    assert isinstance(condition, FieldCondition)
    assert condition.key == qdrant.CLIENT_ID_FIELD
    assert isinstance(condition.match, MatchValue)
    assert condition.match.value == str(client_id)


def test_tenant_filter_two_different_uuids_give_different_filters():
    first = qdrant.tenant_filter(uuid.uuid4())
    second = qdrant.tenant_filter(uuid.uuid4())
    assert first.must[0].match.value != second.must[0].match.value


@pytest.mark.parametrize(
    "bad_client_id",
    [None, "", "not-a-uuid", 12345, str(uuid.uuid4())],
    ids=["none", "empty-string", "plain-string", "int", "uuid-shaped-string"],
)
def test_tenant_filter_rejects_anything_that_is_not_a_real_uuid(bad_client_id):
    with pytest.raises(TypeError):
        qdrant.tenant_filter(bad_client_id)


# --- Live tests against the real test-qdrant service -------------------------
# Seed design (deliberately leak-prone so the CONTROL tests below prove the
# data itself would leak if unfiltered):
#   b_identical  -- tenant B, dense identical to the query vector (would
#                   rank at the top of an unfiltered dense query).
#   b_sparse_only-- tenant B, no dense overlap with the query, sparse
#                   indices identical to the query (found only via sparse).
#   a_dup        -- tenant A, dense identical to b_identical (the exact
#                   same vector, duplicated across tenants).
#   a_weak1/2    -- tenant A, weaker but non-zero dense overlap with the
#                   query (partial component along the query's axis).
#   a_sparse     -- tenant A, no dense overlap, sparse indices identical to
#                   the query (found only via sparse, tenant A's analogue
#                   of b_sparse_only).
#   orphan       -- dense identical to the query, but NO client_id key in
#                   its payload at all (not empty -- entirely absent).


@dataclass(frozen=True)
class SeededIds:
    tenant_a: uuid.UUID
    tenant_b: uuid.UUID
    tenant_c: uuid.UUID
    b_identical: uuid.UUID
    b_sparse_only: uuid.UUID
    a_dup: uuid.UUID
    a_weak1: uuid.UUID
    a_weak2: uuid.UUID
    a_sparse: uuid.UUID
    orphan: uuid.UUID

    @property
    def a_ids(self) -> set[str]:
        return {str(self.a_dup), str(self.a_weak1), str(self.a_weak2), str(self.a_sparse)}

    @property
    def b_ids(self) -> set[str]:
        return {str(self.b_identical), str(self.b_sparse_only)}


async def _seed_two_tenants(client, name: str) -> SeededIds:
    await qdrant.ensure_collection(client, name)
    ids = SeededIds(
        tenant_a=uuid.uuid4(),
        tenant_b=uuid.uuid4(),
        tenant_c=uuid.uuid4(),
        b_identical=uuid.uuid4(),
        b_sparse_only=uuid.uuid4(),
        a_dup=uuid.uuid4(),
        a_weak1=uuid.uuid4(),
        a_weak2=uuid.uuid4(),
        a_sparse=uuid.uuid4(),
        orphan=uuid.uuid4(),
    )
    points = [
        PointStruct(
            id=str(ids.b_identical),
            vector={"dense": QUERY_DENSE, "sparse": EMPTY_SPARSE},
            payload={"client_id": str(ids.tenant_b)},
        ),
        PointStruct(
            id=str(ids.b_sparse_only),
            vector={"dense": _onehot(999), "sparse": QUERY_SPARSE},
            payload={"client_id": str(ids.tenant_b)},
        ),
        PointStruct(
            id=str(ids.a_dup),
            vector={"dense": QUERY_DENSE, "sparse": EMPTY_SPARSE},
            payload={"client_id": str(ids.tenant_a)},
        ),
        PointStruct(
            id=str(ids.a_weak1),
            vector={"dense": _mixed({0: 0.5, 1: 0.5}), "sparse": EMPTY_SPARSE},
            payload={"client_id": str(ids.tenant_a)},
        ),
        PointStruct(
            id=str(ids.a_weak2),
            vector={"dense": _mixed({0: 0.2, 2: 0.8}), "sparse": EMPTY_SPARSE},
            payload={"client_id": str(ids.tenant_a)},
        ),
        PointStruct(
            id=str(ids.a_sparse),
            vector={"dense": _onehot(500), "sparse": QUERY_SPARSE},
            payload={"client_id": str(ids.tenant_a)},
        ),
        PointStruct(
            id=str(ids.orphan),
            vector={"dense": QUERY_DENSE, "sparse": EMPTY_SPARSE},
            payload={},
        ),
    ]
    result = await client.upsert(collection_name=name, points=points, wait=True)
    assert result.status == "completed"
    return ids


async def _dense_query(client, name: str, query_filter: Filter | None):
    result = await client.query_points(
        collection_name=name,
        query=QUERY_DENSE,
        using=qdrant.DENSE_VECTOR_NAME,
        query_filter=query_filter,
        limit=_QUERY_LIMIT,
        with_payload=True,
    )
    return result.points


async def _sparse_query(client, name: str, query_filter: Filter | None):
    result = await client.query_points(
        collection_name=name,
        query=QUERY_SPARSE,
        using=qdrant.SPARSE_VECTOR_NAME,
        query_filter=query_filter,
        limit=_QUERY_LIMIT,
        with_payload=True,
    )
    return result.points


async def _hybrid_query(client, name: str, tenant_filter_obj: Filter):
    # The mandated design: the filter goes inside BOTH prefetches, not only
    # (or in addition to) the outer query -- see the 1.3.d decision log for
    # the live finding on why this is the one configuration the permanent
    # tests assert.
    result = await client.query_points(
        collection_name=name,
        prefetch=[
            Prefetch(
                query=QUERY_DENSE,
                using=qdrant.DENSE_VECTOR_NAME,
                filter=tenant_filter_obj,
                limit=_QUERY_LIMIT,
            ),
            Prefetch(
                query=QUERY_SPARSE,
                using=qdrant.SPARSE_VECTOR_NAME,
                filter=tenant_filter_obj,
                limit=_QUERY_LIMIT,
            ),
        ],
        query=FusionQuery(fusion=Fusion.RRF),
        limit=_QUERY_LIMIT,
        with_payload=True,
    )
    return result.points


def _assert_exact_tenant_result(points, expected_ids: set[str], expected_client_id: str) -> None:
    actual_ids = {str(p.id) for p in points}
    assert actual_ids == expected_ids
    for point in points:
        assert point.payload.get("client_id") == expected_client_id


async def test_control_unfiltered_queries_prove_the_seed_data_is_leak_prone():
    url, key = require_test_qdrant()
    client = qdrant.build_qdrant_client(url, key)
    name = f"isolation_{uuid.uuid4()}"
    try:
        ids = await _seed_two_tenants(client, name)

        dense_points = await _dense_query(client, name, query_filter=None)
        scores = {str(p.id): p.score for p in dense_points}
        # B's identical point ranks at the very top (tied only with A's
        # deliberate duplicate of the same vector) -- strictly above every
        # other, weaker A point. This is what a missing/broken filter would
        # let leak.
        top_score = max(scores.values())
        assert scores[str(ids.b_identical)] == top_score
        assert scores[str(ids.b_identical)] > scores[str(ids.a_weak1)]
        assert scores[str(ids.b_identical)] > scores[str(ids.a_weak2)]
        assert scores[str(ids.b_identical)] > scores[str(ids.a_sparse)]

        sparse_points = await _sparse_query(client, name, query_filter=None)
        sparse_ids = {str(p.id) for p in sparse_points}
        assert str(ids.b_sparse_only) in sparse_ids
    finally:
        if await client.collection_exists(name):
            await client.delete_collection(name)
        await client.close()


async def test_dense_query_filtered_to_tenant_a_returns_exactly_tenant_a():
    url, key = require_test_qdrant()
    client = qdrant.build_qdrant_client(url, key)
    name = f"isolation_{uuid.uuid4()}"
    try:
        ids = await _seed_two_tenants(client, name)
        points = await _dense_query(client, name, qdrant.tenant_filter(ids.tenant_a))
        _assert_exact_tenant_result(points, ids.a_ids, str(ids.tenant_a))
    finally:
        if await client.collection_exists(name):
            await client.delete_collection(name)
        await client.close()


async def test_sparse_query_filtered_to_tenant_a_returns_only_the_a_point_that_matches():
    url, key = require_test_qdrant()
    client = qdrant.build_qdrant_client(url, key)
    name = f"isolation_{uuid.uuid4()}"
    try:
        ids = await _seed_two_tenants(client, name)
        points = await _sparse_query(client, name, qdrant.tenant_filter(ids.tenant_a))
        _assert_exact_tenant_result(points, {str(ids.a_sparse)}, str(ids.tenant_a))
    finally:
        if await client.collection_exists(name):
            await client.delete_collection(name)
        await client.close()


async def test_hybrid_query_filtered_to_tenant_a_returns_exactly_tenant_a():
    url, key = require_test_qdrant()
    client = qdrant.build_qdrant_client(url, key)
    name = f"isolation_{uuid.uuid4()}"
    try:
        ids = await _seed_two_tenants(client, name)
        points = await _hybrid_query(client, name, qdrant.tenant_filter(ids.tenant_a))
        _assert_exact_tenant_result(points, ids.a_ids, str(ids.tenant_a))
    finally:
        if await client.collection_exists(name):
            await client.delete_collection(name)
        await client.close()


async def test_all_three_query_types_filtered_to_tenant_b_return_exactly_tenant_b():
    url, key = require_test_qdrant()
    client = qdrant.build_qdrant_client(url, key)
    name = f"isolation_{uuid.uuid4()}"
    try:
        ids = await _seed_two_tenants(client, name)
        tenant_b_filter = qdrant.tenant_filter(ids.tenant_b)

        dense_points = await _dense_query(client, name, tenant_b_filter)
        _assert_exact_tenant_result(dense_points, ids.b_ids, str(ids.tenant_b))

        sparse_points = await _sparse_query(client, name, tenant_b_filter)
        _assert_exact_tenant_result(sparse_points, {str(ids.b_sparse_only)}, str(ids.tenant_b))

        hybrid_points = await _hybrid_query(client, name, tenant_b_filter)
        _assert_exact_tenant_result(hybrid_points, ids.b_ids, str(ids.tenant_b))
    finally:
        if await client.collection_exists(name):
            await client.delete_collection(name)
        await client.close()


async def test_a_tenant_with_no_points_gets_an_empty_result_from_every_query_type():
    url, key = require_test_qdrant()
    client = qdrant.build_qdrant_client(url, key)
    name = f"isolation_{uuid.uuid4()}"
    try:
        ids = await _seed_two_tenants(client, name)
        tenant_c_filter = qdrant.tenant_filter(ids.tenant_c)

        assert await _dense_query(client, name, tenant_c_filter) == []
        assert await _sparse_query(client, name, tenant_c_filter) == []
        assert await _hybrid_query(client, name, tenant_c_filter) == []
    finally:
        if await client.collection_exists(name):
            await client.delete_collection(name)
        await client.close()


async def test_a_near_miss_client_id_matches_nothing():
    url, key = require_test_qdrant()
    client = qdrant.build_qdrant_client(url, key)
    name = f"isolation_{uuid.uuid4()}"
    try:
        ids = await _seed_two_tenants(client, name)
        near_miss_filter = qdrant.tenant_filter(_near_miss(ids.tenant_a))

        assert await _dense_query(client, name, near_miss_filter) == []
        assert await _sparse_query(client, name, near_miss_filter) == []
        assert await _hybrid_query(client, name, near_miss_filter) == []
    finally:
        if await client.collection_exists(name):
            await client.delete_collection(name)
        await client.close()


# --- Prefetch independence: proves the dense and sparse sub-queries of a
# hybrid search are checked SEPARATELY, not that "a filter somewhere in the
# hybrid query" happens to be enough. With a large limit and no
# score_threshold, an *unfiltered* dense sub-query returns every point that
# has a dense vector at all, regardless of how weakly it scores (confirmed
# live in the CONTROL test above) -- so a collection containing any
# cross-tenant point reachable by dense would fail under a dropped dense
# filter no matter what the sparse side does, making the two tests below
# indistinguishable unless each one's points are reachable through only ONE
# channel. Qdrant allows a point to omit a named vector entirely (confirmed
# live), which is what makes that isolation possible: a point with no dense
# vector is invisible to every dense (sub-)query, and one with no sparse
# vector is invisible to every sparse (sub-)query, exactly like the
# CLIENT_ID_FIELD-less orphan point is invisible to every tenant filter.
async def test_hybrid_dense_leak_is_caught_independently_of_the_sparse_prefetch():
    url, key = require_test_qdrant()
    client = qdrant.build_qdrant_client(url, key)
    name = f"isolation_dense_only_{uuid.uuid4()}"
    try:
        await qdrant.ensure_collection(client, name)
        tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
        a_point, b_point = uuid.uuid4(), uuid.uuid4()
        points = [
            PointStruct(
                id=str(a_point), vector={"dense": QUERY_DENSE}, payload={"client_id": str(tenant_a)}
            ),
            PointStruct(
                id=str(b_point), vector={"dense": QUERY_DENSE}, payload={"client_id": str(tenant_b)}
            ),
        ]
        await client.upsert(collection_name=name, points=points, wait=True)

        result = await _hybrid_query(client, name, qdrant.tenant_filter(tenant_a))
        _assert_exact_tenant_result(result, {str(a_point)}, str(tenant_a))
    finally:
        if await client.collection_exists(name):
            await client.delete_collection(name)
        await client.close()


async def test_hybrid_sparse_leak_is_caught_independently_of_the_dense_prefetch():
    url, key = require_test_qdrant()
    client = qdrant.build_qdrant_client(url, key)
    name = f"isolation_sparse_only_{uuid.uuid4()}"
    try:
        await qdrant.ensure_collection(client, name)
        tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
        a_point, b_point = uuid.uuid4(), uuid.uuid4()
        points = [
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
        ]
        await client.upsert(collection_name=name, points=points, wait=True)

        result = await _hybrid_query(client, name, qdrant.tenant_filter(tenant_a))
        _assert_exact_tenant_result(result, {str(a_point)}, str(tenant_a))
    finally:
        if await client.collection_exists(name):
            await client.delete_collection(name)
        await client.close()
