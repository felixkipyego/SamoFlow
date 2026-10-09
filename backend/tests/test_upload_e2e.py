# backend/tests/test_upload_e2e.py
# Task 2.7.e: the full end-to-end live test matrix for the upload adapter
# -- the proof that everything built across 2.7.a-d holds together through
# the REAL path (upload_storage.py -> upload_sniff.py -> ingest_upload() ->
# finish_ingest() -> embed_and_upsert() -> handle_ingest_upload() ->
# worker.run()), not a restatement of any single task's own unit-level
# proof. A dedicated new file, not an addition to test_job_handlers.py's
# own already-completed "Task 2.7.d" section (rule 2 -- do not modify a
# completed task) -- every helper this file needs that 2.7.d already built
# (_prepare_env(), _seed_tenant_upload_source()) is imported from there
# instead of duplicated, matching this project's own established cross-
# test-file-import precedent (test_env_consistency.py from test_env_
# example.py; test_qdrant.py from test_qdrant_collection.py; test_job_
# handlers.py's own import of _docx_with_invalid_xml_content() from
# test_upload_adapter.py).
#
# Five of the six required scenarios run through the REAL worker.py loop
# (enqueue() -> run() -> claim_next_job() -> dispatch -> handle_ingest_
# upload() -> ingest_upload() -> finish_ingest() -> embed_and_upsert() ->
# mark_job_succeeded()/mark_job_failed()), live against BOTH real services
# together (live_test_services(), tests/conftest.py) -- matching test_job_
# handlers.py's own established methodology exactly, never calling handle_
# ingest_upload()/ingest_upload() directly.
#
# The SIXTH (oversized-file rejection) deliberately does NOT run through
# worker.run() -- confirmed live, by grepping app/ for every real reference
# to Settings.upload_max_size_bytes, that it is used in exactly ONE place:
# the `max_size_bytes` argument to upload_storage.save(). save() is the
# upload-RECEIVING boundary (the not-yet-built Phase 6 endpoint) -- it runs
# strictly BEFORE a sources.config entry or a job can ever exist for that
# upload, and nothing in ingest_upload()/handle_ingest_upload() re-checks
# size during job processing. There is therefore no "job item" for an
# oversized file to fail AS: save() never lets its bytes reach disk, a
# config entry, or an enqueue() call in the first place. Confirmed with
# the user directly before building this (2026-10-09): this is a real,
# correct architectural property, not a gap -- the test below proves the
# REAL boundary instead, using the REAL, production-configured Settings.
# upload_max_size_bytes default (20MB), not test_upload_storage.py's own
# small synthetic ceiling (999/1000 bytes, 2.7.a).
#
# (1)'s own "real search_points() call" is built as a real client.query_
# points() call -- this codebase's own actual mechanism (confirmed by grep:
# "search_points" appears nowhere under app/ or tests/; test_qdrant_
# isolation.py's own 1.3.d precedent already established query_points() as
# the real primitive, and test_qdrant_read_path_guard.py's own header
# comment states plainly that the old REST search/recommend operations are
# "unified into query_points()" in this client version) -- sparse-only,
# not hybrid/dense: patch_embed_dense() (tests/conftest.py) returns an
# identical all-zero vector for every chunk regardless of content (2.5's
# own established "values don't matter, only point-count/determinism
# matters" precedent), so a dense query cannot discriminate between
# documents at all; embed_sparse() is the real, local, unmocked BM25
# computation (fastembed, no network call), so a sparse query built from a
# verbatim snippet of each fixture's own real text is a genuine retrieval
# proof, not a tautology. Every snippet below was confirmed live (see this
# task's own build process) to land inside exactly one fixture's own real
# post-chunking text, with zero cross-fixture collision among the other
# three.
import asyncio
import uuid

import pytest
import sqlalchemy as sa
from qdrant_client.http.models import SparseVector

from app import qdrant
from app.config import get_settings
from app.ingest import qdrant_writer as qdrant_writer_module
from app.ingest import upload_adapter as upload_adapter_module
from app.ingest import upload_storage
from app.ingest.embedding import embed_sparse
from app.ingest.models import Document
from app.ingest.repository import IngestRepository
from app.ingest.upload_storage import UploadTooLargeError
from app.worker import run
from tests.conftest import (
    VALID_ENV,
    _fetch_job,
    db_session,
    read_docx_fixture,
    read_markdown_fixture,
    read_pdf_fixture,
    read_text_fixture,
    set_valid_env,
)
from tests.test_job_handlers import _prepare_env, _seed_tenant_upload_source
from tests.test_upload_adapter import _docx_with_invalid_xml_content
from tests.test_upload_sniff import _docx_shaped_zip

# --- (1): all four formats in one job, each really indexed and findable ---

_SEARCH_SNIPPETS = {
    "report.pdf": "a flattening test can confirm content",
    "report.docx": "carrying a genuine sentence of its own real content",
    "notes.txt": "produces a single flattened block",
    "notes.md": "a bare #hashtag with no space after the hash",
}


async def _documents_for(source_id: uuid.UUID) -> list[Document]:
    async with db_session() as session:
        return (
            (await session.execute(sa.select(Document).where(Document.source_id == source_id)))
            .scalars()
            .all()
        )


async def test_all_four_formats_ingest_in_one_job_and_content_is_findable_via_real_search(
    monkeypatch, live_test_services, tmp_path
):
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
    assert job.error is None

    documents = await _documents_for(ids["source_id"])
    assert {doc.file_name for doc in documents} == set(_SEARCH_SNIPPETS)
    assert all(doc.status == "extracted" for doc in documents)

    # The real search proof: a query_points() call per format, using a
    # real sparse vector built from that format's own real text -- not
    # scroll() (existence only), and not the all-zero dense vector
    # (content-blind). Each snippet must rank among the top results AND
    # the winning point's own payload must really contain it.
    for file_name, snippet in _SEARCH_SNIPPETS.items():
        [sparse] = await embed_sparse([snippet])
        result = await client.query_points(
            collection_name=qdrant.COLLECTION_NAME,
            query=SparseVector(indices=sparse.indices, values=sparse.values),
            using=qdrant.SPARSE_VECTOR_NAME,
            query_filter=qdrant.tenant_filter(ids["tenant_id"]),
            limit=5,
            with_payload=True,
        )
        points = result.points
        assert points, f"no search results at all for {file_name}'s own real text"
        matches = [p for p in points if p.payload.get("file_name") == file_name]
        assert matches, f"{file_name} did not rank among the top results for its own real text"
        assert snippet in matches[0].payload["text"]


# --- (2): oversized rejection, proven at the real boundary it actually ---
# --- occurs at -- NOT through worker.run(). See this file's own header --
# --- comment for why.                                                  ---


def test_an_oversized_upload_is_rejected_at_the_real_upload_receiving_boundary(
    monkeypatch, tmp_path
):
    set_valid_env(monkeypatch, VALID_ENV)
    limit = get_settings().upload_max_size_bytes
    data = b"\x00" * (limit + 1)

    with pytest.raises(UploadTooLargeError):
        upload_storage.save(data, storage_dir=tmp_path, max_size_bytes=limit)

    # Nothing was written -- confirmed on disk, not merely that the right
    # exception type was raised. There is no sources.config entry and no
    # job for this attempt either: save() is called before either can
    # exist, so there is no further "through the worker loop" step to
    # prove for this scenario (see this file's own header comment).
    assert list(tmp_path.iterdir()) == []


# --- (3): the 2.7.b zip-bomb-shaped docx, rejected through the full job ---
# --- path this time, not upload_sniff.py in isolation.                 ---


async def test_a_zip_bomb_shaped_docx_is_rejected_through_the_real_worker_loop(
    monkeypatch, live_test_services, tmp_path
):
    client = await _prepare_env(
        monkeypatch, live_test_services, UPLOAD_STORAGE_PATH=str(tmp_path)
    )

    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("extract_docx() was called for a zip-bomb-shaped upload")

    monkeypatch.setattr(upload_adapter_module, "extract_docx", _must_not_be_called)

    bomb = b"\x00" * (200 * 1024 * 1024)
    data = _docx_shaped_zip(bomb)
    assert len(data) < 1024 * 1024  # really is bomb-shaped: tiny on disk

    ids = await _seed_tenant_upload_source(tmp_path, [(data, "totally_a_docx.docx")])
    await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=10)

    job = await _fetch_job(ids["job_id"])
    assert job.status == "succeeded"  # a rejected item is not a job failure
    assert job.error is None

    assert await _documents_for(ids["source_id"]) == []

    records, _ = await client.scroll(
        collection_name=qdrant.COLLECTION_NAME,
        scroll_filter=qdrant.tenant_filter(ids["tenant_id"]),
        limit=10,
    )
    assert records == []


# --- (4): sniff_file_type() governs by content, never the claimed name ---


async def test_a_mismatched_extension_is_governed_by_real_content_not_the_claimed_name(
    monkeypatch, live_test_services, tmp_path
):
    client = await _prepare_env(
        monkeypatch, live_test_services, UPLOAD_STORAGE_PATH=str(tmp_path)
    )

    pdf_bytes_under_txt_name = read_pdf_fixture("multi_page.pdf")
    text_bytes_under_pdf_name = read_text_fixture("sample.txt").encode("utf-8")

    ids = await _seed_tenant_upload_source(
        tmp_path,
        [
            (pdf_bytes_under_txt_name, "disguised.txt"),
            (text_bytes_under_pdf_name, "disguised.pdf"),
        ],
    )

    await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=10)

    job = await _fetch_job(ids["job_id"])
    assert job.status == "succeeded"
    assert job.error is None

    documents = {doc.file_name: doc for doc in await _documents_for(ids["source_id"])}
    assert set(documents) == {"disguised.txt", "disguised.pdf"}
    assert all(doc.status == "extracted" for doc in documents.values())

    records, _ = await client.scroll(
        collection_name=qdrant.COLLECTION_NAME,
        scroll_filter=qdrant.tenant_filter(ids["tenant_id"]),
        limit=20,
        with_payload=True,
    )
    by_file: dict[str, list[str]] = {}
    for record in records:
        by_file.setdefault(record.payload["file_name"], []).append(record.payload["text"])

    # The ".txt"-named file's own real content really is the PDF's own
    # text -- sniffed and extracted AS pdf, governed by content, never the
    # claimed ".txt" extension (pdf/docx detection never consults the
    # filename at all, 2.7.a).
    assert any("flattening test" in text for text in by_file["disguised.txt"])
    # The ".pdf"-named file's own real content really is the plain-text
    # fixture's own text -- it is neither a real PDF nor a real zip, so it
    # falls straight through to the txt/md content check and is extracted
    # as plain text despite its claimed ".pdf" extension.
    assert any("flattened block" in text for text in by_file["disguised.pdf"])


# --- (5): idempotency proven across two separate, real job runs ---------


async def test_the_same_upload_id_ingested_twice_across_two_real_jobs_stays_idempotent(
    monkeypatch, live_test_services, tmp_path
):
    # Distinct from test_upload_adapter.py's own test_rerun_with_identical_
    # content_skips_rechunk_and_reembed (2.7.c): that test calls ingest_
    # upload() twice directly in the same process. This proves the SAME
    # property across two genuinely independent enqueue()->run() cycles --
    # the realistic shape of a scheduled re-check or a retried job hitting
    # the same upload again -- and additionally confirms no duplicate
    # points land in Qdrant at the collection level, not just that the
    # second UploadIngestResult says "unchanged".
    client = await _prepare_env(
        monkeypatch, live_test_services, UPLOAD_STORAGE_PATH=str(tmp_path)
    )

    data = read_pdf_fixture("multi_page.pdf")
    ids = await _seed_tenant_upload_source(tmp_path, [(data, "report.pdf")])

    await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=10)
    first_job = await _fetch_job(ids["job_id"])
    assert first_job.status == "succeeded"

    first_count = await client.count(collection_name=qdrant.COLLECTION_NAME)

    calls: list[int] = []
    original = qdrant_writer_module.embed_and_upsert

    async def _counting(*args, **kwargs):
        calls.append(1)
        return await original(*args, **kwargs)

    monkeypatch.setattr(qdrant_writer_module, "embed_and_upsert", _counting)

    # A second, real job for the same source -- sources.config is
    # unchanged (same upload_id, same original_filename).
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_id"], session=session)
        second_job = await repo.enqueue(
            job_type="ingest_upload", source_id=ids["source_id"], payload={}
        )
        await session.commit()
        second_job_id = second_job.id

    await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=10)

    second_job_row = await _fetch_job(second_job_id)
    assert second_job_row.status == "succeeded"
    assert second_job_row.error is None
    assert calls == []  # embed_and_upsert() was never called on the re-run

    second_count = await client.count(collection_name=qdrant.COLLECTION_NAME)
    assert second_count.count == first_count.count  # no duplicate points

    documents = await _documents_for(ids["source_id"])
    assert len(documents) == 1  # one document row, not two


# --- (6): both named corrupt-docx exception types, caught cleanly -------


def _docx_missing_required_opc_parts() -> bytes:
    # Passes sniff_file_type() (word/document.xml is present, well under
    # the size ceiling) but is missing every OTHER real OPC package part
    # ([Content_Types].xml, _rels/.rels, etc.) python-docx's own Document()
    # needs. Reuses test_upload_sniff.py's own _docx_shaped_zip() helper --
    # already built for exactly this "only word/document.xml" shape --
    # rather than duplicating its construction. Confirmed live before
    # writing this test (not assumed from the 2.7.c decision log alone):
    # python-docx's own internal zip-member lookup raises a bare KeyError
    # ("There is no item named '[Content_Types].xml' in the archive") for
    # this exact shape. This is the first real regression test for that
    # specific claim -- confirmed by grep that "KeyError" appeared nowhere
    # under tests/ before this file.
    return _docx_shaped_zip(b"<w:document/>")


async def test_both_named_corrupt_docx_exception_types_are_caught_cleanly_through_the_worker_loop(
    monkeypatch, live_test_services, tmp_path
):
    client = await _prepare_env(
        monkeypatch, live_test_services, UPLOAD_STORAGE_PATH=str(tmp_path)
    )

    uploads = [
        (_docx_missing_required_opc_parts(), "missing_parts.docx"),
        (_docx_with_invalid_xml_content(), "invalid_xml.docx"),
    ]
    ids = await _seed_tenant_upload_source(tmp_path, uploads)

    await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=10)

    job = await _fetch_job(ids["job_id"])
    assert job.status == "succeeded"  # neither corrupt item crashes the job
    assert job.error is None

    assert await _documents_for(ids["source_id"]) == []  # neither leaves a row

    records, _ = await client.scroll(
        collection_name=qdrant.COLLECTION_NAME,
        scroll_filter=qdrant.tenant_filter(ids["tenant_id"]),
        limit=10,
    )
    assert records == []
