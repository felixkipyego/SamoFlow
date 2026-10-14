# backend/scripts/retrieval_eval.py
# Task 3.7.c: the retrieval-only eval -- seeds a disposable Qdrant
# COLLECTION from the frozen synthetic corpus, retrieves through the REAL
# `retrieve()` (real tenant filter, real hybrid RRF fusion, real gate
# signal), and reports recall/MRR/gate-sweep metrics. No answer model, no
# LLM judge anywhere in this module -- that is 3.7.d+'s own scope.
#
# Lives in backend/scripts/, NOT backend/app/evals/ (its first location)
# -- moved here after `tests/test_qdrant_read_path_guard.py`'s own AST
# scan (which covers backend/app/ specifically) flagged 4 real
# violations on the original location, confirmed live, not guessed: (1)
# rule (a), import confinement -- `app.qdrant`/`qdrant_client` imports
# are restricted to a narrow production allowlist (app/retrieval/
# service.py, the ingestion adapters, app/main.py, app/qdrant.py itself)
# this module is NOT on, per direct instruction ("No AST guard allowlist
# is widened"); (2) rule (d), an ABSOLUTE prohibition, not merely
# allowlist-gated -- `delete_collection()` is "forbidden in application
# code everywhere" (the guard's own message), because snapshot/recreate/
# delete-collection operations could destroy every tenant's data, and
# this eval's own "disposable collection, torn down afterwards" design
# (per direct instruction) genuinely needs exactly that call. Rule (d)
# cannot be satisfied by any allowlist addition -- the only fix is
# living outside backend/app/ entirely, matching where every other real-
# Qdrant-write research/eval script in this project already lives
# (backend/scripts/manual_research_*.py, all of which call upsert_
# points()/client.delete() directly, never flagged by this guard because
# it only ever scans backend/app/).
#
# `retrieve()` itself (app/retrieval/service.py) is UNCHANGED by this
# move or by anything else in this module -- its own filter/tenant
# logic, the read-path guard's own naming of it as the one file allowed
# to query Qdrant, and the reranker-hook raise are untouched. This
# module calls the real function, unmodified, with collection_name as an
# explicit argument it ALREADY accepted before this task.
#
# Determinism without an API key: dense QUERY embeddings are the one
# real network call `retrieve()` would otherwise make, so they are
# frozen alongside the corpus (`evals/golden_set/query_vectors.json`,
# keyed by item id, refreshed by `manual_refresh_golden_set_query_
# vectors.py`) and served through `frozen_dense_embedder()` below -- a
# context manager that temporarily replaces `app.ingest.embedding.
# _get_client()`'s own return value for the duration of the eval, then
# restores it. This IS the one seam this task adds at the embedder
# boundary specifically, per direct instruction -- and it needed NO new
# production code: `_get_client()` was ALREADY a bare, monkeypatchable
# `@lru_cache` singleton before this task, the identical seam
# `_patch_embed_dense_constant_nonzero()` (tests/test_agent_graph_
# retrieve.py) already uses for the same reason. Sparse QUERY embeddings
# are NOT frozen -- `embed_sparse_query()` is local/CPU-bound fastembed
# BM25 math with no network call and no API key, confirmed deterministic
# per text (app/ingest/embedding.py's own docstring); freezing it would
# add a second cache for zero benefit.
import asyncio
import hashlib
import json
import uuid
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ConfigDict
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    HnswConfigDiff,
    MatchValue,
    Modifier,
    SparseVectorParams,
    VectorParams,
)

from app import qdrant
from app.agent.graph import _prepare_node
from app.evals.golden_set import (
    DEFAULT_ITEMS_PATH,
    DEFAULT_SYNTHETIC_CORPUS_PATH,
    GOLDEN_SET_DIR,
    FrozenCorpus,
    GoldenSet,
    GoldenSetItem,
    assert_embedding_config_is_current,
    load_golden_set,
    resolve_item_sources,
)
from app.evals.golden_set import (
    load_frozen_corpus as _load_frozen_corpus_unchecked,
)
from app.evals.retrieval_metrics import (
    ItemRetrievalResult,
    all_documents_recall_at_k,
    gate_pass_shares,
    mean_reciprocal_rank,
    recall_at_k,
    score_distribution,
)
from app.ingest.embedding import EMBEDDING_MODEL, SPARSE_EMBEDDING_MODEL, SparseVectorData
from app.ingest.qdrant_writer import build_payload, build_point, build_point_id, upsert_points
from app.qdrant import (
    DENSE_VECTOR_NAME,
    DENSE_VECTOR_SIZE,
    SPARSE_VECTOR_NAME,
    _create_client_id_index,
    _create_source_id_index,
)
from app.retrieval.service import retrieve

DEFAULT_QUERY_VECTORS_PATH = GOLDEN_SET_DIR / "query_vectors.json"
MAX_K = 8
# Fixed, not uuid4() -- determinism (this task's own explicit requirement)
# needs the SAME document/source ids on every run, not just the same
# vectors. Point ids (build_point_id()) are derived from document_id,
# chunk_index and embedding_version, matching docs/SPEC.md §5.3's own
# "deterministic point IDs ... so a re-run overwrites instead of
# duplicating" production discipline -- a fresh random document_id per
# eval run broke that invariant for eval data specifically, and was
# confirmed LIVE to be the real cause of a genuine run-to-run flake: two
# runs of the identical frozen vectors produced different recall@1 for
# borderline near-tied RRF fusion scores, because the tie-break order
# between two equally-fused-scored points depends on their own point ids
# when Qdrant's own ranking has to break an exact tie. Fixing determinism
# at the source (stable ids) rather than loosening the baseline
# comparison's own tolerance.
_NAMESPACE = uuid.UUID("3f6a1d0a-5e4b-4b8a-9c1d-6a1f8e2b7c40")
_SOURCE_ID = uuid.uuid5(_NAMESPACE, "golden-set-retrieval-eval-source")


def _document_id(corpus_name: str, document_name: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, f"{corpus_name}:{document_name}")


RECALL_MRR_CATEGORIES = ("answerable", "follow_up", "multi_source", "injection")
# Every category except "greeting" -- the router decides greetings before
# retrieve() is ever called, so they have no best_dense_score to sweep.
GATE_SWEEP_CATEGORIES = (
    "answerable", "related_unanswerable", "off_topic", "follow_up", "multi_source", "injection",
)


def load_and_check_frozen_corpus(path: Path) -> FrozenCorpus:
    """The real "on load, compare with current, fail loudly" entry point
    (this task's own item 1) -- `app.evals.golden_set.load_frozen_corpus`
    itself cannot do this (see that module's own docstring for why); this
    wrapper, living outside the Qdrant read-path guard's scanned tree,
    fetches the embedder's own CURRENT configuration and compares it.
    """
    corpus = _load_frozen_corpus_unchecked(path)
    assert_embedding_config_is_current(
        dense_embedding_model=corpus.dense_embedding_model,
        dense_vector_size=corpus.dense_vector_size,
        sparse_embedding_model=corpus.sparse_embedding_model,
        current_dense_embedding_model=EMBEDDING_MODEL,
        current_dense_vector_size=DENSE_VECTOR_SIZE,
        current_sparse_embedding_model=SPARSE_EMBEDDING_MODEL,
        refreeze_hint=(
            "re-run backend/scripts/manual_build_golden_set_synthetic_corpus.py to refreeze "
            f"the {corpus.corpus!r} corpus"
        ),
    )
    return corpus


def build_prepared_query(item: GoldenSetItem) -> str:
    """The IDENTICAL join/prepare logic the real graph's own prepare node
    uses (`_prepare_node`, app/agent/graph.py) -- imported directly, never
    reimplemented, per direct instruction. Works uniformly for every
    category: an item with no `prior_turns` naturally takes `_prepare_
    node`'s own "nothing to join with" branch and returns the raw
    question unchanged.
    """
    messages = [HumanMessage(content=text) for text in item.prior_turns]
    messages.append(HumanMessage(content=item.question))
    result = _prepare_node({"messages": messages})
    return result["prepared_query"]


def query_text_hash(prepared_query: str) -> str:
    return hashlib.sha256(prepared_query.encode("utf-8")).hexdigest()[:16]


class QueryVectorStaleError(ValueError):
    """Raised when a golden-set item's own CURRENT prepared query (via
    `_prepare_node`, right now) no longer matches the query text the
    frozen vector cache recorded it for -- e.g. the item's own `question`
    or `prior_turns` was edited after the cache was last refreshed. Never
    silently embeds the new text for real (that would defeat the whole
    "deterministic, no API key" point) and never silently reuses the
    stale vector for different text (a quiet wrong number) -- the fix is
    always re-running the refresh script.
    """


class _FrozenQueryVectorFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dense_embedding_model: str
    dense_vector_size: int
    sparse_embedding_model: str
    # item id -> {"query_hash": ..., "dense_vector": [...]}
    items: dict[str, dict]


@dataclass(frozen=True)
class FrozenQueryVectors:
    vectors_by_item_id: dict[str, list[float]] = field(default_factory=dict)
    hashes_by_item_id: dict[str, str] = field(default_factory=dict)

    def resolve(self, item: GoldenSetItem, prepared_query: str) -> list[float]:
        if item.id not in self.vectors_by_item_id:
            raise QueryVectorStaleError(
                f"item {item.id!r} has no frozen query vector -- run "
                "backend/scripts/manual_refresh_golden_set_query_vectors.py"
            )
        current_hash = query_text_hash(prepared_query)
        frozen_hash = self.hashes_by_item_id[item.id]
        if current_hash != frozen_hash:
            raise QueryVectorStaleError(
                f"item {item.id!r}'s own prepared query no longer matches its frozen vector "
                f"(hash {frozen_hash!r} != current {current_hash!r}) -- its question or prior_"
                "turns changed since the last refresh; run backend/scripts/manual_refresh_"
                "golden_set_query_vectors.py"
            )
        return self.vectors_by_item_id[item.id]


def load_and_check_frozen_query_vectors(
    path: Path = DEFAULT_QUERY_VECTORS_PATH,
) -> FrozenQueryVectors:
    raw = json.loads(path.read_text(encoding="utf-8"))
    parsed = _FrozenQueryVectorFile.model_validate(raw)
    assert_embedding_config_is_current(
        dense_embedding_model=parsed.dense_embedding_model,
        dense_vector_size=parsed.dense_vector_size,
        sparse_embedding_model=parsed.sparse_embedding_model,
        current_dense_embedding_model=EMBEDDING_MODEL,
        current_dense_vector_size=DENSE_VECTOR_SIZE,
        current_sparse_embedding_model=SPARSE_EMBEDDING_MODEL,
        refreeze_hint=(
            "re-run backend/scripts/manual_refresh_golden_set_query_vectors.py to refreeze "
            "the query-vector cache"
        ),
    )
    vectors = {item_id: entry["dense_vector"] for item_id, entry in parsed.items.items()}
    hashes = {item_id: entry["query_hash"] for item_id, entry in parsed.items.items()}
    return FrozenQueryVectors(vectors_by_item_id=vectors, hashes_by_item_id=hashes)


class _FrozenEmbeddingResponseItem:
    def __init__(self, vector: list[float]) -> None:
        self.embedding = vector


class _FrozenEmbeddingResponse:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.data = [_FrozenEmbeddingResponseItem(v) for v in vectors]


class _FrozenEmbeddingsResource:
    def __init__(self, vector_by_text: dict[str, list[float]]) -> None:
        self._vector_by_text = vector_by_text

    async def create(self, *, model, input):  # noqa: A002 -- matches the real SDK's own param name
        try:
            vectors = [self._vector_by_text[text] for text in input]
        except KeyError as exc:
            raise QueryVectorStaleError(
                f"no frozen dense vector for query text {exc.args[0]!r} -- it was not included "
                "in the frozen query-vector cache passed to frozen_dense_embedder()"
            ) from exc
        return _FrozenEmbeddingResponse(vectors)


class _FrozenEmbeddingClient:
    def __init__(self, vector_by_text: dict[str, list[float]]) -> None:
        self.embeddings = _FrozenEmbeddingsResource(vector_by_text)


@contextmanager
def frozen_dense_embedder(vector_by_text: dict[str, list[float]]) -> Iterator[None]:
    """The one seam this task adds, confined entirely to eval code (see
    this module's own header comment). Temporarily replaces `app.ingest.
    embedding._get_client`'s own return value with a fake that serves
    `vector_by_text`'s own frozen vectors, keyed by the exact query text
    requested -- an unrecognised text raises `QueryVectorStaleError`
    loudly rather than falling back to a real network call or a quiet
    zero vector. Restores the real `_get_client` on exit unconditionally.
    """
    import app.ingest.embedding as embedding_module

    original = embedding_module._get_client
    embedding_module._get_client = lambda: _FrozenEmbeddingClient(vector_by_text)
    try:
        yield
    finally:
        embedding_module._get_client = original


# Task 3.7.e: a small, deliberate addition to this 3.7.c function (flagged
# per rule 2, not a silent change) -- every point this function writes now
# also carries a payload field identifying it as eval-seeded data, so a
# `--sweep` cleanup pass (manual_run_answer_eval.py) can find and remove
# leftover points from a crashed run via a plain Qdrant filter, regardless
# of which tenant_id produced them. Harmless for 3.7.c's own caller (its
# own collection is dropped wholesale, never scanned by payload) and for
# every other reader (`chunk_from_scored_point()` reads specific keys by
# name, confirmed by reading it directly -- an extra, unindexed payload
# key is never a schema concern for Qdrant or for this codebase).
EVAL_MARKER_FIELD = "_samoflow_eval_marker"
EVAL_MARKER_VALUE = "answer-eval"


def eval_marker_filter() -> Filter:
    """The one Filter a `--sweep` cleanup pass needs: every point ANY
    eval run has ever seeded, across every tenant_id a crashed run may
    have used -- unlike `qdrant.tenant_filter()`, deliberately not
    scoped to one tenant, since sweeping exists precisely because a
    crashed run's own tenant_id was never recorded anywhere durable.
    """
    return Filter(
        must=[FieldCondition(key=EVAL_MARKER_FIELD, match=MatchValue(value=EVAL_MARKER_VALUE))]
    )


async def _create_exact_search_collection(client: AsyncQdrantClient, name: str) -> None:
    """Part C (Task 4.1 follow-up): the SAME schema `qdrant.ensure_
    collection()` creates (reusing its own private index helpers
    directly, not duplicated here), with ONE deliberate difference --
    `hnsw_config=HnswConfigDiff(m=0)` disables HNSW graph construction
    for this collection entirely, forcing Qdrant's own exact/brute-force
    scoring for every query against it. `m=16` (the server-side default
    `ensure_collection()` otherwise leaves in place) is an APPROXIMATE
    nearest-neighbor index -- confirmed live to be the real, previously
    undiagnosed source of this eval's own run-to-run nondeterminism
    (both the corpus's own dense/sparse vectors AND the query vectors
    were ALREADY fully frozen before this fix -- re-confirmed by reading
    the committed `synthetic_corpus.json` directly, real 1536-dim dense
    vectors and real sparse indices/values stored per chunk, never
    recomputed -- so identical input data was already guaranteed; HNSW's
    own approximate, construction-order-sensitive graph traversal was
    the one remaining variable). Deliberately NOT a change to `app.
    qdrant.ensure_collection()` itself (production code, untouched,
    still approximate -- the right tradeoff at real tenant scale): this
    function exists only for this disposable, eval-only collection,
    never reused in `app/`.
    """
    await client.create_collection(
        collection_name=name,
        vectors_config={
            DENSE_VECTOR_NAME: VectorParams(size=DENSE_VECTOR_SIZE, distance=Distance.COSINE)
        },
        sparse_vectors_config={SPARSE_VECTOR_NAME: SparseVectorParams(modifier=Modifier.IDF)},
        hnsw_config=HnswConfigDiff(m=0),
    )
    await _create_client_id_index(client, name)
    await _create_source_id_index(client, name)


async def seed_frozen_corpus(
    client: AsyncQdrantClient, collection_name: str, tenant_id: uuid.UUID, corpus: FrozenCorpus
) -> dict[str, uuid.UUID]:
    """Writes every chunk of `corpus` into `collection_name` under
    `tenant_id`, reusing its own ALREADY-frozen dense/sparse vectors
    (never re-embedded) via the real `build_payload()`/`build_point()`/
    `upsert_points()` primitives (Step 2.5). Returns the document-name ->
    UUID mapping used -- `doc_id` in the real payload shape is a UUID,
    but this corpus's own `document` field is a stable short name (e.g.
    "shipping-domestic"), not a UUID, so one must be assigned.
    """
    await _create_exact_search_collection(client, collection_name)
    document_id_by_name = {name: _document_id(corpus.corpus, name) for name in corpus.documents}
    points = []
    for chunk in corpus.chunks:
        document_id = document_id_by_name[chunk.document]
        payload = build_payload(
            tenant_id=tenant_id,
            source_id=_SOURCE_ID,
            source_type="url",
            document_id=document_id,
            source_url=f"https://eval.example/{chunk.document}",
            file_name=None,
            title=chunk.document,
            heading_path=tuple(chunk.heading_path),
            chunk_index=chunk.chunk_index,
            content_hash=chunk.content_hash,
            embedding_model=corpus.dense_embedding_model,
            embedding_version=corpus.embedding_version,
            text=chunk.text,
        )
        payload[EVAL_MARKER_FIELD] = EVAL_MARKER_VALUE
        point_id = build_point_id(document_id, chunk.chunk_index, corpus.embedding_version)
        sparse = SparseVectorData(indices=chunk.sparse_indices, values=chunk.sparse_values)
        points.append(build_point(point_id, payload, chunk.dense_vector, sparse))
    await upsert_points(client, collection_name, points)
    return document_id_by_name


async def retrieve_item_result(
    item: GoldenSetItem,
    corpus: FrozenCorpus,
    collection_name: str,
    tenant_id: uuid.UUID,
    document_id_by_name: dict[str, uuid.UUID],
) -> ItemRetrievalResult:
    """Retrieves through the real `retrieve()` for one item and converts
    its `ScoredPoint`s into ranked `(document, chunk_index)` keys by
    reversing `document_id_by_name` -- the only translation needed
    because the frozen corpus names documents by a short string while
    Qdrant's own payload stores a UUID.
    """
    document_name_by_id = {str(v): k for k, v in document_id_by_name.items()}
    prepared_query = build_prepared_query(item)
    result = await retrieve(tenant_id, prepared_query, k=MAX_K, collection_name=collection_name)
    # Part C (Task 4.1 follow-up): a deterministic tie-break, applied
    # HERE in eval code only -- never inside retrieve() itself (production
    # code, untouched; real tenant traffic has no need for a fixed tie-
    # break, since there is no "the SAME query run three times and
    # compared" requirement in production the way there is for this
    # eval). `result.points` is Qdrant's own returned order; re-sorted
    # by (score DESCENDING, point id ASCENDING) -- `point.id` is fully
    # deterministic (build_point_id(), derived from document_id/chunk_
    # index/embedding_version, all fixed by the frozen corpus), so this
    # sort is total and stable regardless of whatever order Qdrant itself
    # returned equal- or near-equal-scored points in.
    sorted_points = sorted(result.points, key=lambda point: (-point.score, str(point.id)))
    retrieved_keys = tuple(
        (document_name_by_id[point.payload["doc_id"]], point.payload["chunk_index"])
        for point in sorted_points
    )

    resolved_sources = resolve_item_sources(item, corpus)
    expected_chunk_keys: set[tuple[str, int]] = set()
    expected_doc_to_chunk_keys: dict[str, frozenset[tuple[str, int]]] = {}
    for source in resolved_sources:
        keys = frozenset((source.document, idx) for idx in source.chunk_indices)
        expected_chunk_keys |= keys
        expected_doc_to_chunk_keys[source.document] = keys

    return ItemRetrievalResult(
        item_id=item.id,
        category=item.category,
        expected_chunk_keys=frozenset(expected_chunk_keys),
        expected_doc_to_chunk_keys=(
            expected_doc_to_chunk_keys if item.category == "multi_source" else {}
        ),
        retrieved_keys=retrieved_keys,
        best_dense_score=result.best_dense_score,
    )


async def run_retrieval_eval(
    golden_set: GoldenSet,
    corpus: FrozenCorpus,
    query_vectors: FrozenQueryVectors,
    client: AsyncQdrantClient,
) -> list[ItemRetrievalResult]:
    """Full orchestration: creates a freshly-named, genuinely disposable
    Qdrant COLLECTION (not merely a disposable tenant inside the shared
    production collection, per direct instruction), seeds it from
    `corpus`, retrieves every non-greeting `corpus="synthetic"` item
    through the real `retrieve()` with frozen dense query vectors, then
    DROPS the whole collection before returning -- regardless of whether
    the caller's own test later fails an assertion on the result.

    A dedicated collection per run (not the shared `COLLECTION_NAME` plus
    a disposable tenant_id, this function's own first design) was
    confirmed LIVE to be necessary for real determinism, not just
    tidiness: reusing one long-lived collection across many seed/delete
    cycles left Qdrant's own internal segment state history-dependent,
    and a genuine RRF near-tie between two chunks resolved its own
    rank-1 winner differently across otherwise-identical runs (same
    frozen vectors, same deterministic point ids) depending on that
    history. A brand-new collection has no such history on every run.
    `retrieve()` itself is unchanged either way -- `collection_name` was
    already a plain parameter it accepts, not new production code.
    """
    collection_name = f"eval_golden_set_{uuid.uuid4().hex[:12]}"
    tenant_id = uuid.uuid4()
    items = [
        item
        for item in golden_set.items
        if item.category != "greeting" and item.corpus == corpus.corpus
    ]
    vector_by_text: dict[str, list[float]] = {}
    for item in items:
        prepared_query = build_prepared_query(item)
        vector_by_text[prepared_query] = query_vectors.resolve(item, prepared_query)

    try:
        document_id_by_name = await seed_frozen_corpus(client, collection_name, tenant_id, corpus)
        results = []
        with frozen_dense_embedder(vector_by_text):
            for item in items:
                results.append(
                    await retrieve_item_result(
                        item, corpus, collection_name, tenant_id, document_id_by_name
                    )
                )
        return results
    finally:
        await client.delete_collection(collection_name)


RECALL_K_VALUES = (1, 3, 5, 8)
# 0.10, 0.15, ..., 0.50 -- 9 thresholds. Reported only: Settings.gate_
# relevance_threshold is NOT read or changed anywhere in this module.
# Tuning it is Step 3.7.f's own job, once this sweep's own real numbers
# exist to tune against.
GATE_SWEEP_THRESHOLDS = tuple(round(0.10 + 0.05 * i, 2) for i in range(9))


def summarize_results(results: list[ItemRetrievalResult]) -> dict:
    """Builds the full report: recall@{1,3,5,8} + MRR for each of
    RECALL_MRR_CATEGORIES, the multi_source-only all-documents recall,
    and the gate sweep (pass share per threshold + score distribution)
    for each of GATE_SWEEP_CATEGORIES. `results` is expected to already
    exclude greeting items (run_retrieval_eval() never retrieves them).
    """
    by_category: dict[str, list[ItemRetrievalResult]] = defaultdict(list)
    for result in results:
        by_category[result.category].append(result)

    recall_at_k_by_category = {}
    mrr_by_category = {}
    for category in RECALL_MRR_CATEGORIES:
        category_results = by_category.get(category)
        if not category_results:
            continue
        recall_at_k_by_category[category] = {
            str(k): recall_at_k(category_results, k) for k in RECALL_K_VALUES
        }
        mrr_by_category[category] = mean_reciprocal_rank(category_results)

    multi_source_results = by_category.get("multi_source", [])
    multi_source_all_documents_recall = (
        {str(k): all_documents_recall_at_k(multi_source_results, k) for k in RECALL_K_VALUES}
        if multi_source_results
        else {}
    )

    gate_sweep = {}
    for category in GATE_SWEEP_CATEGORIES:
        category_results = by_category.get(category)
        if not category_results:
            continue
        scores = [r.best_dense_score for r in category_results]
        present_scores = [s for s in scores if s is not None]
        gate_sweep[category] = {
            "pass_shares": {
                str(t): v for t, v in gate_pass_shares(scores, list(GATE_SWEEP_THRESHOLDS)).items()
            },
            "distribution": (
                asdict(score_distribution(present_scores)) if present_scores else None
            ),
        }

    return {
        "recall_at_k": recall_at_k_by_category,
        "mrr": mrr_by_category,
        "multi_source_all_documents_recall_at_k": multi_source_all_documents_recall,
        "gate_sweep": gate_sweep,
    }


DEFAULT_BASELINE_PATH = GOLDEN_SET_DIR / "retrieval_baseline.json"

# Part C (Task 4.1 follow-up): REMOVED. The tolerance band these two
# constants used to define (RECALL_TOLERANCE_ITEMS = 1, MRR_TOLERANCE =
# 0.05) existed to absorb a real, previously root-caused noise source --
# Reciprocal Rank Fusion produces a genuine EXACT mathematical tie
# whenever two candidates swap dense/sparse ranks with each other
# (1/(60+1)+1/(60+2) == 1/(60+2)+1/(60+1)), and Qdrant's own server did
# not guarantee a deterministic tiebreak order for such a tie. That root
# cause is now closed at the source, not merely tolerated: (1) the
# disposable eval collection is created with `hnsw_config=HnswConfigDiff
# (m=0)` (_create_exact_search_collection() above), forcing exact/brute-
# force scoring with no approximate-index traversal order in play at
# all; (2) `retrieve_item_result()` above now re-sorts `result.points`
# by (score DESCENDING, point id ASCENDING) itself, in eval code only,
# so even a genuine Qdrant-side exact-score tie resolves identically
# every run regardless of whatever order the server itself returned it
# in. Confirmed live, not assumed: 3 consecutive `make eval` runs on
# unchanged code now produce a BYTE-IDENTICAL summary_retrieval.json
# (diffed directly, zero differences) -- the exact proof this tolerance
# band was only ever a workaround for. See PROJECT_SPEC.md's own Task
# 4.1 follow-up entry for the full before/after and the now-RESOLVED §9
# marker. `assert_no_regression()` below now requires an EXACT match
# (within `1e-9`, pure floating-point representation noise, never a real
# tolerance band) -- any real regression, however small, now fails loudly
# instead of hiding inside a band sized for noise that no longer exists.


def load_baseline(path: Path = DEFAULT_BASELINE_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def assert_no_regression(report: dict, baseline: dict) -> None:
    """Raises `AssertionError` naming every metric that fell below its
    own EXACT baseline value (within 1e-9, pure floating-point
    representation noise -- not a real tolerance band, see this
    module's own header comment immediately above for why a band is no
    longer needed at all). An IMPROVEMENT (actual > baseline) always
    passes, by construction. No longer takes `category_sizes` (every
    real tolerance calculation that used to need it is gone) -- removed
    from the signature rather than kept as dead weight; see this
    function's own call sites for the matching update.
    """
    failures = []
    for category, per_k in baseline["recall_at_k"].items():
        for k, baseline_value in per_k.items():
            actual_value = report["recall_at_k"][category][k]
            if actual_value < baseline_value - 1e-9:
                failures.append(
                    f"recall@{k} [{category}]: {actual_value:.3f} < baseline {baseline_value:.3f}"
                )
    for category, baseline_value in baseline["mrr"].items():
        actual_value = report["mrr"][category]
        if actual_value < baseline_value - 1e-9:
            failures.append(f"mrr [{category}]: {actual_value:.3f} < baseline {baseline_value:.3f}")
    for k, baseline_value in baseline["multi_source_all_documents_recall_at_k"].items():
        actual_value = report["multi_source_all_documents_recall_at_k"][k]
        if actual_value < baseline_value - 1e-9:
            failures.append(
                f"multi_source_all_documents_recall@{k}: {actual_value:.3f} < baseline "
                f"{baseline_value:.3f}"
            )
    if failures:
        raise AssertionError("retrieval metric regression(s) detected:\n" + "\n".join(failures))


# --- CLI entry point (`make eval`, Task 3.7.e item 3) ------------------------
# The free, deterministic half of the eval rule -- no LLM call anywhere in
# this path (frozen dense query vectors, local/free sparse embedding),
# real Qdrant only (test-qdrant). Writes/overwrites the one COMMITTED
# summary file this entry point owns and prints a delta table against
# whatever was previously committed there, per direct instruction ("so
# changes show up in git and in reports").
DEFAULT_SUMMARY_PATH = GOLDEN_SET_DIR / "summary_retrieval.json"


async def _run_and_summarize(client: AsyncQdrantClient) -> dict:
    golden_set = load_golden_set(DEFAULT_ITEMS_PATH)
    corpus = load_and_check_frozen_corpus(DEFAULT_SYNTHETIC_CORPUS_PATH)
    query_vectors = load_and_check_frozen_query_vectors(DEFAULT_QUERY_VECTORS_PATH)
    results = await run_retrieval_eval(golden_set, corpus, query_vectors, client)
    return summarize_results(results)


def main() -> None:
    # Local imports, matching manual_run_answer_eval.py's own established
    # convention: point_test_env_at_env_vars() must run before Settings
    # is ever constructed, and this module's own top-level imports (used
    # by the pytest-driven live test) must stay importable with no env at
    # all -- so the env-pointing dance stays inside main(), never at
    # module scope.
    from scripts._shared import point_test_env_at_env_vars

    point_test_env_at_env_vars()
    from app.config import get_settings
    from app.evals.eval_summary import format_delta_table, load_previous_summary, write_summary

    settings = get_settings()
    client = qdrant.build_qdrant_client(settings.qdrant_url, settings.qdrant_api_key_str())
    try:
        summary = asyncio.run(_run_and_summarize(client))
    finally:
        asyncio.run(client.close())

    previous = load_previous_summary(DEFAULT_SUMMARY_PATH)
    print(format_delta_table(previous, summary))
    write_summary(DEFAULT_SUMMARY_PATH, summary)
    print(f"\nSummary written to {DEFAULT_SUMMARY_PATH} -- commit it to track this run.")


if __name__ == "__main__":
    main()
