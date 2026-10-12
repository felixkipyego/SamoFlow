# backend/app/retrieval/service.py
# Task 3.1.c [SECURITY]: retrieve(client_id, query, k) -- "the only code
# path that reads Qdrant" (docs/SPEC.md §6.3). Lives here, specifically,
# because test_qdrant_read_path_guard.py's own READ_ALLOWLIST/
# CLIENT_ACCESS_ALLOWLIST (Task 1.3.e) already name exactly this path as
# the one file allowed both to hold a Qdrant client and to call a read
# method on one -- confirmed live by reading that guard directly before
# writing a line of this file, not assumed. Putting retrieve() anywhere
# else would fail that guard.
#
# Hybrid query: the Qdrant Query API, a dense Prefetch and a sparse
# Prefetch, fused server-side with Reciprocal Rank Fusion -- confirmed
# live, against the real pinned test-qdrant service, with a two-tenant
# smoke probe (one point per tenant, a filtered query returning only the
# requesting tenant's own point with a real fused score) before writing
# this file, not assumed from the qdrant-client API surface alone.
#
# Exclusively the READ-ONLY client (Task 3.1.b's own get_qdrant_read_
# client()), never get_qdrant_client() (the read-write one) -- this is
# the one file in this codebase that only ever queries, never writes, so
# it is also the one file with no structural reason to ever hold the
# read-write key at all. Confirmed by reading this module's own imports
# below: qdrant_writer.py (the write-only counterpart) is never imported
# here, and vice versa.
#
# Filter discipline, matching the two already-specified [SECURITY]
# markers exactly, not redesigned here: tenant_filter() (app/qdrant.py)
# is applied inside BOTH prefetches AND the outer query -- re-confirmed
# at Task 1.3.e's own H1 housekeeping that this exact configuration
# returns byte-for-byte identical results to filtering both prefetches
# alone, so the outer filter costs nothing and gives one more
# independent layer; never outer-only (a small prefetch limit could let
# another tenant's candidates crowd out this tenant's own matches before
# fusion runs). The HIDDEN_FIELD (app/ingest/qdrant_writer.py, Task
# 2.9.e) must_not exclusion rides alongside tenant_filter()'s own
# condition, in the identical both-prefetches-plus-outer shape -- a
# disabled source's soft-hidden points must never surface here, the
# first and only real consumer of that flag.
import uuid

from qdrant_client.models import (
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchValue,
    Prefetch,
    ScoredPoint,
    SparseVector,
)

from app.config import get_settings
from app.ingest.embedding import embed_dense, embed_sparse_query
from app.ingest.qdrant_writer import HIDDEN_FIELD
from app.qdrant import (
    COLLECTION_NAME,
    DENSE_VECTOR_NAME,
    SPARSE_VECTOR_NAME,
    get_qdrant_read_client,
    tenant_filter,
)


def _retrieval_filter(client_id: uuid.UUID) -> Filter:
    """The ONE filter every sub-query and the outer query in retrieve()
    below are scoped with -- reuses tenant_filter()'s own `must` list
    directly (never a second, parallel client_id condition) and adds the
    HIDDEN_FIELD exclusion alongside it, matching the exact shape both
    already-recorded [SECURITY] Open markers specify. `must_not` is the
    correct primitive for "exclude any point where this field is True,
    but still match a point where it is absent" -- confirmed live at
    Task 2.9.e that Qdrant's own `match=MatchValue(value=True)` never
    matches an absent field, so every point written before HIDDEN_FIELD
    existed (and every point after a real `unhide_points_for_source()`
    call) still matches this filter normally, with no separate `IsNull`
    handling needed.
    """
    return Filter(
        must=tenant_filter(client_id).must,
        must_not=[FieldCondition(key=HIDDEN_FIELD, match=MatchValue(value=True))],
    )


async def retrieve(
    client_id: uuid.UUID,
    query: str,
    k: int | None = None,
    *,
    collection_name: str = COLLECTION_NAME,
) -> list[ScoredPoint]:
    """The only code path in this codebase that reads Qdrant (docs/SPEC.md
    §6.3). All calls are async, and the filter is built fresh inside this
    function on every call -- never cached, never built in a global
    retriever.

    `collection_name` defaults to the real `COLLECTION_NAME` -- matching
    every other Qdrant-touching primitive in this codebase (`embed_and_
    upsert()`/`finish_ingest()`/`reconcile_tenant()`, all take an explicit
    `collection_name`), an ordinary production caller never needs to pass
    it. Deliberately NOT given a `client` parameter the same way those
    other primitives are, though -- the one real, intentional deviation
    from that pattern: this function always obtains its OWN client via
    `get_qdrant_read_client()` internally, never a caller-injected one,
    since accepting an injectable client would let a caller silently
    defeat the whole point of Task 3.1.b's own least-privilege read-only
    key by passing the read-write client instead.

    `client_id` MUST be `VerifiedIdentity.tenant_id` (`app/auth/
    dependencies.py`, Task 1.4.h) specifically, threaded through
    `get_tenant_repository()`'s own identical identity -- NEVER a value
    re-derived from a request body, query parameter, or any other
    client-suppliable field. This function has no way to verify where
    its own `client_id` argument came from (it only knows what it is
    given, per this project's own established `TenantScopedRepository`
    precedent for the identical trust boundary) -- the caller built at
    Step 3.3/3.4 carries that obligation, not this function. Recorded
    here, explicitly, so a future caller cannot get this wrong: see the
    matching [SECURITY] Open marker (recorded at the Step 1.4 breakdown)
    for the full reasoning.

    `k` defaults to `Settings.retrieval_k` (an explicit starting point,
    8, agreed before building -- Settings-configurable so it can be
    retuned against the golden set, Step 3.7, without a code change).
    The per-sub-query candidate pool is a SEPARATE, larger number,
    `Settings.retrieval_prefetch_limit` (40) -- docs/SPEC.md §6.3's own
    reranker bullet names "roughly 20 to 50 candidates" as the pool a
    reranker would work over, a different quantity from the final
    result count a caller actually receives.

    Hybrid query: one dense Prefetch (`embed_dense()`, Task 2.5.a,
    confirmed reusable for the query side unchanged -- OpenAI embeddings
    have no query/passage distinction) and one sparse Prefetch
    (`embed_sparse_query()`, Task 3.1.a, the query-side sibling to the
    document-side `embed_sparse()` -- using the wrong one here would
    silently apply the wrong half of the BM25 formula), fused server-side
    via `FusionQuery(fusion=Fusion.RRF)` -- confirmed live, against the
    real pinned test-qdrant service, with a two-tenant smoke probe before
    this function was written.

    Returns ranked, tenant-scoped `ScoredPoint`s with their own payload
    (title, heading_path, source_url, text, etc. -- `with_payload`
    defaults to `True` on `query_points()`, not overridden here) --
    citation-building from that payload, and any source-type-specific
    labeling, is the answer node's own job (Step 3.4), not this
    function's.

    Reranker hook (Task 3.1.e): `Settings.reranker_enabled`, default
    `False` -- when off (the only real behavior today), this function is
    unchanged from Task 3.1.c/3.1.d. When on, this function raises
    `NotImplementedError` rather than silently returning un-reranked
    results as if the flag did something: no model/library choice has
    been made yet (docs/SPEC.md §6.3's own reranker bullet is explicit
    that it is "enabled only if" the golden set, Step 3.7, measures the
    top-result quality as poor -- there is no real signal yet to justify
    one). The raise sits at the exact spot a real reranker will
    eventually occupy: immediately after `client.query_points()` returns,
    before this function hands its own result back.
    """
    settings = get_settings()
    k = settings.retrieval_k if k is None else k
    query_filter = _retrieval_filter(client_id)

    dense_vector = (await embed_dense([query]))[0]
    sparse_vector = (await embed_sparse_query([query]))[0]

    client = get_qdrant_read_client()
    result = await client.query_points(
        collection_name=collection_name,
        prefetch=[
            Prefetch(
                query=dense_vector,
                using=DENSE_VECTOR_NAME,
                filter=query_filter,
                limit=settings.retrieval_prefetch_limit,
            ),
            Prefetch(
                query=SparseVector(indices=sparse_vector.indices, values=sparse_vector.values),
                using=SPARSE_VECTOR_NAME,
                filter=query_filter,
                limit=settings.retrieval_prefetch_limit,
            ),
        ],
        query=FusionQuery(fusion=Fusion.RRF),
        query_filter=query_filter,
        limit=k,
    )
    # Reranker hook (Task 3.1.e): the exact slot a real reranker will
    # eventually occupy -- right after the fused Query API call returns,
    # before this function hands its own result back. No model/library
    # choice is made here, per direct instruction: docs/SPEC.md §6.3's
    # own reranker bullet is explicit that it is "enabled only if" the
    # golden set (Step 3.7, not yet built) measures the top-result
    # quality as poor, so there is no real signal yet to justify one.
    # Flipping the flag on MUST fail loud, not silently no-op and return
    # un-reranked results as if nothing were different -- a caller
    # enabling a feature that quietly does nothing is a worse failure
    # mode than a clear, immediate error naming exactly what is missing.
    if settings.reranker_enabled:
        raise NotImplementedError(
            "Settings.reranker_enabled is True, but the reranker itself is not built yet "
            "(Task 3.1.e deferred this; see docs/SPEC.md §6.3 and PROJECT_SPEC.md's own "
            "Step 3.1 closure summary)."
        )
    return result.points
