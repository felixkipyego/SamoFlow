#!/usr/bin/env python3
# backend/scripts/manual_refresh_golden_set_query_vectors.py
# Task 3.7.c: (re)builds the FROZEN dense query-vector cache
# (evals/golden_set/query_vectors.json) the retrieval eval
# (scripts/retrieval_eval.py) reads instead of calling a real embedding
# API at eval time. A MANUAL, OPT-IN script making REAL OpenAI calls
# (cents of cost -- one dense embedding per non-greeting golden-set
# item) -- NEVER run by pytest/CI (not under tests/, no test_ prefix,
# not imported by anything).
#
# Run it by hand whenever evals/golden_set/items.yaml changes a
# question/prior_turns, or whenever the embedding model itself changes
# (the staleness guard, scripts/retrieval_eval.py's own
# `load_and_check_frozen_query_vectors()`, will refuse to load a cache
# that no longer matches the current embedder either way):
#
#   cd backend
#   export OPENAI_API_KEY=sk-...                      # a REAL key
#   python scripts/manual_refresh_golden_set_query_vectors.py
#
# Each item's own query text is built via `build_prepared_query()`
# (scripts/retrieval_eval.py) -- the REAL `_prepare_node` join logic,
# imported, never reimplemented, so this cache is always keyed by
# EXACTLY what `retrieve()` would actually be asked at eval time.
import asyncio
import json

from app.evals.golden_set import DEFAULT_ITEMS_PATH, load_golden_set
from app.ingest.embedding import EMBEDDING_MODEL, SPARSE_EMBEDDING_MODEL, embed_dense
from app.qdrant import DENSE_VECTOR_SIZE
from scripts.retrieval_eval import DEFAULT_QUERY_VECTORS_PATH, build_prepared_query, query_text_hash


async def main() -> None:
    golden_set = load_golden_set(DEFAULT_ITEMS_PATH)
    items = [item for item in golden_set.items if item.category != "greeting"]
    prepared_queries = [build_prepared_query(item) for item in items]

    print(f"Embedding {len(prepared_queries)} prepared queries...")
    vectors = await embed_dense(prepared_queries)

    entries = {
        item.id: {"query_hash": query_text_hash(prepared), "dense_vector": vector}
        for item, prepared, vector in zip(items, prepared_queries, vectors, strict=True)
    }
    payload = {
        "dense_embedding_model": EMBEDDING_MODEL,
        "dense_vector_size": DENSE_VECTOR_SIZE,
        "sparse_embedding_model": SPARSE_EMBEDDING_MODEL,
        "items": entries,
    }
    DEFAULT_QUERY_VECTORS_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(entries)} query vectors to {DEFAULT_QUERY_VECTORS_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
