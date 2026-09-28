# backend/tests/test_qdrant_collection.py
# Tests for Task 1.3.c's collection schema (backend/app/qdrant.py).
#
# Two groups:
#   - offline tests for the pure comparison functions (_schema_differences()
#     and friends, _mismatch_message()), plus ensure_collection()'s 409
#     "already exists" race path via a minimal fake client: hand-built
#     qdrant_client model objects and a fake client, no Qdrant needed.
#   - live tests against the real test-qdrant service, each on its own
#     uuid-named collection, always deleted in a finally block: create on a
#     fresh name, a second ensure_collection() call is a no-op, a pre-created
#     mismatched collection raises CollectionSchemaMismatch naming the real
#     difference (never the API key), and a collection missing only its
#     payload indexes gets them repaired.
import httpx
import pytest
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.http.models import (
    CollectionConfig,
    CollectionInfo,
    CollectionParams,
    CollectionStatus,
    Distance,
    HnswConfig,
    KeywordIndexParams,
    Modifier,
    OptimizersConfig,
    OptimizersStatusOneOf,
    PayloadIndexInfo,
    PayloadSchemaType,
    SparseVectorParams,
    VectorParams,
)

from app import qdrant
from tests.conftest import live_qdrant_collection, require_test_qdrant

COLLECTION_NAME = "knowledge_chunks"

# A matching client_id/source_id payload_schema, reused as a base by tests
# that only vary the vectors.
_MATCHING_PAYLOAD_SCHEMA = {
    "client_id": PayloadIndexInfo(
        data_type=PayloadSchemaType.KEYWORD,
        params=KeywordIndexParams(type=PayloadSchemaType.KEYWORD, is_tenant=True),
        points=0,
    ),
    "source_id": PayloadIndexInfo(data_type=PayloadSchemaType.KEYWORD, params=None, points=0),
}

# A matching dense+sparse vectors config, reused by tests that only vary the
# payload_schema.
_MATCHING_VECTORS = {"dense": VectorParams(size=1536, distance=Distance.COSINE)}
_MATCHING_SPARSE_VECTORS = {"sparse": SparseVectorParams(modifier=Modifier.IDF)}


def _make_collection_info(
    *, vectors=None, sparse_vectors=None, payload_schema=None
) -> CollectionInfo:
    # The boilerplate fields below (status, optimizer_status, hnsw_config,
    # etc.) are required by CollectionInfo/CollectionConfig but irrelevant to
    # _schema_differences() -- filled with harmless real-shaped values so
    # only the fields under test vary between callers.
    return CollectionInfo(
        status=CollectionStatus.GREEN,
        optimizer_status=OptimizersStatusOneOf.OK,
        segments_count=1,
        config=CollectionConfig(
            params=CollectionParams(vectors=vectors, sparse_vectors=sparse_vectors),
            hnsw_config=HnswConfig(m=16, ef_construct=100, full_scan_threshold=10000),
            optimizer_config=OptimizersConfig(default_segment_number=0, flush_interval_sec=5),
        ),
        payload_schema=payload_schema if payload_schema is not None else {},
    )


def _matching_info() -> CollectionInfo:
    return _make_collection_info(
        vectors=_MATCHING_VECTORS,
        sparse_vectors=_MATCHING_SPARSE_VECTORS,
        payload_schema=_MATCHING_PAYLOAD_SCHEMA,
    )


def test_schema_differences_is_empty_for_a_matching_collection():
    assert qdrant._schema_differences(_matching_info(), COLLECTION_NAME) == []


def test_schema_differences_reports_wrong_dense_size():
    info = _make_collection_info(
        vectors={"dense": VectorParams(size=768, distance=Distance.COSINE)},
        sparse_vectors=_MATCHING_SPARSE_VECTORS,
        payload_schema=_MATCHING_PAYLOAD_SCHEMA,
    )
    diffs = qdrant._schema_differences(info, COLLECTION_NAME)
    assert len(diffs) == 1
    assert "768" in diffs[0]
    assert "1536" in diffs[0]


def test_schema_differences_reports_wrong_distance():
    info = _make_collection_info(
        vectors={"dense": VectorParams(size=1536, distance=Distance.EUCLID)},
        sparse_vectors=_MATCHING_SPARSE_VECTORS,
        payload_schema=_MATCHING_PAYLOAD_SCHEMA,
    )
    diffs = qdrant._schema_differences(info, COLLECTION_NAME)
    assert len(diffs) == 1
    assert "Euclid" in diffs[0]
    assert "Cosine" in diffs[0]


def test_schema_differences_reports_missing_dense_vector():
    info = _make_collection_info(
        vectors={},
        sparse_vectors=_MATCHING_SPARSE_VECTORS,
        payload_schema=_MATCHING_PAYLOAD_SCHEMA,
    )
    diffs = qdrant._schema_differences(info, COLLECTION_NAME)
    assert len(diffs) == 1
    assert "missing dense vector" in diffs[0]


def test_schema_differences_reports_missing_sparse_vector():
    info = _make_collection_info(
        vectors=_MATCHING_VECTORS,
        sparse_vectors={},
        payload_schema=_MATCHING_PAYLOAD_SCHEMA,
    )
    diffs = qdrant._schema_differences(info, COLLECTION_NAME)
    assert len(diffs) == 1
    assert "missing sparse vector" in diffs[0]


def test_schema_differences_reports_sparse_vector_without_idf():
    info = _make_collection_info(
        vectors=_MATCHING_VECTORS,
        sparse_vectors={"sparse": SparseVectorParams()},
        payload_schema=_MATCHING_PAYLOAD_SCHEMA,
    )
    diffs = qdrant._schema_differences(info, COLLECTION_NAME)
    assert len(diffs) == 1
    assert "IDF" in diffs[0]


def test_schema_differences_reports_missing_client_id_index():
    payload_schema = dict(_MATCHING_PAYLOAD_SCHEMA)
    del payload_schema["client_id"]
    info = _make_collection_info(
        vectors=_MATCHING_VECTORS,
        sparse_vectors=_MATCHING_SPARSE_VECTORS,
        payload_schema=payload_schema,
    )
    diffs = qdrant._schema_differences(info, COLLECTION_NAME)
    assert len(diffs) == 1
    assert "missing payload index on 'client_id'" in diffs[0]


def test_schema_differences_reports_client_id_index_without_is_tenant():
    payload_schema = dict(_MATCHING_PAYLOAD_SCHEMA)
    payload_schema["client_id"] = PayloadIndexInfo(
        data_type=PayloadSchemaType.KEYWORD,
        params=KeywordIndexParams(type=PayloadSchemaType.KEYWORD, is_tenant=False),
        points=0,
    )
    info = _make_collection_info(
        vectors=_MATCHING_VECTORS,
        sparse_vectors=_MATCHING_SPARSE_VECTORS,
        payload_schema=payload_schema,
    )
    diffs = qdrant._schema_differences(info, COLLECTION_NAME)
    assert len(diffs) == 1
    assert "is_tenant" in diffs[0]


def test_schema_differences_reports_missing_source_id_index():
    payload_schema = dict(_MATCHING_PAYLOAD_SCHEMA)
    del payload_schema["source_id"]
    info = _make_collection_info(
        vectors=_MATCHING_VECTORS,
        sparse_vectors=_MATCHING_SPARSE_VECTORS,
        payload_schema=payload_schema,
    )
    diffs = qdrant._schema_differences(info, COLLECTION_NAME)
    assert len(diffs) == 1
    assert "missing payload index on 'source_id'" in diffs[0]


def test_schema_differences_reports_wrong_source_id_index_type():
    payload_schema = dict(_MATCHING_PAYLOAD_SCHEMA)
    payload_schema["source_id"] = PayloadIndexInfo(
        data_type=PayloadSchemaType.INTEGER, params=None, points=0
    )
    info = _make_collection_info(
        vectors=_MATCHING_VECTORS,
        sparse_vectors=_MATCHING_SPARSE_VECTORS,
        payload_schema=payload_schema,
    )
    diffs = qdrant._schema_differences(info, COLLECTION_NAME)
    assert len(diffs) == 1
    assert "integer" in diffs[0]


def test_schema_differences_reports_every_difference_at_once():
    info = _make_collection_info(vectors={}, sparse_vectors={}, payload_schema={})
    diffs = qdrant._schema_differences(info, COLLECTION_NAME)
    assert len(diffs) == 4


def test_mismatch_message_lists_every_difference_and_contains_no_key():
    distinctive_key = "distinctive-test-key-should-not-leak"  # noqa: S105
    diffs = qdrant._schema_differences(
        _make_collection_info(vectors={}, sparse_vectors={}, payload_schema={}), COLLECTION_NAME
    )
    message = qdrant._mismatch_message(diffs, COLLECTION_NAME)
    for diff in diffs:
        assert diff in message
    assert distinctive_key not in message


# --- Offline: ensure_collection()'s 409 "already exists" race path ---------
# A minimal fake client -- only the methods ensure_collection() actually
# calls -- so the race between collection_exists() reporting missing and
# create_collection() finding another caller already won can be exercised
# deterministically, with no live Qdrant and no real concurrency needed.


def _conflict_response(status_code: int) -> UnexpectedResponse:
    return UnexpectedResponse(
        status_code=status_code,
        reason_phrase="Conflict" if status_code == 409 else "Internal Server Error",
        content=b'{"status":{"error":"simulated"}}',
        headers=httpx.Headers(),
    )


class _FakeRacingClient:
    def __init__(self, *, create_status_code: int, get_collection_info: CollectionInfo | None):
        self._create_status_code = create_status_code
        self._get_collection_info = get_collection_info
        self.get_collection_calls = 0

    async def collection_exists(self, name: str) -> bool:
        # ensure_collection() only ever calls this once, before attempting
        # create_collection() -- always reports "missing" here, so the
        # fake exercises the same path a real race would: the check said
        # no, then create_collection() found otherwise.
        return False

    async def create_collection(self, **kwargs) -> None:
        raise _conflict_response(self._create_status_code)

    async def get_collection(self, name: str) -> CollectionInfo:
        self.get_collection_calls += 1
        assert self._get_collection_info is not None, (
            "get_collection() must not be called when create_collection() raised a non-409 status"
        )
        return self._get_collection_info


async def test_ensure_collection_recovers_from_a_409_race_when_the_result_matches():
    client = _FakeRacingClient(create_status_code=409, get_collection_info=_matching_info())
    await qdrant.ensure_collection(client, COLLECTION_NAME)
    assert client.get_collection_calls == 1


async def test_ensure_collection_raises_from_a_409_race_when_the_result_mismatches():
    mismatched = _make_collection_info(
        vectors={"dense": VectorParams(size=768, distance=Distance.COSINE)},
        sparse_vectors=_MATCHING_SPARSE_VECTORS,
        payload_schema=_MATCHING_PAYLOAD_SCHEMA,
    )
    client = _FakeRacingClient(create_status_code=409, get_collection_info=mismatched)
    with pytest.raises(qdrant.CollectionSchemaMismatch) as exc_info:
        await qdrant.ensure_collection(client, COLLECTION_NAME)
    assert "768" in str(exc_info.value)


async def test_ensure_collection_propagates_a_non_409_error_from_create_collection():
    client = _FakeRacingClient(create_status_code=500, get_collection_info=None)
    with pytest.raises(UnexpectedResponse) as exc_info:
        await qdrant.ensure_collection(client, COLLECTION_NAME)
    assert exc_info.value.status_code == 500
    assert client.get_collection_calls == 0


# --- Live tests against the real test-qdrant service -----------------------
# Each test uses conftest.py's live_qdrant_collection() for the
# require_test_qdrant() -> build_qdrant_client() -> unique name ->
# try/finally(delete-if-exists, close) scaffold (duplication check after
# 1.3.c/d/e; previously repeated inline / via a local _cleanup() helper).


async def _create_bare_collection(
    client, name: str, *, dense_size: int = 1536, distance: Distance = Distance.COSINE
) -> None:
    # Local helper (duplication check after 1.3.c/d/e): the four tests below
    # each pre-create a collection with dense+sparse vectors but no payload
    # indexes, varying only dense_size/distance -- this collapses four
    # near-identical client.create_collection(...) calls into one.
    await client.create_collection(
        collection_name=name,
        vectors_config={"dense": VectorParams(size=dense_size, distance=distance)},
        sparse_vectors_config={"sparse": SparseVectorParams(modifier=Modifier.IDF)},
    )


async def test_ensure_collection_creates_and_reads_back_the_full_schema():
    async with live_qdrant_collection("schema_probe") as (client, name):
        await qdrant.ensure_collection(client, name)

        info = await client.get_collection(name)
        assert info.config.params.vectors["dense"].size == qdrant.DENSE_VECTOR_SIZE
        assert info.config.params.vectors["dense"].distance == Distance.COSINE
        assert info.config.params.sparse_vectors["sparse"].modifier == Modifier.IDF

        client_id_index = info.payload_schema["client_id"]
        assert client_id_index.data_type == PayloadSchemaType.KEYWORD
        assert client_id_index.params.is_tenant is True

        source_id_index = info.payload_schema["source_id"]
        assert source_id_index.data_type == PayloadSchemaType.KEYWORD


async def test_ensure_collection_called_twice_is_a_no_op():
    async with live_qdrant_collection("schema_probe") as (client, name):
        await qdrant.ensure_collection(client, name)
        before = (await client.get_collection(name)).model_dump()

        await qdrant.ensure_collection(client, name)
        after = (await client.get_collection(name)).model_dump()

        assert before == after


async def test_ensure_collection_raises_on_wrong_dense_size():
    _, key = require_test_qdrant()
    async with live_qdrant_collection("schema_probe") as (client, name):
        await _create_bare_collection(client, name, dense_size=768)
        with pytest.raises(qdrant.CollectionSchemaMismatch) as exc_info:
            await qdrant.ensure_collection(client, name)
        message = str(exc_info.value)
        assert "768" in message
        assert "1536" in message
        assert key not in message


async def test_ensure_collection_raises_on_wrong_distance():
    _, key = require_test_qdrant()
    async with live_qdrant_collection("schema_probe") as (client, name):
        await _create_bare_collection(client, name, distance=Distance.EUCLID)
        with pytest.raises(qdrant.CollectionSchemaMismatch) as exc_info:
            await qdrant.ensure_collection(client, name)
        message = str(exc_info.value)
        assert "Euclid" in message
        assert key not in message


async def test_ensure_collection_repairs_both_missing_indexes():
    async with live_qdrant_collection("schema_probe") as (client, name):
        await _create_bare_collection(client, name)
        info_before = await client.get_collection(name)
        assert info_before.payload_schema == {}

        await qdrant.ensure_collection(client, name)

        info_after = await client.get_collection(name)
        client_id_index = info_after.payload_schema["client_id"]
        assert client_id_index.data_type == PayloadSchemaType.KEYWORD
        assert client_id_index.params.is_tenant is True
        assert info_after.payload_schema["source_id"].data_type == PayloadSchemaType.KEYWORD


async def test_ensure_collection_raises_rather_than_replacing_a_non_tenant_index():
    _, key = require_test_qdrant()
    async with live_qdrant_collection("schema_probe") as (client, name):
        await _create_bare_collection(client, name)
        await client.create_payload_index(
            collection_name=name,
            field_name="client_id",
            field_schema=KeywordIndexParams(type=PayloadSchemaType.KEYWORD, is_tenant=False),
        )
        await client.create_payload_index(
            collection_name=name, field_name="source_id", field_schema=PayloadSchemaType.KEYWORD
        )

        with pytest.raises(qdrant.CollectionSchemaMismatch) as exc_info:
            await qdrant.ensure_collection(client, name)
        message = str(exc_info.value)
        assert "is_tenant" in message
        assert key not in message

        # Never silently replaced: the index is still there, still not
        # tenant-optimized.
        info = await client.get_collection(name)
        assert info.payload_schema["client_id"].params.is_tenant is False
