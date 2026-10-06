# backend/tests/test_web_adapter.py
# Task 2.6.c: tests for app/ingest/web_adapter.py's ingest_url() -- the
# first real caller tying together fetch_with_redirects() (2.3),
# extract_html() (2.4.b), compute_content_hash() (2.4.c) and
# embed_and_upsert() (2.5.e). [SECURITY] rigor throughout, matching
# web_adapter.py's own header: this is the first code path that fetches a
# tenant-supplied URL and writes into the shared, multi-tenant Qdrant
# collection.
#
# Live against BOTH real services together (test-db AND test-qdrant),
# using the new live_test_services() fixture (tests/conftest.py) -- the
# second real caller the two-service watch-item marker (recorded at the
# duplication check after 2.5.d/e/f) anticipated. Also against a real
# local HTTP server (tests/conftest.py's local_http_server()), matching
# test_safe_fetch.py's/test_domain_verification.py's own established
# pattern for exercising HTTPS-only production code against plain HTTP.
#
# is_unsafe_destination_ip is monkeypatched so 127.0.0.1 is treated as
# safe FOR TEST PURPOSES ONLY -- the identical small helper
# test_safe_fetch.py and test_domain_verification.py each already define
# locally. This is now the THIRD occurrence (flagged here, not
# consolidated now, matching this project's own established "flag, don't
# fix mid-task" discipline -- a candidate for the next duplication
# check).
import http.server
import socket
import uuid

import sqlalchemy as sa

from app import qdrant
from app.ingest import safe_fetch as safe_fetch_module
from app.ingest import web_adapter as web_adapter_module
from app.ingest.ip_safety import is_unsafe_destination_ip as _real_is_unsafe_destination_ip
from app.ingest.models import Document, Source
from app.ingest.repository import IngestRepository
from app.ingest.web_adapter import ingest_url
from app.tenancy.models import Tenant
from tests.conftest import (
    assert_no_socket_connections,
    db_session,
    local_http_server,
    patch_embed_dense,
)

_LOOPBACK_HOST = "127.0.0.1"

# 50+ words (NEARLY_EMPTY_WORD_THRESHOLD, extract_html.py) -- real content,
# not nearly-empty.
_RICH_BODY_V1 = (
    "<html><head><title>Ingest Proof</title></head><body>"
    "<h2>Section</h2><p>" + " ".join(f"word{i}" for i in range(60)) + "</p></body></html>"
)
_RICH_BODY_V2 = (
    "<html><head><title>Ingest Proof</title></head><body>"
    "<h2>Section</h2><p>" + " ".join(f"changed{i}" for i in range(60)) + "</p></body></html>"
)
# Under 50 words -- deliberately nearly-empty.
_SPARSE_BODY = "<html><head><title>Sparse</title></head><body><p>too short</p></body></html>"


def _fake_is_unsafe_except_loopback(ip) -> bool:
    if str(ip) == _LOOPBACK_HOST:
        return False
    return _real_is_unsafe_destination_ip(ip)


def _scripted_handler(routes: dict):
    # routes: path -> (status_code, body_bytes), read fresh on EVERY
    # request -- a caller can mutate the dict between requests (used by
    # the "changed content" test below) to change what the SAME running
    # server returns on a later request.
    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            status, body = routes.get(self.path, (404, b"not found"))
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return _Handler


def _unused_loopback_port() -> int:
    # Task 2.6.c, test (e): a port nothing listens on, to force a genuine
    # connection-level failure (ECONNREFUSED) -- NOT a 404, which
    # fetch_with_redirects() never raises for (confirmed by reading
    # safe_fetch.py directly: it only raises for an SSRF rejection, a
    # size/redirect-cap violation, or a real transport-level error; an
    # ordinary non-redirect HTTP status like 404 returns normally). Bind
    # to an ephemeral port and close it immediately so nothing is
    # listening there.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((_LOOPBACK_HOST, 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


async def _seed_tenant_source_and_verified_domain(domain: str = _LOOPBACK_HOST) -> dict:
    tenant_id = uuid.uuid4()
    async with db_session() as session:
        session.add(Tenant(id=tenant_id, name="Web Adapter Proof Tenant", status="active"))
        await session.commit()

    async with db_session() as session:
        source = Source(
            tenant_id=tenant_id, type="urls", refresh_interval="daily", status="active"
        )
        session.add(source)
        await session.commit()
        source_id = source.id

    async with db_session() as session:
        repo = IngestRepository(tenant_id=tenant_id, session=session)
        verified_domain = await repo.claim_domain(domain)
        await session.commit()
        domain_id = verified_domain.id

    # Raw UPDATE straight to "verified", matching
    # test_revocation_preserves_indexed_content.py's own established
    # precedent for this exact need -- DNS verification itself is not
    # under test here.
    async with db_session() as session:
        await session.execute(
            sa.text(
                "UPDATE verified_domains SET status = 'verified', verified_at = now() "
                "WHERE id = :id"
            ),
            {"id": domain_id},
        )
        await session.commit()

    return {"tenant_id": tenant_id, "source_id": source_id}


async def _ingest(client, collection_name: str, *, tenant_id, source_id, url):
    async with db_session() as session:
        result = await ingest_url(
            session,
            client,
            collection_name,
            tenant_id=tenant_id,
            source_id=source_id,
            source_type="urls",
            url=url,
        )
        await session.commit()
    return result


async def _fetch_document(source_id: uuid.UUID, url: str) -> Document:
    async with db_session() as session:
        return (
            await session.execute(
                sa.select(Document).where(Document.source_id == source_id, Document.url == url)
            )
        ).scalar_one()


async def test_first_time_ingest_creates_document_and_indexes_content(
    monkeypatch, live_test_services
):
    # (a) first-time ingest: a new document row is created and the real
    # content is really chunked, embedded and upserted into Qdrant.
    monkeypatch.setattr(
        safe_fetch_module, "is_unsafe_destination_ip", _fake_is_unsafe_except_loopback
    )
    patch_embed_dense(monkeypatch)
    _, client, collection_name = live_test_services
    await qdrant.ensure_collection(client, collection_name)
    ids = await _seed_tenant_source_and_verified_domain()

    with local_http_server(_scripted_handler({"/": (200, _RICH_BODY_V1.encode())})) as port:
        url = f"http://{_LOOPBACK_HOST}:{port}/"
        result = await _ingest(
            client, collection_name, tenant_id=ids["tenant_id"], source_id=ids["source_id"], url=url
        )

    assert result.status == "ingested"
    assert result.document_id is not None

    document = await _fetch_document(ids["source_id"], url)
    assert document.id == result.document_id
    assert document.title == "Ingest Proof"
    assert document.status == "extracted"
    assert document.is_nearly_empty is False
    assert document.content_hash is not None

    count = await client.count(collection_name=collection_name)
    assert count.count >= 1

    records, _ = await client.scroll(
        collection_name=collection_name,
        scroll_filter=qdrant.tenant_filter(ids["tenant_id"]),
        limit=10,
    )
    assert len(records) >= 1
    assert all(record.payload["doc_id"] == str(result.document_id) for record in records)


async def test_rerun_with_identical_content_skips_rechunk_and_reembed(
    monkeypatch, live_test_services
):
    # (b) the core idempotency property: re-running against IDENTICAL
    # content must NOT re-chunk or re-embed at all -- proven directly by
    # counting calls to embed_and_upsert() itself, not just by observing
    # the end result.
    monkeypatch.setattr(
        safe_fetch_module, "is_unsafe_destination_ip", _fake_is_unsafe_except_loopback
    )
    patch_embed_dense(monkeypatch)
    _, client, collection_name = live_test_services
    await qdrant.ensure_collection(client, collection_name)
    ids = await _seed_tenant_source_and_verified_domain()

    with local_http_server(_scripted_handler({"/": (200, _RICH_BODY_V1.encode())})) as port:
        url = f"http://{_LOOPBACK_HOST}:{port}/"

        first = await _ingest(
            client, collection_name, tenant_id=ids["tenant_id"], source_id=ids["source_id"], url=url
        )
        assert first.status == "ingested"
        count_after_first = await client.count(collection_name=collection_name)

        calls = []
        original = web_adapter_module.embed_and_upsert

        async def _counting(*args, **kwargs):
            calls.append(1)
            return await original(*args, **kwargs)

        monkeypatch.setattr(web_adapter_module, "embed_and_upsert", _counting)

        second = await _ingest(
            client, collection_name, tenant_id=ids["tenant_id"], source_id=ids["source_id"], url=url
        )

    assert second.status == "unchanged"
    assert second.document_id == first.document_id
    assert calls == []  # embed_and_upsert() was never called the second time
    count_after_second = await client.count(collection_name=collection_name)
    assert count_after_second.count == count_after_first.count


async def test_rerun_with_changed_content_reembeds_same_document(monkeypatch, live_test_services):
    # (c) re-running against CHANGED content re-embeds for real, keeping
    # the same document_id (build_point_id()'s own determinism, 2.5.c,
    # depends on the SAME document_id being reused across re-runs of the
    # same URL).
    monkeypatch.setattr(
        safe_fetch_module, "is_unsafe_destination_ip", _fake_is_unsafe_except_loopback
    )
    patch_embed_dense(monkeypatch)
    _, client, collection_name = live_test_services
    await qdrant.ensure_collection(client, collection_name)
    ids = await _seed_tenant_source_and_verified_domain()

    routes = {"/": (200, _RICH_BODY_V1.encode())}
    with local_http_server(_scripted_handler(routes)) as port:
        url = f"http://{_LOOPBACK_HOST}:{port}/"

        first = await _ingest(
            client, collection_name, tenant_id=ids["tenant_id"], source_id=ids["source_id"], url=url
        )
        assert first.status == "ingested"
        hash_after_first = (await _fetch_document(ids["source_id"], url)).content_hash

        routes["/"] = (200, _RICH_BODY_V2.encode())  # same server, new content

        second = await _ingest(
            client, collection_name, tenant_id=ids["tenant_id"], source_id=ids["source_id"], url=url
        )

    assert second.status == "ingested"
    assert second.document_id == first.document_id

    document = await _fetch_document(ids["source_id"], url)
    assert document.content_hash != hash_after_first  # proves the row really was updated

    records, _ = await client.scroll(
        collection_name=collection_name,
        scroll_filter=qdrant.tenant_filter(ids["tenant_id"]),
        limit=10,
    )
    assert len(records) >= 1
    assert all("changed" in record.payload["text"] for record in records)
    assert not any("word" in record.payload["text"] for record in records)


async def test_unverified_domain_is_skipped_with_zero_network_activity(
    monkeypatch, live_test_services
):
    # (d) an unverified (here: entirely absent) domain is rejected before
    # any fetch is even attempted -- proven at the socket level, not just
    # by the returned status.
    #
    # assert_no_socket_connections() is applied via monkeypatch.context(),
    # scoped tightly around just the ingest_url() call below -- it patches
    # socket.socket.connect GLOBALLY, which would just as happily break
    # the real AsyncQdrantClient's own HTTP calls (confirmed live:
    # applying it any earlier breaks qdrant.ensure_collection() itself,
    # not just the intended fetch check) AND live_test_services' own
    # Qdrant-collection teardown (confirmed live: the outer `monkeypatch`
    # fixture's own undo only runs AFTER live_test_services' finalizer,
    # since fixtures tear down in reverse setup order -- the outer
    # fixture's patch would still be active during that cleanup).
    _, client, collection_name = live_test_services
    await qdrant.ensure_collection(client, collection_name)

    tenant_id = uuid.uuid4()
    async with db_session() as session:
        session.add(Tenant(id=tenant_id, name="No Verified Domain Tenant", status="active"))
        await session.commit()

    async with db_session() as session:
        source = Source(
            tenant_id=tenant_id, type="urls", refresh_interval="daily", status="active"
        )
        session.add(source)
        await session.commit()
        source_id = source.id

    url = f"http://{_LOOPBACK_HOST}:1/never-fetched"
    with monkeypatch.context() as scoped_monkeypatch:
        assert_no_socket_connections(scoped_monkeypatch)
        result = await _ingest(
            client, collection_name, tenant_id=tenant_id, source_id=source_id, url=url
        )

    assert result.status == "skipped"
    assert "not verified" in result.reason


async def test_connection_failure_is_reported_as_failed(monkeypatch, live_test_services):
    # (e) a genuine connection-level failure (nothing listening on this
    # port) is reported as IngestResult(status="failed", ...) -- NOT a
    # 404, which never raises at all (see _unused_loopback_port()'s own
    # comment above for why).
    monkeypatch.setattr(
        safe_fetch_module, "is_unsafe_destination_ip", _fake_is_unsafe_except_loopback
    )
    _, client, collection_name = live_test_services
    await qdrant.ensure_collection(client, collection_name)
    ids = await _seed_tenant_source_and_verified_domain()

    port = _unused_loopback_port()
    url = f"http://{_LOOPBACK_HOST}:{port}/"
    result = await _ingest(
        client, collection_name, tenant_id=ids["tenant_id"], source_id=ids["source_id"], url=url
    )

    assert result.status == "failed"
    assert result.document_id is None
    assert result.reason is not None

    count = await client.count(collection_name=collection_name)
    assert count.count == 0


async def test_nearly_empty_content_is_ingested_and_flagged(monkeypatch, live_test_services):
    # (f) nearly-empty content is still real content -- ingested
    # normally, with is_nearly_empty persisted True, not treated as a
    # failure or a skip.
    monkeypatch.setattr(
        safe_fetch_module, "is_unsafe_destination_ip", _fake_is_unsafe_except_loopback
    )
    patch_embed_dense(monkeypatch)
    _, client, collection_name = live_test_services
    await qdrant.ensure_collection(client, collection_name)
    ids = await _seed_tenant_source_and_verified_domain()

    with local_http_server(_scripted_handler({"/": (200, _SPARSE_BODY.encode())})) as port:
        url = f"http://{_LOOPBACK_HOST}:{port}/"
        result = await _ingest(
            client, collection_name, tenant_id=ids["tenant_id"], source_id=ids["source_id"], url=url
        )

    assert result.status == "ingested"
    document = await _fetch_document(ids["source_id"], url)
    assert document.is_nearly_empty is True
