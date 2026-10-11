# backend/app/ingest/reconcile.py
# Task 2.9.f [SECURITY]: the nightly reconcile's own detection+deletion
# primitives -- resolves the 2.5.d-recorded content-shrink marker
# (PROJECT_SPEC.md §9): re-running ingestion for a document that now
# produces FEWER chunks than before leaves the old, excess chunk_index
# positions as orphaned, stale points in Qdrant, since `upsert_points()`
# only ever upserts the points it is given.
#
# Algorithm, decided with the user directly before building (not the
# originally-proposed shape): NOT "compute expected point_ids via
# build_point_id() and diff against Qdrant's actual ids" -- that needs a
# chunk COUNT to enumerate chunk_index values, and no chunk count is
# stored anywhere in Postgres (confirmed live: Document has no
# chunk_count column), only Qdrant's own current points know it, which is
# exactly the thing that might be wrong after a shrink. Computing
# "expected" that way is circular.
#
# The real mechanism: scroll a tenant's own Qdrant points (not walk
# Postgres first), and for each point, ask whether Postgres JUSTIFIES it:
#   (a) does a `Document` row with this `doc_id` exist for this tenant at
#       all -- if not, orphaned (a stray/unrelated point, or a document
#       deleted through some path that left Qdrant behind).
#   (b) if it exists, does the point's own `content_hash` payload field
#       (written once per document, Task 2.5.c's own build_payload(),
#       and identical across every one of that document's chunks) match
#       `documents.content_hash`'s CURRENT value -- if not, this point is
#       a leftover from a SUPERSEDED version of the document's content,
#       exactly the content-shrink case.
# No chunk-count prediction, no re-embedding, no re-chunking -- both
# signals already exist in both stores today.
#
# This file is deliberately separate from app/ingest/qdrant_writer.py,
# not an addition to it: qdrant_writer.py is write-only by design
# (test_qdrant_read_path_guard.py's own READ_ALLOWLIST never includes
# it, confirmed live before this task started -- the write path must
# never need read access). This module's whole job is reading Qdrant
# (client.scroll(), a READ-classified method) to compare against
# Postgres, so it is the first module OUTSIDE app/retrieval/service.py
# to need READ_ALLOWLIST membership -- added there (and to
# CLIENT_ACCESS_ALLOWLIST) instead of weakening that boundary on the
# write side.
#
# Hidden (soft-disabled, Task 2.9.e) points need NO special-casing here,
# confirmed by construction: a disabled source's points still have a
# real, unchanged `Document` row with a matching `content_hash` -- they
# are judged justified exactly like any other still-valid point. This
# function never reads `HIDDEN_FIELD`/`source.enabled` at all; "orphaned
# vs. legitimately absent" and "hidden vs. visible" are different
# questions, and this module only ever answers the first one.
#
# Blast radius, decided with the user directly: real deletion (not a
# dry-run-first mode), Qdrant before any other side effect -- matching
# Task 2.9.d/e's own established "the worse failure mode is content
# staying answerable when it shouldn't be" ordering exactly, since an
# orphaned point IS exactly content that should not still be answerable.
# Every deletion is logged (tenant_id, document_id, point count) per
# `_cleanup_stale_crawl_documents()`'s own established logging
# precedent (Task 2.9.d) -- the agreed-on safety net in place of a
# second, parallel dry-run code path.
import logging
import uuid

from qdrant_client import AsyncQdrantClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingest.models import Document
from app.qdrant import tenant_filter

logger = logging.getLogger(__name__)

# Qdrant's own real scroll page size for this pass -- large enough that a
# realistic tenant's own point set reconciles in a small, bounded number
# of round trips, small enough to never hold an unbounded result set in
# memory at once. Not Settings-configurable: this is a pure internal
# pagination mechanic with no tenant-facing meaning of its own, matching
# `docx_max_part_size_bytes`'s own "Settings-free internal constant"
# treatment where no caller has ever needed to override it.
_SCROLL_PAGE_SIZE = 256


async def find_orphaned_points(
    session: AsyncSession,
    client: AsyncQdrantClient,
    collection_name: str,
    *,
    tenant_id: uuid.UUID,
) -> list[tuple[str, uuid.UUID | None]]:
    """Scrolls every one of `tenant_id`'s own Qdrant points (paginated via
    `client.scroll()`'s own `offset`, looping until it returns `None` --
    confirmed live, not assumed, that `scroll()` returns `(records,
    next_page_offset)` and accepts that same value back as `offset`) and
    returns `(point_id, document_id)` for every one judged orphaned --
    `document_id` is `None` for a point with no resolvable `doc_id`
    payload field at all (structurally shouldn't happen, since
    `build_payload()` always writes one, but a point malformed enough to
    lack it cannot be matched to anything, so it is orphaned by
    definition, matching this module's own header comment).

    Caches each distinct `doc_id` encountered against its own ONE lookup
    of `documents.content_hash` (`tenant_id`-scoped) -- a multi-chunk
    document has many points sharing the same `doc_id`, and re-querying
    Postgres once per POINT rather than once per DOCUMENT would be a
    real, avoidable N-queries-per-document cost for no benefit (rule 11).

    Detection only -- never deletes, never mutates anything. Returns
    point ids as plain `str`, not `uuid.UUID`: confirmed live that
    `client.scroll()`'s own `Record.id` already comes back as the
    canonical lowercase string form even for a point upserted with a
    `uuid.UUID` id, and that this exact string round-trips correctly as
    `client.delete()`'s own `points_selector` with no conversion needed.
    """
    orphaned: list[tuple[str, uuid.UUID | None]] = []
    content_hash_by_doc_id: dict[uuid.UUID, str | None] = {}

    next_offset = None
    while True:
        records, next_offset = await client.scroll(
            collection_name=collection_name,
            scroll_filter=tenant_filter(tenant_id),
            limit=_SCROLL_PAGE_SIZE,
            offset=next_offset,
            with_payload=True,
            with_vectors=False,
        )
        for record in records:
            raw_doc_id = (record.payload or {}).get("doc_id")
            if raw_doc_id is None:
                orphaned.append((record.id, None))
                continue
            doc_id = uuid.UUID(raw_doc_id)
            if doc_id not in content_hash_by_doc_id:
                content_hash_by_doc_id[doc_id] = (
                    await session.execute(
                        select(Document.content_hash).where(
                            Document.id == doc_id, Document.tenant_id == tenant_id
                        )
                    )
                ).scalar_one_or_none()
            current_hash = content_hash_by_doc_id[doc_id]
            if current_hash is None:
                orphaned.append((record.id, doc_id))  # no matching Document row at all
            elif record.payload.get("content_hash") != current_hash:
                orphaned.append((record.id, doc_id))  # leftover from a superseded version
        if next_offset is None:
            break
    return orphaned


async def reconcile_tenant(
    session: AsyncSession,
    client: AsyncQdrantClient,
    collection_name: str,
    *,
    tenant_id: uuid.UUID,
) -> int:
    """Finds and hard-deletes every orphaned point for `tenant_id`
    (`find_orphaned_points()` above), then the real Qdrant delete --
    skipped entirely when nothing is orphaned, matching `upsert_points()`'s
    own established "empty input is a clean no-op, no network call"
    precedent (Task 2.5.d), not because an empty `points_selector` list is
    confirmed to error the way an empty upsert list is, but because there
    is nothing a real call could usefully do here either way. Returns the
    number of points deleted, for logging/testing, matching
    `ensure_scheduler_ticks_seeded()`'s own "return what happened"
    precedent (Task 2.9.a).

    Logged per DOCUMENT, not per point (tenant_id, document_id, point
    count) -- matching `_cleanup_stale_crawl_documents()`'s own
    established per-deletion logging precedent (Task 2.9.d), the agreed
    safety net for this pass's own real, unsupervised blast radius, in
    place of a separate dry-run mode.
    """
    orphaned = await find_orphaned_points(session, client, collection_name, tenant_id=tenant_id)
    if not orphaned:
        return 0

    point_ids = [point_id for point_id, _ in orphaned]
    await client.delete(collection_name=collection_name, points_selector=point_ids)

    counts_by_document: dict[uuid.UUID | None, int] = {}
    for _, document_id in orphaned:
        counts_by_document[document_id] = counts_by_document.get(document_id, 0) + 1
    for document_id, count in counts_by_document.items():
        logger.info(
            "reconcile_tenant: tenant %s deleted %d orphaned point(s) for document %s",
            tenant_id,
            count,
            document_id,
        )
    return len(point_ids)
