# backend/tests/test_agent_graph_retrieve.py
# Task 3.3.c [SECURITY]: the real retrieve node, proven end to end through
# the real guarded entry point (run_turn()/read_state(), never the node
# called directly) -- the one new thing this file proves is GRAPH-LEVEL
# tenant-identity wiring: that tenant_id reaching retrieve() inside a real
# compiled graph run comes from the current call's own config (built fresh
# by _build_thread_config() from VerifiedIdentity), through
# tenant_id_from_config(), with no other path for it to leak in or out.
#
# Deliberately NOT re-proving, here, what is already proven at a lower
# layer:
#   - HIDDEN_FIELD exclusion and the tenant_filter() shape itself -- proven
#     directly through retrieve() in test_retrieval_isolation.py (Task
#     3.1.d), against a much larger matrix (dense/sparse/hybrid leak, a
#     near-miss client_id, an orphan point, a no-points tenant). This file
#     only needs ONE non-overlapping-topic pair per tenant to prove the
#     graph wiring; it does not re-run that matrix.
#   - tenant_id_from_config()'s own validation (missing key, wrong type) --
#     proven directly in test_agent_context.py (Task 3.3.a). This file only
#     exercises the real, well-formed path through it.
#
# Real content, real embed_and_upsert() writes, real retrieve() call, a
# real compiled graph, a real checkpointer against test-db -- only
# embed_dense()'s own paid OpenAI call is stubbed (patch_embed_dense(),
# this project's own established convention); embed_sparse()/
# embed_sparse_query() both make real fastembed calls. Seeded into the
# REAL app.qdrant.COLLECTION_NAME, not a disposable per-test collection
# name: _retrieve_node calls retrieve(tenant_id, query) with no
# collection_name override (matching production), so there is no way to
# point it at a throwaway collection -- matching this project's own
# established precedent (test_job_handlers.py, test_upload_e2e.py, both
# already write real points into COLLECTION_NAME against test-qdrant with
# no per-point cleanup), relying on fresh random tenant_id/document_id
# values for isolation between runs, not on a disposable collection name.
import uuid

import pytest

from app import qdrant
from app.agent import checkpointer
from app.agent.entrypoint import read_state, run_turn
from app.agent.graph import build_graph
from app.auth.dependencies import VerifiedIdentity
from app.ingest.extract_html import extract_html
from app.ingest.qdrant_writer import embed_and_upsert
from tests.conftest import (
    patch_embed_dense,
    require_test_qdrant,
    require_test_qdrant_read_only_key,
    reset_checkpoint_env,
)

pytestmark = pytest.mark.usefixtures(
    "close_any_checkpointer_pool_left_open", "close_the_shared_qdrant_read_client_once"
)

_ORG = "https://tenant.example"
_SOURCE_ID = uuid.uuid4()


def _identity(tenant_id: uuid.UUID) -> VerifiedIdentity:
    return VerifiedIdentity(tenant_id=tenant_id, vid=uuid.uuid4(), org=_ORG)


def _page(title: str, body: str) -> str:
    return f"<html><head><title>{title}</title></head><body><h2>S</h2><p>{body}</p></body></html>"


async def _collect(aiter):
    return [x async for x in aiter]


async def _fresh_real_graph(monkeypatch):
    # Same pattern as test_agent_graph.py's own _fresh_real_graph(), but
    # this file's own tests drive the real retrieve node, which (unlike
    # Task 3.3.b's stubs) actually calls get_settings()/get_qdrant_read_
    # client() internally -- needs the real Qdrant env active too.
    #
    # Passes the Qdrant overrides INTO reset_checkpoint_env()'s own
    # **extra_overrides (added this task), rather than calling
    # set_real_qdrant_env() as a separate second set_valid_env(): found
    # live while building this test that a second, separate call
    # re-applies VALID_ENV's own fake DATABASE_URL wholesale, silently
    # clobbering the real one reset_checkpoint_env() just set -- the
    # identical bug class _prepare_env()'s own header comment
    # (test_job_handlers.py) already warns about, just not yet hit by any
    # existing caller until this file combined the checkpointer and
    # Qdrant env setups for the first time.
    qdrant_url, qdrant_key = require_test_qdrant()
    qdrant_read_only_key = require_test_qdrant_read_only_key()
    await reset_checkpoint_env(
        monkeypatch,
        QDRANT_URL=qdrant_url,
        QDRANT_API_KEY=qdrant_key,
        QDRANT_READ_ONLY_API_KEY=qdrant_read_only_key,
    )
    await checkpointer.open_checkpointer_pool()
    cp = checkpointer.get_checkpointer()
    await cp.setup()
    return build_graph(cp)


async def _seed_tenant(client, tenant_id: uuid.UUID, title: str, body: str, url: str) -> uuid.UUID:
    document_id = uuid.uuid4()
    await embed_and_upsert(
        extract_html(_page(title, body)),
        client=client,
        collection_name=qdrant.COLLECTION_NAME,
        tenant_id=tenant_id,
        source_id=_SOURCE_ID,
        source_type="urls",
        document_id=document_id,
        source_url=url,
        file_name=None,
        embedding_version="v1",
    )
    return document_id


async def _seed_two_tenants_with_non_overlapping_content(
    client,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    # Distinct, non-overlapping vocabulary per tenant (matching Task
    # 3.1.c/3.1.d's own established pattern): dense is stubbed to a
    # constant vector by patch_embed_dense(), so the real sparse (BM25)
    # side of the hybrid query is what makes tenant_filter() the only
    # thing standing between a cross-topic query and the other tenant's
    # content.
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    doc_a = await _seed_tenant(
        client, tenant_a, "Tenant A Doc", "zebra giraffe elephant safari wildlife",
        "https://tenant-a.example/page",
    )
    doc_b = await _seed_tenant(
        client, tenant_b, "Tenant B Doc", "mortgage interest rate amortization loan",
        "https://tenant-b.example/page",
    )
    return tenant_a, doc_a, tenant_b, doc_b


async def test_run_turn_as_tenant_a_with_a_query_matching_tenant_bs_topic_stays_scoped_to_tenant_a(
    monkeypatch,
):
    patch_embed_dense(monkeypatch)
    url, key = require_test_qdrant()
    client = qdrant.build_qdrant_client(url, key)
    await qdrant.ensure_collection(client, qdrant.COLLECTION_NAME)

    tenant_a, doc_a, _tenant_b, doc_b = await _seed_two_tenants_with_non_overlapping_content(client)
    await client.close()

    graph = await _fresh_real_graph(monkeypatch)
    identity_a = _identity(tenant_a)
    conversation_id = uuid.uuid4()

    # Proves the retrieve -> gate_answer edge fired for real (3.3.b's own
    # trade-off item 2): the query text matches tenant B's own topic, so
    # if tenant scoping were broken this would still "succeed" at
    # retrieval -- the gate_answer stub's own NotImplementedError is only
    # reached if the real retrieve node itself completed without raising.
    with pytest.raises(NotImplementedError, match="Step 3.4"):
        await _collect(
            run_turn(
                graph, identity_a, conversation_id, {"messages": ["mortgage interest rate"]}
            )
        )

    snapshot = await read_state(graph, identity_a, conversation_id)
    chunks = snapshot.values["retrieved_chunks"]
    assert len(chunks) >= 1
    assert all(chunk["document_id"] == str(doc_a) for chunk in chunks)
    assert all(chunk["document_id"] != str(doc_b) for chunk in chunks)
    assert any("zebra" in chunk["text"] for chunk in chunks)
    assert all("mortgage" not in chunk["text"] for chunk in chunks)


async def test_run_turn_as_tenant_b_with_a_query_matching_tenant_as_topic_stays_scoped_to_tenant_b(
    monkeypatch,
):
    patch_embed_dense(monkeypatch)
    url, key = require_test_qdrant()
    client = qdrant.build_qdrant_client(url, key)
    await qdrant.ensure_collection(client, qdrant.COLLECTION_NAME)

    _tenant_a, doc_a, tenant_b, doc_b = await _seed_two_tenants_with_non_overlapping_content(client)
    await client.close()

    graph = await _fresh_real_graph(monkeypatch)
    identity_b = _identity(tenant_b)
    conversation_id = uuid.uuid4()

    with pytest.raises(NotImplementedError, match="Step 3.4"):
        await _collect(
            run_turn(
                graph, identity_b, conversation_id, {"messages": ["zebra giraffe safari"]}
            )
        )

    snapshot = await read_state(graph, identity_b, conversation_id)
    chunks = snapshot.values["retrieved_chunks"]
    assert len(chunks) >= 1
    assert all(chunk["document_id"] == str(doc_b) for chunk in chunks)
    assert all(chunk["document_id"] != str(doc_a) for chunk in chunks)
    assert any("mortgage" in chunk["text"] for chunk in chunks)
    assert all("zebra" not in chunk["text"] for chunk in chunks)
