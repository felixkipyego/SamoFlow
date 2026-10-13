# backend/tests/test_golden_set.py
# Task 3.7.b: the golden-set schema/loader (app/evals/golden_set.py) and
# the committed synthetic-corpus half of the set itself. Offline only --
# resolution against the real frozen corpus is pure text containment
# (no Qdrant, no embeddings consumed), so this needs no live services
# and no skips.
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.evals.golden_set import (
    CATEGORIES,
    DEFAULT_ITEMS_PATH,
    DEFAULT_SYNTHETIC_CORPUS_PATH,
    EvidencePassageNotFoundError,
    FrozenChunk,
    FrozenCorpus,
    GoldenSet,
    GoldenSetItem,
    load_frozen_corpus,
    load_golden_set,
    resolve_golden_set,
    resolve_item_sources,
)

_VALID_ITEM = {
    "id": "x-01",
    "category": "answerable",
    "corpus": "synthetic",
    "question": "how much does shipping cost",
    "expected_route": "answer",
    "expected_can_answer": True,
    "expected_sources": [{"document": "shipping-domestic", "evidence_passage": "a sentence"}],
}


def _item(**overrides) -> dict:
    return {**_VALID_ITEM, **overrides}


# --- strict schema: unknown/missing/malformed fields --------------------


def test_unknown_field_on_an_item_is_rejected():
    with pytest.raises(ValidationError):
        GoldenSetItem.model_validate(_item(not_a_real_field="x"))


def test_unknown_field_on_an_expected_source_is_rejected():
    with pytest.raises(ValidationError):
        GoldenSetItem.model_validate(
            _item(expected_sources=[{"document": "d", "evidence_passage": "p", "chunk_id": "c1"}])
        )


def test_missing_required_field_is_rejected():
    broken = _item()
    del broken["question"]
    with pytest.raises(ValidationError):
        GoldenSetItem.model_validate(broken)


def test_invalid_category_value_is_rejected():
    with pytest.raises(ValidationError):
        GoldenSetItem.model_validate(_item(category="not_a_real_category"))


def test_invalid_expected_route_value_is_rejected():
    with pytest.raises(ValidationError):
        GoldenSetItem.model_validate(_item(expected_route="not_a_real_route"))


def test_empty_question_is_rejected():
    with pytest.raises(ValidationError):
        GoldenSetItem.model_validate(_item(question=""))


def test_overlong_evidence_passage_is_rejected():
    with pytest.raises(ValidationError):
        GoldenSetItem.model_validate(
            _item(
                expected_sources=[
                    {"document": "shipping-domestic", "evidence_passage": "x" * 301}
                ]
            )
        )


def test_duplicate_item_ids_are_rejected():
    with pytest.raises(ValidationError):
        GoldenSet.model_validate({"items": [_item(id="dup"), _item(id="dup")]})


# --- category/route/can_answer cross-field shape ------------------------


def test_greeting_with_expected_can_answer_true_is_rejected():
    with pytest.raises(ValidationError):
        GoldenSetItem.model_validate(
            _item(
                id="g-01", category="greeting", expected_route="greeting",
                expected_can_answer=True, expected_sources=[],
            )
        )


def test_off_topic_routed_to_answer_is_rejected():
    with pytest.raises(ValidationError):
        GoldenSetItem.model_validate(
            _item(
                id="o-01", category="off_topic", expected_route="answer",
                expected_can_answer=False, expected_sources=[],
            )
        )


def test_follow_up_with_no_prior_turns_is_rejected():
    with pytest.raises(ValidationError):
        GoldenSetItem.model_validate(_item(id="f-01", category="follow_up", prior_turns=[]))


def test_multi_source_with_only_one_document_is_rejected():
    with pytest.raises(ValidationError):
        GoldenSetItem.model_validate(
            _item(
                id="m-01", category="multi_source",
                expected_sources=[
                    {"document": "shipping-domestic", "evidence_passage": "a"},
                    {"document": "shipping-domestic", "evidence_passage": "b"},
                ],
            )
        )


def test_multi_source_with_two_distinct_documents_is_accepted():
    item = GoldenSetItem.model_validate(
        _item(
            id="m-02", category="multi_source",
            expected_sources=[
                {"document": "shipping-domestic", "evidence_passage": "a"},
                {"document": "returns-general", "evidence_passage": "b"},
            ],
        )
    )
    assert item.category == "multi_source"


# --- evidence-passage resolution ("fails loudly if nothing matches") ----


def _corpus(chunks: list[FrozenChunk]) -> FrozenCorpus:
    return FrozenCorpus(corpus="synthetic", documents=["doc-a"], chunks=chunks)


def _chunk(document: str, text: str, chunk_index: int = 0) -> FrozenChunk:
    return FrozenChunk(
        document=document, chunk_index=chunk_index, heading_path=["Section"], text=text,
        content_hash="hash", embedding_model="fake", embedding_version="fake",
        dense_vector=[0.0], sparse_indices=[], sparse_values=[],
    )


def test_resolve_item_sources_finds_the_real_chunk():
    item = GoldenSetItem.model_validate(
        _item(
            expected_sources=[
                {"document": "doc-a", "evidence_passage": "exact phrase"}
            ]
        )
    )
    corpus = _corpus([_chunk("doc-a", "some text with the exact phrase inside it")])
    resolved = resolve_item_sources(item, corpus)
    assert resolved[0].chunk_indices == (0,)


def test_resolve_item_sources_raises_loudly_when_nothing_matches():
    item = GoldenSetItem.model_validate(
        _item(
            expected_sources=[
                {"document": "doc-a", "evidence_passage": "a phrase that does not exist"}
            ]
        )
    )
    corpus = _corpus([_chunk("doc-a", "completely unrelated text")])
    with pytest.raises(EvidencePassageNotFoundError):
        resolve_item_sources(item, corpus)


def test_resolve_item_sources_raises_when_the_document_itself_is_absent():
    item = GoldenSetItem.model_validate(
        _item(expected_sources=[{"document": "doc-missing", "evidence_passage": "exact phrase"}])
    )
    corpus = _corpus([_chunk("doc-a", "exact phrase lives in the wrong document")])
    with pytest.raises(EvidencePassageNotFoundError):
        resolve_item_sources(item, corpus)


def test_resolve_golden_set_skips_items_whose_corpus_has_no_frozen_snapshot():
    golden_set = GoldenSet.model_validate({"items": [_item(corpus="company_site")]})
    resolved = resolve_golden_set(golden_set, corpora={})
    assert resolved == {}


# --- the committed synthetic-corpus half of the real golden set ---------


def test_committed_golden_set_loads_and_validates():
    golden_set = load_golden_set(DEFAULT_ITEMS_PATH)
    assert golden_set.items


def test_committed_frozen_corpus_loads_and_validates():
    corpus = load_frozen_corpus(DEFAULT_SYNTHETIC_CORPUS_PATH)
    assert corpus.chunks
    assert corpus.corpus == "synthetic"


def test_every_evidence_passage_in_the_committed_set_resolves_to_a_real_chunk():
    golden_set = load_golden_set(DEFAULT_ITEMS_PATH)
    corpus = load_frozen_corpus(DEFAULT_SYNTHETIC_CORPUS_PATH)
    synthetic_items = [item for item in golden_set.items if item.corpus == "synthetic"]
    assert synthetic_items, "expected at least one synthetic-corpus item in the committed set"
    resolved = resolve_golden_set(golden_set, {"synthetic": corpus})
    for item in synthetic_items:
        assert item.id in resolved, f"item {item.id!r} was not resolved against the corpus"


def test_committed_category_counts_are_within_15_and_20():
    golden_set = load_golden_set(DEFAULT_ITEMS_PATH)
    counts = golden_set.category_counts()
    assert set(counts) == set(CATEGORIES)
    for category, count in counts.items():
        assert 15 <= count <= 20, f"category {category!r} has {count} items, expected 15-20"


def test_every_synthetic_corpus_document_is_referenced_by_at_least_one_item():
    golden_set = load_golden_set(DEFAULT_ITEMS_PATH)
    corpus = load_frozen_corpus(DEFAULT_SYNTHETIC_CORPUS_PATH)
    referenced = {
        source.document
        for item in golden_set.items
        if item.corpus == "synthetic"
        for source in item.expected_sources
    }
    missing = set(corpus.documents) - referenced
    assert not missing, f"document(s) never referenced by any item: {sorted(missing)}"


def test_escalation_is_not_a_category():
    # Task 3.7.b's own explicit scope limit: escalation-trigger items
    # depend on Step 4.4 (docs/SPEC.md §7's own YAML trigger-phrase file,
    # not yet built) -- faking expected behavior for a mechanism that
    # doesn't exist would be a guess, not data. See PROJECT_SPEC.md's own
    # §9 marker, owner Step 4.4.
    assert "escalation" not in CATEGORIES


def test_pages_directory_has_no_orphaned_page_missing_from_the_frozen_corpus():
    pages_dir = Path(DEFAULT_SYNTHETIC_CORPUS_PATH).parent / "pages"
    page_documents = {p.stem for p in pages_dir.glob("*.md")}
    corpus = load_frozen_corpus(DEFAULT_SYNTHETIC_CORPUS_PATH)
    assert page_documents == set(corpus.documents)
