# backend/app/qdrant.py
# Task 1.3.b: async Qdrant client plumbing (no collection schema yet --
# that's 1.3.c). Nothing here reads the environment or calls get_settings()
# at import time (module import must succeed with an empty environment,
# matching app/db.py's/app/config.py's own rule): get_qdrant_client() is
# @lru_cache-wrapped, same lazy-singleton pattern as db.py's get_engine(),
# so the client is built once, on first real use, not at import.
#
# Construction does not block: verified by reading AsyncQdrantRemote.__init__
# directly (installed qdrant-client==1.19.1). check_compatibility=True (the
# default, kept and passed explicitly here) does perform a network request,
# but via `Thread(target=self._check_compatibility, ..., daemon=True).start()`
# -- a background OS thread, not the event loop -- so it never blocks async
# code, and it only ever emits a UserWarning (client/server version
# mismatch, or server unreachable), never raises. The engineering rule
# ("no blocking calls in handlers") is not actually at risk here, so no
# custom async compatibility check was built; the live test instead calls
# the library's own public qdrant_client.common.version_check functions
# directly (get_server_version/is_compatible) for a deterministic assertion,
# reusing the library's own mechanism rather than a parallel custom one
# (rule 11).
#
# Constructing a client with an api_key over a plain "http://" URL emits a
# second UserWarning ("Api key is used with an insecure connection."),
# confirmed by reading the same source (async_qdrant_remote.py). This is not
# new information -- plain HTTP inside the Docker network is a known,
# already-tracked Phase 7 concern (PROJECT_SPEC.md's open markers) -- so it
# is filtered here, narrowly (exact message, scoped to this one
# construction via warnings.catch_warnings(), never a global filter) rather
# than left to print on every client construction.
import re
import warnings
from collections.abc import Mapping
from functools import lru_cache

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.http.models import (
    CollectionInfo,
    Distance,
    KeywordIndexParams,
    Modifier,
    PayloadSchemaType,
    SparseVectorParams,
    VectorParams,
)

from app.config import get_settings

# Exact text confirmed against the installed qdrant-client==1.19.1
# (async_qdrant_remote.py); filtered by message, not blanket-suppressed, so
# an unrelated UserWarning is never accidentally swallowed.
_INSECURE_API_KEY_WARNING = "Api key is used with an insecure connection."


def build_qdrant_client(url: str, api_key: str) -> AsyncQdrantClient:
    # Pure factory: takes its arguments directly, never reads Settings
    # itself, so it can be unit-tested with fake values and no environment.
    # REST only (prefer_grpc=False): matches the approved 1.3 design and
    # this project's existing plain-HTTP style elsewhere; avoids a second
    # wire protocol for no current benefit. The key is passed via the
    # client's own api_key argument only -- never logged, never interpolated
    # into a message anywhere in this module.
    with warnings.catch_warnings():
        # message is matched as a regex by warnings.filterwarnings, not a
        # literal string; re.escape() keeps this an exact match so an
        # unrelated UserWarning whose text happens to contain regex
        # metacharacters is never accidentally swallowed too.
        warnings.filterwarnings(
            "ignore",
            message=re.escape(_INSECURE_API_KEY_WARNING),
            category=UserWarning,
        )
        return AsyncQdrantClient(
            url=url,
            api_key=api_key,
            prefer_grpc=False,
            check_compatibility=True,
        )


@lru_cache
def get_qdrant_client() -> AsyncQdrantClient:
    settings = get_settings()
    return build_qdrant_client(settings.qdrant_url, settings.qdrant_api_key_str())


# --- Task 1.3.c: collection schema ------------------------------------------
# One shared collection (docs/SPEC.md §5.2): a named dense vector and a named
# sparse vector on the same point, a tenant-optimised keyword index on
# client_id, and a plain keyword index on source_id. No other payload indexes
# are created here -- §5.2 lists more payload fields, but only these two are
# indexed at this step.
COLLECTION_NAME = "knowledge_chunks"
DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"
# ASSUMPTION: text-embedding-3-small's output dimension. No real embedding
# exists yet -- revisit at Step 2.5 before any data is written; if the model
# or dimension changes, the (still-empty) collection must be recreated, not
# migrated in place.
DENSE_VECTOR_SIZE = 1536
CLIENT_ID_FIELD = "client_id"
SOURCE_ID_FIELD = "source_id"

_DISTANCE = Distance.COSINE


class CollectionSchemaMismatch(Exception):
    """Raised by ensure_collection() when an existing collection's schema
    differs from the expected one in any way other than a missing payload
    index (which is repaired instead -- see ensure_collection()'s docstring).
    The message lists every difference found; it is built only from schema
    metadata (vector sizes, distances, index types), never from Settings or
    the client's own headers, so it can never contain the API key.
    """


def _dense_vector_diff(
    vectors: Mapping[str, VectorParams] | VectorParams | None, name: str
) -> str | None:
    if not isinstance(vectors, Mapping):
        # None, or a single unnamed VectorParams -- either way, there is no
        # vector named "dense".
        return f"{name!r}: missing dense vector {DENSE_VECTOR_NAME!r}"
    dense = vectors.get(DENSE_VECTOR_NAME)
    if dense is None:
        return f"{name!r}: missing dense vector {DENSE_VECTOR_NAME!r}"
    if dense.size != DENSE_VECTOR_SIZE:
        return (
            f"{name!r}: dense vector {DENSE_VECTOR_NAME!r} size is {dense.size}, "
            f"expected {DENSE_VECTOR_SIZE}"
        )
    if dense.distance != _DISTANCE:
        return (
            f"{name!r}: dense vector {DENSE_VECTOR_NAME!r} distance is "
            f"{dense.distance}, expected {_DISTANCE}"
        )
    return None


def _sparse_vector_diff(
    sparse_vectors: Mapping[str, SparseVectorParams] | None, name: str
) -> str | None:
    sparse = (sparse_vectors or {}).get(SPARSE_VECTOR_NAME)
    if sparse is None:
        return f"{name!r}: missing sparse vector {SPARSE_VECTOR_NAME!r}"
    if sparse.modifier != Modifier.IDF:
        return f"{name!r}: sparse vector {SPARSE_VECTOR_NAME!r} is missing the IDF modifier"
    return None


def _client_id_index_diff(payload_schema: Mapping[str, object], name: str) -> str | None:
    index = (payload_schema or {}).get(CLIENT_ID_FIELD)
    if index is None:
        return f"{name!r}: missing payload index on {CLIENT_ID_FIELD!r}"
    if index.data_type != PayloadSchemaType.KEYWORD:
        return (
            f"{name!r}: payload index on {CLIENT_ID_FIELD!r} is "
            f"{index.data_type.value}, expected keyword"
        )
    if not (index.params and index.params.is_tenant):
        return f"{name!r}: payload index on {CLIENT_ID_FIELD!r} is not tenant-optimized (is_tenant)"
    return None


def _source_id_index_diff(payload_schema: Mapping[str, object], name: str) -> str | None:
    index = (payload_schema or {}).get(SOURCE_ID_FIELD)
    if index is None:
        return f"{name!r}: missing payload index on {SOURCE_ID_FIELD!r}"
    if index.data_type != PayloadSchemaType.KEYWORD:
        return (
            f"{name!r}: payload index on {SOURCE_ID_FIELD!r} is "
            f"{index.data_type.value}, expected keyword"
        )
    return None


def _schema_differences(actual_collection_info: CollectionInfo, name: str) -> list[str]:
    # Pure comparison -- no IO -- against the expected schema, so it is
    # unit-testable offline with hand-built qdrant_client model objects.
    # Field paths read: config.params.vectors[dense].{size,distance},
    # config.params.sparse_vectors[sparse].modifier,
    # payload_schema[client_id].{data_type,params.is_tenant},
    # payload_schema[source_id].data_type -- all confirmed live against a
    # freshly created collection (Task 1.3.c decision log).
    params = actual_collection_info.config.params
    diffs = [
        _dense_vector_diff(params.vectors, name),
        _sparse_vector_diff(params.sparse_vectors, name),
        _client_id_index_diff(actual_collection_info.payload_schema, name),
        _source_id_index_diff(actual_collection_info.payload_schema, name),
    ]
    return [d for d in diffs if d is not None]


def _mismatch_message(diffs: list[str], name: str) -> str:
    # Pure and offline-testable: built only from diff strings and the
    # collection name, never from Settings or the client, so it can never
    # contain the API key.
    return f"Collection {name!r} does not match the expected schema:\n" + "\n".join(diffs)


async def _create_client_id_index(client: AsyncQdrantClient, name: str) -> None:
    await client.create_payload_index(
        collection_name=name,
        field_name=CLIENT_ID_FIELD,
        field_schema=KeywordIndexParams(type=PayloadSchemaType.KEYWORD, is_tenant=True),
    )


async def _create_source_id_index(client: AsyncQdrantClient, name: str) -> None:
    await client.create_payload_index(
        collection_name=name,
        field_name=SOURCE_ID_FIELD,
        field_schema=PayloadSchemaType.KEYWORD,
    )


async def _create_collection_and_indexes(client: AsyncQdrantClient, name: str) -> None:
    await client.create_collection(
        collection_name=name,
        vectors_config={
            DENSE_VECTOR_NAME: VectorParams(size=DENSE_VECTOR_SIZE, distance=_DISTANCE)
        },
        sparse_vectors_config={SPARSE_VECTOR_NAME: SparseVectorParams(modifier=Modifier.IDF)},
    )
    await _create_client_id_index(client, name)
    await _create_source_id_index(client, name)


async def ensure_collection(client: AsyncQdrantClient, name: str = COLLECTION_NAME) -> None:
    """Idempotent setup: create the collection and both indexes if it does
    not exist; if it does, verify it against the expected schema instead of
    trusting it blindly.

    A fully missing payload index is repaired (create_payload_index is
    additive -- this covers a crash between create_collection and index
    creation). Anything else that differs (wrong dense size or distance, a
    missing vector, a sparse vector without the IDF modifier, an index that
    exists with different parameters) raises CollectionSchemaMismatch instead
    of being silently fixed, since that could mean the collection already
    holds data shaped differently than expected.
    """
    if not await client.collection_exists(name):
        try:
            await _create_collection_and_indexes(client, name)
            return
        except UnexpectedResponse as exc:
            if exc.status_code != 409:
                raise
            # Two callers raced: another one created it between our
            # collection_exists() check and our create_collection() call.
            # Fall through and verify what actually exists now, rather than
            # trusting our own create attempt.

    info = await client.get_collection(name)
    payload_schema = info.payload_schema or {}
    client_id_missing = CLIENT_ID_FIELD not in payload_schema
    source_id_missing = SOURCE_ID_FIELD not in payload_schema

    hard_diffs = [
        _dense_vector_diff(info.config.params.vectors, name),
        _sparse_vector_diff(info.config.params.sparse_vectors, name),
        None if client_id_missing else _client_id_index_diff(payload_schema, name),
        None if source_id_missing else _source_id_index_diff(payload_schema, name),
    ]
    if any(d is not None for d in hard_diffs):
        raise CollectionSchemaMismatch(_mismatch_message(_schema_differences(info, name), name))

    if client_id_missing:
        await _create_client_id_index(client, name)
    if source_id_missing:
        await _create_source_id_index(client, name)
