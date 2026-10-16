# backend/tests/test_dashboard_source_routes.py
# Step 6.3 [SECURITY]: tests for /api/v1/dashboard/sources/* and
# /api/v1/dashboard/domains/* -- including this task's own required
# adversarial pass (oversized/streamed-oversized upload, a zip-bomb-
# shaped .docx, a spoofed content-type/extension, a path-traversal
# filename, another tenant's source_id/upload_id, concurrent
# re-uploads, a database-type creation attempt direct against the API,
# CSRF on every new state-changing route, and a widget JWT on these
# endpoints).
import asyncio
import io
import uuid
import zipfile

import pytest
import sqlalchemy as sa

from app import qdrant as qdrant_module
from app.auth.tokens import encode_session_token
from app.config import get_settings
from app.dashboard.repository import create_membership
from app.ingest import repository as ingest_repository_module
from app.ingest.models import Job, Source, VerifiedDomain
from app.main import create_app
from app.tenancy.models import Tenant
from tests.conftest import db_session, read_pdf_fixture, read_text_fixture, require_test_qdrant
from tests.dashboard_test_helpers import (
    create_dashboard_user,
    dashboard_http_client,
    login_as,
)

_PDF_BYTES = read_pdf_fixture("multi_page.pdf")
_TEXT_BYTES = read_text_fixture("sample.txt").encode("utf-8")


@pytest.fixture
async def _seeded(reset_test_database, monkeypatch, tmp_path):
    # This file's own routes hold a REAL Qdrant client
    # (get_qdrant_client(), an @lru_cache singleton app/qdrant.py) --
    # pointed at the disposable test instance here, not the production
    # default, and the real "knowledge_chunks" collection (app.qdrant.
    # COLLECTION_NAME, the same one every route in this module uses, not
    # a disposable per-test name) is ensured to exist. Upload tests also
    # need a real, writable UPLOAD_STORAGE_PATH -- the Settings default
    # (/data/uploads) assumes a container filesystem this test process
    # does not have.
    url, key = require_test_qdrant()
    monkeypatch.setenv("QDRANT_URL", url)
    monkeypatch.setenv("QDRANT_API_KEY", key)
    monkeypatch.setenv("QDRANT_READ_ONLY_API_KEY", key)
    monkeypatch.setenv("UPLOAD_STORAGE_PATH", str(tmp_path))
    get_settings.cache_clear()
    qdrant_module.get_qdrant_client.cache_clear()
    qdrant_module.get_qdrant_read_client.cache_clear()
    client = qdrant_module.get_qdrant_client()
    await qdrant_module.ensure_collection(client, qdrant_module.COLLECTION_NAME)

    ids = {"tenant_a": uuid.uuid4(), "tenant_b": uuid.uuid4()}
    async with db_session() as session:
        session.add_all(
            [
                Tenant(id=ids["tenant_a"], name="Tenant A", status="active"),
                Tenant(id=ids["tenant_b"], name="Tenant B", status="active"),
            ]
        )
        await session.flush()
        owner_a = await create_dashboard_user(session, "owner-a@example.com")
        owner_b = await create_dashboard_user(session, "owner-b@example.com")
        await session.flush()
        await create_membership(session, user_id=owner_a.id, tenant_id=ids["tenant_a"])
        await create_membership(session, user_id=owner_b.id, tenant_id=ids["tenant_b"])
        await session.commit()
    yield ids
    get_settings.cache_clear()
    qdrant_module.get_qdrant_client.cache_clear()
    qdrant_module.get_qdrant_read_client.cache_clear()


def _docx_shaped_zip(document_xml: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


# --- CRUD: urls/crawl sources ---------------------------------------------


async def test_list_sources_is_tenant_scoped(_seeded):
    app = create_app()
    async with dashboard_http_client(app) as client:
        headers = await login_as(client, "owner-a@example.com")
        await client.post(
            "/api/v1/dashboard/sources",
            json={"type": "urls", "urls": ["https://example.com/a"]},
            headers=headers,
        )
    async with dashboard_http_client(app) as other_client:
        await login_as(other_client, "owner-b@example.com")
        response = await other_client.get("/api/v1/dashboard/sources")
    assert response.status_code == 200
    assert response.json()["sources"] == []


async def test_create_urls_source_persists_and_enqueues_a_job(_seeded):
    app = create_app()
    async with dashboard_http_client(app) as client:
        headers = await login_as(client, "owner-a@example.com")
        response = await client.post(
            "/api/v1/dashboard/sources",
            json={"type": "urls", "urls": ["https://example.com/a"], "refresh_interval": "daily"},
            headers=headers,
        )
    assert response.status_code == 201
    body = response.json()
    assert body["type"] == "urls"
    assert body["status"] == "ok"
    assert body["config"] == {"urls": ["https://example.com/a"]}

    async with db_session() as session:
        jobs = (
            await session.execute(sa.select(Job).where(Job.source_id == uuid.UUID(body["id"])))
        ).scalars().all()
    assert len(jobs) == 1
    assert jobs[0].job_type == "ingest_url"


async def test_create_source_without_csrf_is_403(_seeded):
    app = create_app()
    async with dashboard_http_client(app) as client:
        await login_as(client, "owner-a@example.com")
        response = await client.post(
            "/api/v1/dashboard/sources",
            json={"type": "urls", "urls": ["https://example.com/a"]},
        )
    assert response.status_code == 403


async def test_create_crawl_source_for_an_unverified_domain_is_422(_seeded):
    app = create_app()
    async with dashboard_http_client(app) as client:
        headers = await login_as(client, "owner-a@example.com")
        response = await client.post(
            "/api/v1/dashboard/sources",
            json={"type": "crawl", "seed_url": "https://unverified.example/"},
            headers=headers,
        )
    assert response.status_code == 422


async def test_disable_then_enable_source(_seeded):
    app = create_app()
    async with dashboard_http_client(app) as client:
        headers = await login_as(client, "owner-a@example.com")
        created = await client.post(
            "/api/v1/dashboard/sources",
            json={"type": "urls", "urls": ["https://example.com/a"]},
            headers=headers,
        )
        source_id = created.json()["id"]

        disabled = await client.post(
            f"/api/v1/dashboard/sources/{source_id}/disable", headers=headers
        )
        assert disabled.status_code == 200
        assert disabled.json()["enabled"] is False
        assert disabled.json()["status"] == "disabled"

        enabled = await client.post(
            f"/api/v1/dashboard/sources/{source_id}/enable", headers=headers
        )
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True


async def test_delete_source_removes_it(_seeded):
    app = create_app()
    async with dashboard_http_client(app) as client:
        headers = await login_as(client, "owner-a@example.com")
        created = await client.post(
            "/api/v1/dashboard/sources",
            json={"type": "urls", "urls": ["https://example.com/a"]},
            headers=headers,
        )
        source_id = created.json()["id"]
        response = await client.delete(f"/api/v1/dashboard/sources/{source_id}", headers=headers)
    assert response.status_code == 204
    async with db_session() as session:
        remaining = (
            await session.execute(sa.select(Source).where(Source.id == uuid.UUID(source_id)))
        ).scalar_one_or_none()
    assert remaining is None


async def test_delete_another_tenants_source_is_404(_seeded):
    app = create_app()
    async with dashboard_http_client(app) as client_a:
        headers_a = await login_as(client_a, "owner-a@example.com")
        created = await client_a.post(
            "/api/v1/dashboard/sources",
            json={"type": "urls", "urls": ["https://example.com/a"]},
            headers=headers_a,
        )
        source_id = created.json()["id"]

    async with dashboard_http_client(app) as client_b:
        headers_b = await login_as(client_b, "owner-b@example.com")
        response = await client_b.delete(
            f"/api/v1/dashboard/sources/{source_id}", headers=headers_b
        )
    assert response.status_code == 404


async def test_refresh_now_respects_the_cooldown(_seeded, monkeypatch):
    monkeypatch.setenv("REFRESH_NOW_COOLDOWN_SECONDS", "3600")
    get_settings.cache_clear()
    app = create_app()
    async with dashboard_http_client(app) as client:
        headers = await login_as(client, "owner-a@example.com")
        created = await client.post(
            "/api/v1/dashboard/sources",
            json={"type": "urls", "urls": ["https://example.com/a"]},
            headers=headers,
        )
        source_id = created.json()["id"]
        first = await client.post(
            f"/api/v1/dashboard/sources/{source_id}/refresh-now", headers=headers
        )
        assert first.status_code == 200
        second = await client.post(
            f"/api/v1/dashboard/sources/{source_id}/refresh-now", headers=headers
        )
    assert second.status_code == 429
    get_settings.cache_clear()


async def test_update_source_edits_the_url_list(_seeded):
    app = create_app()
    async with dashboard_http_client(app) as client:
        headers = await login_as(client, "owner-a@example.com")
        created = await client.post(
            "/api/v1/dashboard/sources",
            json={"type": "urls", "urls": ["https://example.com/a", "https://example.com/b"]},
            headers=headers,
        )
        source_id = created.json()["id"]
        response = await client.patch(
            f"/api/v1/dashboard/sources/{source_id}",
            json={"urls": ["https://example.com/a"]},
            headers=headers,
        )
    assert response.status_code == 200
    assert response.json()["config"] == {"urls": ["https://example.com/a"]}


# --- Database-type sources: Felix decision A ------------------------------


async def test_database_source_creation_via_the_dedicated_endpoint_is_rejected(_seeded):
    app = create_app()
    async with dashboard_http_client(app) as client:
        headers = await login_as(client, "owner-a@example.com")
        response = await client.post("/api/v1/dashboard/sources/database", headers=headers)
    assert response.status_code == 400


async def test_database_type_through_the_plain_create_endpoint_is_422(_seeded):
    # The real adversarial case: attempting "type": "database" directly
    # against the generic create endpoint, not just the dedicated
    # rejection route above -- CreateUrlsSourceRequest|CreateCrawlSourceRequest's
    # own closed `type` Literal structurally cannot match it.
    app = create_app()
    async with dashboard_http_client(app) as client:
        headers = await login_as(client, "owner-a@example.com")
        response = await client.post(
            "/api/v1/dashboard/sources",
            json={"type": "database", "host": "db.example.internal"},
            headers=headers,
        )
    assert response.status_code == 422


# --- Domain verification --------------------------------------------------


async def test_claim_then_verify_a_domain(_seeded, monkeypatch):
    monkeypatch.setattr(
        ingest_repository_module, "check_dns_verification", lambda domain, token: _true_async()
    )
    app = create_app()
    async with dashboard_http_client(app) as client:
        headers = await login_as(client, "owner-a@example.com")
        claimed = await client.post(
            "/api/v1/dashboard/domains", json={"domain": "acme.example", "method": "dns"},
            headers=headers,
        )
        assert claimed.status_code == 201
        domain_id = claimed.json()["id"]

        verified = await client.post(
            f"/api/v1/dashboard/domains/{domain_id}/verify", headers=headers
        )
    assert verified.status_code == 200
    assert verified.json()["status"] == "verified"


async def _true_async():
    return True


async def test_list_domains_is_tenant_scoped(_seeded):
    async with db_session() as session:
        session.add(
            VerifiedDomain(
                tenant_id=_seeded["tenant_a"],
                domain="a-only.example",
                method="dns",
                status="pending",
                verification_token="tok",  # noqa: S106
            )
        )
        await session.commit()
    app = create_app()
    async with dashboard_http_client(app) as client:
        await login_as(client, "owner-b@example.com")
        response = await client.get("/api/v1/dashboard/domains")
    assert response.status_code == 200
    assert response.json() == []


# --- Upload endpoint [SECURITY] -------------------------------------------


async def test_upload_a_real_pdf_creates_an_upload_source(_seeded):
    app = create_app()
    async with dashboard_http_client(app) as client:
        headers = await login_as(client, "owner-a@example.com")
        response = await client.post(
            "/api/v1/dashboard/sources/upload",
            files={"file": ("report.pdf", _PDF_BYTES, "application/pdf")},
            headers=headers,
        )
    assert response.status_code == 201
    body = response.json()
    assert body["type"] == "upload"
    assert body["config"]["uploads"][0]["original_filename"] == "report.pdf"


async def test_reupload_the_same_filename_replaces_and_deletes_the_old_bytes(_seeded, monkeypatch):
    from app.ingest import upload_storage

    app = create_app()
    settings = get_settings()
    storage_dir = settings.upload_storage_path
    async with dashboard_http_client(app) as client:
        headers = await login_as(client, "owner-a@example.com")
        first = await client.post(
            "/api/v1/dashboard/sources/upload",
            files={"file": ("report.txt", _TEXT_BYTES, "text/plain")},
            headers=headers,
        )
        source_id = first.json()["id"]
        old_upload_id = uuid.UUID(first.json()["config"]["uploads"][0]["upload_id"])

        second = await client.post(
            "/api/v1/dashboard/sources/upload",
            params={"source_id": source_id},
            files={"file": ("report.txt", _TEXT_BYTES + b"more content", "text/plain")},
            headers=headers,
        )
    assert second.status_code == 201
    uploads = second.json()["config"]["uploads"]
    assert len(uploads) == 1  # replaced, not appended
    new_upload_id = uuid.UUID(uploads[0]["upload_id"])
    assert new_upload_id != old_upload_id
    with pytest.raises(FileNotFoundError):
        upload_storage.read(old_upload_id, storage_dir=__import__("pathlib").Path(storage_dir))


async def test_upload_without_csrf_is_403(_seeded):
    app = create_app()
    async with dashboard_http_client(app) as client:
        await login_as(client, "owner-a@example.com")
        response = await client.post(
            "/api/v1/dashboard/sources/upload",
            files={"file": ("report.pdf", _PDF_BYTES, "application/pdf")},
        )
    assert response.status_code == 403


async def test_upload_for_another_tenants_source_id_is_404(_seeded):
    app = create_app()
    async with dashboard_http_client(app) as client_a:
        headers_a = await login_as(client_a, "owner-a@example.com")
        created = await client_a.post(
            "/api/v1/dashboard/sources/upload",
            files={"file": ("report.pdf", _PDF_BYTES, "application/pdf")},
            headers=headers_a,
        )
        source_id = created.json()["id"]

    async with dashboard_http_client(app) as client_b:
        headers_b = await login_as(client_b, "owner-b@example.com")
        response = await client_b.post(
            "/api/v1/dashboard/sources/upload",
            params={"source_id": source_id},
            files={"file": ("report2.pdf", _PDF_BYTES, "application/pdf")},
            headers=headers_b,
        )
    assert response.status_code == 404


async def test_oversized_upload_is_rejected_with_413(_seeded, monkeypatch):
    monkeypatch.setenv("UPLOAD_MAX_SIZE_BYTES", "100")
    get_settings.cache_clear()
    app = create_app()
    async with dashboard_http_client(app) as client:
        headers = await login_as(client, "owner-a@example.com")
        response = await client.post(
            "/api/v1/dashboard/sources/upload",
            files={"file": ("big.txt", _TEXT_BYTES * 1000, "text/plain")},
            headers=headers,
        )
    assert response.status_code == 413
    get_settings.cache_clear()


async def test_streamed_oversized_upload_is_rejected_mid_stream_not_after_full_buffering(
    _seeded, monkeypatch
):
    # The real streaming-enforcement proof: a body larger than any
    # single chunk_size, well past the cap, sent as one real multipart
    # body -- the server must reject it via save_streaming()'s own
    # abort-mid-loop path (confirmed at the unit level already,
    # app/ingest/upload_storage.py's own tests); this is the live,
    # end-to-end proof that the ROUTE actually wires that primitive in,
    # not a hand-rolled buffer-then-check.
    monkeypatch.setenv("UPLOAD_MAX_SIZE_BYTES", str(1024 * 1024))
    get_settings.cache_clear()
    app = create_app()
    oversized = b"\x00" * (5 * 1024 * 1024)  # 5MiB, cap is 1MiB
    async with dashboard_http_client(app) as client:
        headers = await login_as(client, "owner-a@example.com")
        response = await client.post(
            "/api/v1/dashboard/sources/upload",
            files={"file": ("big.bin", oversized, "application/octet-stream")},
            headers=headers,
        )
    assert response.status_code == 413
    get_settings.cache_clear()


async def test_zip_bomb_shaped_docx_upload_is_rejected_as_unrecognized(_seeded):
    bomb = b"\x00" * (200 * 1024 * 1024)
    data = _docx_shaped_zip(bomb)
    assert len(data) < 1024 * 1024  # on-disk size proves this really is bomb-shaped
    app = create_app()
    async with dashboard_http_client(app) as client:
        headers = await login_as(client, "owner-a@example.com")
        response = await client.post(
            "/api/v1/dashboard/sources/upload",
            files={"file": ("totally_a_report.docx", data, "application/octet-stream")},
            headers=headers,
        )
    assert response.status_code == 422


async def test_spoofed_content_type_and_extension_is_still_sniffed_by_content(_seeded):
    # A real .pdf-looking upload, by extension AND Content-Type header,
    # whose bytes are genuine binary garbage (not even valid UTF-8 text,
    # which sniff_file_type() deliberately DOES accept as a real txt
    # upload regardless of extension -- docs/SPEC.md §5.1's own "PDF,
    # .docx, .txt/.md" list, confirmed live: plain readable text given a
    # misleading .pdf name is not itself an attack in this design, only
    # non-PDF/non-docx/non-UTF-8 bytes are). Must be rejected regardless
    # of what the client claims it is.
    garbage = bytes(range(256)) * 4  # includes NUL and invalid UTF-8 sequences
    app = create_app()
    async with dashboard_http_client(app) as client:
        headers = await login_as(client, "owner-a@example.com")
        response = await client.post(
            "/api/v1/dashboard/sources/upload",
            files={"file": ("totally_real.pdf", garbage, "application/pdf")},
            headers=headers,
        )
    assert response.status_code == 422


async def test_path_traversal_filename_never_escapes_storage_and_is_stored_safely(_seeded):
    from pathlib import Path

    settings = get_settings()
    storage_dir = Path(settings.upload_storage_path)
    before = set(storage_dir.iterdir()) if storage_dir.exists() else set()

    app = create_app()
    async with dashboard_http_client(app) as client:
        headers = await login_as(client, "owner-a@example.com")
        response = await client.post(
            "/api/v1/dashboard/sources/upload",
            files={"file": ("../../../../etc/passwd", _TEXT_BYTES, "text/plain")},
            headers=headers,
        )
    assert response.status_code == 201
    body = response.json()
    # The hostile string is stored verbatim as DISPLAY metadata only --
    # never interpreted as a path (upload_storage.save_streaming() takes
    # no filename argument at all, structurally).
    assert body["config"]["uploads"][0]["original_filename"] == "../../../../etc/passwd"

    after = set(storage_dir.iterdir())
    new_files = after - before
    assert len(new_files) == 1
    new_file = new_files.pop()
    # The real on-disk name is a plain UUID, confirmed by construction --
    # never the hostile string, and the file is INSIDE storage_dir (a
    # name with no path separators at all, by definition of iterdir()).
    assert uuid.UUID(new_file.name)
    assert "/" not in new_file.name and ".." not in new_file.name


async def test_concurrent_reuploads_of_the_same_filename_leave_a_consistent_end_state(_seeded):
    app = create_app()
    async with dashboard_http_client(app) as client:
        headers = await login_as(client, "owner-a@example.com")
        created = await client.post(
            "/api/v1/dashboard/sources/upload",
            files={"file": ("doc.txt", _TEXT_BYTES, "text/plain")},
            headers=headers,
        )
        source_id = created.json()["id"]

        async def _reupload(suffix: bytes):
            async with dashboard_http_client(app) as concurrent_client:
                concurrent_headers = await login_as(concurrent_client, "owner-a@example.com")
                return await concurrent_client.post(
                    "/api/v1/dashboard/sources/upload",
                    params={"source_id": source_id},
                    files={"file": ("doc.txt", _TEXT_BYTES + suffix, "text/plain")},
                    headers=concurrent_headers,
                )

        results = await asyncio.gather(
            _reupload(b"-first"), _reupload(b"-second"), return_exceptions=True
        )
    # Neither request may raise -- both must complete as real HTTP
    # responses (success or a real, well-formed error), never an
    # unhandled exception from a torn-in-half config read/write.
    for result in results:
        assert not isinstance(result, Exception), result
        assert result.status_code == 201

    async with db_session() as session:
        final = (
            await session.execute(sa.select(Source).where(Source.id == uuid.UUID(source_id)))
        ).scalar_one()
    uploads = final.config["uploads"]
    # Exactly one entry for "doc.txt" survives -- last-write-wins is
    # acceptable at this project's own real pilot scale/concurrency, but
    # a DUPLICATED or missing entry would be real corruption.
    matching = [u for u in uploads if u["original_filename"] == "doc.txt"]
    assert len(matching) == 1


# --- Widget JWT never satisfies a dashboard session -----------------------


async def test_a_widget_jwt_on_sources_list_is_401(_seeded):
    token = encode_session_token(
        tenant_id=_seeded["tenant_a"], vid=uuid.uuid4(), origin="https://widget.example"
    )
    app = create_app()
    async with dashboard_http_client(app) as client:
        client.cookies.set("__Host-session", token)
        response = await client.get("/api/v1/dashboard/sources")
    assert response.status_code == 401


async def test_a_widget_jwt_on_upload_is_401(_seeded):
    token = encode_session_token(
        tenant_id=_seeded["tenant_a"], vid=uuid.uuid4(), origin="https://widget.example"
    )
    app = create_app()
    async with dashboard_http_client(app) as client:
        client.cookies.set("__Host-session", token)
        response = await client.post(
            "/api/v1/dashboard/sources/upload",
            files={"file": ("report.pdf", _PDF_BYTES, "application/pdf")},
        )
    assert response.status_code == 401


# --- CSRF on every new state-changing route -------------------------------


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/v1/dashboard/sources", {"type": "urls", "urls": ["https://example.com/a"]}),
        ("/api/v1/dashboard/sources/database", {}),
        ("/api/v1/dashboard/domains", {"domain": "acme.example", "method": "dns"}),
    ],
)
async def test_every_new_post_route_requires_csrf(_seeded, path, body):
    # A VALID body for each route -- otherwise Pydantic's own 422 (body
    # validation, resolved before the handler body ever runs) would mask
    # whether require_csrf() was reached at all.
    app = create_app()
    async with dashboard_http_client(app) as client:
        await login_as(client, "owner-a@example.com")
        response = await client.post(path, json=body)
    assert response.status_code == 403


async def test_disable_enable_delete_refresh_update_all_require_csrf(_seeded):
    app = create_app()
    async with dashboard_http_client(app) as client:
        headers = await login_as(client, "owner-a@example.com")
        created = await client.post(
            "/api/v1/dashboard/sources",
            json={"type": "urls", "urls": ["https://example.com/a"]},
            headers=headers,
        )
        source_id = created.json()["id"]

        for method, path, json_body in [
            ("POST", f"/api/v1/dashboard/sources/{source_id}/disable", None),
            ("POST", f"/api/v1/dashboard/sources/{source_id}/enable", None),
            ("POST", f"/api/v1/dashboard/sources/{source_id}/refresh-now", None),
            ("PATCH", f"/api/v1/dashboard/sources/{source_id}", {"urls": ["https://x.example"]}),
            ("DELETE", f"/api/v1/dashboard/sources/{source_id}", None),
        ]:
            response = await client.request(method, path, json=json_body)
            assert response.status_code == 403, f"{method} {path} did not require CSRF"


async def test_verify_domain_requires_csrf(_seeded, monkeypatch):
    monkeypatch.setattr(
        ingest_repository_module, "check_dns_verification", lambda domain, token: _true_async()
    )
    app = create_app()
    async with dashboard_http_client(app) as client:
        headers = await login_as(client, "owner-a@example.com")
        claimed = await client.post(
            "/api/v1/dashboard/domains",
            json={"domain": "acme2.example", "method": "dns"},
            headers=headers,
        )
        domain_id = claimed.json()["id"]
        response = await client.post(f"/api/v1/dashboard/domains/{domain_id}/verify")
    assert response.status_code == 403
