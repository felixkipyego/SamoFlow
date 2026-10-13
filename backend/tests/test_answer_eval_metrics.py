# backend/tests/test_answer_eval_metrics.py
# Task 3.7.d: deterministic answer-eval metrics (app/evals/answer_eval_
# metrics.py) against hand-built TurnOutcome inputs. Offline only.
import pytest

from app.agent.graph import (
    _DEPENDENCY_FAILURE_TEXT,
    _GUARDRAIL_REPLY_TEXT,
    _NO_ANSWER_FALLBACK_TEXT,
    _sanitize_answer_text,
)
from app.evals.answer_eval_metrics import (
    TurnOutcome,
    can_answer_confusion_matrix,
    citation_hit_rate,
    citation_is_a_hit,
    group_by_category,
    infer_actual_route,
    route_confusion_matrix,
    zero_citation_violations,
)


def _outcome(**overrides) -> TurnOutcome:
    defaults = dict(
        item_id="x-01",
        category="answerable",
        expected_route="answer",
        expected_can_answer=True,
        expected_evidence_passages=("Standard domestic shipping",),
        actual_route="answer",
        actual_can_answer=True,
        cited_chunk_labels=("c1",),
        citations=({"url": "https://eval.example/doc", "heading_path": []},),
        retrieved_chunk_texts=("Standard domestic shipping takes 5 to 7 days.",),
        final_text="Standard domestic shipping takes 5 to 7 days.",
    )
    defaults.update(overrides)
    return TurnOutcome(**defaults)


# --- infer_actual_route ----------------------------------------------------


def test_infer_actual_route_matches_the_sanitised_guardrail_text():
    text = _sanitize_answer_text(_GUARDRAIL_REPLY_TEXT)
    assert infer_actual_route(text, can_answer=False) == "greeting"


def test_infer_actual_route_matches_the_sanitised_no_answer_text():
    text = _sanitize_answer_text(_NO_ANSWER_FALLBACK_TEXT)
    assert infer_actual_route(text, can_answer=False) == "gate_fallback"


def test_infer_actual_route_matches_the_sanitised_dependency_failure_text():
    text = _sanitize_answer_text(_DEPENDENCY_FAILURE_TEXT)
    assert infer_actual_route(text, can_answer=False) == "dependency_failure"


def test_infer_actual_route_is_answer_when_can_answer_true_and_text_is_novel():
    assert infer_actual_route("A real generated answer.", can_answer=True) == "answer"


def test_infer_actual_route_is_unknown_when_can_answer_false_and_text_is_novel():
    # A genuine anomaly -- can_answer=False but the text matches none of
    # the three known fixed replies. Never silently coerced.
    assert infer_actual_route("some unexpected text", can_answer=False) == "unknown"


# --- route_confusion_matrix --------------------------------------------------


def test_route_confusion_matrix_counts_matches_and_mismatches():
    outcomes = [
        _outcome(expected_route="answer", actual_route="answer"),
        _outcome(expected_route="answer", actual_route="gate_fallback"),
        _outcome(expected_route="gate_fallback", actual_route="gate_fallback"),
    ]
    matrix = route_confusion_matrix(outcomes)
    assert matrix["answer"] == {"answer": 1, "gate_fallback": 1}
    assert matrix["gate_fallback"] == {"gate_fallback": 1}


# --- can_answer_confusion_matrix --------------------------------------------


def test_can_answer_confusion_matrix_classifies_all_four_cells():
    outcomes = [
        _outcome(expected_can_answer=True, actual_can_answer=True),  # TP
        _outcome(expected_can_answer=False, actual_can_answer=True),  # FP
        _outcome(expected_can_answer=False, actual_can_answer=False),  # TN
        _outcome(expected_can_answer=True, actual_can_answer=False),  # FN
    ]
    confusion = can_answer_confusion_matrix(outcomes)
    assert confusion.true_positive == 1
    assert confusion.false_positive == 1
    assert confusion.true_negative == 1
    assert confusion.false_negative == 1
    assert confusion.total == 4
    assert confusion.accuracy == 0.5


def test_can_answer_confusion_matrix_accuracy_rejects_empty_input():
    confusion = can_answer_confusion_matrix([])
    with pytest.raises(ValueError, match="no outcomes"):
        _ = confusion.accuracy


# --- citation hit rate -------------------------------------------------------


def test_citation_is_a_hit_true_when_cited_chunk_contains_the_passage():
    outcome = _outcome(
        cited_chunk_labels=("c1",),
        retrieved_chunk_texts=("The warranty lasts 1 year for headphones.",),
        expected_evidence_passages=("warranty lasts 1 year",),
    )
    assert citation_is_a_hit(outcome) is True


def test_citation_is_a_hit_false_when_cited_chunk_lacks_the_passage():
    outcome = _outcome(
        cited_chunk_labels=("c1",),
        retrieved_chunk_texts=("Completely unrelated text.",),
        expected_evidence_passages=("warranty lasts 1 year",),
    )
    assert citation_is_a_hit(outcome) is False


def test_citation_is_a_hit_false_when_nothing_was_cited():
    outcome = _outcome(cited_chunk_labels=(), retrieved_chunk_texts=("some text",))
    assert citation_is_a_hit(outcome) is False


def test_citation_is_a_hit_none_when_the_turn_never_answered():
    outcome = _outcome(actual_can_answer=False)
    assert citation_is_a_hit(outcome) is None


def test_citation_is_a_hit_resolves_the_second_cited_label_correctly():
    outcome = _outcome(
        cited_chunk_labels=("c2",),
        retrieved_chunk_texts=("first chunk, irrelevant", "second chunk has the fact"),
        expected_evidence_passages=("has the fact",),
    )
    assert citation_is_a_hit(outcome) is True


def test_citation_hit_rate_averages_over_applicable_turns_only():
    outcomes = [
        _outcome(
            cited_chunk_labels=("c1",), retrieved_chunk_texts=("the fact is here",),
            expected_evidence_passages=("the fact is here",),
        ),
        _outcome(
            cited_chunk_labels=("c1",), retrieved_chunk_texts=("nothing relevant",),
            expected_evidence_passages=("the fact is here",),
        ),
        _outcome(actual_can_answer=False),  # not applicable, excluded from the average
    ]
    assert citation_hit_rate(outcomes) == 0.5


def test_citation_hit_rate_rejects_no_applicable_turns():
    with pytest.raises(ValueError, match="no can_answer=true"):
        citation_hit_rate([_outcome(actual_can_answer=False)])


# --- zero-citation rule -------------------------------------------------------


def test_zero_citation_rule_holds_when_gate_fallback_has_no_citations():
    outcome = _outcome(
        actual_route="gate_fallback", actual_can_answer=False,
        citations=(), cited_chunk_labels=(),
    )
    assert zero_citation_violations([outcome]) == []


def test_zero_citation_rule_flags_citations_present_on_a_gate_fallback_turn():
    outcome = _outcome(
        item_id="bad-1", actual_route="gate_fallback", actual_can_answer=False,
        citations=({"url": "https://eval.example/x", "heading_path": []},),
        cited_chunk_labels=("c1",),
    )
    assert zero_citation_violations([outcome]) == ["bad-1"]


def test_zero_citation_rule_flags_cited_labels_with_no_citations_object():
    # Citations list could be empty while cited_chunk_labels is not (a
    # real possible inconsistency) -- either signal alone must trigger.
    outcome = _outcome(
        item_id="bad-2", actual_route="gate_fallback", actual_can_answer=False,
        citations=(), cited_chunk_labels=("c1",),
    )
    assert zero_citation_violations([outcome]) == ["bad-2"]


def test_zero_citation_rule_does_not_flag_a_real_answer_turn():
    outcome = _outcome(actual_route="answer", actual_can_answer=True)
    assert zero_citation_violations([outcome]) == []


# --- group_by_category -------------------------------------------------------


def test_group_by_category_partitions_outcomes():
    outcomes = [
        _outcome(category="answerable"),
        _outcome(category="off_topic"),
        _outcome(category="answerable"),
    ]
    grouped = group_by_category(outcomes)
    assert len(grouped["answerable"]) == 2
    assert len(grouped["off_topic"]) == 1
