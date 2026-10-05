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

from app.ingest.embedding import SparseVectorData, embed_sparse


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
