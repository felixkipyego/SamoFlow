# backend/tests/test_retrieval_eval.py
# Task 3.7.c: the real retrieval-only eval, gated on a real test-qdrant
# (retrieve() genuinely queries Qdrant over the network) but needing NO
# real OpenAI key at all -- dense query vectors are frozen (evals/
# golden_set/query_vectors.json), sparse is local/free. Skips (not
# fails) when TEST_QDRANT_URL is unset, matching every other test-
# qdrant-gated test's own convention; only runs for real under `make
# test-all` or with TEST_QDRANT_* exported by hand.
import json

import pytest

from app import qdrant
from app.evals.golden_set import (
    DEFAULT_ITEMS_PATH,
    DEFAULT_SYNTHETIC_CORPUS_PATH,
    EmbeddingStalenessError,
    load_golden_set,
)
from app.ingest.embedding import EMBEDDING_MODEL, SPARSE_EMBEDDING_MODEL
from app.qdrant import DENSE_VECTOR_SIZE
from scripts.retrieval_eval import (
    DEFAULT_BASELINE_PATH,
    DEFAULT_QUERY_VECTORS_PATH,
    assert_no_regression,
    load_and_check_frozen_corpus,
    load_and_check_frozen_query_vectors,
    load_baseline,
    run_retrieval_eval,
    summarize_results,
)
from tests.conftest import require_test_qdrant, set_real_qdrant_env

pytestmark = pytest.mark.usefixtures("close_the_shared_qdrant_read_client_once")


def test_committed_synthetic_corpus_embedding_config_is_not_stale():
    # The real, committed fixture itself -- if this ever fails, the
    # corpus genuinely needs refreezing, not a test fix.
    load_and_check_frozen_corpus(DEFAULT_SYNTHETIC_CORPUS_PATH)


def test_committed_query_vector_cache_embedding_config_is_not_stale():
    load_and_check_frozen_query_vectors(DEFAULT_QUERY_VECTORS_PATH)


def test_load_and_check_frozen_corpus_raises_loudly_on_a_stale_corpus(tmp_path):
    path = tmp_path / "corpus.json"
    path.write_text(
        json.dumps(
            {
                "corpus": "synthetic",
                "documents": ["doc-a"],
                "dense_embedding_model": "text-embedding-ada-002",
                "dense_vector_size": DENSE_VECTOR_SIZE,
                "sparse_embedding_model": SPARSE_EMBEDDING_MODEL,
                "embedding_version": "test-v1",
                "chunks": [
                    {
                        "document": "doc-a", "chunk_index": 0, "heading_path": ["S"],
                        "text": "t", "content_hash": "h", "dense_vector": [0.0],
                        "sparse_indices": [], "sparse_values": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(EmbeddingStalenessError):
        load_and_check_frozen_corpus(path)


def test_load_and_check_frozen_query_vectors_raises_loudly_on_a_stale_cache(tmp_path):
    path = tmp_path / "query_vectors.json"
    path.write_text(
        json.dumps(
            {
                "dense_embedding_model": EMBEDDING_MODEL,
                "dense_vector_size": DENSE_VECTOR_SIZE + 1,
                "sparse_embedding_model": SPARSE_EMBEDDING_MODEL,
                "items": {},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(EmbeddingStalenessError):
        load_and_check_frozen_query_vectors(path)


async def test_retrieval_metrics_meet_the_committed_baseline(monkeypatch):
    # Settings (read by retrieve()'s own get_qdrant_read_client() inside
    # run_retrieval_eval()) must point at test-qdrant...
    set_real_qdrant_env(monkeypatch)
    # ...and this test's OWN seeding/cleanup client is built directly
    # (qdrant.build_qdrant_client(), never the cached get_qdrant_client()
    # singleton), matching test_agent_graph_retrieve.py's/test_agent_
    # graph_prepare.py's own established live-Qdrant-test convention --
    # avoids the identical cross-event-loop lru_cache hazard those files'
    # own header comments already document for a cached client reused
    # across pytest-asyncio's own per-test event loops.
    url, key = require_test_qdrant()
    golden_set = load_golden_set(DEFAULT_ITEMS_PATH)
    corpus = load_and_check_frozen_corpus(DEFAULT_SYNTHETIC_CORPUS_PATH)
    query_vectors = load_and_check_frozen_query_vectors(DEFAULT_QUERY_VECTORS_PATH)
    baseline = load_baseline(DEFAULT_BASELINE_PATH)

    client = qdrant.build_qdrant_client(url, key)
    try:
        results = await run_retrieval_eval(golden_set, corpus, query_vectors, client)
    finally:
        await client.close()

    # Every non-greeting synthetic item was actually retrieved -- a
    # silently-shrunk result set (e.g. a filter bug dropping items)
    # would otherwise still "pass" a baseline comparison that only ever
    # checks aggregate shares.
    expected_item_count = sum(
        1
        for item in golden_set.items
        if item.category != "greeting" and item.corpus == "synthetic"
    )
    assert len(results) == expected_item_count

    category_sizes = golden_set.category_counts()
    report = summarize_results(results)
    assert_no_regression(report, baseline, category_sizes)
