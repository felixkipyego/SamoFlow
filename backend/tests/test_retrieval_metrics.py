# backend/tests/test_retrieval_metrics.py
# Task 3.7.c: pure metric math (app/evals/retrieval_metrics.py) against
# hand-built inputs -- no Qdrant, no retrieve(), no live services.
import pytest

from app.evals.retrieval_metrics import (
    ItemRetrievalResult,
    all_documents_recall_at_k,
    gate_pass_shares,
    mean_reciprocal_rank,
    recall_at_k,
    score_distribution,
)


def _result(
    item_id: str,
    expected: set[tuple[str, int]],
    retrieved: list[tuple[str, int]],
    *,
    doc_map: dict[str, set[tuple[str, int]]] | None = None,
    best_dense_score: float | None = 0.5,
) -> ItemRetrievalResult:
    return ItemRetrievalResult(
        item_id=item_id,
        category="answerable",
        expected_chunk_keys=frozenset(expected),
        expected_doc_to_chunk_keys={k: frozenset(v) for k, v in (doc_map or {}).items()},
        retrieved_keys=tuple(retrieved),
        best_dense_score=best_dense_score,
    )


# --- recall_at_k ----------------------------------------------------------


def test_recall_at_k_hit_at_rank_1_counts_for_every_k():
    results = [_result("a", {("doc", 0)}, [("doc", 0), ("other", 1)])]
    assert recall_at_k(results, 1) == 1.0
    assert recall_at_k(results, 3) == 1.0


def test_recall_at_k_hit_outside_the_window_does_not_count():
    results = [_result("a", {("doc", 0)}, [("x", 9), ("y", 9), ("doc", 0)])]
    assert recall_at_k(results, 1) == 0.0
    assert recall_at_k(results, 2) == 0.0
    assert recall_at_k(results, 3) == 1.0


def test_recall_at_k_no_hit_anywhere_is_zero():
    results = [_result("a", {("doc", 0)}, [("x", 1), ("y", 2)])]
    assert recall_at_k(results, 8) == 0.0


def test_recall_at_k_averages_across_items():
    results = [
        _result("hit", {("doc", 0)}, [("doc", 0)]),
        _result("miss", {("doc", 0)}, [("other", 1)]),
    ]
    assert recall_at_k(results, 1) == 0.5


def test_recall_at_k_rejects_empty_results():
    with pytest.raises(ValueError, match="at least one"):
        recall_at_k([], 1)


def test_recall_at_k_any_expected_chunk_counts_as_a_hit():
    # expected_chunk_keys can legitimately hold more than one chunk_index
    # for a single logical source (a passage spanning an overlap window).
    results = [_result("a", {("doc", 0), ("doc", 1)}, [("doc", 1)])]
    assert recall_at_k(results, 1) == 1.0


# --- all_documents_recall_at_k (multi_source only) -------------------------


def test_all_documents_recall_requires_every_document_present():
    both = _result(
        "both", set(), [("doc-a", 0), ("doc-b", 0)],
        doc_map={"doc-a": {("doc-a", 0)}, "doc-b": {("doc-b", 0)}},
    )
    only_one = _result(
        "one", set(), [("doc-a", 0), ("other", 9)],
        doc_map={"doc-a": {("doc-a", 0)}, "doc-b": {("doc-b", 0)}},
    )
    assert all_documents_recall_at_k([both], 8) == 1.0
    assert all_documents_recall_at_k([only_one], 8) == 0.0


def test_all_documents_recall_respects_k_independently_per_document():
    # doc-a found at rank 1, doc-b only at rank 3 -- present at k=3 but not k=2.
    result = _result(
        "r", set(), [("doc-a", 0), ("x", 9), ("doc-b", 0)],
        doc_map={"doc-a": {("doc-a", 0)}, "doc-b": {("doc-b", 0)}},
    )
    assert all_documents_recall_at_k([result], 2) == 0.0
    assert all_documents_recall_at_k([result], 3) == 1.0


def test_all_documents_recall_rejects_an_item_with_no_doc_map():
    result = _result("a", {("doc", 0)}, [("doc", 0)])  # doc_map omitted -> empty
    with pytest.raises(ValueError, match="multi_source"):
        all_documents_recall_at_k([result], 8)


# --- mean_reciprocal_rank ---------------------------------------------------


def test_mrr_of_a_single_rank_1_hit_is_1():
    results = [_result("a", {("doc", 0)}, [("doc", 0)])]
    assert mean_reciprocal_rank(results) == 1.0


def test_mrr_of_a_rank_3_hit_is_one_third():
    results = [_result("a", {("doc", 0)}, [("x", 1), ("y", 2), ("doc", 0)])]
    assert mean_reciprocal_rank(results) == pytest.approx(1 / 3)


def test_mrr_of_no_hit_is_zero():
    results = [_result("a", {("doc", 0)}, [("x", 1)])]
    assert mean_reciprocal_rank(results) == 0.0


def test_mrr_averages_reciprocal_ranks_across_items():
    results = [
        _result("rank1", {("doc", 0)}, [("doc", 0)]),
        _result("rank2", {("doc", 0)}, [("x", 1), ("doc", 0)]),
    ]
    assert mean_reciprocal_rank(results) == pytest.approx((1.0 + 0.5) / 2)


def test_mrr_rejects_empty_results():
    with pytest.raises(ValueError, match="at least one"):
        mean_reciprocal_rank([])


# --- gate_pass_shares --------------------------------------------------------


def test_gate_pass_shares_counts_scores_at_or_above_threshold():
    shares = gate_pass_shares([0.1, 0.3, 0.5], thresholds=[0.2, 0.4])
    assert shares[0.2] == pytest.approx(2 / 3)  # 0.3 and 0.5 pass
    assert shares[0.4] == pytest.approx(1 / 3)  # only 0.5 passes


def test_gate_pass_shares_none_score_never_passes():
    shares = gate_pass_shares([None, 0.9], thresholds=[0.1])
    assert shares[0.1] == pytest.approx(0.5)


def test_gate_pass_shares_boundary_is_inclusive():
    shares = gate_pass_shares([0.2], thresholds=[0.2])
    assert shares[0.2] == 1.0


def test_gate_pass_shares_rejects_empty_scores():
    with pytest.raises(ValueError, match="at least one"):
        gate_pass_shares([], thresholds=[0.1])


# --- score_distribution ------------------------------------------------------


def test_score_distribution_min_median_max():
    dist = score_distribution([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8])
    assert dist.minimum == 0.1
    assert dist.maximum == 0.8
    assert dist.median == pytest.approx(0.45)
    assert dist.p25 < dist.median < dist.p75


def test_score_distribution_single_value_is_degenerate():
    dist = score_distribution([0.42])
    assert dist.minimum == dist.p25 == dist.median == dist.p75 == dist.maximum == 0.42


def test_score_distribution_rejects_empty_scores():
    with pytest.raises(ValueError, match="at least one"):
        score_distribution([])
