# backend/tests/test_reconcile.py
# Task 2.9.f [SECURITY]: tests for app/ingest/reconcile.py's own two real
# primitives, find_orphaned_points() and reconcile_tenant() -- tested
# DIRECTLY here (matching test_scheduler.py's/test_database_adapter.py's
# own "test the primitive, not only through the handler" precedent), live
# against BOTH real services together: the real `documents` rows
# find_orphaned_points() compares against, and the real Qdrant points it
# scrolls. handle_reconcile()'s own self-re-enqueuing shape -- the real
# JOB_HANDLERS entry wiring this primitive into the worker loop -- is
# tested separately, through the REAL worker.run() loop, in
# test_job_handlers.py (matching every other job-handler task's own proof
# requirement: not called in isolation).
#
# Algorithm under test, decided with the user directly before building
# (see app/ingest/reconcile.py's own header comment for the full
# reasoning): NOT build_point_id()-based prediction (needs a chunk count
# nothing in Postgres stores) -- instead, scroll a tenant's own Qdrant
# points and ask whether Postgres justifies each one: does a matching
# `Document` row exist at all, and if so, does the point's own
# `content_hash` payload match that row's CURRENT value.
import uuid

import sqlalchemy as sa

from app import qdrant
from app.ingest.extract_html import extract_html
from app.ingest.models import Document, Source
from app.ingest.qdrant_writer import embed_and_upsert, finish_ingest
from app.ingest.reconcile import find_orphaned_points, reconcile_tenant
from app.ingest.repository import IngestRepository
from app.tenancy.models import Tenant
from tests.conftest import db_session, patch_embed_dense

_SOURCE_URL = "https://reconcile-proof.example/page"


def _html_with_sections(*section_bodies: str) -> str:
    # One <h2> section per body string -- chunking.py never merges text
    # across a heading boundary, so each short section becomes exactly
    # one chunk (confirmed by this module's own design, Task 2.4.c; this
    # is the SAME construction test_qdrant_writer.py's own 2.5.d
    # reproduction already used, not reimplemented here since it is a
    # plain, local, pure function with no state to share across files).
    sections = "\n".join(
        f"<h2>Section {i}</h2><p>{body}</p>" for i, body in enumerate(section_bodies, start=1)
    )
    return f"<html><head><title>Doc</title></head><body>{sections}</body></html>"


async def _create_tenant_and_source(name: str) -> dict:
    tenant_id = uuid.uuid4()
    async with db_session() as session:
        session.add(Tenant(id=tenant_id, name=name, status="active"))
        await session.flush()  # the FK create_source() needs must exist first
        repo = IngestRepository(tenant_id=tenant_id, session=session)
        source = await repo.create_source(
            source_type="urls", config={"urls": [_SOURCE_URL]}, refresh_interval="daily"
        )
        await session.commit()
        return {"tenant_id": tenant_id, "source_id": source.id}


async def _ingest(client, collection_name, *, tenant_id, source_id, html) -> uuid.UUID:
    # Deliberately finish_ingest() (Task 2.5.e), not the lower-level
    # embed_and_upsert() test_qdrant_writer.py's own 2.5.d reproduction
    # uses: THIS module's own algorithm compares against
    # `documents.content_hash`, so the test needs a REAL `Document` row
    # too, which only finish_ingest() writes. Re-calling with the SAME
    # `_SOURCE_URL` finds and updates the SAME row each time (finish_
    # ingest()'s own existing-row lookup is keyed on source_id+url, not
    # document_id), exactly matching a real re-crawl of the same page.
    content = extract_html(html)
    async with db_session() as session:
        _, document_id = await finish_ingest(
            session,
            client,
            collection_name,
            content,
            tenant_id=tenant_id,
            source_id=source_id,
            source_type="urls",
            url=_SOURCE_URL,
            file_name=None,
            embedding_version="v1",
        )
        await session.commit()
    return document_id


async def test_reconcile_removes_exactly_the_orphans_left_by_a_content_shrink(
    live_test_services, monkeypatch
):
    patch_embed_dense(monkeypatch)
    _, client, collection_name = live_test_services
    await qdrant.ensure_collection(client, collection_name)
    ids = await _create_tenant_and_source("Reconcile Shrink Proof Tenant")

    # First run: 5 sections -> 5 chunks/points, matching test_qdrant_
    # writer.py's own already-proven 2.5.d reproduction shape exactly.
    document_id = await _ingest(
        client, collection_name, tenant_id=ids["tenant_id"], source_id=ids["source_id"],
        html=_html_with_sections("one", "two", "three", "four", "five"),
    )
    count_after_first = await client.count(
        collection_name=collection_name,
        count_filter=qdrant.tenant_filter(ids["tenant_id"]),
    )
    assert count_after_first.count == 5

    # Second run, same URL, SHRUNK to 3 sections -- the gap this task
    # resolves: confirmed live (proving the gap exists before the fix,
    # the same way 2.9.d proved staleness existed before its own
    # cleanup), the collection still holds all 5 points, not 3.
    document_id_again = await _ingest(
        client, collection_name, tenant_id=ids["tenant_id"], source_id=ids["source_id"],
        html=_html_with_sections("one (updated)", "two (updated)", "three (updated)"),
    )
    assert document_id_again == document_id  # same row, updated in place, not a new one
    count_before_reconcile = await client.count(
        collection_name=collection_name,
        count_filter=qdrant.tenant_filter(ids["tenant_id"]),
    )
    assert count_before_reconcile.count == 5  # the gap, proven live before the fix runs

    async with db_session() as session:
        deleted = await reconcile_tenant(
            session, client, collection_name, tenant_id=ids["tenant_id"]
        )
    assert deleted == 2  # exactly the 2 stale, superseded-content points

    count_after_reconcile = await client.count(
        collection_name=collection_name,
        count_filter=qdrant.tenant_filter(ids["tenant_id"]),
    )
    assert count_after_reconcile.count == 3  # exactly the 3 current points, nothing else gone

    records, _ = await client.scroll(
        collection_name=collection_name,
        scroll_filter=qdrant.tenant_filter(ids["tenant_id"]),
        limit=20,
    )
    async with db_session() as session:
        current_hash = (
            await session.execute(
                sa.select(Document.content_hash).where(Document.id == document_id)
            )
        ).scalar_one()
    assert all(record.payload["content_hash"] == current_hash for record in records)


async def test_reconcile_leaves_a_hidden_disabled_sources_points_alone(
    live_test_services, monkeypatch
):
    patch_embed_dense(monkeypatch)
    _, client, collection_name = live_test_services
    await qdrant.ensure_collection(client, collection_name)
    ids = await _create_tenant_and_source("Reconcile Hidden-Source Proof Tenant")

    await _ingest(
        client, collection_name, tenant_id=ids["tenant_id"], source_id=ids["source_id"],
        html=_html_with_sections("alpha", "beta"),
    )
    count_before = await client.count(
        collection_name=collection_name, count_filter=qdrant.tenant_filter(ids["tenant_id"])
    )
    assert count_before.count == 2

    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_id"], session=session)
        await repo.disable_source(
            ids["source_id"], client=client, collection_name=collection_name
        )
        await session.commit()

    async with db_session() as session:
        deleted = await reconcile_tenant(
            session, client, collection_name, tenant_id=ids["tenant_id"]
        )
    # Hidden, not orphaned: still justified by a real, unchanged Document
    # row with a matching content_hash -- "orphaned vs. legitimately
    # absent" and "hidden vs. visible" are different questions, and this
    # pass only ever answers the first one.
    assert deleted == 0

    count_after = await client.count(
        collection_name=collection_name, count_filter=qdrant.tenant_filter(ids["tenant_id"])
    )
    assert count_after.count == 2


async def test_reconcile_catches_a_stray_point_with_no_matching_document_at_all(
    live_test_services, monkeypatch
):
    # Proves generality beyond the content-shrink scenario: an orphan
    # from an entirely different, unrelated cause (no Document row ever
    # existed for this document_id -- simulating some other future bug,
    # not content-shrink specifically) is also caught.
    patch_embed_dense(monkeypatch)
    _, client, collection_name = live_test_services
    await qdrant.ensure_collection(client, collection_name)
    ids = await _create_tenant_and_source("Reconcile Stray-Point Proof Tenant")

    stray_document_id = uuid.uuid4()  # deliberately never given a real Document row
    content = extract_html(_html_with_sections("a stray, unrelated point"))
    await embed_and_upsert(
        content,
        client=client,
        collection_name=collection_name,
        tenant_id=ids["tenant_id"],
        source_id=ids["source_id"],
        source_type="urls",
        document_id=stray_document_id,
        source_url="https://reconcile-proof.example/stray",
        file_name=None,
        embedding_version="v1",
    )
    count_before = await client.count(
        collection_name=collection_name, count_filter=qdrant.tenant_filter(ids["tenant_id"])
    )
    assert count_before.count == 1

    async with db_session() as session:
        orphans = await find_orphaned_points(
            session, client, collection_name, tenant_id=ids["tenant_id"]
        )
    assert len(orphans) == 1
    assert orphans[0][1] == stray_document_id

    async with db_session() as session:
        deleted = await reconcile_tenant(
            session, client, collection_name, tenant_id=ids["tenant_id"]
        )
    assert deleted == 1

    count_after = await client.count(
        collection_name=collection_name, count_filter=qdrant.tenant_filter(ids["tenant_id"])
    )
    assert count_after.count == 0


async def test_reconcile_never_touches_a_different_tenants_legitimate_points(
    live_test_services, monkeypatch
):
    patch_embed_dense(monkeypatch)
    _, client, collection_name = live_test_services
    await qdrant.ensure_collection(client, collection_name)
    ids_a = await _create_tenant_and_source("Reconcile Isolation Proof Tenant A")
    ids_b = await _create_tenant_and_source("Reconcile Isolation Proof Tenant B")

    # Tenant A: a genuine content-shrink orphan, the thing THIS reconcile
    # call should remove.
    await _ingest(
        client, collection_name, tenant_id=ids_a["tenant_id"], source_id=ids_a["source_id"],
        html=_html_with_sections("one", "two", "three"),
    )
    await _ingest(
        client, collection_name, tenant_id=ids_a["tenant_id"], source_id=ids_a["source_id"],
        html=_html_with_sections("one (updated)"),
    )

    # Tenant B: its own real, CURRENT, still-legitimate point -- never
    # touched by tenant A's own reconcile call below.
    await _ingest(
        client, collection_name, tenant_id=ids_b["tenant_id"], source_id=ids_b["source_id"],
        html=_html_with_sections("tenant b's own untouched content"),
    )
    count_b_before = await client.count(
        collection_name=collection_name, count_filter=qdrant.tenant_filter(ids_b["tenant_id"])
    )
    assert count_b_before.count == 1

    async with db_session() as session:
        deleted = await reconcile_tenant(
            session, client, collection_name, tenant_id=ids_a["tenant_id"]
        )
    assert deleted == 2  # tenant A's own 2 orphaned points, nothing from tenant B

    count_a_after = await client.count(
        collection_name=collection_name, count_filter=qdrant.tenant_filter(ids_a["tenant_id"])
    )
    assert count_a_after.count == 1  # tenant A's own single current point survives

    count_b_after = await client.count(
        collection_name=collection_name, count_filter=qdrant.tenant_filter(ids_b["tenant_id"])
    )
    assert count_b_after.count == 1  # tenant B's own point: byte-for-byte untouched

    async with db_session() as session:
        source_b_row = (
            await session.execute(sa.select(Source).where(Source.id == ids_b["source_id"]))
        ).scalar_one()
    assert source_b_row.enabled is True
