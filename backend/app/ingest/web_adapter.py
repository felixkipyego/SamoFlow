# backend/app/ingest/web_adapter.py
# Task 2.6.c: the per-URL ingest primitive -- the first real caller tying
# together fetch_with_redirects() (2.3), extract_html() (2.4.b),
# compute_content_hash() (2.4.c) and embed_and_upsert() (2.5.e). [SECURITY]
# rigor throughout: this is the first code path that fetches a tenant-
# supplied URL and writes its content into the shared, multi-tenant Qdrant
# collection, depending directly on 2.3's own SSRF guard and 1.3.d's own
# tenant-isolation guarantee holding.
#
# Shared by BOTH the `urls` (2.6.d) and `crawl` (2.6.e) adapters -- one
# primitive underneath two job types, per the Step 2.6 breakdown's own
# decision (c). Deliberately a plain function taking `session`/`client`/
# `tenant_id`/`source_id` as parameters, not an IngestRepository method:
# `IngestRepository` has no Document/Source methods at all yet (confirmed
# live, by grep, before building -- this task does not add any either,
# matching its own file list), and this primitive's own real callers
# (2.6.d/e's job handlers) don't naturally hold a tenant-scoped repository
# instance either, matching revoke_domain()'s own "plain function, session
# passed in" precedent for the identical reason.
#
# A design-wording issue found and corrected before building, not followed
# literally: the task's own design named "chunk_text() -> embed_and_upsert()"
# as two separate steps. Reading embed_and_upsert()'s own real source
# (2.5.e) shows it already calls chunk_text() AND compute_content_hash()
# internally -- calling chunk_text() again here would be pure waste,
# discarding real chunking work, and passing its output to embed_and_
# upsert() would not even match that function's own signature (it takes
# `content: ExtractedContent`, not `list[Chunk]`). A second, direct call
# to compute_content_hash() over the identical, already-in-memory
# ExtractedContent IS still made -- not real duplicated work (a pure,
# cheap, deterministic function producing the byte-identical result) --
# for the content-hash COMPARISON against the stored row, a concern
# embed_and_upsert() has no reason to know about.
#
# Duplication check after 2.7.a/b/c: this function's own tail -- the
# content-hash comparison above, the unchanged-shortcut, document_id
# resolution, the embed_and_upsert() call, and the create/update
# Document write -- was confirmed structurally IDENTICAL to Task 2.7.c's
# own ingest_upload() (upload_adapter.py), once that second real caller
# existed, and extracted into qdrant_writer.py's own finish_ingest() --
# see that function's own docstring for the full reasoning (including
# the IS NULL-producing `== None` unification for the url/file_name
# lookup). ingest_url() below now does ONLY its own genuinely distinct
# half (domain verification, fetch, extract) before delegating.
#
# Duplication check after 2.6.a/b/c, item C1 [SECURITY] fix: a real gap
# found, not assumed safe -- ingest_url() originally checked
# verified_domains only against the URL's ORIGINAL host, before calling
# fetch_with_redirects(), which re-validates SSRF-safety (the IP) per hop
# but has no concept of tenant domain verification at all. A redirect to
# a different, unverified-but-IP-safe host would have had that host's
# own content fetched and persisted under this tenant's own `documents`
# row. Fixed by widening fetch_with_redirects()'s own return type
# (safe_fetch.py's new FetchResult) to expose the final hop's URL, and
# re-checking verified_domains against it too, before any content is
# chunked/embedded/persisted -- see ingest_url()'s own docstring for the
# full reasoning, including why only the original and final hosts are
# checked, never every intermediate redirect hop.
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from qdrant_client import AsyncQdrantClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import DateTimeClock
from app.ingest.extract_html import extract_html
from app.ingest.models import VerifiedDomain
from app.ingest.qdrant_writer import finish_ingest
from app.ingest.safe_fetch import UnsafeFetchError, fetch_with_redirects

# Task 2.6.c: the first real value for this parameter of embed_and_upsert()
# (2.5.e) -- no caller existed before this task to need one. Part of the
# uuid5 point-id formula (build_point_id(), 2.5.c): bumping this value is a
# deliberate, "every point for every document gets a new id" decision (the
# OLD points become orphaned until Step 2.9's reconcile removes them,
# matching the already-documented stale-chunk Open marker, Task 2.5.d) --
# never bumped casually, matching POINT_ID_NAMESPACE's/DENSE_VECTOR_SIZE's
# own "fixed forever until a deliberate, documented decision" precedent.
EMBEDDING_VERSION = "v1"


@dataclass(frozen=True)
class IngestResult:
    """Task 2.6.c. Plain string status, matching this project's own
    established style (no enum used anywhere in this codebase, confirmed
    by grep before choosing this shape) -- one of:
      - "ingested": content was new or changed; chunked, embedded and
        upserted for real.
      - "unchanged": content_hash matched the stored row; re-chunking/
        re-embedding was SKIPPED entirely (the core idempotency property
        this task exists to prove); only last_seen_at was updated.
      - "skipped": the URL's own domain is not verified for this tenant.
        Two distinct cases, both reported this way (`reason` names
        which): (1) the ORIGINAL url's host isn't verified -- no fetch
        was ever attempted; or (2) a redirect moved the fetch to a
        DIFFERENT host that also isn't verified for this tenant (Task
        2.6.c's own duplication check, C1 [SECURITY] fix) -- a fetch DID
        happen here, but its content is discarded unpersisted, never
        chunked/embedded. Either way, nothing from an unverified host
        ever reaches `embed_and_upsert()` or the `documents` table.
      - "failed": fetch_with_redirects() itself failed (an SSRF rejection,
        a timeout, a size/redirect-cap violation, or a real connection-
        level error) -- `reason` carries a plain description. Deliberately
        NOT used for an embed_and_upsert() failure -- see ingest_url()'s
        own docstring for why that propagates uncaught instead.
    """

    status: str
    document_id: uuid.UUID | None = None
    reason: str | None = None


async def _domain_is_verified(session: AsyncSession, tenant_id: uuid.UUID, hostname: str) -> bool:
    # Task 2.6.c's own duplication check (C1 [SECURITY] fix): extracted
    # so ingest_url() can run the exact same check twice -- once against
    # the URL's original host (before fetching at all) and again against
    # the FINAL host a redirect may have moved the fetch to (before
    # persisting anything) -- without duplicating this SELECT's own
    # exact-string-match wording in two places. See ingest_url()'s own
    # docstring for why only these two hosts are checked, never every
    # intermediate redirect hop.
    return (
        await session.execute(
            select(VerifiedDomain.id).where(
                VerifiedDomain.tenant_id == tenant_id,
                VerifiedDomain.domain == hostname,
                VerifiedDomain.status == "verified",
            )
        )
    ).scalar_one_or_none() is not None


async def ingest_url(
    session: AsyncSession,
    client: AsyncQdrantClient,
    collection_name: str,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    source_type: str,
    url: str,
    clock: DateTimeClock = lambda: datetime.now(UTC),
) -> IngestResult:
    """Fetches, extracts and (if changed) embeds+upserts one URL's own
    content for one tenant, creating or updating its `documents` row.
    Everything from the content-hash comparison onward (the unchanged-
    shortcut, embed_and_upsert()'s own atomicity, document_id reuse,
    nearly-empty handling, the `documents` row write) is qdrant_writer.py's
    own finish_ingest() -- shared with ingest_upload() (2.7.c) since the
    duplication check after 2.7.a/b/c; see that function's own docstring
    for the full reasoning. What stays here, genuinely unique to a URL
    fetch:

    Domain verification (docs/SPEC.md §5.5, §16's own "crawling an
    unverified domain is rejected" acceptance line): checked FIRST, via a
    direct query against `verified_domains` (status == "verified",
    exact-string match against the URL's own host -- no normalization
    applied here, deliberately: `verified_domains.domain` is stored
    exactly as the tenant typed it, per that column's own documented
    Step-6-owned open marker; inventing a second, independently-decided
    normalization rule here instead of extending that one marker would
    risk the two drifting apart). Zero network activity if this check
    fails -- fetch_with_redirects() is never even called.

    HTML decoding: fetch_with_redirects() returns raw bytes with no access
    to the response's own Content-Type/charset header (that function's own
    public contract, 2.3.d) -- decoded here as UTF-8 with errors="replace",
    matching domain_verification.py's own already-established "never crash
    on untrusted external bytes" posture. A non-UTF-8 page could suffer a
    few garbled characters; this is a deliberate v1 scope limit for a
    primitive (rule 11), not full charset-sniffing -- flagged as an
    ASSUMPTION, not silently decided.

    Redirect-to-a-different-host re-check (Task 2.6.c's own duplication
    check, C1 [SECURITY] fix): fetch_with_redirects() re-validates
    SSRF-safety (the IP) on every hop, but has no concept of tenant domain
    verification at all -- left unchecked, a verified domain redirecting
    to a different, unverified-but-IP-safe external host would have that
    host's own content fetched and persisted under THIS tenant's own
    `documents` row, defeating domain verification's actual purpose
    (proving tenant control over the content source), not merely an SSRF
    concern. Fixed by checking `fetch_with_redirects()`'s own returned
    `final_url` against `verified_domains` too, but ONLY when it differs
    from the original host -- the common, no-redirect case pays zero
    extra query cost. Deliberately NOT checking every intermediate hop:
    SSRF-safety (every hop's IP must be safe) and domain-ownership
    verification (whose content is this) are different guarantees -- a
    redirect that bounces through some unrelated-but-safe intermediate
    host (a URL shortener, an http-to-https canonicalizer) and lands back
    on the tenant's own verified domain is normal, harmless web behavior;
    rejecting it would be over-restrictive for no real benefit. What
    actually matters for content provenance is only the ORIGINAL host
    (gates whether anything is fetched at all) and the FINAL host (whose
    content is about to be persisted) -- confirmed by reasoning through
    this explicitly, not assumed safe, before building it.
    """
    hostname = httpx.URL(url).host
    if not await _domain_is_verified(session, tenant_id, hostname):
        return IngestResult(status="skipped", reason=f"domain {hostname!r} is not verified")

    try:
        fetch_result = await fetch_with_redirects(url)
    except (UnsafeFetchError, httpx.HTTPError) as exc:
        return IngestResult(status="failed", reason=f"{type(exc).__name__}: {exc}")

    final_hostname = fetch_result.final_url.host
    if final_hostname != hostname and not await _domain_is_verified(
        session, tenant_id, final_hostname
    ):
        return IngestResult(
            status="skipped",
            reason=(
                f"redirected from {hostname!r} to {final_hostname!r}, "
                "which is not verified"
            ),
        )

    content = extract_html(fetch_result.body.decode("utf-8", errors="replace"))

    status, document_id = await finish_ingest(
        session,
        client,
        collection_name,
        content,
        tenant_id=tenant_id,
        source_id=source_id,
        source_type=source_type,
        url=url,
        file_name=None,
        embedding_version=EMBEDDING_VERSION,
        clock=clock,
    )
    return IngestResult(status=status, document_id=document_id)
