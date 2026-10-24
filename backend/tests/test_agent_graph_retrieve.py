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
# embed_dense()'s own paid OpenAI call is stubbed (originally patch_
# embed_dense(), this project's own established convention -- changed at
# Task 3.4.d to a LOCAL, non-zero-constant variant, see _patch_embed_
# dense_constant_nonzero()'s own header comment for why the shared
# all-zero helper stopped being safe to use here once the gate became
# real); embed_sparse()/embed_sparse_query() both make real fastembed
# calls. Seeded into the REAL app.qdrant.COLLECTION_NAME, not a
# disposable per-test collection name: _retrieve_node calls
# retrieve(tenant_id, query) with no collection_name override (matching
# production), so there is no way to point it at a throwaway collection.
#
# Task 3.3.d: unlike test_job_handlers.py/test_upload_e2e.py (which leave
# their own seeded points in COLLECTION_NAME permanently), this file's own
# two tests delete what they seed, by client_id, in a try/finally -- so a
# long-lived local test-qdrant doesn't accumulate data from repeated runs
# of this file specifically. Reuses `app.qdrant.tenant_filter()` directly
# (the same filter retrieve() itself is scoped by), not a hand-built one.
#
# Task 3.4.b: gate_answer is no longer a stub -- the real answer node now
# completes the turn instead of raising, so these two tests' own
# "NotImplementedError naming Step 3.4" assertion (the retrieve ->
# gate_answer wiring proof) is updated to assert the turn completes with
# a real final AIMessage instead. `get_chat_model()` is stubbed with
# `FakeAnswerToolChatModel` (tests/conftest.py, shared with
# test_agent_graph_answer.py) -- this file's own job is still only
# proving retrieve()'s own tenant isolation through the real graph, not
# re-proving the answer node's own mechanism (that is test_agent_graph_
# answer.py's job) -- the fake's own answer content is a trivial,
# fixed string.
import uuid

import pytest

from app import qdrant
from app.agent import checkpointer
from app.agent import graph as graph_module
from app.agent.entrypoint import read_state, run_turn
from app.agent.graph import build_graph
from app.auth.dependencies import VerifiedIdentity
from app.ingest import embedding
from app.ingest.extract_html import extract_html
from app.ingest.qdrant_writer import embed_and_upsert
from tests.conftest import (
    FakeAnswerToolChatModel,
    build_fake_answer_arg_chunks,
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


class _ConstantNonZeroEmbeddingItem:
    def __init__(self, vector: list[float]) -> None:
        self.embedding = vector


class _ConstantNonZeroEmbeddingResponse:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.data = [_ConstantNonZeroEmbeddingItem(v) for v in vectors]


class _ConstantNonZeroEmbeddingsResource:
    async def create(self, *, model, input):  # noqa: A002 -- matches the real SDK's own param name
        vector = [1.0] + [0.0] * (qdrant.DENSE_VECTOR_SIZE - 1)
        return _ConstantNonZeroEmbeddingResponse([vector for _ in input])


class _ConstantNonZeroEmbeddingClient:
    def __init__(self) -> None:
        self.embeddings = _ConstantNonZeroEmbeddingsResource()


def _patch_embed_dense_constant_nonzero(monkeypatch) -> None:
    # Task 3.4.d [SECURITY] follow-up: `patch_embed_dense()` (tests/
    # conftest.py), this file's own original choice, returns an ALL-ZERO
    # vector -- fine when the gate was a pass-through stub, but Task
    # 3.4.d's real gate reads `best_dense_score` (a real Qdrant cosine
    # score), and an all-zero query against all-zero points scores
    # exactly 0.0, BELOW the gate's own default threshold -- confirmed
    # live, this broke both of this file's own tests (routed to the
    # `no_answer_fallback` stub instead of reaching the real answer node,
    # which is NOT what either test is trying to prove). NOT fixed by
    # changing the shared `patch_embed_dense()` helper itself (other
    # files rely on its exact all-zero/degenerate behavior) -- a local,
    # scoped replacement instead: a FIXED, non-zero vector, identical for
    # every input, same as before (dense is still uninformative/constant,
    # so the real sparse/BM25 channel is still what differentiates
    # tenants, unchanged from this file's own original intent) but with a
    # real, defined, comfortably-above-threshold cosine score (1.0,
    # identical vectors) instead of an undefined-in-spirit 0.0.
    monkeypatch.setattr(embedding, "_get_client", lambda: _ConstantNonZeroEmbeddingClient())


def _patch_fake_answer_model(monkeypatch) -> None:
    chunks = build_fake_answer_arg_chunks(
        can_answer=True, used_chunk_ids=["c1"], answer="A fixed test answer."
    )
    fake = FakeAnswerToolChatModel(arg_chunks=chunks)
    monkeypatch.setattr(graph_module, "get_chat_model", lambda: fake)
    monkeypatch.setattr(graph_module, "get_stream_writer", lambda: (lambda event: None))


async def _delete_tenants_points(client, *tenant_ids: uuid.UUID) -> None:
    for tenant_id in tenant_ids:
        await client.delete(
            collection_name=qdrant.COLLECTION_NAME, points_selector=qdrant.tenant_filter(tenant_id)
        )


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
    await checkpointer.run_checkpointer_setup()
    await checkpointer.open_checkpointer_pool()
    cp = checkpointer.get_checkpointer()
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
    client, tenant_a: uuid.UUID, tenant_b: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID]:
    # Distinct, non-overlapping vocabulary per tenant (matching Task
    # 3.1.c/3.1.d's own established pattern): dense is stubbed to a
    # constant vector by patch_embed_dense(), so the real sparse (BM25)
    # side of the hybrid query is what makes tenant_filter() the only
    # thing standing between a cross-topic query and the other tenant's
    # content.
    #
    # Takes tenant_a/tenant_b as PARAMETERS (Task 3.3.d), not generated
    # here: both callers now generate them before entering their own
    # try/finally, so the finally's own cleanup knows both tenant ids
    # even if this function raises partway through (e.g. tenant_a's own
    # embed_and_upsert() succeeds, tenant_b's then fails) -- the points
    # already written for tenant_a must still be deleted, not orphaned.
    doc_a = await _seed_tenant(
        client, tenant_a, "Tenant A Doc", "zebra giraffe elephant safari wildlife",
        "https://tenant-a.example/page",
    )
    doc_b = await _seed_tenant(
        client, tenant_b, "Tenant B Doc", "mortgage interest rate amortization loan",
        "https://tenant-b.example/page",
    )
    return doc_a, doc_b


async def test_run_turn_as_tenant_a_with_a_query_matching_tenant_bs_topic_stays_scoped_to_tenant_a(
    monkeypatch,
):
    _patch_embed_dense_constant_nonzero(monkeypatch)
    _patch_fake_answer_model(monkeypatch)
    url, key = require_test_qdrant()
    client = qdrant.build_qdrant_client(url, key)
    await qdrant.ensure_collection(client, qdrant.COLLECTION_NAME)

    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    try:
        doc_a, doc_b = await _seed_two_tenants_with_non_overlapping_content(
            client, tenant_a, tenant_b
        )
        graph = await _fresh_real_graph(monkeypatch)
        identity_a = _identity(tenant_a)
        conversation_id = uuid.uuid4()

        # Proves the retrieve -> gate -> answer edge fired for real
        # (3.3.b's own trade-off item 2, updated at 3.4.b now that gate_
        # answer is no longer a stub): the query text matches tenant B's
        # own topic, so if tenant scoping were broken this would still
        # "succeed" at retrieval -- the turn only completes with a real
        # final AIMessage if the real retrieve node itself completed
        # without raising, scoped correctly.
        results = await _collect(
            run_turn(
                graph, identity_a, conversation_id, {"messages": ["mortgage interest rate"]}
            )
        )
        assert results

        snapshot = await read_state(graph, identity_a, conversation_id)
        chunks = snapshot.values["retrieved_chunks"]
        assert len(chunks) >= 1
        assert all(chunk["document_id"] == str(doc_a) for chunk in chunks)
        assert all(chunk["document_id"] != str(doc_b) for chunk in chunks)
        assert any("zebra" in chunk["text"] for chunk in chunks)
        assert all("mortgage" not in chunk["text"] for chunk in chunks)
        assert snapshot.values["can_answer"] is True
    finally:
        await _delete_tenants_points(client, tenant_a, tenant_b)
        await client.close()


async def test_run_turn_as_tenant_b_with_a_query_matching_tenant_as_topic_stays_scoped_to_tenant_b(
    monkeypatch,
):
    _patch_embed_dense_constant_nonzero(monkeypatch)
    _patch_fake_answer_model(monkeypatch)
    url, key = require_test_qdrant()
    client = qdrant.build_qdrant_client(url, key)
    await qdrant.ensure_collection(client, qdrant.COLLECTION_NAME)

    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    try:
        doc_a, doc_b = await _seed_two_tenants_with_non_overlapping_content(
            client, tenant_a, tenant_b
        )
        graph = await _fresh_real_graph(monkeypatch)
        identity_b = _identity(tenant_b)
        conversation_id = uuid.uuid4()

        results = await _collect(
            run_turn(
                graph, identity_b, conversation_id, {"messages": ["zebra giraffe safari"]}
            )
        )
        assert results

        snapshot = await read_state(graph, identity_b, conversation_id)
        chunks = snapshot.values["retrieved_chunks"]
        assert len(chunks) >= 1
        assert all(chunk["document_id"] == str(doc_b) for chunk in chunks)
        assert all(chunk["document_id"] != str(doc_a) for chunk in chunks)
        assert any("mortgage" in chunk["text"] for chunk in chunks)
        assert all("zebra" not in chunk["text"] for chunk in chunks)
        assert snapshot.values["can_answer"] is True
    finally:
        await _delete_tenants_points(client, tenant_a, tenant_b)
        await client.close()
