# backend/tests/test_job_handlers.py
# Task 2.6.d: tests for app/ingest/job_handlers.py's handle_ingest_url()
# -- the `urls` adapter, the first real JOB_HANDLERS entry beyond
# noop/sleep test scaffolding, and the first real caller of the full
# ingestion pipeline THROUGH the actual worker loop: enqueue() -> real
# claim_next_job() -> dispatch -> handle_ingest_url() -> ingest_url()
# (2.6.c) per URL -> embed_and_upsert() (2.5.e) -> mark_job_succeeded().
#
# Task 2.6.e part 2 extends this file with handle_ingest_crawl()'s own
# end-to-end tests -- the `crawl` adapter, run through the SAME real
# worker.py loop, matching handle_ingest_url()'s own established
# methodology exactly (test (g)'s own requirement: not a separate code
# path, not called in isolation).
#
# Live against BOTH real services together (live_test_services(),
# tests/conftest.py), run through app.worker.run() itself -- the SAME
# loop 2.1.d/e's own tests already proved claim/dispatch/mark and
# graceful-shutdown behavior against, not a separate code path. Unlike
# every other live-Qdrant test so far, handle_ingest_url()/handle_ingest_
# crawl() do not take a collection_name parameter -- they always use the
# real, hardcoded app.qdrant.COLLECTION_NAME (matching a real deployment,
# which has exactly one shared collection) -- so these tests point
# QDRANT_URL/QDRANT_API_KEY (via set_valid_env(), matching test_qdrant.py's
# own established "point the real Settings-read client at test-qdrant"
# pattern) at the real test-qdrant instance, then call
# qdrant.ensure_collection() against COLLECTION_NAME themselves
# (idempotent -- safe even if another test already created it), rather
# than using live_test_services()'s own random per-test collection name.
import asyncio
import decimal
import http.server
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import sqlalchemy as sa

from app import qdrant
from app import worker as worker_module
from app.ingest import database_adapter as database_adapter_module
from app.ingest import safe_fetch as safe_fetch_module
from app.ingest import upload_storage
from app.ingest.models import Document, Job, Source
from app.ingest.repository import IngestRepository
from app.tenancy.models import Tenant
from app.worker import check_heartbeat_fresh, run
from tests.conftest import (
    VALID_ENV,
    _fetch_job,
    db_session,
    fake_is_unsafe_except_loopback,
    local_http_server,
    local_ipv6_http_server,
    patch_embed_dense,
    read_docx_fixture,
    read_markdown_fixture,
    read_pdf_fixture,
    read_text_fixture,
    require_test_database,
    require_test_qdrant,
    scripted_handler,
    set_valid_env,
)

# Task 2.8.e: reuses test_database_adapter.py's own _admin_connection() --
# the shared shape the duplication check after 2.8.a/b/c already
# extracted/consolidated there -- rather than a third, separate copy of
# "parse TEST_DATABASE_URL and connect as the admin role" here.
from tests.test_database_adapter import _admin_connection

# Task 2.7.d: reuses test_upload_adapter.py's own real corrupt-docx
# construction instead of duplicating it -- matching this project's own
# established cross-test-file-import precedent (test_env_consistency.py
# importing from test_env_example.py; test_qdrant.py from test_qdrant_
# collection.py; the duplication check after 2.7.a/b/c's own A2).
from tests.test_upload_adapter import _docx_with_invalid_xml_content

_LOOPBACK_HOST = "127.0.0.1"

_BODY_A = (
    "<html><head><title>Page A</title></head><body>"
    "<h2>Section</h2><p>" + " ".join(f"alpha{i}" for i in range(60)) + "</p></body></html>"
)
_BODY_B = (
    "<html><head><title>Page B</title></head><body>"
    "<h2>Section</h2><p>" + " ".join(f"beta{i}" for i in range(60)) + "</p></body></html>"
)


async def _fetch_source(source_id: uuid.UUID) -> Source:
    # Task 2.9.a: matches _fetch_job()'s own identical one-line shape
    # (tests/conftest.py) -- re-reads a Source row fresh from the real
    # database after a handler has run, needed by this task's own
    # last_run_at assertions (a value set inside the handler's own
    # session/transaction, not visible on any Source object a test might
    # still be holding from before the job ran).
    async with db_session() as session:
        return (await session.execute(sa.select(Source).where(Source.id == source_id))).scalar_one()


async def _prepare_env(monkeypatch, live_test_services, **extra_overrides) -> tuple:
    # Must be ONE set_valid_env() call carrying every override together:
    # it re-applies the full VALID_ENV dict plus only the overrides THIS
    # call names, so a second, separate call (e.g. just QDRANT_URL/
    # QDRANT_API_KEY) would silently reset DATABASE_URL back to VALID_
    # ENV's own fake placeholder -- confirmed by reading set_valid_env()
    # directly before relying on it twice. `**extra_overrides` (Task
    # 2.7.d): lets handle_ingest_upload()'s own tests add UPLOAD_STORAGE_
    # PATH into the SAME single call, for the identical reason -- a
    # second, separate set_valid_env() call just for that one variable
    # would reset DATABASE_URL/QDRANT_URL/QDRANT_API_KEY right back to
    # VALID_ENV's own fake placeholders.
    database_url, client, _unused_collection_name = live_test_services
    qdrant_url, qdrant_key = require_test_qdrant()
    set_valid_env(
        monkeypatch,
        VALID_ENV,
        DATABASE_URL=database_url,
        QDRANT_URL=qdrant_url,
        QDRANT_API_KEY=qdrant_key,
        **extra_overrides,
    )
    qdrant.get_qdrant_client.cache_clear()
    monkeypatch.setattr(
        safe_fetch_module, "is_unsafe_destination_ip", fake_is_unsafe_except_loopback
    )
    patch_embed_dense(monkeypatch)
    await qdrant.ensure_collection(client, qdrant.COLLECTION_NAME)
    return client


# Duplication check after 2.8.d/e/f: this file's own four `_seed_tenant_*`
# helpers (urls/2.6.d, crawl/2.6.e, upload/2.7.d, db/2.8.e) each repeated a
# byte-identical "create Tenant -> commit" block and a byte-identical
# "create Source -> commit -> enqueue -> commit -> return {tenant_id,
# source_id, job_id}" tail, differing only in the `type`/`config`/
# `job_type` values passed in -- past this project's own established
# 3rd-occurrence extraction threshold (the 4th occurrence, added by
# 2.8.e's own `_seed_tenant_db_source`, is what crossed it). Extracted into
# two small, separately-callable pieces rather than one combined helper,
# since each adapter's own distinct middle step (a domain claim+verify;
# `upload_storage.save()`; `create_db_connection()`) sits at a genuinely
# different point relative to tenant/source creation for each adapter --
# a single combined helper would need a hook/callback parameter to thread
# that step through, which is more machinery than two plain, sequentially-
# called functions need. Each of the four wrappers below keeps its own
# exact middle step, unchanged in substance; only the surrounding
# boilerplate moved.
async def _create_tenant(name: str) -> uuid.UUID:
    tenant_id = uuid.uuid4()
    async with db_session() as session:
        session.add(Tenant(id=tenant_id, name=name, status="active"))
        await session.commit()
    return tenant_id


async def _create_source_and_enqueue(
    tenant_id: uuid.UUID, source_type: str, config: dict, job_type: str
) -> dict:
    async with db_session() as session:
        source = Source(
            tenant_id=tenant_id,
            type=source_type,
            config=config,
            refresh_interval="daily",
            status="active",
        )
        session.add(source)
        await session.commit()
        source_id = source.id

    async with db_session() as session:
        repo = IngestRepository(tenant_id=tenant_id, session=session)
        job = await repo.enqueue(job_type=job_type, source_id=source_id, payload={})
        await session.commit()
        job_id = job.id

    return {"tenant_id": tenant_id, "source_id": source_id, "job_id": job_id}


async def _seed_tenant_source_and_verified_domain(urls: list[str]) -> dict:
    tenant_id = await _create_tenant("Urls Adapter Proof Tenant")

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

    return await _create_source_and_enqueue(tenant_id, "urls", {"urls": urls}, "ingest_url")


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

    # Task 2.9.a: last_run_at set once the handler returns normally --
    # the basic "does this one line fire at all" proof; the more subtle
    # "still fires despite a per-item failure" case is proven separately
    # below (test_one_unverified_url_is_skipped_...) and for a genuinely
    # different handler shape (test_a_mixed_outcome_db_sync_job_...).
    source_row = await _fetch_source(ids["source_id"])
    assert source_row.last_run_at is not None


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

    # Task 2.9.a: the subtler, explicitly-named case -- a source whose own
    # job had a real per-item failure (the unverified URL) still genuinely
    # RAN, so last_run_at is set exactly the same as a clean, all-succeeded
    # run (test_a_multi_url_job_... above) -- "attempted, not guaranteed"
    # applies to last_run_at too, not only to the job's own final status.
    source_row = await _fetch_source(ids["source_id"])
    assert source_row.last_run_at is not None


# --- Task 2.6.e part 2: handle_ingest_crawl() -----------------------------
# Same live-against-both-real-services methodology as handle_ingest_url()
# above, run through the SAME real worker.py loop (test (g)'s own
# requirement -- every test below uses run(), never calls handle_ingest_
# crawl() directly).


def _rich_page(title: str, word_prefix: str, extra_html: str = "") -> str:
    words = " ".join(f"{word_prefix}{i}" for i in range(60))
    return (
        f"<html><head><title>{title}</title></head><body><h2>S</h2>"
        f"<p>{words}</p>{extra_html}</body></html>"
    )


def _tracking_handler(routes: dict, hits: list, delay_seconds: float = 0):
    # A local variant of scripted_handler() (tests/conftest.py) that also
    # records every requested path -- needed by tests (c)/(e) below to
    # prove a specific path was NEVER requested, not merely absent from
    # the final result. `delay_seconds` (duplication check after
    # 2.6.d/2.6.e/2.6.f: collapsed from a separate _slow_tracking_handler(),
    # which was this function plus one time.sleep() call) gives test (f)'s
    # own multi-page crawl real, non-trivial elapsed time to prove the
    # heartbeat is written WHILE busy, not just once at job start/end.
    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            if delay_seconds:
                time.sleep(delay_seconds)
            status, body = routes.get(self.path, (404, b"not found"))
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return _Handler


async def _seed_tenant_crawl_source_and_verified_domain(seed_url: str) -> dict:
    tenant_id = await _create_tenant("Crawl Adapter Proof Tenant")

    async with db_session() as session:
        repo = IngestRepository(tenant_id=tenant_id, session=session)
        verified_domain = await repo.claim_domain(_LOOPBACK_HOST)
        await session.commit()
        domain_id = verified_domain.id

    async with db_session() as session:
        await session.execute(
            sa.text(
                "UPDATE verified_domains SET status = 'verified', verified_at = now() "
                "WHERE id = :id"
            ),
            {"id": domain_id},
        )
        await session.commit()

    return await _create_source_and_enqueue(
        tenant_id, "crawl", {"seed_url": seed_url}, "ingest_crawl"
    )


async def _documents_for(source_id: uuid.UUID) -> list[Document]:
    async with db_session() as session:
        return (
            (await session.execute(sa.select(Document).where(Document.source_id == source_id)))
            .scalars()
            .all()
        )


async def test_full_crawl_via_sitemap_ingests_every_page(monkeypatch, live_test_services):
    # (a): sitemap present, several real pages, all correctly ingested,
    # real Qdrant points queryable.
    client = await _prepare_env(monkeypatch, live_test_services)
    routes: dict = {}
    with local_http_server(_tracking_handler(routes, [])) as port:
        seed = f"http://{_LOOPBACK_HOST}:{port}"
        page1 = f"{seed}/page1"
        page2 = f"{seed}/page2"
        sitemap_xml = (
            '<?xml version="1.0"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<url><loc>{seed}/</loc></url>"
            f"<url><loc>{page1}</loc></url>"
            f"<url><loc>{page2}</loc></url>"
            "</urlset>"
        ).encode()
        routes["/"] = (200, _rich_page("Home", "home").encode())
        routes["/page1"] = (200, _rich_page("Page1", "one").encode())
        routes["/page2"] = (200, _rich_page("Page2", "two").encode())
        routes["/robots.txt"] = (200, f"Sitemap: {seed}/sitemap.xml\n".encode())
        routes["/sitemap.xml"] = (200, sitemap_xml)

        ids = await _seed_tenant_crawl_source_and_verified_domain(seed)
        await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=15)

    job = await _fetch_job(ids["job_id"])
    assert job.status == "succeeded"
    assert job.error is None

    documents = await _documents_for(ids["source_id"])
    assert {doc.url for doc in documents} == {seed, page1, page2}

    records, _ = await client.scroll(
        collection_name=qdrant.COLLECTION_NAME,
        scroll_filter=qdrant.tenant_filter(ids["tenant_id"]),
        limit=10,
    )
    assert len(records) >= 3
    assert {record.payload["source_url"] for record in records} == {seed, page1, page2}


async def test_crawl_with_no_sitemap_falls_back_to_real_link_following(
    monkeypatch, live_test_services
):
    # (b): no sitemap anywhere -- BFS discovers pages via real <a href>
    # tags on real served pages, including a back-link (proves dedup via
    # the visited set, not just BFS order).
    client = await _prepare_env(monkeypatch, live_test_services)
    routes: dict = {}
    with local_http_server(_tracking_handler(routes, [])) as port:
        seed = f"http://{_LOOPBACK_HOST}:{port}"
        child1 = f"{seed}/child1"
        child2 = f"{seed}/child2"
        routes["/"] = (
            200,
            _rich_page("Home", "home", '<a href="/child1">c1</a><a href="/child2">c2</a>').encode(),
        )
        routes["/child1"] = (
            200,
            # Links back to the seed and to child2 -- both already
            # visited by the time this page is reached in BFS order.
            _rich_page("Child1", "one", '<a href="/">home</a><a href="/child2">c2</a>').encode(),
        )
        routes["/child2"] = (200, _rich_page("Child2", "two").encode())
        # No "/robots.txt" and no "/sitemap.xml" routes -- both 404,
        # confirmed (part 1) to mean "crawl allowed, no sitemap."

        ids = await _seed_tenant_crawl_source_and_verified_domain(seed)
        await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=15)

    job = await _fetch_job(ids["job_id"])
    assert job.status == "succeeded"

    documents = await _documents_for(ids["source_id"])
    assert {doc.url for doc in documents} == {seed, child1, child2}

    records, _ = await client.scroll(
        collection_name=qdrant.COLLECTION_NAME,
        scroll_filter=qdrant.tenant_filter(ids["tenant_id"]),
        limit=10,
    )
    assert len(records) >= 3


async def test_robots_disallowed_path_is_never_fetched_during_a_crawl(
    monkeypatch, live_test_services
):
    # (c): proven DIRECTLY via a hit-tracking handler, not merely by the
    # disallowed page's absence from the final result.
    client = await _prepare_env(monkeypatch, live_test_services)
    routes: dict = {}
    hits: list[str] = []
    with local_http_server(_tracking_handler(routes, hits)) as port:
        seed = f"http://{_LOOPBACK_HOST}:{port}"
        allowed = f"{seed}/allowed"
        disallowed = f"{seed}/disallowed"
        routes["/"] = (
            200,
            _rich_page(
                "Home", "home", '<a href="/allowed">a</a><a href="/disallowed">d</a>'
            ).encode(),
        )
        routes["/allowed"] = (200, _rich_page("Allowed", "ok").encode())
        routes["/disallowed"] = (200, _rich_page("Disallowed", "bad").encode())
        routes["/robots.txt"] = (200, b"User-agent: *\nDisallow: /disallowed\n")

        ids = await _seed_tenant_crawl_source_and_verified_domain(seed)
        await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=15)

    assert "/disallowed" not in hits  # never requested at all, not just absent from the result

    documents = await _documents_for(ids["source_id"])
    assert {doc.url for doc in documents} == {seed, allowed}

    records, _ = await client.scroll(
        collection_name=qdrant.COLLECTION_NAME,
        scroll_filter=qdrant.tenant_filter(ids["tenant_id"]),
        limit=10,
    )
    assert all(record.payload["source_url"] != disallowed for record in records)


async def test_the_per_crawl_page_cap_stops_discovery_at_the_right_point(
    monkeypatch, live_test_services
):
    # (d): a server offering 8 linked pages, a cap of 3 -- confirms the
    # CAP, not an arbitrary number, is what stopped discovery.
    client = await _prepare_env(monkeypatch, live_test_services)
    monkeypatch.setenv("CRAWL_PAGE_CAP", "3")
    routes: dict = {}
    with local_http_server(_tracking_handler(routes, [])) as port:
        seed = f"http://{_LOOPBACK_HOST}:{port}"
        children = [f"{seed}/p{i}" for i in range(8)]
        links_html = "".join(f'<a href="/p{i}">p{i}</a>' for i in range(8))
        routes["/"] = (200, _rich_page("Home", "home", links_html).encode())
        for i in range(8):
            routes[f"/p{i}"] = (200, _rich_page(f"Page{i}", f"w{i}").encode())

        ids = await _seed_tenant_crawl_source_and_verified_domain(seed)
        await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=15)

    job = await _fetch_job(ids["job_id"])
    assert job.status == "succeeded"

    documents = await _documents_for(ids["source_id"])
    # Exactly 3 (the cap): the seed plus the first 2 children in link
    # order -- not 1 + len(children) == 9, which an unenforced cap would
    # have produced given 8 real, reachable pages were on offer.
    assert len(documents) == 3
    assert seed in {doc.url for doc in documents}
    assert {doc.url for doc in documents} <= {seed, *children}

    records, _ = await client.scroll(
        collection_name=qdrant.COLLECTION_NAME,
        scroll_filter=qdrant.tenant_filter(ids["tenant_id"]),
        limit=20,
    )
    assert len(records) < 8  # proves the cap, not a coincidence of content size


async def test_an_out_of_scope_domain_link_is_never_fetched_mid_crawl(
    monkeypatch, live_test_services
):
    # (e): ties 2.3's own SSRF discipline and 2.6.c's own unverified-
    # domain rejection together, exercised through the real crawl path.
    # The "attacker" target is a SECOND real, listening server (IPv6
    # loopback, a genuinely different host from the seed's IPv4 one) --
    # proving "never fetched" via that server's own zero hit count, not
    # merely the link's absence from the final result.
    client = await _prepare_env(monkeypatch, live_test_services)
    evil_hits: list[str] = []
    with local_ipv6_http_server(
        _tracking_handler({"/evil": (200, b"should never be served")}, evil_hits)
    ) as evil_port:
        routes: dict = {}
        with local_http_server(_tracking_handler(routes, [])) as port:
            seed = f"http://{_LOOPBACK_HOST}:{port}"
            child = f"{seed}/child"
            evil_url = f"http://[::1]:{evil_port}/evil"
            routes["/"] = (
                200,
                _rich_page(
                    "Home", "home", f'<a href="/child">c</a><a href="{evil_url}">evil</a>'
                ).encode(),
            )
            routes["/child"] = (200, _rich_page("Child", "ok").encode())

            ids = await _seed_tenant_crawl_source_and_verified_domain(seed)
            await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=15)

    assert evil_hits == []  # the out-of-scope host never received a single request

    job = await _fetch_job(ids["job_id"])
    assert job.status == "succeeded"
    documents = await _documents_for(ids["source_id"])
    assert {doc.url for doc in documents} == {seed, child}

    records, _ = await client.scroll(
        collection_name=qdrant.COLLECTION_NAME,
        scroll_filter=qdrant.tenant_filter(ids["tenant_id"]),
        limit=10,
    )
    # No content from the out-of-scope host was ever chunked/embedded
    # either -- not merely absent from Postgres.
    assert all("evil" not in record.payload["source_url"] for record in records)


async def test_heartbeat_is_written_mid_crawl_not_only_at_job_boundaries(
    monkeypatch, live_test_services, tmp_path
):
    # (f): a multi-page crawl with real, non-trivial per-page latency --
    # proves write_heartbeat() is called repeatedly DURING the crawl
    # (with real elapsed time between calls), not just once at job start.
    await _prepare_env(monkeypatch, live_test_services)
    heartbeat_path = tmp_path / "heartbeat"
    monkeypatch.setattr(worker_module, "HEARTBEAT_PATH", heartbeat_path)
    # 0.3s -> a 0.9s staleness window (HEARTBEAT_STALE_MULTIPLIER=3) --
    # short enough to prove this without an excessively slow test, but
    # with enough slack over a real Postgres round trip (the _fetch_job()
    # call below, after run() returns) that THIS test's own assertion
    # timing doesn't become the flaky thing being measured. Confirmed
    # live: a tighter 0.05s/0.15s window made the freshness check itself
    # flake on ordinary DB-query overhead, not a real heartbeat gap.
    monkeypatch.setenv("WORKER_POLL_INTERVAL_SECONDS", "0.3")

    calls: list[float] = []
    original_write_heartbeat = worker_module.write_heartbeat

    def _counting_write_heartbeat(path=heartbeat_path):
        calls.append(time.monotonic())
        return original_write_heartbeat(path)

    monkeypatch.setattr(worker_module, "write_heartbeat", _counting_write_heartbeat)

    routes: dict = {}
    page_delay_seconds = 0.08
    with local_http_server(_tracking_handler(routes, [], page_delay_seconds)) as port:
        seed = f"http://{_LOOPBACK_HOST}:{port}"
        links_html = "".join(f'<a href="/p{i}">p{i}</a>' for i in range(4))
        routes["/"] = (200, _rich_page("Home", "home", links_html).encode())
        for i in range(4):
            routes[f"/p{i}"] = (200, _rich_page(f"Page{i}", f"w{i}").encode())

        ids = await _seed_tenant_crawl_source_and_verified_domain(seed)
        await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=15)

    job = await _fetch_job(ids["job_id"])
    assert job.status == "succeeded"

    # One heartbeat write per page actually visited (seed + 4 children) --
    # proves the handler itself writes progress, not just run()'s own
    # once-per-iteration write before the handler was even dispatched.
    assert len(calls) >= 5
    # Real elapsed time passed between the first and last write -- this
    # could not be true if every write happened in one burst at the end.
    assert calls[-1] - calls[0] >= page_delay_seconds * 3
    # The real heartbeat file genuinely reflects this: fresh right now,
    # against the tightened staleness threshold.
    assert check_heartbeat_fresh(heartbeat_path) is True


# --- Task 2.7.d: handle_ingest_upload() -----------------------------------
# Same live-against-both-real-services methodology as handle_ingest_url()/
# handle_ingest_crawl() above, run through the SAME real worker.py loop
# (test (c)'s own requirement -- every test below uses run(), never calls
# handle_ingest_upload() directly). No HTTP server anywhere in this
# section -- uploads have no fetch step at all; upload_storage.save()
# under a real tmp_path IS "the upload", matching test_upload_adapter.py's
# own established pattern exactly. UPLOAD_STORAGE_PATH is folded into the
# SAME _prepare_env() call as every other override (see that function's
# own updated comment for why a second, separate set_valid_env() call
# would silently undo DATABASE_URL/QDRANT_URL/QDRANT_API_KEY).

_DOCX_MAX_PART_SIZE = 100 * 1024 * 1024
_UPLOAD_MAX_SIZE = 50 * 1024 * 1024


async def _seed_tenant_upload_source(storage_dir, uploads: list[tuple[bytes, str]]) -> dict:
    tenant_id = await _create_tenant("Upload Adapter Proof Tenant")

    config_uploads = [
        {
            "upload_id": str(
                upload_storage.save(data, storage_dir=storage_dir, max_size_bytes=_UPLOAD_MAX_SIZE)
            ),
            "original_filename": filename,
        }
        for data, filename in uploads
    ]

    return await _create_source_and_enqueue(
        tenant_id, "upload", {"uploads": config_uploads}, "ingest_upload"
    )


async def test_a_multi_format_upload_job_is_claimed_processed_and_succeeds_through_the_real_loop(
    monkeypatch, live_test_services, tmp_path
):
    # (a)/(c): proves the FULL real path -- enqueue() -> the real worker
    # run() loop (the SAME function 2.1.d/e's own tests exercise, not
    # called in isolation here either) -> claim_next_job() -> dispatch ->
    # handle_ingest_upload() -> ingest_upload() per entry (2.7.c) ->
    # finish_ingest() (2.7's own duplication-check extraction) ->
    # embed_and_upsert() -> mark_job_succeeded(). All four supported
    # formats in ONE job, each a real fixture file, not a synthetic stub.
    client = await _prepare_env(
        monkeypatch, live_test_services, UPLOAD_STORAGE_PATH=str(tmp_path)
    )

    uploads = [
        (read_pdf_fixture("multi_page.pdf"), "report.pdf"),
        (read_docx_fixture("nested_headings.docx"), "report.docx"),
        (read_text_fixture("sample.txt").encode("utf-8"), "notes.txt"),
        (read_markdown_fixture("nested_headings.md").encode("utf-8"), "notes.md"),
    ]
    ids = await _seed_tenant_upload_source(tmp_path, uploads)

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
    assert {doc.file_name for doc in documents} == {
        "report.pdf",
        "report.docx",
        "notes.txt",
        "notes.md",
    }
    assert all(doc.url is None for doc in documents)  # keyed by file_name, never url
    assert all(doc.status == "extracted" for doc in documents)

    records, _ = await client.scroll(
        collection_name=qdrant.COLLECTION_NAME,
        scroll_filter=qdrant.tenant_filter(ids["tenant_id"]),
        limit=20,
    )
    assert len(records) >= 4
    assert all(record.payload["source_type"] == "upload" for record in records)
    assert all(record.payload["source_url"] is None for record in records)
    assert {record.payload["file_name"] for record in records} >= {
        "report.pdf",
        "report.docx",
        "notes.txt",
        "notes.md",
    }


async def test_a_mixed_outcome_upload_job_succeeds_with_each_item_independent(
    monkeypatch, live_test_services, tmp_path
):
    # (b): the partial-failure/job-success policy, confirmed identical to
    # handle_ingest_url()'s own -- a job whose upload list mixes a valid
    # file with a corrupt one and an unrecognized-type one still succeeds
    # overall (every entry got a recorded, definitive outcome -- not
    # "every entry must succeed"); the valid file is really ingested, and
    # neither the corrupt nor the unrecognized entry leaves a documents
    # row at all.
    await _prepare_env(monkeypatch, live_test_services, UPLOAD_STORAGE_PATH=str(tmp_path))

    uploads = [
        (read_pdf_fixture("multi_page.pdf"), "report.pdf"),
        (_docx_with_invalid_xml_content(), "broken.docx"),
        (b"\x89PNG\r\n\x1a\n" + b"some binary payload", "mystery.bin"),
    ]
    ids = await _seed_tenant_upload_source(tmp_path, uploads)

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
    # Exactly the one valid file persisted -- the corrupt and unrecognized
    # entries left no row at all, not a row with some "failed" status.
    assert {doc.file_name for doc in documents} == {"report.pdf"}


# --- Task 2.8.e [SECURITY]: handle_ingest_db() ----------------------------
# Same live-against-both-real-services methodology, run through the SAME
# real worker.py loop (test (d)'s own requirement -- every test below uses
# run(), never calls handle_ingest_db() directly). The "remote" tenant
# database being synced is a real Postgres role + a real table created on
# the SAME test-db service this suite's own app tables already live on --
# the quickest real target available; Step 2.8.f's own job is to decide
# whether reusing test-db this way is the long-term convention for this
# step's end-to-end matrix, not decided here. connect_safely()'s own guard
# correctly treats 127.0.0.1 as unsafe by default (it's loopback) --
# bypassed FOR TEST PURPOSES ONLY via the already-established
# fake_is_unsafe_except_loopback() pattern, patched on database_adapter_
# module directly (matching test_database_adapter.py's own identical
# precedent -- the name is looked up there, not in job_handlers.py).


@asynccontextmanager
async def _probe_table_with_rows(rows: list[dict]):
    """A real, fresh readonly Postgres role plus a real table (populated
    with `rows`) on the real test-db service -- same `CREATE ROLE`/`GRANT`
    shape as test_database_adapter.py's own `readonly_and_writable_roles`
    fixture (not reused directly: that fixture creates a READONLY/WRITABLE
    PAIR with no table of its own; this needs exactly one readonly role
    plus a real data table, a genuinely different shape). Random suffix,
    not a fixed name -- safe even if a prior run's own teardown was ever
    interrupted mid-way.
    """
    suffix = uuid.uuid4().hex[:8]
    role = f"probe_dbsync_{suffix}"
    table = f"probe_products_{suffix}"
    database_name = urlsplit(require_test_database()).path.lstrip("/")

    admin = await _admin_connection()
    try:
        await admin.execute(f"CREATE ROLE {role} LOGIN PASSWORD 'dbsync-pw'")  # noqa: S106
        await admin.execute(f'GRANT CONNECT ON DATABASE "{database_name}" TO {role}')
        await admin.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
        await admin.execute(
            f"CREATE TABLE public.{table} (id int, name text, price numeric, external_id text)"
        )
        await admin.execute(f"GRANT SELECT ON public.{table} TO {role}")
        for row in rows:
            await admin.execute(
                f"INSERT INTO public.{table} (id, name, price, external_id) "  # noqa: S608
                "VALUES ($1, $2, $3, $4)",
                row["id"],
                row["name"],
                # Decimal, not a bare float: asyncpg encodes a Python float
                # as its own imprecise IEEE754 double representation, which
                # a `numeric` column then stores exactly (confirmed live --
                # a bare 19.99 round-trips as
                # Decimal('19.98999999999999843...')), breaking this test's
                # own plain substring assertions below for no reason
                # related to the code under test.
                decimal.Decimal(str(row["price"])),
                row.get("external_id"),
            )
        yield {"role": role, "password": "dbsync-pw", "table": f"public.{table}"}
    finally:
        await admin.execute(f"DROP TABLE IF EXISTS public.{table}")
        await admin.execute(f"REVOKE ALL ON SCHEMA public FROM {role}")
        await admin.execute(f'REVOKE ALL ON DATABASE "{database_name}" FROM {role}')
        await admin.execute(f"DROP ROLE IF EXISTS {role}")
        await admin.close()


async def _seed_tenant_db_source(
    *, role: str, password: str, allowlisted_tables: dict, row_templates: dict
) -> dict:
    url = urlsplit(require_test_database())
    tenant_id = await _create_tenant("Database Adapter Job Proof Tenant")

    async with db_session() as session:
        repo = IngestRepository(tenant_id=tenant_id, session=session)
        db_connection = await repo.create_db_connection(
            host=url.hostname,
            credentials={
                "database": url.path.lstrip("/"),
                "user": role,
                "password": password,
                "port": str(url.port),
                "sslmode": "disable",  # test-db runs plain TCP, no TLS configured at all
            },
            allowlisted_tables=allowlisted_tables,
            row_templates=row_templates,
        )
        await session.commit()
        db_connection_id = db_connection.id

    ids = await _create_source_and_enqueue(
        tenant_id, "database", {"db_connection_id": str(db_connection_id)}, "ingest_db"
    )
    ids["db_connection_id"] = db_connection_id
    return ids


_PRODUCTS_TEMPLATE_CONFIG = {
    "columns": ["name", "price"],
    "template": "Product: {name}, priced at {price}",
    "primary_key": "id",
}


async def test_a_multi_row_db_sync_job_is_claimed_processed_and_succeeds_through_the_real_loop(
    monkeypatch, live_test_services
):
    # (a)/(d): the FULL real path -- enqueue() -> the real worker run()
    # loop (the SAME function every other handler's own tests above
    # exercise) -> claim_next_job() -> dispatch -> handle_ingest_db() ->
    # fetch_readonly_rows() (2.8.b) per table -> ingest_db_row() (2.8.d)
    # per row -> finish_ingest() -> embed_and_upsert() ->
    # mark_job_succeeded(). Every row in a real allowlisted table lands as
    # a real, queryable Qdrant chunk.
    client = await _prepare_env(monkeypatch, live_test_services)
    monkeypatch.setattr(
        database_adapter_module, "is_unsafe_destination_ip", fake_is_unsafe_except_loopback
    )

    rows = [
        {"id": 1, "name": "Widget", "price": 9.99},
        {"id": 2, "name": "Gadget", "price": 19.99},
        {"id": 3, "name": "Gizmo", "price": 29.99},
    ]
    async with _probe_table_with_rows(rows) as probe:
        ids = await _seed_tenant_db_source(
            role=probe["role"],
            password=probe["password"],
            allowlisted_tables={"tables": [probe["table"]]},
            row_templates={probe["table"]: _PRODUCTS_TEMPLATE_CONFIG},
        )

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
        assert {doc.row_identity for doc in documents} == {
            f"{probe['table']}:1",
            f"{probe['table']}:2",
            f"{probe['table']}:3",
        }
        assert all(doc.url is None and doc.file_name is None for doc in documents)
        assert all(doc.status == "extracted" for doc in documents)

        records, _ = await client.scroll(
            collection_name=qdrant.COLLECTION_NAME,
            scroll_filter=qdrant.tenant_filter(ids["tenant_id"]),
            limit=20,
        )
        assert len(records) >= 3
        assert all(record.payload["source_type"] == "database" for record in records)
        assert all(
            record.payload["source_url"] is None and record.payload["file_name"] is None
            for record in records
        )
        rendered_texts = {record.payload["text"] for record in records}
        assert any("Widget" in text and "9.99" in text for text in rendered_texts)
        assert any("Gadget" in text and "19.99" in text for text in rendered_texts)
        assert any("Gizmo" in text and "29.99" in text for text in rendered_texts)


async def test_a_mixed_outcome_db_sync_job_succeeds_with_each_row_independent(
    monkeypatch, live_test_services
):
    # (b): a table whose own rows mix one that fails row_templates
    # rendering (a NULL value in the column configured as `primary_key`,
    # raising MissingPrimaryKeyError, app/ingest/row_templates.py) with
    # rows that succeed -- confirms per-row independence through the real
    # handler: the bad row is logged and skipped, the good rows still
    # ingest, and the JOB still succeeds overall (every row got a
    # definitive outcome -- not "every row must succeed").
    await _prepare_env(monkeypatch, live_test_services)
    monkeypatch.setattr(
        database_adapter_module, "is_unsafe_destination_ip", fake_is_unsafe_except_loopback
    )

    rows = [
        {"id": 1, "name": "Widget", "price": 9.99, "external_id": "ext-1"},
        {"id": 2, "name": "Gadget", "price": 19.99, "external_id": None},  # bad: NULL identity
        {"id": 3, "name": "Gizmo", "price": 29.99, "external_id": "ext-3"},
    ]
    async with _probe_table_with_rows(rows) as probe:
        # primary_key is "external_id" here (NOT the real `id` column) --
        # a plain column name, per row_templates.py's own scheme, so a
        # NULL in it is a per-ROW data condition, not a config problem.
        template_config = {**_PRODUCTS_TEMPLATE_CONFIG, "primary_key": "external_id"}
        ids = await _seed_tenant_db_source(
            role=probe["role"],
            password=probe["password"],
            allowlisted_tables={"tables": [probe["table"]]},
            row_templates={probe["table"]: template_config},
        )

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
        # Exactly the two good rows persisted -- the NULL-identity row left
        # no row at all, not a row with some "failed" status.
        assert {doc.row_identity for doc in documents} == {
            f"{probe['table']}:ext-1",
            f"{probe['table']}:ext-3",
        }

        # Task 2.9.a: proven here too (the identical handle_ingest_url()
        # case above already covers the basic mechanism and a url-shaped
        # partial failure) specifically because handle_ingest_db()'s own
        # control flow is the most structurally different of the four
        # handlers (the try/finally closing a real connection sits BEFORE
        # this write) -- confirms the write point chosen still correctly
        # fires after that, not skipped by the connection-cleanup path.
        source_row = await _fetch_source(ids["source_id"])
        assert source_row.last_run_at is not None


async def test_a_stale_row_templates_entry_for_a_removed_table_is_rejected_not_synced(
    monkeypatch, live_test_services
):
    # (c): row_templates carries an entry for a table that is NOT (or no
    # longer) in allowlisted_tables -- a real, reachable config-drift state
    # (the two are independently-editable JSONB columns on the same
    # db_connections row, Task 2.1.a). Confirms this is rejected cleanly
    # (logged, not queried) rather than silently skipped (no trace at all)
    # or silently synced (queried anyway) -- while a second, genuinely
    # allowlisted table in the SAME job still processes normally.
    client = await _prepare_env(monkeypatch, live_test_services)
    monkeypatch.setattr(
        database_adapter_module, "is_unsafe_destination_ip", fake_is_unsafe_except_loopback
    )

    allowed_rows = [{"id": 1, "name": "Widget", "price": 9.99}]
    async with _probe_table_with_rows(allowed_rows) as allowed_probe:
        stale_table = "public.this_table_was_removed_from_the_allowlist"
        ids = await _seed_tenant_db_source(
            role=allowed_probe["role"],
            password=allowed_probe["password"],
            # The stale entry is allowlisted nowhere -- only allowed_probe's
            # own table is.
            allowlisted_tables={"tables": [allowed_probe["table"]]},
            row_templates={
                allowed_probe["table"]: _PRODUCTS_TEMPLATE_CONFIG,
                stale_table: _PRODUCTS_TEMPLATE_CONFIG,
            },
        )

        await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=10)

        job = await _fetch_job(ids["job_id"])
        # The job still succeeds -- one table's own stale config does not
        # abort the rest of the job.
        assert job.status == "succeeded"
        assert job.attempts == 0
        assert job.error is None

        async with db_session() as session:
            documents = (
                await session.execute(
                    sa.select(Document).where(Document.source_id == ids["source_id"])
                )
            ).scalars().all()
        # Exactly the genuinely-allowlisted table's own row persisted --
        # the stale table was never queried at all (not even an attempt),
        # so it left no row, no partial row, nothing.
        assert {doc.row_identity for doc in documents} == {f"{allowed_probe['table']}:1"}

        records, _ = await client.scroll(
            collection_name=qdrant.COLLECTION_NAME,
            scroll_filter=qdrant.tenant_filter(ids["tenant_id"]),
            limit=20,
        )
        assert len(records) >= 1
        assert all(record.payload["source_type"] == "database" for record in records)


# --- Task 2.9.a [SECURITY]: handle_scheduler_tick() -----------------------
# Same real-worker-loop methodology as every handler above (test (g)'s own
# requirement -- every test below uses run(), never calls handle_scheduler_
# tick() directly). No Qdrant needed at all -- this handler only ever
# touches Postgres (enqueueing other jobs, never embedding/upserting
# anything itself), so these tests use _seeded_tenant + a real test-db
# connection only, matching test_worker.py's own reap-test precedent
# (test_run_reaps_a_stuck_job_during_its_own_loop), not live_test_services().
# get_due_sources()'s/ensure_scheduler_ticks_seeded()'s own pure-query
# behavior is tested directly, offline-of-a-handler, in test_scheduler.py --
# these tests only prove the HANDLER wires them correctly through the real
# loop, matching every prior job-handler task's own proof requirement.


async def test_scheduler_tick_enqueues_one_job_per_due_source_and_reschedules_itself(
    monkeypatch, _seeded_tenant
):
    tenant_id = _seeded_tenant
    set_valid_env(monkeypatch, VALID_ENV)

    async with db_session() as session:
        urls_source = Source(
            tenant_id=tenant_id,
            type="urls",
            config={"urls": []},
            refresh_interval="daily",
            status="active",
        )
        crawl_source = Source(
            tenant_id=tenant_id,
            type="crawl",
            config={"seed_url": "http://example.invalid/"},
            refresh_interval="daily",
            status="active",
        )
        upload_source = Source(
            tenant_id=tenant_id,
            type="upload",
            config={"uploads": []},
            refresh_interval="daily",
            status="active",
        )
        db_source = Source(
            tenant_id=tenant_id,
            type="database",
            config={"db_connection_id": str(uuid.uuid4())},
            refresh_interval="daily",
            status="active",
        )
        session.add_all([urls_source, crawl_source, upload_source, db_source])
        await session.commit()
        source_id_by_type = {
            "urls": urls_source.id,
            "crawl": crawl_source.id,
            "upload": upload_source.id,
            "database": db_source.id,
        }

        repo = IngestRepository(tenant_id=tenant_id, session=session)
        tick_job = await repo.enqueue(job_type="scheduler_tick")
        await session.commit()
        tick_job_id = tick_job.id

    await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=10)

    tick_job_after = await _fetch_job(tick_job_id)
    assert tick_job_after.status == "succeeded"

    async with db_session() as session:
        enqueued = (
            await session.execute(
                sa.select(Job).where(
                    Job.tenant_id == tenant_id, Job.job_type != "scheduler_tick"
                )
            )
        ).scalars().all()
    by_type = {job.job_type: job for job in enqueued}
    assert set(by_type.keys()) == {"ingest_url", "ingest_crawl", "ingest_upload", "ingest_db"}
    assert by_type["ingest_url"].source_id == source_id_by_type["urls"]
    assert by_type["ingest_crawl"].source_id == source_id_by_type["crawl"]
    assert by_type["ingest_upload"].source_id == source_id_by_type["upload"]
    assert by_type["ingest_db"].source_id == source_id_by_type["database"]
    assert all(job.status == "pending" for job in enqueued)

    # The self-re-enqueue: a second, NOT-yet-claimable scheduler_tick now
    # exists for this same tenant -- the original (succeeded) one plus the
    # new one, never a third.
    async with db_session() as session:
        ticks = (
            await session.execute(
                sa.select(Job).where(
                    Job.tenant_id == tenant_id, Job.job_type == "scheduler_tick"
                )
            )
        ).scalars().all()
    assert len(ticks) == 2
    new_tick = next(job for job in ticks if job.id != tick_job_id)
    assert new_tick.status == "pending"
    assert new_tick.next_run_at > datetime.now(UTC) + timedelta(seconds=1)


async def test_scheduler_tick_does_not_reenqueue_itself_once_its_tenant_is_suspended(
    monkeypatch, _seeded_tenant
):
    # Resolves the suspended-tenant Open marker's own layer 2 (app/ingest/
    # scheduler.py's header comment has the full three-layer design) --
    # the layer that actually matters for a chain already in flight:
    # ensure_scheduler_ticks_seeded() alone would never touch this tenant,
    # since it only ever looks at tenants with NO pending/running tick,
    # and this one already has a real, pending scheduler_tick job. A due
    # source exists too, proving suspension stops BOTH the self-re-enqueue
    # AND the per-source enqueueing, not merely one of the two.
    tenant_id = _seeded_tenant
    set_valid_env(monkeypatch, VALID_ENV)

    async with db_session() as session:
        source = Source(
            tenant_id=tenant_id,
            type="urls",
            config={"urls": []},
            refresh_interval="daily",
            status="active",
        )
        session.add(source)
        await session.commit()

        repo = IngestRepository(tenant_id=tenant_id, session=session)
        tick_job = await repo.enqueue(job_type="scheduler_tick")
        await session.commit()
        tick_job_id = tick_job.id

        await session.execute(
            sa.update(Tenant).where(Tenant.id == tenant_id).values(status="suspended")
        )
        await session.commit()

    await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=10)

    tick_job_after = await _fetch_job(tick_job_id)
    # The job itself still succeeds -- a cheap check that correctly found
    # nothing to do is not a failure.
    assert tick_job_after.status == "succeeded"

    async with db_session() as session:
        all_jobs_for_tenant = (
            await session.execute(sa.select(Job).where(Job.tenant_id == tenant_id))
        ).scalars().all()
    # Exactly the one original tick (now succeeded) -- no new scheduler_
    # tick re-enqueued, and no ingest_url job for the due source either.
    assert len(all_jobs_for_tenant) == 1
    assert all_jobs_for_tenant[0].id == tick_job_id


async def test_scheduler_tick_never_touches_another_tenants_sources(monkeypatch, _seeded_tenant):
    tenant_a = _seeded_tenant
    tenant_b = uuid.uuid4()
    set_valid_env(monkeypatch, VALID_ENV)

    async with db_session() as session:
        session.add(Tenant(id=tenant_b, name="Scheduler Isolation Proof Tenant B", status="active"))
        await session.commit()

        source_a = Source(
            tenant_id=tenant_a,
            type="urls",
            config={"urls": []},
            refresh_interval="daily",
            status="active",
        )
        source_b = Source(
            tenant_id=tenant_b,
            type="urls",
            config={"urls": []},
            refresh_interval="daily",
            status="active",
        )
        session.add_all([source_a, source_b])
        await session.commit()

        # Only tenant A gets a scheduler_tick job -- tenant B's own due
        # source must still never be touched, proving the due-query and
        # the enqueue loop are both genuinely scoped by job.tenant_id, not
        # merely "no other tenant happened to have a tick running".
        await IngestRepository(tenant_id=tenant_a, session=session).enqueue(
            job_type="scheduler_tick"
        )
        await session.commit()

    await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=10)

    async with db_session() as session:
        tenant_b_non_tick_jobs = (
            await session.execute(
                sa.select(Job).where(
                    Job.tenant_id == tenant_b, Job.job_type != "scheduler_tick"
                )
            )
        ).scalars().all()
    # Filtered to exclude "scheduler_tick" deliberately: tenant B has none
    # of ITS OWN to begin with, so this same run() iteration's own
    # bootstrap sweep (ensure_scheduler_ticks_seeded(), unrelated to
    # handle_scheduler_tick() itself) correctly seeds one for tenant B too
    # -- that is expected, separate behavior, not what this test is about.
    # What must never happen, and is what this test actually checks: an
    # "ingest_url" job for tenant B's own source, which would mean tenant
    # A's own scheduler_tick leaked across the tenant boundary.
    assert tenant_b_non_tick_jobs == []


async def test_run_seeds_a_scheduler_tick_for_a_tenant_with_none_during_its_own_loop(
    monkeypatch, _seeded_tenant
):
    # Confirms ensure_scheduler_ticks_seeded() (tested directly, offline-
    # of-a-handler, in test_scheduler.py) genuinely fires from inside a
    # real run() iteration -- matching test_run_reaps_a_stuck_job_during_
    # its_own_loop's own identical "direct proof exists, confirm it also
    # fires through the real loop" precedent.
    tenant_id = _seeded_tenant
    set_valid_env(monkeypatch, VALID_ENV)

    await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=10)

    async with db_session() as session:
        ticks = (
            await session.execute(
                sa.select(Job).where(
                    Job.tenant_id == tenant_id, Job.job_type == "scheduler_tick"
                )
            )
        ).scalars().all()
    # Exactly one, still "pending", not yet claimed: seeded by this same
    # iteration's own bootstrap sweep, with next_run_at deliberately
    # `scheduler_tick_interval_seconds` in the future, NOT immediately
    # claimable (see ensure_scheduler_ticks_seeded()'s own docstring for
    # the real race this delay closes -- a freshly-bootstrapped tick must
    # never be able to compete with this same tenant's own already-pending
    # real work for the very next claim slot). This one iteration's own
    # _claim_and_process_one_job() therefore finds nothing claimable for
    # this tenant at all, correctly -- the tick will run on a LATER
    # iteration, once its own delay elapses.
    assert len(ticks) == 1
    assert ticks[0].status == "pending"
    assert ticks[0].next_run_at > datetime.now(UTC) + timedelta(seconds=1)
