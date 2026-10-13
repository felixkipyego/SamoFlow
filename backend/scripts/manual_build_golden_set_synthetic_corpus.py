#!/usr/bin/env python3
# backend/scripts/manual_build_golden_set_synthetic_corpus.py
# Task 3.7.b: builds the FROZEN synthetic-corpus fixture golden_set.py's
# own tests read (evals/golden_set/synthetic_corpus.json) from the raw
# markdown pages under evals/golden_set/pages/ -- run through the REAL
# ingestion path (extract_markdown -> chunk_text -> embed_dense/embed_
# sparse, Step 2.4/2.5), never hand-made chunks. A MANUAL, OPT-IN script
# making REAL OpenAI dense-embedding calls (cents of cost -- ~35 chunks
# of text-embedding-3-small) -- NEVER run by pytest/CI (not under tests/,
# no test_ prefix, not imported by anything). Sparse/BM25 embedding
# (fastembed) is fully LOCAL -- no network call, no cost.
#
# Run it by hand, whenever the pages under evals/golden_set/pages/
# change (this is the one-time "capture", matching the frozen-crawl-
# snapshot precedent, Task 3.7.a PART 3 -- evals must never silently
# re-embed on every eval run):
#
#   cd backend
#   export OPENAI_API_KEY=sk-...                      # a REAL key
#   python scripts/manual_build_golden_set_synthetic_corpus.py
#
# No Qdrant/test-db involved at all -- this script only reads local
# markdown files and writes one local JSON file, so there is nothing to
# clean up afterward (unlike every real-API script that seeds Qdrant).
import asyncio
import json
from pathlib import Path

from app.ingest.chunking import chunk_text, compute_content_hash
from app.ingest.embedding import EMBEDDING_MODEL, embed_dense, embed_sparse
from app.ingest.extract_text import extract_markdown

_GOLDEN_SET_DIR = Path(__file__).resolve().parents[2] / "evals" / "golden_set"
_PAGES_DIR = _GOLDEN_SET_DIR / "pages"
_OUTPUT_PATH = _GOLDEN_SET_DIR / "synthetic_corpus.json"
_EMBEDDING_VERSION = "golden-set-synthetic-v1"


async def main() -> None:
    page_paths = sorted(_PAGES_DIR.glob("*.md"))
    if not page_paths:
        raise SystemExit(f"No .md pages found under {_PAGES_DIR}")

    documents: list[str] = []
    # One flat list of (document, Chunk) across every page, so a single
    # pair of embed_dense()/embed_sparse() calls covers the whole corpus
    # (one HTTP round trip for every dense embedding, matching every
    # other research script's own batching precedent) rather than one
    # call per page.
    flat_chunks: list[tuple[str, object]] = []
    content_hash_by_document: dict[str, str] = {}

    for page_path in page_paths:
        document = page_path.stem
        documents.append(document)
        extracted = extract_markdown(page_path.read_text(encoding="utf-8"))
        chunks = chunk_text(extracted)
        if not chunks:
            raise SystemExit(f"{page_path} produced zero chunks -- check its own content")
        content_hash_by_document[document] = compute_content_hash(extracted)
        for chunk in chunks:
            flat_chunks.append((document, chunk))

    texts = [chunk.text for _, chunk in flat_chunks]
    print(f"Embedding {len(texts)} chunks across {len(documents)} documents...")
    dense_vectors = await embed_dense(texts)
    sparse_vectors = await embed_sparse(texts)

    frozen_chunks = []
    zipped = zip(flat_chunks, dense_vectors, sparse_vectors, strict=True)
    for (document, chunk), dense, sparse in zipped:
        frozen_chunks.append(
            {
                "document": document,
                "chunk_index": chunk.chunk_index,
                "heading_path": list(chunk.heading_path),
                "text": chunk.text,
                "content_hash": content_hash_by_document[document],
                "embedding_model": EMBEDDING_MODEL,
                "embedding_version": _EMBEDDING_VERSION,
                "dense_vector": dense,
                "sparse_indices": sparse.indices,
                "sparse_values": sparse.values,
            }
        )

    frozen_corpus = {"corpus": "synthetic", "documents": documents, "chunks": frozen_chunks}
    _OUTPUT_PATH.write_text(json.dumps(frozen_corpus, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(frozen_chunks)} chunks ({len(documents)} documents) to {_OUTPUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
