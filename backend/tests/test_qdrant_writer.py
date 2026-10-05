# backend/tests/test_qdrant_writer.py
# Task 2.5.c: tests for deterministic point construction
# (backend/app/ingest/qdrant_writer.py). [SECURITY]-level rigor, per the
# Step 2.5 decision entry's own note.
#
# Two groups, matching test_qdrant_isolation.py's own convention:
#   - offline tests for build_point_id()/build_payload()/build_point()
#     themselves: no Qdrant needed, pure functions.
#   - one live test against the real test-qdrant service, proving the
#     canonical client_id form this module writes is genuinely matched by
#     app.qdrant.tenant_filter() -- NOT re-proving tenant_filter()'s own
#     cross-tenant leak-proofing mechanics, which test_qdrant_isolation.py
#     (Task 1.3.d) already covers exhaustively; this test's only new claim
#     is that build_payload()'s own client_id value is written in the
#     exact form that function expects.
import uuid

import pytest
from qdrant_client.http.models import PointStruct, SparseVector

from app import qdrant
from app.ingest.embedding import SparseVectorData
from app.ingest.qdrant_writer import build_payload, build_point, build_point_id
from tests.conftest import live_qdrant_collection


def test_build_point_id_same_inputs_produce_the_same_id_every_time():
    document_id = uuid.uuid4()

    first = build_point_id(document_id, 0, "v1")
    second = build_point_id(document_id, 0, "v1")

    assert first == second


def test_build_point_id_different_document_id_produces_a_different_id():
    chunk_index, embedding_version = 0, "v1"

    first = build_point_id(uuid.uuid4(), chunk_index, embedding_version)
    second = build_point_id(uuid.uuid4(), chunk_index, embedding_version)

    assert first != second


def test_build_point_id_different_chunk_index_produces_a_different_id():
    document_id, embedding_version = uuid.uuid4(), "v1"

    first = build_point_id(document_id, 0, embedding_version)
    second = build_point_id(document_id, 1, embedding_version)

    assert first != second


def test_build_point_id_different_embedding_version_produces_a_different_id():
    document_id, chunk_index = uuid.uuid4(), 0

    first = build_point_id(document_id, chunk_index, "v1")
    second = build_point_id(document_id, chunk_index, "v2")

    assert first != second


def _sample_payload(**overrides) -> dict:
    defaults = {
        "tenant_id": uuid.uuid4(),
        "source_id": uuid.uuid4(),
        "source_type": "urls",
        "document_id": uuid.uuid4(),
        "source_url": "https://example.com/page",
        "file_name": None,
        "title": "Example Page",
        "heading_path": ("Intro", "Getting started"),
        "chunk_index": 0,
        "content_hash": "abc123",
        "embedding_model": "text-embedding-3-small",
        "embedding_version": "v1",
        "text": "hello world",
    }
    defaults.update(overrides)
    return build_payload(**defaults)


def test_build_payload_contains_every_required_field_correctly_typed():
    tenant_id = uuid.uuid4()
    source_id = uuid.uuid4()
    document_id = uuid.uuid4()

    payload = _sample_payload(tenant_id=tenant_id, source_id=source_id, document_id=document_id)

    # docs/SPEC.md §5.2's own named fields, exactly -- no more, no fewer.
    assert set(payload.keys()) == {
        "client_id",
        "source_id",
        "source_type",
        "doc_id",
        "source_url",
        "file_name",
        "title",
        "heading_path",
        "chunk_index",
        "content_hash",
        "embedding_model",
        "embedding_version",
        "text",
    }
    assert payload["client_id"] == str(tenant_id)
    assert isinstance(payload["client_id"], str)
    assert payload["source_id"] == str(source_id)
    assert payload["doc_id"] == str(document_id)
    assert payload["source_type"] == "urls"
    assert payload["heading_path"] == ["Intro", "Getting started"]
    assert isinstance(payload["heading_path"], list)
    assert payload["chunk_index"] == 0
    assert isinstance(payload["chunk_index"], int)
    assert payload["content_hash"] == "abc123"
    assert payload["embedding_model"] == "text-embedding-3-small"
    assert payload["embedding_version"] == "v1"
    assert payload["text"] == "hello world"


def test_build_payload_rejects_a_non_uuid_tenant_id():
    with pytest.raises(TypeError):
        _sample_payload(tenant_id="not-a-uuid")


def test_build_payload_web_source_has_source_url_and_no_file_name():
    payload = _sample_payload(source_url="https://example.com/page", file_name=None)

    assert payload["source_url"] == "https://example.com/page"
    assert payload["file_name"] is None
    assert "source_url" in payload
    assert "file_name" in payload


def test_build_payload_upload_source_has_file_name_and_no_source_url():
    payload = _sample_payload(source_url=None, file_name="report.pdf")

    assert payload["file_name"] == "report.pdf"
    assert payload["source_url"] is None
    assert "source_url" in payload
    assert "file_name" in payload


def test_build_payload_database_source_has_neither_source_url_nor_file_name():
    # Task 2.4.a's own confirmed-valid third case: a `database`-source
    # document has neither -- both keys stay present, both None, not
    # omitted.
    payload = _sample_payload(source_url=None, file_name=None, source_type="database")

    assert payload["source_url"] is None
    assert payload["file_name"] is None
    assert "source_url" in payload
    assert "file_name" in payload


def test_build_point_vector_names_match_the_real_collection_schema():
    payload = _sample_payload()
    point_id = build_point_id(uuid.uuid4(), 0, "v1")
    sparse = SparseVectorData(indices=[1, 2, 3], values=[1.1, 2.2, 3.3])

    point = build_point(point_id, payload, [0.1, 0.2, 0.3], sparse)

    assert isinstance(point, PointStruct)
    assert point.id == point_id
    # Imported directly from app.qdrant (Task 1.3.c's own schema), not
    # hardcoded strings here -- if the schema's vector names ever changed,
    # this test would still correctly track them.
    assert set(point.vector.keys()) == {qdrant.DENSE_VECTOR_NAME, qdrant.SPARSE_VECTOR_NAME}
    assert point.vector[qdrant.DENSE_VECTOR_NAME] == [0.1, 0.2, 0.3]
    real_sparse = point.vector[qdrant.SPARSE_VECTOR_NAME]
    assert isinstance(real_sparse, SparseVector)
    assert real_sparse.indices == [1, 2, 3]
    assert real_sparse.values == [1.1, 2.2, 3.3]
    assert point.payload == payload


async def test_a_point_written_with_the_canonical_client_id_form_is_found_by_tenant_filter():
    # THE critical live proof (Task 2.5.c, point 5): build_payload()'s own
    # str(tenant_id) form for client_id must be genuinely matched by
    # app.qdrant.tenant_filter() against a real collection -- not merely
    # plausible-looking. A bare upsert here, scoped to this one test; the
    # full upsert primitive is Task 2.5.d's own job.
    async with live_qdrant_collection("point_construction") as (client, name):
        await qdrant.ensure_collection(client, name)

        tenant_id = uuid.uuid4()
        document_id = uuid.uuid4()
        payload = build_payload(
            tenant_id=tenant_id,
            source_id=uuid.uuid4(),
            source_type="urls",
            document_id=document_id,
            source_url="https://example.com/page",
            file_name=None,
            title="Example Page",
            heading_path=("Intro",),
            chunk_index=0,
            content_hash="abc123",
            embedding_model="text-embedding-3-small",
            embedding_version="v1",
            text="hello world",
        )
        point_id = build_point_id(document_id, 0, "v1")
        sparse = SparseVectorData(indices=[10, 20], values=[1.5, 2.5])
        point = build_point(point_id, payload, [0.0] * qdrant.DENSE_VECTOR_SIZE, sparse)

        await client.upsert(collection_name=name, points=[point])

        records, _ = await client.scroll(
            collection_name=name, scroll_filter=qdrant.tenant_filter(tenant_id), limit=10
        )

        assert len(records) == 1
        assert records[0].id == str(point_id)
        assert records[0].payload["client_id"] == str(tenant_id)
