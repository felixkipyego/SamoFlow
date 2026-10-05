# backend/app/ingest/embedding.py
# Task 2.5.a: dense embedding primitive -- shared logic, called by Step
# 2.6's job handler later (2.5 stays a callerless library, matching 2.3/
# 2.4's own precedent, per the Step 2.5 decision entry's point (e)).
#
# embed_dense() batches the whole input list into ONE API call --
# confirmed live by reading the installed SDK's own method signature
# (openai.resources.embeddings.AsyncEmbeddings.create's `input` parameter
# accepts a sequence of strings directly, not just a single string), not
# assumed from documentation alone. The real OpenAI embeddings endpoint
# returns results in the same order as the input list (confirmed live
# during this task's own real API call), so no re-sorting by the
# response's own `index` field is needed -- `response.data` is already
# positionally aligned with `texts`.
#
# Client: a lazy @lru_cache singleton, matching get_settings()/
# get_engine()/get_qdrant_client()'s own established pattern -- built
# once, on first real use, not at import.
#
# Retry/backoff: relies entirely on the SDK's own built-in retry (max_
# retries=2, the installed default, not overridden), confirmed live by
# reading openai._base_client's actual source rather than assumed: it
# retries on 408/409/429/5xx and on connection-level errors, honors a
# server `Retry-After` header when present (capped at its own maximum),
# otherwise applies exponential backoff with 0-25% jitter. A transient
# blip is absorbed inside ONE embed_dense() call, typically within a few
# seconds. If those 2 retries are exhausted (or the error is permanent --
# e.g. AuthenticationError, BadRequestError), the exception is left to
# propagate UNCAUGHT and unwrapped, same "no redundant translation
# layer" reasoning as extract_pdf.py/extract_docx.py.
#
# This composes safely with Step 2.1's own job-level retry (mark_job_
# failed()'s own backoff, job_retry_base_seconds-based): the two layers
# operate at different time scales and different failure granularities,
# not a multiplicative "retry storm". The SDK's own inner layer has a
# small, FIXED budget (2 retries) that always resolves quickly (succeeds
# or finally raises) within the SAME call; the job-level outer layer only
# ever engages AFTER that inner budget is exhausted, and its own backoff
# (60s+ by default) governs the NEXT, entirely separate attempt at the
# WHOLE job, not a nested retry of the same call. No stacking risk.
from dataclasses import dataclass
from functools import lru_cache

from fastembed import SparseTextEmbedding
from openai import AsyncOpenAI

from app.config import get_settings

# Step 2.4 decision (a)'s own example model, confirmed still the target
# here -- a module constant (matching DENSE_VECTOR_SIZE's own precedent),
# not a Settings field, since nothing else needs this configurable yet.
EMBEDDING_MODEL = "text-embedding-3-small"

# Task 2.5.b. Qdrant's own published BM25 sparse model, meant to be paired
# with modifier="idf" on the sparse vector config (already set at Task
# 1.3.c) -- confirmed live by reading the installed model class's own
# docstring, which states this pairing explicitly.
SPARSE_EMBEDDING_MODEL = "Qdrant/bm25"


@dataclass(frozen=True)
class SparseVectorData:
    # A local, qdrant-client-agnostic stand-in for qdrant_client.http.
    # models.SparseVector -- confirmed field-for-field identical (same two
    # field names, same types: indices: List[int], values: List[float],
    # checked directly against the installed qdrant-client's own
    # SparseVector.model_fields). embedding.py deliberately imports
    # NOTHING from qdrant_client (confirmed by grep) -- matching embed_
    # dense()'s own precedent of zero Qdrant coupling, and keeping this
    # callerless module out of test_qdrant_read_path_guard.py's
    # CLIENT_ACCESS_ALLOWLIST, whose own stated meaning is "may obtain a
    # real Qdrant client" (this module never does). The real SparseVector
    # gets constructed later, at Step 2.5's point-construction task,
    # exactly where that guard's own comment already expects a new
    # allowlist entry for the ingestion-writer file to land.
    indices: list[int]
    values: list[float]


@lru_cache
def _get_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=get_settings().openai_api_key_str())


# Task 2.5.b. A lazy @lru_cache singleton, matching _get_client()'s own
# pattern -- the model's own first instantiation downloads ~18 small files
# (per-language stopword lists; confirmed live, ~3.7s cold with network
# access, then cached under FASTEMBED_CACHE_PATH/the OS temp dir -- see
# the matching Open marker below, mirroring the tiktoken cl100k_base
# finding from 2.4.c), so building it once and reusing it matters.
@lru_cache
def _get_sparse_model() -> SparseTextEmbedding:
    return SparseTextEmbedding(model_name=SPARSE_EMBEDDING_MODEL)


async def embed_dense(texts: list[str]) -> list[list[float]]:
    # Empty input: no API call at all, not a clean rejection -- an empty
    # batch is a valid, reachable state (e.g. an empty ExtractedContent),
    # and there is nothing for the API to do with zero inputs.
    if not texts:
        return []
    client = _get_client()
    response = await client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [item.embedding for item in response.data]


async def embed_sparse(texts: list[str]) -> list[SparseVectorData]:
    # Empty input: no model call at all, matching embed_dense()'s own
    # reasoning -- an empty batch is a valid, reachable state.
    if not texts:
        return []
    model = _get_sparse_model()
    # fastembed is local/CPU-bound (onnxruntime), not an I/O-bound network
    # call -- embed() itself is synchronous; there is nothing to await
    # here, unlike embed_dense()'s real network round trip. Returns a
    # generator, positionally aligned with `texts` (confirmed live: a
    # 3-item batch with a repeated first/third text produced identical,
    # correctly-ordered output at both positions).
    #
    # Values are NOT raw occurrence counts, confirmed live by reading the
    # installed model's own _term_frequency() source and by inspecting
    # real output: Bm25.embed() computes the TF-SATURATION half of the
    # BM25 formula (count * (k+1) / (count + k*(1-b+b*doc_len/avg_len)),
    # defaults k=1.2 b=0.75 avg_len=256.0) -- e.g. a single occurrence in
    # a 3-token document yields ~1.678, not 1.0. This is deliberate and
    # correct, not a bug: Qdrant's own idf modifier (configured at Task
    # 1.3.c on this exact sparse vector slot) supplies the remaining IDF
    # factor at query time, and the model's own docstring confirms this
    # is the intended, documented pairing for Qdrant/bm25 -- together the
    # two halves form the complete BM25 score. The two halves are stable
    # per text/corpus-position (same text -> same TF-saturation value
    # every call, confirmed live), so determinism holds even though the
    # values are not bare counts.
    results = model.embed(texts)
    return [
        SparseVectorData(indices=result.indices.tolist(), values=result.values.tolist())
        for result in results
    ]
