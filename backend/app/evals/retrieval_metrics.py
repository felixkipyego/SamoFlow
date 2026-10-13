# backend/app/evals/retrieval_metrics.py
# Task 3.7.c: pure metric math over already-retrieved results -- no
# Qdrant, no embeddings, no network anywhere in this module. Orchestration
# (seeding a disposable collection, calling the real retrieve(), building
# `ItemRetrievalResult`s) lives in `retrieval_eval.py`; this module is
# unit-tested directly against hand-built inputs.
import statistics
from dataclasses import dataclass

ChunkKey = tuple[str, int]  # (document, chunk_index)


@dataclass(frozen=True)
class ItemRetrievalResult:
    """One golden-set item's own real retrieval outcome. `retrieved_keys`
    is the RANKED list of (document, chunk_index) pairs `retrieve()`
    actually returned, truncated to the largest k this project measures
    (8) -- recall@1/3/5/8 are computed by slicing this ONE ranked list,
    never by re-querying per k (the ranking for a smaller k is always a
    prefix of the larger one, confirmed by how Qdrant's own Query API
    orders results -- re-querying would cost 4x the round trips for
    identical information).
    """

    item_id: str
    category: str
    # ANY-match target: the union of every expected_sources entry's own
    # resolved chunk_indices, used by recall@k/MRR uniformly (including
    # multi_source, where it answers "was ANY expected chunk found").
    expected_chunk_keys: frozenset[ChunkKey]
    # multi_source only: one key-set PER expected document, used by
    # all_documents_recall_at_k() to require every document represented,
    # not just one of them. Empty for every other category.
    expected_doc_to_chunk_keys: dict[str, frozenset[ChunkKey]]
    retrieved_keys: tuple[ChunkKey, ...]
    best_dense_score: float | None


def _hit_rank(expected: frozenset[ChunkKey], retrieved: tuple[ChunkKey, ...], k: int) -> int | None:
    """1-indexed rank of the first key in `retrieved[:k]` that is also in
    `expected`, or None if no such key exists within that slice.
    """
    for rank, key in enumerate(retrieved[:k], start=1):
        if key in expected:
            return rank
    return None


def recall_at_k(results: list[ItemRetrievalResult], k: int) -> float:
    """Fraction of `results` with at least one expected chunk within the
    top k retrieved keys (the ANY-match definition -- see
    `ItemRetrievalResult`'s own docstring).
    """
    if not results:
        raise ValueError("recall_at_k() needs at least one result")
    hits = sum(
        1 for r in results if _hit_rank(r.expected_chunk_keys, r.retrieved_keys, k) is not None
    )
    return hits / len(results)


def all_documents_recall_at_k(results: list[ItemRetrievalResult], k: int) -> float:
    """multi_source's own stricter measure: ALL of an item's expected
    documents must each have at least one matching chunk within the top
    k, not merely any one of them. Every `result` passed in must carry a
    non-empty `expected_doc_to_chunk_keys` (the caller filters to
    category="multi_source" first) -- raises if that invariant is
    violated, rather than silently treating a 0-document item as a
    vacuous pass.
    """
    if not results:
        raise ValueError("all_documents_recall_at_k() needs at least one result")
    hits = 0
    for r in results:
        if not r.expected_doc_to_chunk_keys:
            raise ValueError(
                f"item {r.item_id!r} has no expected_doc_to_chunk_keys -- "
                "all_documents_recall_at_k() is only meaningful for multi_source items"
            )
        if all(
            _hit_rank(doc_keys, r.retrieved_keys, k) is not None
            for doc_keys in r.expected_doc_to_chunk_keys.values()
        ):
            hits += 1
    return hits / len(results)


def mean_reciprocal_rank(results: list[ItemRetrievalResult]) -> float:
    """MRR using the SAME any-match hit definition as recall_at_k, over
    the full retrieved list (no k truncation) -- an item with no hit at
    all contributes 0, matching the standard MRR convention.
    """
    if not results:
        raise ValueError("mean_reciprocal_rank() needs at least one result")
    total = 0.0
    for r in results:
        rank = _hit_rank(r.expected_chunk_keys, r.retrieved_keys, len(r.retrieved_keys))
        if rank is not None:
            total += 1.0 / rank
    return total / len(results)


def gate_pass_shares(scores: list[float | None], thresholds: list[float]) -> dict[float, float]:
    """For each threshold, the fraction of `scores` that are not None and
    >= that threshold -- a missing score (no points retrieved at all)
    never passes, regardless of threshold. Reported only (docs/SPEC.md
    §6.3's own gate threshold is a Settings field, unchanged by this
    sweep) -- see retrieval_eval.py's own module docstring.
    """
    if not scores:
        raise ValueError("gate_pass_shares() needs at least one score")
    return {
        t: sum(1 for s in scores if s is not None and s >= t) / len(scores) for t in thresholds
    }


@dataclass(frozen=True)
class ScoreDistribution:
    minimum: float
    p25: float
    median: float
    p75: float
    maximum: float


def score_distribution(scores: list[float]) -> ScoreDistribution:
    """min/p25/median/p75/max over `scores` -- every item in the category
    must carry a real score (callers drop `None`s, i.e. items with
    nothing retrieved at all, before calling this; a category where
    every item scored None has no meaningful distribution to report).
    Quartiles via the stdlib `statistics` module (`quantiles(n=4)`,
    "exclusive" method, its own default) -- no numpy/pandas dependency
    for five numbers, matching this project's own established "no heavy
    dependency where stdlib suffices" discipline (PROJECT_SPEC.md's own
    PART 4 decision keeping arize-phoenix-evals's pandas/numpy tree
    dev-only is the identical discipline, one level up).
    """
    if not scores:
        raise ValueError("score_distribution() needs at least one score")
    if len(scores) == 1:
        value = scores[0]
        return ScoreDistribution(minimum=value, p25=value, median=value, p75=value, maximum=value)
    q1, _, q3 = statistics.quantiles(scores, n=4)
    return ScoreDistribution(
        minimum=min(scores), p25=q1, median=statistics.median(scores), p75=q3, maximum=max(scores)
    )
