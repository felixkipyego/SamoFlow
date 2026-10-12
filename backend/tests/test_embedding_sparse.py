# backend/tests/test_embedding_sparse.py
# Task 2.5.b: tests for embed_sparse() (backend/app/ingest/embedding.py).
# Offline after the model's own one-time download (no test database, no
# OpenAI calls) but DELIBERATELY makes REAL fastembed calls -- no
# stubbing, unlike test_embedding.py's own embed_dense() tests. This is a
# deliberate difference, not an inconsistency: fastembed is a free, local,
# model-based library with no billing/API-key concern (unlike embed_dense()'s
# real, paid OpenAI dependency), so there is nothing to protect the
# committed suite from by stubbing it.
from qdrant_client.http.models import SparseVector

from app.ingest.embedding import SparseVectorData, embed_sparse, embed_sparse_query


async def test_embed_sparse_returns_sparse_vectors_with_matching_indices_and_values_length():
    result = await embed_sparse(["a simple test sentence"])

    assert len(result) == 1
    vector = result[0]
    assert isinstance(vector, SparseVectorData)
    assert len(vector.indices) == len(vector.values)
    assert len(vector.indices) > 0
    assert all(isinstance(i, int) for i in vector.indices)
    assert all(isinstance(v, float) for v in vector.values)


async def test_embed_sparse_is_deterministic_across_separate_calls():
    first = await embed_sparse(["determinism check text"])
    second = await embed_sparse(["determinism check text"])

    assert first[0].indices == second[0].indices
    assert first[0].values == second[0].values


async def test_embed_sparse_different_texts_produce_different_sparse_vectors():
    result = await embed_sparse(["the quick brown fox", "a totally unrelated sentence about cats"])

    assert result[0].indices != result[1].indices


async def test_embed_sparse_values_are_bm25_tf_saturation_not_raw_occurrence_counts():
    # Task 2.5.b, confirmed live by reading the installed fastembed Bm25
    # model's own _term_frequency() source, not assumed from documentation:
    # embed_sparse() does NOT produce bare occurrence counts. It produces
    # the TF-SATURATION half of the BM25 formula (count * (k + 1) /
    # (count + k * (1 - b + b * doc_len / avg_len)), defaults k=1.2
    # b=0.75 avg_len=256.0) -- Qdrant's own idf modifier (already
    # configured on this sparse vector slot at Task 1.3.c) supplies the
    # remaining IDF factor at query time; together the two halves form
    # the complete, standard BM25 score. This is the documented,
    # intended pairing for Qdrant/bm25 + modifier="idf", not a defect --
    # a single occurrence in a short document is confirmed here to NOT
    # equal 1.0.
    result = await embed_sparse(["one two three"])

    vector = result[0]
    assert len(vector.values) == 3
    for value in vector.values:
        assert value != 1.0
        assert 1.0 < value < 2.2  # k + 1 = 2.2 is this formula's own ceiling


async def test_embed_sparse_with_empty_input_returns_empty_list():
    result = await embed_sparse([])

    assert result == []


async def test_embed_sparse_empty_string_in_a_batch_yields_an_empty_sparse_vector():
    result = await embed_sparse(["some real text", ""])

    assert result[1].indices == []
    assert result[1].values == []


async def test_embed_sparse_output_converts_losslessly_into_a_real_qdrant_sparse_vector():
    # SparseVectorData is a local, qdrant-client-agnostic stand-in --
    # this proves it is a safe, lossless substitute for the real
    # qdrant_client.http.models.SparseVector (constructed later, at the
    # point-construction step), not just a shape that happens to look
    # similar on paper.
    result = await embed_sparse(["a simple test sentence"])
    data = result[0]

    real_vector = SparseVector(indices=data.indices, values=data.values)

    assert real_vector.indices == data.indices
    assert real_vector.values == data.values


async def test_embed_sparse_batch_output_order_matches_input_order():
    texts = ["first distinct text", "second distinct text", "first distinct text"]

    result = await embed_sparse(texts)

    assert len(result) == 3
    # Same text at positions 0 and 2 -> identical sparse vectors at those
    # exact positions, confirming output order is positionally aligned
    # with input order, not merely a set of correct-but-unordered results.
    assert result[0].indices == result[2].indices
    assert result[0].values == result[2].values
    # The middle, different text produces a different vector.
    assert result[1].indices != result[0].indices


# --- Task 3.1.a: embed_sparse_query() -- the query-side sibling --------


async def test_embed_sparse_query_returns_sparse_vectors_with_matching_indices_and_values_length():
    result = await embed_sparse_query(["a simple test query"])

    assert len(result) == 1
    vector = result[0]
    assert isinstance(vector, SparseVectorData)
    assert len(vector.indices) == len(vector.values)
    assert len(vector.indices) > 0
    assert all(isinstance(i, int) for i in vector.indices)
    assert all(isinstance(v, float) for v in vector.values)


async def test_embed_sparse_query_is_deterministic_across_separate_calls():
    first = await embed_sparse_query(["determinism check query"])
    second = await embed_sparse_query(["determinism check query"])

    assert first[0].indices == second[0].indices
    assert first[0].values == second[0].values


async def test_embed_sparse_query_different_texts_produce_different_sparse_vectors():
    result = await embed_sparse_query(
        ["the quick brown fox", "a totally unrelated sentence about cats"]
    )

    assert result[0].indices != result[1].indices


async def test_embed_sparse_query_values_are_a_flat_one_not_tf_saturation():
    # Task 3.1.a, confirmed live by reading the installed fastembed Bm25
    # model's own query_embed() source, not assumed from documentation:
    # unlike embed_sparse()'s own document-side TF-saturation values
    # (test_embed_sparse_values_are_bm25_tf_saturation_not_raw_occurrence_
    # counts above), the query side assigns a flat weight of 1.0 to every
    # token, per query_embed()'s own docstring ("we don't need to use
    # weights in the query... assign a weight of 1.0 to them").
    result = await embed_sparse_query(["one two three"])

    vector = result[0]
    assert len(vector.values) == 3
    assert all(value == 1.0 for value in vector.values)


async def test_embed_sparse_query_repeated_token_collapses_to_one_entry():
    # Task 3.1.a, confirmed live: "one one one" is three occurrences of a
    # single token, and query_embed() -- like embed(), confirmed by the
    # same live check -- produces exactly one entry per unique token, not
    # one per raw occurrence. The two sides agree on ENTRY COUNT; they
    # disagree on the entry's own WEIGHT (see the test immediately below),
    # which is the real, distinguishing property this module cares about.
    result = await embed_sparse_query(["one one one"])

    vector = result[0]
    assert len(vector.indices) == 1
    assert vector.values == [1.0]


async def test_embed_sparse_query_differs_from_the_document_side_for_the_identical_text():
    # Task 3.1.a: the core reason this is a SEPARATE function, not a mode
    # parameter on embed_sparse() -- proven directly, not just asserted
    # in a docstring. Both sides produce one entry per unique token (same
    # index SET for identical text, confirmed live), so entry count is not
    # where they differ -- confirmed live, by reading query_embed()'s own
    # source, that the WEIGHT is: embed_sparse() applies TF-saturation
    # weighting (a repeated token's own value reflects its real occurrence
    # count); embed_sparse_query() assigns every token a flat 1.0,
    # regardless of how many times it repeats. A caller that accidentally
    # called the wrong one for the wrong side would get a silently wrong
    # relevance score, not an error -- this test is the proof that the two
    # real computations actually differ, not merely two code paths that
    # happen to agree.
    text = "one one two three"

    doc_result = await embed_sparse([text])
    query_result = await embed_sparse_query([text])

    doc_vector = doc_result[0]
    query_vector = query_result[0]

    # Same three unique tokens either way -- the SET of indices matches,
    # even though `query_embed()`'s own internal `set()` deduplication
    # can return them in a different order than `embed()` does.
    assert set(query_vector.indices) == set(doc_vector.indices)
    assert all(value == 1.0 for value in query_vector.values)
    assert any(value != 1.0 for value in doc_vector.values)


async def test_embed_sparse_query_with_empty_input_returns_empty_list():
    result = await embed_sparse_query([])

    assert result == []


async def test_embed_sparse_query_empty_string_in_a_batch_yields_an_empty_sparse_vector():
    result = await embed_sparse_query(["some real query", ""])

    assert result[1].indices == []
    assert result[1].values == []


async def test_embed_sparse_query_output_converts_losslessly_into_a_real_qdrant_sparse_vector():
    result = await embed_sparse_query(["a simple test query"])
    data = result[0]

    real_vector = SparseVector(indices=data.indices, values=data.values)

    assert real_vector.indices == data.indices
    assert real_vector.values == data.values


async def test_embed_sparse_query_batch_output_order_matches_input_order():
    texts = ["first distinct query", "second distinct query", "first distinct query"]

    result = await embed_sparse_query(texts)

    assert len(result) == 3
    assert result[0].indices == result[2].indices
    assert result[0].values == result[2].values
    assert result[1].indices != result[0].indices
