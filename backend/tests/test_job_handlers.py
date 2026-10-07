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
import http.server
import socket
import threading
import time
import uuid
from contextlib import contextmanager

import sqlalchemy as sa

from app import qdrant
from app import worker as worker_module
from app.ingest import safe_fetch as safe_fetch_module
from app.ingest.models import Document, Source
from app.ingest.repository import IngestRepository
from app.tenancy.models import Tenant
from app.worker import check_heartbeat_fresh, run
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


def _tracking_handler(routes: dict, hits: list):
    # A local variant of scripted_handler() (tests/conftest.py) that also
    # records every requested path -- needed by tests (c)/(e) below to
    # prove a specific path was NEVER requested, not merely absent from
    # the final result. A second near-duplicate of this handler shape
    # (scripted_handler's own (status, body) routing); kept local rather
    # than extracted, matching this project's own "flag, don't fix
    # mid-task" discipline -- a candidate for the next duplication check.
    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            status, body = routes.get(self.path, (404, b"not found"))
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return _Handler


def _slow_tracking_handler(routes: dict, hits: list, delay_seconds: float):
    # Task 2.6.e part 2, test (f): identical to _tracking_handler() but
    # sleeps before responding -- gives a multi-page crawl real,
    # non-trivial elapsed time to prove the heartbeat is written WHILE
    # busy, not just once at job start/end.
    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            time.sleep(delay_seconds)
            status, body = routes.get(self.path, (404, b"not found"))
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return _Handler


class _IPv6HTTPServer(http.server.HTTPServer):
    address_family = socket.AF_INET6


@contextmanager
def _local_ipv6_http_server(handler_cls: type[http.server.BaseHTTPRequestHandler]):
    # A second occurrence of test_web_adapter.py's own identical shape
    # (that file's own C1-fix test, 2.6.d's duplication check) -- the
    # only portable way to stand up a second, genuinely different "host"
    # without OS-level network configuration (confirmed live there: a
    # second IPv4 loopback alias fails with "Can't assign requested
    # address" on this machine; ::1 is a real, independently bindable
    # loopback address on both Linux and macOS with zero special setup).
    # Kept local rather than extracted, matching the identical "flag,
    # don't fix mid-task" discipline as _tracking_handler() above.
    server = _IPv6HTTPServer(("::1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        thread.join()


async def _seed_tenant_crawl_source_and_verified_domain(seed_url: str) -> dict:
    tenant_id = uuid.uuid4()
    async with db_session() as session:
        session.add(Tenant(id=tenant_id, name="Crawl Adapter Proof Tenant", status="active"))
        await session.commit()

    async with db_session() as session:
        source = Source(
            tenant_id=tenant_id,
            type="crawl",
            config={"seed_url": seed_url},
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
        job = await repo.enqueue(job_type="ingest_crawl", source_id=source_id, payload={})
        await session.commit()
        job_id = job.id

    return {"tenant_id": tenant_id, "source_id": source_id, "job_id": job_id}


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
    with _local_ipv6_http_server(
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
    with local_http_server(_slow_tracking_handler(routes, [], page_delay_seconds)) as port:
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
