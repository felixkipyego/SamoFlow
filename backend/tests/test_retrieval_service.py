# backend/tests/test_retrieval_service.py
# Task 3.1.c [SECURITY]: minimal direct tests proving retrieve() itself
# (app/retrieval/service.py) wires the real Query API together correctly
# and returns real, tenant-scoped, ranked results for a real query --
# live against the real test-qdrant service, real content, real
# embed_and_upsert() writes, a real retrieve() call, nothing mocked at
# the retrieve() level itself (only embed_dense()'s own paid OpenAI call
# is stubbed, patch_embed_dense()'s own established, project-wide
# convention -- embed_sparse_query() makes a real fastembed call here,
# same as every ingestion-side test's own embed_sparse()).
#
# Deliberately NOT the isolation-proof matrix (dense/sparse/hybrid tenant
# leak, no-points tenant, near-miss client_id, orphan point, the
# HIDDEN_FIELD exclusion case) -- that is Task 3.1.d's own [SECURITY]
# task, re-running test_qdrant_isolation.py's own cases THROUGH retrieve()
# itself. This file only proves the basic real-query/real-ranking shape.
import uuid

import pytest
from qdrant_client.models import ScoredPoint

from app import qdrant
from app.ingest.extract_html import extract_html
from app.ingest.qdrant_writer import embed_and_upsert
from app.retrieval.service import chunk_from_scored_point, retrieve
from tests.conftest import live_qdrant_collection, patch_embed_dense, set_real_qdrant_env

pytestmark = pytest.mark.usefixtures("close_the_shared_qdrant_read_client_once")

_SOURCE_ID = uuid.uuid4()


def _page(title: str, body: str) -> str:
    return f"<html><head><title>{title}</title></head><body><h2>S</h2><p>{body}</p></body></html>"


async def _seed_two_tenants_with_real_content(client, name: str) -> tuple[uuid.UUID, uuid.UUID]:
    # Phase-3.1 duplication check (2026-11-01): this exact two-tenant,
    # two-document setup existed as byte-identical code in both
    # test_retrieve_returns_only_the_requesting_tenants_own_ranked_results
    # and test_retrieve_scoped_to_the_other_tenant_returns_only_that_
    # tenants_own_results -- extracted here, A.2's approved fix. Distinct,
    # non-overlapping vocabulary per tenant so the real sparse (BM25) side
    # of the hybrid query genuinely differentiates them -- dense is
    # stubbed to a constant vector by patch_embed_dense(), so the sparse
    # side is what makes this a real, non-degenerate ranking proof.
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()

    await embed_and_upsert(
        extract_html(_page("Tenant A Doc", "zebra giraffe elephant safari wildlife")),
        client=client,
        collection_name=name,
        tenant_id=tenant_a,
        source_id=_SOURCE_ID,
        source_type="urls",
        document_id=uuid.uuid4(),
        source_url="https://tenant-a.example/page",
        file_name=None,
        embedding_version="v1",
    )
    await embed_and_upsert(
        extract_html(_page("Tenant B Doc", "mortgage interest rate amortization loan")),
        client=client,
        collection_name=name,
        tenant_id=tenant_b,
        source_id=_SOURCE_ID,
        source_type="urls",
        document_id=uuid.uuid4(),
        source_url="https://tenant-b.example/page",
        file_name=None,
        embedding_version="v1",
    )
    return tenant_a, tenant_b


async def test_retrieve_returns_only_the_requesting_tenants_own_ranked_results(
    monkeypatch,
):
    patch_embed_dense(monkeypatch)
    set_real_qdrant_env(monkeypatch)
    async with live_qdrant_collection("retrieve_probe") as (client, name):
        await qdrant.ensure_collection(client, name)

        tenant_a, _tenant_b = await _seed_two_tenants_with_real_content(client, name)

        results = await retrieve(tenant_a, "zebra giraffe safari", 8, collection_name=name)

        assert len(results) >= 1
        assert all(point.payload["client_id"] == str(tenant_a) for point in results)
        assert any("zebra" in point.payload["text"] for point in results)
        # Ranked: scores non-increasing down the real, server-fused result.
        scores = [point.score for point in results]
        assert scores == sorted(scores, reverse=True)


async def test_retrieve_scoped_to_the_other_tenant_returns_only_that_tenants_own_results(
    monkeypatch,
):
    patch_embed_dense(monkeypatch)
    set_real_qdrant_env(monkeypatch)
    async with live_qdrant_collection("retrieve_probe") as (client, name):
        await qdrant.ensure_collection(client, name)

        _tenant_a, tenant_b = await _seed_two_tenants_with_real_content(client, name)

        results = await retrieve(tenant_b, "mortgage interest rate", 8, collection_name=name)

        assert len(results) >= 1
        assert all(point.payload["client_id"] == str(tenant_b) for point in results)
        assert any("mortgage" in point.payload["text"] for point in results)


async def test_retrieve_a_tenant_with_no_points_gets_an_empty_result(
    monkeypatch,
):
    patch_embed_dense(monkeypatch)
    set_real_qdrant_env(monkeypatch)
    async with live_qdrant_collection("retrieve_probe") as (client, name):
        await qdrant.ensure_collection(client, name)

        tenant_with_content = uuid.uuid4()
        tenant_with_nothing = uuid.uuid4()

        await embed_and_upsert(
            extract_html(_page("Tenant Doc", "zebra giraffe elephant safari wildlife")),
            client=client,
            collection_name=name,
            tenant_id=tenant_with_content,
            source_id=_SOURCE_ID,
            source_type="urls",
            document_id=uuid.uuid4(),
            source_url="https://tenant.example/page",
            file_name=None,
            embedding_version="v1",
        )

        results = await retrieve(
            tenant_with_nothing, "zebra giraffe safari", 8, collection_name=name
        )

        assert results == []


async def test_retrieve_with_reranker_disabled_behaves_unchanged(monkeypatch):
    # Task 3.1.e: Settings.reranker_enabled defaults to False (not set in
    # VALID_ENV at all here, so get_settings() uses the real default,
    # matching an ordinary production environment) -- retrieve() must
    # behave exactly as Task 3.1.c/3.1.d already proved, no new isolation
    # re-proof needed, just confirming the default path is unchanged.
    patch_embed_dense(monkeypatch)
    set_real_qdrant_env(monkeypatch)
    async with live_qdrant_collection("retrieve_probe") as (client, name):
        await qdrant.ensure_collection(client, name)

        tenant_a = uuid.uuid4()
        await embed_and_upsert(
            extract_html(_page("Tenant A Doc", "zebra giraffe elephant safari wildlife")),
            client=client,
            collection_name=name,
            tenant_id=tenant_a,
            source_id=_SOURCE_ID,
            source_type="urls",
            document_id=uuid.uuid4(),
            source_url="https://tenant-a.example/page",
            file_name=None,
            embedding_version="v1",
        )

        results = await retrieve(tenant_a, "zebra giraffe safari", 8, collection_name=name)

        assert len(results) >= 1
        assert all(point.payload["client_id"] == str(tenant_a) for point in results)


async def test_retrieve_with_reranker_enabled_raises_not_implemented(monkeypatch):
    # Task 3.1.e: flipping the flag on must fail loud, not silently
    # no-op and return un-reranked results as if nothing were different.
    patch_embed_dense(monkeypatch)
    set_real_qdrant_env(monkeypatch)
    monkeypatch.setenv("RERANKER_ENABLED", "true")

    async with live_qdrant_collection("retrieve_probe") as (client, name):
        await qdrant.ensure_collection(client, name)

        with pytest.raises(NotImplementedError, match="reranker_enabled"):
            await retrieve(uuid.uuid4(), "zebra giraffe safari", 8, collection_name=name)


async def test_retrieve_uses_the_default_collection_name_when_not_overridden():
    # Confirms the collection_name keyword defaults to the real
    # qdrant.COLLECTION_NAME -- an ordinary production caller never needs
    # to pass it. Offline-safe: no live Qdrant call is actually reached
    # here, since this inspects the function's own default binding.
    import inspect

    default = inspect.signature(retrieve).parameters["collection_name"].default
    assert default == qdrant.COLLECTION_NAME


# --- chunk_from_scored_point() [SECURITY-adjacent] (Task 3.3.a's own
# retroactive fix) ------------------------------------------------------


def test_chunk_from_scored_point_converts_every_field():
    point = ScoredPoint(
        id=str(uuid.uuid4()),
        version=1,
        score=0.87,
        payload={
            "doc_id": "11111111-1111-1111-1111-111111111111",
            "text": "the retrieved text",
            "source_url": "https://tenant.example/page",
            "heading_path": ["Section", "Sub"],
        },
    )
    chunk = chunk_from_scored_point(point)
    assert chunk == {
        "document_id": "11111111-1111-1111-1111-111111111111",
        "text": "the retrieved text",
        "source_url": "https://tenant.example/page",
        "heading_path": ["Section", "Sub"],
        "score": 0.87,
    }


def test_chunk_from_scored_point_defaults_missing_optional_fields():
    # source_url is None (an upload/database-source chunk) and
    # heading_path absent entirely -- both real, valid payload shapes
    # (app/ingest/qdrant_writer.py's own build_payload() docstring).
    point = ScoredPoint(
        id=str(uuid.uuid4()),
        version=1,
        score=0.5,
        payload={
            "doc_id": "22222222-2222-2222-2222-222222222222",
            "text": "a database row's own text",
        },
    )
    chunk = chunk_from_scored_point(point)
    assert chunk["source_url"] is None
    assert chunk["heading_path"] == []


async def test_chunk_from_scored_point_converts_all_four_real_source_types(monkeypatch):
    # Senior-engineer-judgment follow-up: confirms chunk_from_scored_point()
    # never raises for ANY of the four real source types, against payloads
    # built by the real build_payload() (via embed_and_upsert(), never
    # hand-built) -- not just the synthetic payloads the two tests above
    # use. Real source_type strings and source_url/file_name combinations
    # confirmed by reading every real adapter directly before writing this
    # test: web_adapter.py/job_handlers.py ("urls"), crawl.py ("crawl"),
    # upload_adapter.py ("upload", _SOURCE_TYPE), database_adapter.py
    # ("database", _SOURCE_TYPE) -- all four route through this same
    # embed_and_upsert()/build_payload() pair, confirmed by grepping for
    # every real call site of build_payload() in app/: there is exactly
    # one, shared by every adapter via finish_ingest().
    patch_embed_dense(monkeypatch)
    set_real_qdrant_env(monkeypatch)
    async with live_qdrant_collection("retrieve_probe") as (client, name):
        await qdrant.ensure_collection(client, name)
        tenant_id = uuid.uuid4()

        await embed_and_upsert(
            extract_html(_page("URL Doc", "zebra urls content")),
            client=client,
            collection_name=name,
            tenant_id=tenant_id,
            source_id=uuid.uuid4(),
            source_type="urls",
            document_id=uuid.uuid4(),
            source_url="https://tenant.example/urls-page",
            file_name=None,
            embedding_version="v1",
        )
        await embed_and_upsert(
            extract_html(_page("Crawl Doc", "zebra crawl content")),
            client=client,
            collection_name=name,
            tenant_id=tenant_id,
            source_id=uuid.uuid4(),
            source_type="crawl",
            document_id=uuid.uuid4(),
            source_url="https://tenant.example/crawl-page",
            file_name=None,
            embedding_version="v1",
        )
        await embed_and_upsert(
            extract_html(_page("Upload Doc", "zebra upload content")),
            client=client,
            collection_name=name,
            tenant_id=tenant_id,
            source_id=uuid.uuid4(),
            source_type="upload",
            document_id=uuid.uuid4(),
            source_url=None,
            file_name="report.pdf",
            embedding_version="v1",
        )
        await embed_and_upsert(
            extract_html(_page("Database Doc", "zebra database content")),
            client=client,
            collection_name=name,
            tenant_id=tenant_id,
            source_id=uuid.uuid4(),
            source_type="database",
            document_id=uuid.uuid4(),
            source_url=None,
            file_name=None,
            embedding_version="v1",
        )

        results = await retrieve(tenant_id, "zebra", 10, collection_name=name)
        assert len(results) >= 4

        by_source_type = {}
        for point in results:
            chunk = chunk_from_scored_point(point)  # must not raise, for any of the four
            by_source_type[point.payload["source_type"]] = chunk

        assert set(by_source_type) == {"urls", "crawl", "upload", "database"}
        assert by_source_type["urls"]["source_url"] == "https://tenant.example/urls-page"
        assert by_source_type["crawl"]["source_url"] == "https://tenant.example/crawl-page"
        assert by_source_type["upload"]["source_url"] is None
        assert by_source_type["database"]["source_url"] is None
        for chunk in by_source_type.values():
            assert isinstance(chunk["document_id"], str) and chunk["document_id"]
            assert isinstance(chunk["text"], str) and chunk["text"]
            assert isinstance(chunk["heading_path"], list)
            assert isinstance(chunk["score"], float)
