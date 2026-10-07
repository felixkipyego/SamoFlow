# backend/tests/test_job_handlers.py
# Task 2.6.d: tests for app/ingest/job_handlers.py's handle_ingest_url()
# -- the `urls` adapter, the first real JOB_HANDLERS entry beyond
# noop/sleep test scaffolding, and the first real caller of the full
# ingestion pipeline THROUGH the actual worker loop: enqueue() -> real
# claim_next_job() -> dispatch -> handle_ingest_url() -> ingest_url()
# (2.6.c) per URL -> embed_and_upsert() (2.5.e) -> mark_job_succeeded().
#
# Live against BOTH real services together (live_test_services(),
# tests/conftest.py), run through app.worker.run() itself -- the SAME
# loop 2.1.d/e's own tests already proved claim/dispatch/mark and
# graceful-shutdown behavior against, not a separate code path. Unlike
# every other live-Qdrant test so far, handle_ingest_url() does not take
# a collection_name parameter -- it always uses the real, hardcoded
# app.qdrant.COLLECTION_NAME (matching a real deployment, which has
# exactly one shared collection) -- so these tests point QDRANT_URL/
# QDRANT_API_KEY (via set_valid_env(), matching test_qdrant.py's own
# established "point the real Settings-read client at test-qdrant"
# pattern) at the real test-qdrant instance, then call
# qdrant.ensure_collection() against COLLECTION_NAME themselves
# (idempotent -- safe even if another test already created it), rather
# than using live_test_services()'s own random per-test collection name.
import asyncio
import uuid

import sqlalchemy as sa

from app import qdrant
from app.ingest import safe_fetch as safe_fetch_module
from app.ingest.models import Document, Source
from app.ingest.repository import IngestRepository
from app.tenancy.models import Tenant
from app.worker import run
from tests.conftest import (
    VALID_ENV,
    _fetch_job,
    db_session,
    fake_is_unsafe_except_loopback,
    local_http_server,
    patch_embed_dense,
    require_test_qdrant,
    scripted_handler,
    set_valid_env,
)

_LOOPBACK_HOST = "127.0.0.1"

_BODY_A = (
    "<html><head><title>Page A</title></head><body>"
    "<h2>Section</h2><p>" + " ".join(f"alpha{i}" for i in range(60)) + "</p></body></html>"
)
_BODY_B = (
    "<html><head><title>Page B</title></head><body>"
    "<h2>Section</h2><p>" + " ".join(f"beta{i}" for i in range(60)) + "</p></body></html>"
)


async def _prepare_env(monkeypatch, live_test_services) -> tuple:
    # Must be ONE set_valid_env() call carrying every override together:
    # it re-applies the full VALID_ENV dict plus only the overrides THIS
    # call names, so a second, separate call (e.g. just QDRANT_URL/
    # QDRANT_API_KEY) would silently reset DATABASE_URL back to VALID_
    # ENV's own fake placeholder -- confirmed by reading set_valid_env()
    # directly before relying on it twice.
    database_url, client, _unused_collection_name = live_test_services
    qdrant_url, qdrant_key = require_test_qdrant()
    set_valid_env(
        monkeypatch,
        VALID_ENV,
        DATABASE_URL=database_url,
        QDRANT_URL=qdrant_url,
        QDRANT_API_KEY=qdrant_key,
    )
    qdrant.get_qdrant_client.cache_clear()
    monkeypatch.setattr(
        safe_fetch_module, "is_unsafe_destination_ip", fake_is_unsafe_except_loopback
    )
    patch_embed_dense(monkeypatch)
    await qdrant.ensure_collection(client, qdrant.COLLECTION_NAME)
    return client


async def _seed_tenant_source_and_verified_domain(urls: list[str]) -> dict:
    tenant_id = uuid.uuid4()
    async with db_session() as session:
        session.add(Tenant(id=tenant_id, name="Urls Adapter Proof Tenant", status="active"))
        await session.commit()

    async with db_session() as session:
        source = Source(
            tenant_id=tenant_id,
            type="urls",
            config={"urls": urls},
            refresh_interval="daily",
            status="active",
        )
        session.add(source)
        await session.commit()
        source_id = source.id

    async with db_session() as session:
        repo = IngestRepository(tenant_id=tenant_id, session=session)
        verified_domain = await repo.claim_domain(_LOOPBACK_HOST)
        await session.commit()
        domain_id = verified_domain.id

    # Raw UPDATE straight to "verified", matching test_web_adapter.py's
    # own established precedent for this exact need -- DNS verification
    # itself is not under test here.
    async with db_session() as session:
        await session.execute(
            sa.text(
                "UPDATE verified_domains SET status = 'verified', verified_at = now() "
                "WHERE id = :id"
            ),
            {"id": domain_id},
        )
        await session.commit()

    async with db_session() as session:
        repo = IngestRepository(tenant_id=tenant_id, session=session)
        job = await repo.enqueue(job_type="ingest_url", source_id=source_id, payload={})
        await session.commit()
        job_id = job.id

    return {"tenant_id": tenant_id, "source_id": source_id, "job_id": job_id}


async def test_a_multi_url_job_is_claimed_processed_and_succeeds_through_the_real_loop(
    monkeypatch, live_test_services
):
    # (a)/(c): proves the FULL real path -- enqueue() -> the real worker
    # run() loop (the SAME function 2.1.d/e's own tests exercise, not
    # called in isolation here either) -> claim_next_job() -> dispatch ->
    # handle_ingest_url() -> ingest_url() per URL -> embed_and_upsert() ->
    # mark_job_succeeded().
    client = await _prepare_env(monkeypatch, live_test_services)

    with local_http_server(
        scripted_handler({"/a": (200, _BODY_A.encode()), "/b": (200, _BODY_B.encode())})
    ) as port:
        url_a = f"http://{_LOOPBACK_HOST}:{port}/a"
        url_b = f"http://{_LOOPBACK_HOST}:{port}/b"
        ids = await _seed_tenant_source_and_verified_domain([url_a, url_b])

        await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=10)

    job = await _fetch_job(ids["job_id"])
    assert job.status == "succeeded"
    assert job.attempts == 0
    assert job.error is None

    async with db_session() as session:
        documents = (
            await session.execute(
                sa.select(Document).where(Document.source_id == ids["source_id"])
            )
        ).scalars().all()
    assert {doc.url for doc in documents} == {url_a, url_b}
    assert all(doc.status == "extracted" for doc in documents)

    records, _ = await client.scroll(
        collection_name=qdrant.COLLECTION_NAME,
        scroll_filter=qdrant.tenant_filter(ids["tenant_id"]),
        limit=10,
    )
    assert len(records) >= 2
    assert {record.payload["source_url"] for record in records} == {url_a, url_b}


async def test_one_unverified_url_is_skipped_while_valid_urls_in_the_same_job_still_ingest(
    monkeypatch, live_test_services
):
    # (b): the partial-failure/job-success policy -- a job whose list
    # mixes a URL for an unverified domain with valid ones still succeeds
    # overall (every URL got a recorded outcome), the valid URLs are
    # really ingested (real Qdrant points, real documents rows), and the
    # invalid one leaves no document row at all.
    client = await _prepare_env(monkeypatch, live_test_services)

    with local_http_server(
        scripted_handler({"/a": (200, _BODY_A.encode()), "/b": (200, _BODY_B.encode())})
    ) as port:
        url_a = f"http://{_LOOPBACK_HOST}:{port}/a"
        url_b = f"http://{_LOOPBACK_HOST}:{port}/b"
        # Never actually fetched -- ingest_url()'s own domain-verification
        # check (2.6.c) rejects it before any network activity, so this
        # host does not need to exist or resolve at all.
        unverified_url = "http://unverified-for-this-tenant.invalid/page"
        ids = await _seed_tenant_source_and_verified_domain([url_a, unverified_url, url_b])

        await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=10)

    job = await _fetch_job(ids["job_id"])
    assert job.status == "succeeded"
    assert job.attempts == 0
    assert job.error is None

    async with db_session() as session:
        documents = (
            await session.execute(
                sa.select(Document).where(Document.source_id == ids["source_id"])
            )
        ).scalars().all()
    # Exactly the two valid URLs persisted -- the unverified one left no
    # row at all, not a row with some "failed" status.
    assert {doc.url for doc in documents} == {url_a, url_b}

    records, _ = await client.scroll(
        collection_name=qdrant.COLLECTION_NAME,
        scroll_filter=qdrant.tenant_filter(ids["tenant_id"]),
        limit=10,
    )
    assert len(records) >= 2
    assert {record.payload["source_url"] for record in records} == {url_a, url_b}
