# backend/app/ingest/upload_adapter.py
# Task 2.7.c [SECURITY]: the per-upload ingest primitive -- ties together
# upload_storage.read() (2.7.a), sniff_file_type() (2.7.a/2.7.b), the four
# format extractors (extract_pdf/extract_docx/extract_text/extract_markdown,
# 2.4.d/e/f) and qdrant_writer.py's own finish_ingest() (which itself
# wraps embed_and_upsert(), 2.5.e -- shared with ingest_url() since the
# duplication check after 2.7.a/b/c). Mirrors ingest_url()'s own
# `session`/`client`/`tenant_id`/`source_id` as parameters, not an
# IngestRepository method, for the identical reason ingest_url() gives --
# IngestRepository has no Document methods, and this primitive's own real
# caller (2.7.d's job handler) doesn't naturally hold a tenant-scoped
# repository instance either.
#
# Everything from the content-hash comparison onward (the unchanged-
# shortcut, embed_and_upsert()'s own atomicity, document_id reuse, the
# `documents` row write) is qdrant_writer.py's own finish_ingest() --
# shared with ingest_url() (web_adapter.py, 2.6.c) since the duplication
# check after 2.7.a/b/c, once that structurally identical tail was
# confirmed across both real callers. See that function's own docstring
# for the full reasoning (including the IS NULL-producing `== None`
# unification for the url/file_name lookup). This function does ONLY its
# own genuinely distinct half: read, sniff, dispatch to the matching
# extractor, then delegate.
#
# `source_type` is hardcoded "upload" here, NOT a parameter -- unlike
# ingest_url(), which is shared by two job types (`urls`/`crawl`) and so
# takes `source_type` from its own caller, ingest_upload() has exactly one
# real caller (the `upload` job type) and exactly one possible value;
# adding a parameter with only one ever-passed value would be a parameter
# the task does not need (rule 11).
#
# `upload_id`/`storage_dir`/`max_docx_part_size_bytes` are all plain
# parameters, not read from Settings internally -- matching run_crawl()'s
# own `page_cap`/`delay_seconds` precedent (2.6.e): Settings is read ONCE,
# by the real caller (2.7.d's job handler), and passed down as plain
# values, so this primitive stays testable with any value a test wants,
# with no Settings/environment coupling at all.
#
# Corrupt-file handling, matching ingest_url()'s own "expected per-item
# failures don't abort the caller" philosophy: each format's own
# definitive exception type(s) are caught and converted to
# UploadIngestResult(status="failed", ...) -- never propagated. A
# FileNotFoundError from upload_storage.read() is deliberately NOT caught
# here: that means the storage layer does not have the bytes this job
# was told exist, a data-integrity/infrastructure problem (something is
# wrong with the job's own upload_id, not with the uploaded file's own
# content), matching embed_and_upsert()'s own "an infrastructure-level
# failure propagates uncaught, for the job-level retry/backoff to handle
# once" precedent exactly -- not a per-file outcome to paper over.
#
# TWO real findings made live while building THIS primitive, beyond what
# 2.7.b's own re-confirmation covered (2.7.b only re-ran the EXISTING
# 2.4.d/e/f tests, which never tried these particular input shapes) --
# recorded here, not silently patched over, and NOT fixed by editing
# extract_docx.py/extract_text.py's own logic (only their header comments
# were pre-approved for this task; a new Open marker covers the rest):
#
# (1) sniff_file_type()'s OWN txt/md branch already decodes `data` as
# UTF-8 to confirm it's plain text (2.7.a) -- the IDENTICAL check extract_
# text()'s/extract_markdown()'s own `_as_text()` repeats internally.
# Confirmed live: any bytes that reach this function already classified
# "txt"/"md" have therefore ALREADY been proven UTF-8-decodable, so
# UnicodeDecodeError from the dispatched extractor is structurally
# UNREACHABLE through this real call path -- invalid-UTF-8 content is
# always caught upstream, at the sniffing step, and returned as
# status="skipped", never status="failed". Kept in the dispatch table
# below anyway (cheap, correct, guards against sniff_file_type()'s own
# check ever changing without this code following along), but no test
# claims to reach it via ingest_upload() -- that would test something
# that cannot happen.
#
# (2) A docx-shaped ZIP that passes sniff_file_type()'s own check (valid
# zip, `word/document.xml` present, under the size ceiling) can still be
# a genuinely corrupt .docx in a way sniffing never looks at -- that
# check only confirms the MEMBER NAME exists, never that its own content
# is valid, or that every OTHER part a real OPC package needs is present.
# Confirmed live with two real constructions: a zip containing ONLY
# `word/document.xml` (missing `[Content_Types].xml`/`_rels/.rels`)
# raises a bare `KeyError` from python-docx's own internal zip-member
# lookup, NOT `PackageNotFoundError` (extract_docx.py's own header
# comment claims that type covers "certain other package-level
# failures" -- confirmed INCOMPLETE, not assumed accurate); a real,
# complete .docx (built via python-docx itself) with its own `word/
# document.xml` member's CONTENT replaced by invalid XML raises `lxml.
# etree.XMLSyntaxError`, a third, different type neither extract_docx.py's
# own comment nor 2.4.e's own tests ever named. Both are real, reachable
# failure modes for a sniffed-as-docx upload, so both are added to THIS
# module's own dispatch table below -- narrowly scoped to this one call
# site (the only code here that could plausibly raise either), not a
# change to extract_docx.py itself.
#
# documents row: keyed by (source_id, file_name), `url` always None --
# confirmed against 2.4.a's own schema: `ix_documents_source_id_file_
# name_unique` is a partial unique index on (source_id, file_name) WHERE
# file_name IS NOT NULL, the exact mirror of `ix_documents_source_id_url_
# unique`'s own shape for `url`. finish_ingest()'s own single-shape
# lookup (`url=None, file_name=original_filename`) relies on exactly
# this guarantee.
#
# A real gap found while building this, flagged rather than silently
# left implicit: the mapping from a `documents` row to "which storage_id
# currently holds its original bytes" is NOT persisted anywhere in this
# schema (Document has no such column) -- the CURRENT upload_id for a
# given upload source is expected to live in `sources.config` (e.g.
# `{"upload_id": "<uuid>"}`), mirroring `urls`/`crawl` sources' own
# config-driven shape exactly, read by 2.7.d's own job handler, not by
# this primitive. This means a tenant re-uploading a changed file under
# the same `source_id` (a NEW upload_id, saved separately) leaves the
# PREVIOUS upload_id's own on-disk bytes orphaned, with nothing yet
# deleting them -- the identical already-acknowledged class of gap as the
# Step-2.9-owned "stale Qdrant points on content shrink" marker (2.5.d),
# now also true of on-disk upload storage. Recorded as a new Open marker,
# not fixed here (out of scope: deleting the previous upload_id needs
# whoever manages a source's own config transition to know there WAS a
# previous one, which this per-call primitive has no way to know).
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from zipfile import BadZipFile

from docx.opc.exceptions import PackageNotFoundError
from lxml.etree import XMLSyntaxError
from pypdf.errors import PyPdfError
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import DateTimeClock
from app.ingest import upload_storage
from app.ingest.extract_docx import extract_docx
from app.ingest.extract_html import ExtractedContent
from app.ingest.extract_pdf import extract_pdf
from app.ingest.extract_text import extract_markdown, extract_text
from app.ingest.qdrant_writer import finish_ingest
from app.ingest.upload_sniff import UploadKind, sniff_file_type
from app.ingest.web_adapter import EMBEDDING_VERSION

_SOURCE_TYPE = "upload"

# One entry per sniff_file_type() outcome that is ever dispatched to a
# real extractor ("unknown" is handled separately, before this table is
# ever consulted -- it has no extractor at all). Each exception tuple is
# exactly what 2.7.b re-confirmed live for that format, today, not
# trusted from memory -- see this module's own header comment and the
# Step 2.7.c decision log entry.
_ExtractorEntry = tuple[Callable[[bytes], ExtractedContent], tuple[type[Exception], ...]]
_DISPATCH: dict[UploadKind, _ExtractorEntry] = {
    "pdf": (extract_pdf, (PyPdfError,)),
    "docx": (extract_docx, (BadZipFile, PackageNotFoundError, KeyError, XMLSyntaxError)),
    "txt": (extract_text, (UnicodeDecodeError,)),
    "md": (extract_markdown, (UnicodeDecodeError,)),
}


@dataclass(frozen=True)
class UploadIngestResult:
    """Task 2.7.c. An upload-specific equivalent of web_adapter.py's own
    IngestResult (2.6.c), NOT that type reused directly -- decided at
    2.7.b: IngestResult's own docstring enumerates URL-fetch-specific
    meanings verbatim ("the URL's own domain is not verified", "fetch_
    with_redirects() itself failed"), which would not honestly describe
    an upload's own outcomes. Same shape, same plain-string-status style
    (no enum, matching this codebase's own established convention) --
    one of:
      - "ingested": content was new or changed; chunked, embedded and
        upserted for real.
      - "unchanged": content_hash matched the stored row; re-chunking/
        re-embedding was SKIPPED entirely -- the same idempotency
        property ingest_url() proves for URLs, now proven for uploads.
      - "skipped": sniff_file_type() returned "unknown" -- the stored
        bytes don't match any supported format. NO extractor was ever
        attempted, mirroring ingest_url()'s own "skipped" = no fetch
        attempted for an unverified domain.
      - "failed": the sniffed format's own matching extractor raised its
        already-established, format-specific exception on malformed/
        corrupt/wrong-encoding bytes (re-confirmed live at 2.7.b) --
        `reason` names the exception. Deliberately NOT used for a
        FileNotFoundError from upload_storage.read() -- see this
        module's own header comment for why that propagates uncaught
        instead.
    """

    status: str
    document_id: uuid.UUID | None = None
    reason: str | None = None


async def ingest_upload(
    session: AsyncSession,
    client: AsyncQdrantClient,
    collection_name: str,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    upload_id: uuid.UUID,
    original_filename: str,
    storage_dir: Path,
    max_docx_part_size_bytes: int,
    clock: DateTimeClock = lambda: datetime.now(UTC),
) -> UploadIngestResult:
    """Reads, sniffs, extracts and (if changed) embeds+upserts one stored
    upload's own content for one tenant, creating or updating its
    `documents` row (keyed by `file_name`, never `url`). See this
    module's own header comment for the full design reasoning.
    """
    data = upload_storage.read(upload_id, storage_dir=storage_dir)

    kind = sniff_file_type(
        data, original_filename, max_docx_part_size_bytes=max_docx_part_size_bytes
    )
    if kind == "unknown":
        return UploadIngestResult(status="skipped", reason="unrecognized file type")

    extractor, expected_exceptions = _DISPATCH[kind]
    try:
        content = extractor(data)
    except expected_exceptions as exc:
        return UploadIngestResult(status="failed", reason=f"{type(exc).__name__}: {exc}")

    status, document_id = await finish_ingest(
        session,
        client,
        collection_name,
        content,
        tenant_id=tenant_id,
        source_id=source_id,
        source_type=_SOURCE_TYPE,
        url=None,
        file_name=original_filename,
        embedding_version=EMBEDDING_VERSION,
        clock=clock,
    )
    return UploadIngestResult(status=status, document_id=document_id)
