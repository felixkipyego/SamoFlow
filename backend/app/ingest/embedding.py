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
from functools import lru_cache

from openai import AsyncOpenAI

from app.config import get_settings

# Step 2.4 decision (a)'s own example model, confirmed still the target
# here -- a module constant (matching DENSE_VECTOR_SIZE's own precedent),
# not a Settings field, since nothing else needs this configurable yet.
EMBEDDING_MODEL = "text-embedding-3-small"


@lru_cache
def _get_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=get_settings().openai_api_key_str())


async def embed_dense(texts: list[str]) -> list[list[float]]:
    # Empty input: no API call at all, not a clean rejection -- an empty
    # batch is a valid, reachable state (e.g. an empty ExtractedContent),
    # and there is nothing for the API to do with zero inputs.
    if not texts:
        return []
    client = _get_client()
    response = await client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [item.embedding for item in response.data]
