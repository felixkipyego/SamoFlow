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
from app.ingest import embedding
from app.ingest.chunking import chunk_text, compute_content_hash
from app.ingest.embedding import EMBEDDING_MODEL, SparseVectorData, embed_dense, embed_sparse
from app.ingest.extract_html import extract_html
from app.ingest.qdrant_writer import build_payload, build_point, build_point_id, upsert_points
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
        "embedding_model": EMBEDDING_MODEL,
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
    assert payload["embedding_model"] == EMBEDDING_MODEL
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


def _build_real_point(tenant_id: uuid.UUID, sparse_index: int) -> PointStruct:
    # Shared by both halves of the live proof below -- one real point,
    # through the real build_payload()/build_point_id()/build_point() code
    # path, for whichever tenant_id is passed in. sparse_index keeps the
    # two tenants' points from being otherwise-identical twins.
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
        embedding_model=EMBEDDING_MODEL,
        embedding_version="v1",
        text="hello world",
    )
    point_id = build_point_id(document_id, 0, "v1")
    sparse = SparseVectorData(indices=[sparse_index], values=[1.5])
    return build_point(point_id, payload, [0.0] * qdrant.DENSE_VECTOR_SIZE, sparse)


async def test_a_point_written_with_the_canonical_client_id_form_is_found_by_tenant_filter():
    # THE critical live proof (Task 2.5.c, point 5): build_payload()'s own
    # str(tenant_id) form for client_id must be genuinely matched by
    # app.qdrant.tenant_filter() against a real collection -- not merely
    # plausible-looking. A bare upsert here, scoped to this one test; the
    # full upsert primitive is Task 2.5.d's own job.
    #
    # Duplication check after 2.5.a/b/c, C2: extended to seed a SECOND
    # tenant's point through this same real code path and assert tenant
    # B's own tenant_filter() EXCLUDES tenant A's point -- the negative
    # isolation half, not just the positive "A finds A" half originally
    # proven here. test_qdrant_isolation.py (Task 1.3.d) already proves
    # this negative property exhaustively for the underlying mechanism
    # (hand-built payloads using the identical str(uuid.UUID) form); this
    # test's own new claim is that build_payload()/build_point() THEMSELVES
    # produce correctly-isolated points, not a hand-typed test dict.
    async with live_qdrant_collection("point_construction") as (client, name):
        await qdrant.ensure_collection(client, name)

        tenant_a = uuid.uuid4()
        tenant_b = uuid.uuid4()
        point_a = _build_real_point(tenant_a, sparse_index=10)
        point_b = _build_real_point(tenant_b, sparse_index=20)

        await client.upsert(collection_name=name, points=[point_a, point_b])

        records_a, _ = await client.scroll(
            collection_name=name, scroll_filter=qdrant.tenant_filter(tenant_a), limit=10
        )
        records_b, _ = await client.scroll(
            collection_name=name, scroll_filter=qdrant.tenant_filter(tenant_b), limit=10
        )

        assert len(records_a) == 1
        assert records_a[0].id == str(point_a.id)
        assert records_a[0].payload["client_id"] == str(tenant_a)

        assert len(records_b) == 1
        assert records_b[0].id == str(point_b.id)
        assert records_b[0].payload["client_id"] == str(tenant_b)

        # The negative half: tenant A's filter must not surface tenant B's
        # point, and vice versa.
        assert str(point_b.id) not in {r.id for r in records_a}
        assert str(point_a.id) not in {r.id for r in records_b}


# --- Task 2.5.d: upsert_points() and the end-to-end idempotency proof ------
#
# embed_dense() is stubbed in every test below, mirroring test_embedding.py's
# own _patch_client()/_FakeClient pattern exactly (monkeypatching embedding.
# _get_client(), not a shared import across test files -- noted here, not
# consolidated, since this is only the second file needing this exact
# mechanism; a future duplication check can decide whether to extract a
# shared helper). Confirmed with the user before building: 2.5.a's own
# established rule ("verify live once during development, never a real
# OpenAI call in the committed suite") applies here too -- the point-count/
# idempotency properties these tests prove depend on build_point_id()'s
# determinism and Qdrant's own upsert-overwrites-by-id semantics, not on
# what the actual dense vector VALUES are, so a deterministic fake vector
# exercises everything that matters at zero ongoing cost and no API-key
# dependency in CI. embed_sparse() stays genuinely real (fastembed, free
# and local) in every test below, like every other test in this file.
class _FakeEmbeddingItem:
    def __init__(self, vector: list[float]) -> None:
        self.embedding = vector


class _FakeEmbeddingResponse:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.data = [_FakeEmbeddingItem(v) for v in vectors]


class _FakeEmbeddingsResource:
    async def create(self, *, model, input):  # noqa: A002 -- matches the real SDK's own param name
        return _FakeEmbeddingResponse([[0.0] * qdrant.DENSE_VECTOR_SIZE for _ in input])


class _FakeClient:
    def __init__(self) -> None:
        self.embeddings = _FakeEmbeddingsResource()


def _patch_embed_dense(monkeypatch) -> None:
    monkeypatch.setattr(embedding, "_get_client", lambda: _FakeClient())


async def _run_full_pipeline(
    client,
    collection_name: str,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    document_id: uuid.UUID,
    html: str,
    embedding_version: str,
) -> list[PointStruct]:
    # THE full real pipeline (Task 2.5.d, point 3): extract_html() (2.4.b)
    # -> chunk_text()/compute_content_hash() (2.4.c) -> embed_dense()
    # (2.5.a, stubbed per the note above) / embed_sparse() (2.5.b, real) ->
    # build_payload()/build_point_id()/build_point() (2.5.c) ->
    # upsert_points() (this task) -- every stage genuinely real except the
    # one stubbed dense-embedding call.
    content = extract_html(html)
    chunks = chunk_text(content)
    content_hash = compute_content_hash(content)
    texts = [chunk.text for chunk in chunks]

    dense_vectors = await embed_dense(texts)
    sparse_vectors = await embed_sparse(texts)

    points = [
        build_point(
            build_point_id(document_id, chunk.chunk_index, embedding_version),
            build_payload(
                tenant_id=tenant_id,
                source_id=source_id,
                source_type="urls",
                document_id=document_id,
                source_url="https://example.com/page",
                file_name=None,
                title=content.title,
                heading_path=chunk.heading_path,
                chunk_index=chunk.chunk_index,
                content_hash=content_hash,
                embedding_model=EMBEDDING_MODEL,
                embedding_version=embedding_version,
                text=chunk.text,
            ),
            dense_vectors[i],
            sparse_vectors[i],
        )
        for i, chunk in enumerate(chunks)
    ]
    await upsert_points(client, collection_name, points)
    return points


def _html_with_sections(*section_bodies: str) -> str:
    # One <h2> section per body string -- chunking.py never merges text
    # across a heading boundary, so each short section becomes exactly one
    # chunk (confirmed by this module's own design, Task 2.4.c).
    sections = "\n".join(
        f"<h2>Section {i}</h2><p>{body}</p>" for i, body in enumerate(section_bodies, start=1)
    )
    return f"<html><head><title>Doc</title></head><body>{sections}</body></html>"


async def test_upsert_points_batch_upserts_multiple_points_in_one_call_all_queryable(
    monkeypatch,
):
    _patch_embed_dense(monkeypatch)
    async with live_qdrant_collection("upsert_batch") as (client, name):
        await qdrant.ensure_collection(client, name)
        tenant_id, source_id, document_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

        points = await _run_full_pipeline(
            client,
            name,
            tenant_id=tenant_id,
            source_id=source_id,
            document_id=document_id,
            html=_html_with_sections("first section text", "second section text", "third one"),
            embedding_version="v1",
        )

        assert len(points) == 3  # one chunk per section, confirming the batch is genuinely >1
        count = await client.count(collection_name=name)
        assert count.count == 3
        fetched = await client.retrieve(collection_name=name, ids=[p.id for p in points])
        assert {str(r.id) for r in fetched} == {str(p.id) for p in points}


async def test_rerunning_the_identical_pipeline_overwrites_rather_than_duplicates(monkeypatch):
    # THE idempotency proof (Task 2.5.d, point 3) -- the most important
    # test in this task. docs/SPEC.md §5.3: "a re-run overwrites instead of
    # duplicating," proven through the FULL real pipeline end to end, not
    # just build_point_id()'s own isolated determinism (already proven at
    # 2.5.c).
    _patch_embed_dense(monkeypatch)
    async with live_qdrant_collection("idempotency") as (client, name):
        await qdrant.ensure_collection(client, name)
        tenant_id, source_id, document_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        html = _html_with_sections("alpha content", "beta content")

        await _run_full_pipeline(
            client,
            name,
            tenant_id=tenant_id,
            source_id=source_id,
            document_id=document_id,
            html=html,
            embedding_version="v1",
        )
        first_count = await client.count(collection_name=name)
        assert first_count.count == 2

        # Run the EXACT SAME content through the EXACT SAME full pipeline
        # a second time.
        await _run_full_pipeline(
            client,
            name,
            tenant_id=tenant_id,
            source_id=source_id,
            document_id=document_id,
            html=html,
            embedding_version="v1",
        )
        second_count = await client.count(collection_name=name)
        assert second_count.count == 2  # unchanged, not doubled to 4


async def test_upsert_points_with_an_empty_list_is_a_clean_no_op():
    # Task 2.5.d, point 4: confirmed live before building that the real
    # Qdrant server actively REJECTS an empty upsert (400 "Empty update
    # request") -- a clean no-op here is load-bearing, not just tidy,
    # matching embed_dense()/embed_sparse()'s own "empty batch is a valid,
    # reachable state" precedent (e.g. a nearly-empty document, 2.4.b's own
    # is_nearly_empty concept, producing zero chunks).
    async with live_qdrant_collection("upsert_empty") as (client, name):
        await qdrant.ensure_collection(client, name)

        await upsert_points(client, name, [])  # must not raise

        count = await client.count(collection_name=name)
        assert count.count == 0


async def test_rerunning_with_shrunk_content_leaves_the_removed_chunks_orphaned(monkeypatch):
    # Task 2.5.d, point 4 (the real failure-handling/correctness question):
    # investigated live, not assumed. If new content for the SAME document
    # produces FEWER chunks than the old version, do the old chunk_index
    # positions beyond the new count become orphaned, never-cleaned-up
    # points? CONFIRMED YES here, live -- this is a genuine, confirmed gap,
    # not a defect in upsert_points() itself: this function has no way to
    # know how many chunks a document had LAST time (that is Postgres/
    # documents-table information, entirely outside this primitive's own
    # scope). docs/SPEC.md §5.3/§14 already name the owner of exactly this
    # class of cleanup: "A nightly reconcile compares Postgres with Qdrant
    # per tenant and deletes orphaned points" -- Step 2.9 ("Scheduler,
    # plans and reconcile"), not 2.5.d. See the matching Open marker in
    # PROJECT_SPEC.md, owned by Step 2.9.
    _patch_embed_dense(monkeypatch)
    async with live_qdrant_collection("shrink_orphan") as (client, name):
        await qdrant.ensure_collection(client, name)
        tenant_id, source_id, document_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

        old_points = await _run_full_pipeline(
            client,
            name,
            tenant_id=tenant_id,
            source_id=source_id,
            document_id=document_id,
            html=_html_with_sections("one", "two", "three", "four", "five"),
            embedding_version="v1",
        )
        assert len(old_points) == 5
        assert (await client.count(collection_name=name)).count == 5

        new_points = await _run_full_pipeline(
            client,
            name,
            tenant_id=tenant_id,
            source_id=source_id,
            document_id=document_id,
            html=_html_with_sections("one (updated)", "two (updated)", "three (updated)"),
            embedding_version="v1",
        )
        assert len(new_points) == 3

        # The gap: the collection still has all 5 points, not 3 -- chunk
        # indices 3/4's old points were never touched by this second run
        # at all (the new pipeline run never produced a chunk_index 3 or 4
        # to upsert), so they remain exactly as they were.
        total = await client.count(collection_name=name)
        assert total.count == 5

        # The 3 surviving-index points DID correctly overwrite in place
        # (same ids as before, same point count as old_points[:3] -- the
        # positive half of the idempotency guarantee still holds for
        # indices that still exist).
        assert {p.id for p in new_points} == {p.id for p in old_points[:3]}
        updated = await client.retrieve(
            collection_name=name, ids=[old_points[0].id], with_payload=True
        )
        assert updated[0].payload["text"] == "one (updated)"

        # The 2 orphaned points (old chunk_index 3, 4) are still present,
        # completely unchanged, with their OLD content -- confirmed stale,
        # not coincidentally fine.
        orphaned = await client.retrieve(
            collection_name=name, ids=[old_points[3].id, old_points[4].id], with_payload=True
        )
        assert len(orphaned) == 2
        assert {r.payload["text"] for r in orphaned} == {"four", "five"}
