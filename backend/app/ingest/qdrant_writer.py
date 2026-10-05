# backend/app/ingest/qdrant_writer.py
# Task 2.5.c: deterministic point construction -- shared logic, called by
# Step 2.6's job handler later (2.5 stays a callerless library, matching
# 2.3/2.4/2.5.a/2.5.b's own precedent, per the Step 2.5 decision entry's
# point (e)). [SECURITY]-level rigor, per the Step 2.5 decision entry's own
# note: this is the first module to construct the real point data that
# will be written into the shared, multi-tenant knowledge_chunks collection.
#
# CLIENT_ACCESS_ALLOWLIST (test_qdrant_read_path_guard.py): this file is
# added there deliberately, as that guard's own existing comment already
# anticipated ("Step 2.5's ingestion writer will be added here
# deliberately"). Confirmed live by reading the guard's own code before
# writing this module: rule (a) is a pure, import-level AST check -- ANY
# `import qdrant_client` / `from qdrant_client... import ...` outside the
# allow-list trips it, with no distinction between "acquires a real client"
# and "imports a type to construct point data with no client involved at
# all" (the exact same shape that made embed_sparse() trip it in 2.5.b,
# fixed there by NOT importing qdrant_client). Unlike embedding.py, this
# module's whole job IS to construct real qdrant_client model objects
# (PointStruct, SparseVector) -- there is no qdrant-agnostic stand-in that
# makes sense here, so the correct fix this time is the allow-list entry
# itself, not a local shape. This module never acquires a client and never
# calls a method on one (confirmed: no READ_ALLOWLIST entry needed), so it
# stays out of that second allow-list entirely -- matching the Step 1.3.e
# marker's own instruction that the ingestion writer "must call
# write-classified methods only... must not be added to READ_ALLOWLIST".
# This module itself calls no method at all yet (2.5.d's own upsert
# primitive is what will call client.upsert()); it exists here purely
# because it is the first and only file under app/ that needs to import
# qdrant_client at all before 2.5.d exists.
import uuid

from qdrant_client.http.models import PointStruct, SparseVector

from app.ingest.embedding import SparseVectorData
from app.qdrant import DENSE_VECTOR_NAME, SPARSE_VECTOR_NAME

# Task 2.5.c: a project-specific constant, generated ONCE (uuid.uuid4(),
# picked arbitrarily) and hardcoded forever from this point on -- matching
# DENSE_VECTOR_SIZE's own "fixed forever" precedent (app/qdrant.py). This
# must NEVER change once any real point exists: build_point_id()'s own
# determinism (docs/SPEC.md §5.3's "a re-run overwrites instead of
# duplicating") depends on the SAME namespace producing the SAME uuid5
# output for the same document/chunk_index/embedding_version every time,
# forever -- changing this constant would silently re-ID every point ever
# written, turning every future "re-run" into a full duplicate set instead
# of an overwrite.
POINT_ID_NAMESPACE = uuid.UUID("037e5304-2eaf-4612-8776-4736637ca3cf")


def build_point_id(document_id: uuid.UUID, chunk_index: int, embedding_version: str) -> uuid.UUID:
    """Deterministic point ID: the same (document_id, chunk_index,
    embedding_version) triple always produces the same UUID -- changing
    ANY ONE of the three produces a different one. uuid.uuid5 itself is
    already deterministic (unlike uuid4); the only way this function could
    fail to be is POINT_ID_NAMESPACE itself changing, which it never does.
    """
    name = f"{document_id}:{chunk_index}:{embedding_version}"
    return uuid.uuid5(POINT_ID_NAMESPACE, name)


def build_payload(
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    source_type: str,
    document_id: uuid.UUID,
    source_url: str | None,
    file_name: str | None,
    title: str | None,
    heading_path: tuple[str, ...],
    chunk_index: int,
    content_hash: str,
    embedding_model: str,
    embedding_version: str,
    text: str,
) -> dict:
    """The full docs/SPEC.md §5.2 payload shape for one chunk.

    THE CRITICAL DETAIL: client_id is written as str(tenant_id) -- the
    exact canonical lowercase form app/qdrant.py's tenant_filter() matches
    against (confirmed by reading that function directly: it builds
    MatchValue(value=str(client_id)) from a real uuid.UUID, and
    uuid.UUID.__str__ always returns canonical lowercase form regardless of
    how the UUID was constructed). Writing anything other than this exact
    str(uuid.UUID) form here would make every future tenant-filtered query
    for this tenant silently return nothing -- see Task 2.5.c's own live
    proof (test_qdrant_writer.py) that a point written this way is
    genuinely found by tenant_filter(tenant_id) against a real collection.

    source_url/file_name: both always present as dict keys, one None when
    not applicable -- matching the Document model's own mutual-exclusivity
    convention exactly (app/ingest/models.py's `url`/`file_name` columns,
    Task 2.4.a), not an omitted key. A `database`-source document (2.4.a's
    own confirmed-valid third case) has both None.
    """
    if not isinstance(tenant_id, uuid.UUID):
        raise TypeError(f"tenant_id must be a uuid.UUID instance, got {type(tenant_id).__name__}")
    return {
        "client_id": str(tenant_id),
        "source_id": str(source_id),
        "source_type": source_type,
        "doc_id": str(document_id),
        "source_url": source_url,
        "file_name": file_name,
        "title": title,
        "heading_path": list(heading_path),
        "chunk_index": chunk_index,
        "content_hash": content_hash,
        "embedding_model": embedding_model,
        "embedding_version": embedding_version,
        "text": text,
    }


def build_point(
    point_id: uuid.UUID,
    payload: dict,
    dense_vector: list[float],
    sparse_vector: SparseVectorData,
) -> PointStruct:
    """Combines a point ID, payload and both named vectors into a real
    PointStruct -- dense/sparse vector names (DENSE_VECTOR_NAME/
    SPARSE_VECTOR_NAME, "dense"/"sparse") imported directly from
    app/qdrant.py rather than re-declared here, matching that module's own
    collection schema exactly (Task 1.3.c). sparse_vector is embedding.py's
    own qdrant-agnostic SparseVectorData -- converted into a real
    SparseVector here, in this module specifically, per this task's own
    CLIENT_ACCESS_ALLOWLIST investigation above.
    """
    return PointStruct(
        id=point_id,
        vector={
            DENSE_VECTOR_NAME: dense_vector,
            SPARSE_VECTOR_NAME: SparseVector(
                indices=sparse_vector.indices, values=sparse_vector.values
            ),
        },
        payload=payload,
    )
