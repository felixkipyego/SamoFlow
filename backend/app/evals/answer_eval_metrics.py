# backend/app/evals/answer_eval_metrics.py
# Task 3.7.d [SECURITY]: deterministic (no judge) metrics over a real
# end-to-end run_turn() outcome -- route vs expected_route, can_answer
# confusion matrix, citation hit rate, and the zero-citations-on-a-non-
# answer-turn rule. Pure functions over an already-collected `TurnOutcome`
# -- no Qdrant, no LLM SDK, no network anywhere in this module (safe to
# live under backend/app/, confirmed against the Qdrant read-path guard's
# own scan, which only restricts app.qdrant/qdrant_client imports).
from dataclasses import dataclass

from app.agent.graph import (
    _DEPENDENCY_FAILURE_TEXT,
    _GUARDRAIL_REPLY_TEXT,
    _NO_ANSWER_FALLBACK_TEXT,
    _sanitize_answer_text,
)

# Sanitised once, at import time -- `_sanitize_answer_text()` is pure and
# deterministic (Task 3.4.c), so these never change between calls.
_SANITIZED_GUARDRAIL_TEXT = _sanitize_answer_text(_GUARDRAIL_REPLY_TEXT)
_SANITIZED_NO_ANSWER_TEXT = _sanitize_answer_text(_NO_ANSWER_FALLBACK_TEXT)
_SANITIZED_DEPENDENCY_FAILURE_TEXT = _sanitize_answer_text(_DEPENDENCY_FAILURE_TEXT)


def infer_actual_route(final_text: str, can_answer: bool) -> str:
    """Maps a real completed turn's own final AIMessage text back to one
    of this golden set's own `expected_route` values -- by comparing
    against the graph's own THREE fixed, never-model-written reply
    constants (never by inspecting which LangGraph node fired, which
    would need parsing `stream_mode="updates"` chunk-by-chunk for no
    extra certainty: these three strings are already mutually exclusive
    and exhaustive for every non-answer path, confirmed by reading
    `_guardrail_node`/`_no_answer_fallback_node`/`_dependency_failure_
    fallback_node` directly). `_no_answer_fallback_node`'s own docstring
    confirms its ONE reply text covers BOTH "gate found nothing relevant"
    and "model itself said can_answer=false" -- so `off_topic` and
    `related_unanswerable` items are expected to land here identically,
    by design, not a detection gap.

    `"dependency_failure"` and `"unknown"` are never a valid
    `expected_route` value in this golden set -- either one occurring
    for a real item is a genuine anomaly, correctly surfaced as a route
    MISMATCH rather than silently coerced into one of the three expected
    buckets.
    """
    if final_text == _SANITIZED_GUARDRAIL_TEXT:
        return "greeting"
    if final_text == _SANITIZED_NO_ANSWER_TEXT:
        return "gate_fallback"
    if final_text == _SANITIZED_DEPENDENCY_FAILURE_TEXT:
        return "dependency_failure"
    if can_answer:
        return "answer"
    return "unknown"


@dataclass(frozen=True)
class TurnOutcome:
    """One golden-set item's own real `run_turn()` outcome -- built by the
    runner script from the completed turn's final state, never from a
    raw stream chunk.
    """

    item_id: str
    category: str
    expected_route: str
    expected_can_answer: bool
    expected_evidence_passages: tuple[str, ...]
    actual_route: str
    actual_can_answer: bool
    cited_chunk_labels: tuple[str, ...]
    citations: tuple[dict, ...]
    retrieved_chunk_texts: tuple[str, ...]
    final_text: str


def route_confusion_matrix(outcomes: list[TurnOutcome]) -> dict[str, dict[str, int]]:
    """expected_route -> {actual_route: count}."""
    matrix: dict[str, dict[str, int]] = {}
    for outcome in outcomes:
        row = matrix.setdefault(outcome.expected_route, {})
        row[outcome.actual_route] = row.get(outcome.actual_route, 0) + 1
    return matrix


@dataclass(frozen=True)
class CanAnswerConfusion:
    true_positive: int = 0
    false_positive: int = 0
    true_negative: int = 0
    false_negative: int = 0

    @property
    def total(self) -> int:
        return self.true_positive + self.false_positive + self.true_negative + self.false_negative

    @property
    def accuracy(self) -> float:
        if self.total == 0:
            raise ValueError("no outcomes to compute accuracy over")
        return (self.true_positive + self.true_negative) / self.total


def can_answer_confusion_matrix(outcomes: list[TurnOutcome]) -> CanAnswerConfusion:
    tp = fp = tn = fn = 0
    for outcome in outcomes:
        if outcome.expected_can_answer and outcome.actual_can_answer:
            tp += 1
        elif not outcome.expected_can_answer and outcome.actual_can_answer:
            fp += 1
        elif not outcome.expected_can_answer and not outcome.actual_can_answer:
            tn += 1
        else:
            fn += 1
    return CanAnswerConfusion(
        true_positive=tp, false_positive=fp, true_negative=tn, false_negative=fn
    )


def cited_chunk_texts(outcome: TurnOutcome) -> list[str]:
    """Resolves `cited_chunk_labels` (e.g. `["c1", "c2"]`) back to the
    actual retrieved chunk TEXT at that position -- `_build_messages()`'s
    own positional labelling scheme (`app/agent/graph.py`: `c{index+1}`
    for `retrieved_chunks[index]`), imported nowhere, just the same
    1-indexed convention applied here directly since both already share
    it by construction.
    """
    texts = []
    for label in outcome.cited_chunk_labels:
        if not (label.startswith("c") and label[1:].isdigit()):
            continue
        index = int(label[1:]) - 1
        if 0 <= index < len(outcome.retrieved_chunk_texts):
            texts.append(outcome.retrieved_chunk_texts[index])
    return texts


def citation_is_a_hit(outcome: TurnOutcome) -> bool | None:
    """True if at least one CITED chunk's own text contains at least one
    of the item's own expected evidence passages (resolved exactly as in
    Task 3.7.b -- verbatim substring containment, never a chunk id).
    `None` (not applicable) when the turn never reached a real answer at
    all -- citation hit rate is only meaningful for turns that actually
    cited something.
    """
    if not outcome.actual_can_answer:
        return None
    texts = cited_chunk_texts(outcome)
    if not texts:
        return False
    return any(
        passage in text for text in texts for passage in outcome.expected_evidence_passages
    )


def citation_hit_rate(outcomes: list[TurnOutcome]) -> float:
    hits = [citation_is_a_hit(outcome) for outcome in outcomes]
    applicable = [hit for hit in hits if hit is not None]
    if not applicable:
        raise ValueError("no can_answer=true outcomes to compute a citation hit rate over")
    return sum(applicable) / len(applicable)


def zero_citation_violations(outcomes: list[TurnOutcome]) -> list[str]:
    """Returns the item ids that VIOLATE "zero citations on a gate-
    fallback or can_answer=false turn" -- citations/cited_chunk_labels
    non-empty despite the turn not actually answering. An empty return
    list is the rule holding, not an absence of applicable turns.
    """
    violations = []
    for outcome in outcomes:
        is_non_answer = outcome.actual_route == "gate_fallback" or not outcome.actual_can_answer
        has_citations = bool(outcome.citations) or bool(outcome.cited_chunk_labels)
        if is_non_answer and has_citations:
            violations.append(outcome.item_id)
    return violations


def group_by_category(outcomes: list[TurnOutcome]) -> dict[str, list[TurnOutcome]]:
    grouped: dict[str, list[TurnOutcome]] = {}
    for outcome in outcomes:
        grouped.setdefault(outcome.category, []).append(outcome)
    return grouped
