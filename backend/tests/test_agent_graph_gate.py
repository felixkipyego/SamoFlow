# backend/tests/test_agent_graph_gate.py
# Task 3.4.d [SECURITY]: the real gate node (_gate_node/_route_after_gate,
# app/agent/graph.py) and its one real signal, retrieve()'s new
# RetrievalResult.best_dense_score (app/retrieval/service.py). Built per
# the Step 3.4.d research task's own findings (PROJECT_SPEC.md's own
# dated research entry) -- not repeated here.
#
# Split in two, matching this project's own established "pure unit tests
# first, then the real thing through the real graph" pattern:
#   - Pure, offline, no live services: _gate_node/_route_after_gate as
#     plain functions, fed hand-built state dicts -- fast, no real
#     embeddings needed at all (the gate reads an ALREADY-COMPUTED score,
#     it never embeds anything itself).
#   - Live, through the real graph/retrieve()/Qdrant, with DETERMINISTIC
#     fake dense vectors carrying KNOWN, exact cosine values (reusing
#     test_qdrant_isolation.py's own _onehot()/QUERY_SPARSE and test_
#     retrieval_isolation.py's own _pin_query_embeddings()) -- real
#     embeddings would make cosine values meaningless for a deterministic
#     CI assertion, matching this project's own "fake embeddings in CI,
#     real ones only in opt-in manual scripts" convention.
#
# The isolation proof itself (another tenant's/a hidden point's perfect
# dense match must never raise THIS tenant's own best_dense_score) lives
# in test_retrieval_isolation.py (retrieve()'s own job, Task 3.4.d's
# addition there) -- not duplicated here; this file's own job is the
# GATE's routing decision given a score, not where the score comes from.
import logging
import uuid

import pytest
from qdrant_client.http.models import PointStruct

import app.agent.context as context_module
from app import qdrant
from app.agent import graph as graph_module
from app.agent.entrypoint import read_state, run_turn
from app.config import get_settings
from app.retrieval import service as service_module
from tests.conftest import (
    VALID_ENV,
    FakeAnswerToolChatModel,
    build_fake_answer_arg_chunks,
    require_test_qdrant,
    set_valid_env,
)
from tests.test_agent_graph_retrieve import _collect, _fresh_real_graph, _identity
from tests.test_qdrant_isolation import QUERY_SPARSE, _onehot
from tests.test_retrieval_isolation import _pin_query_embeddings

pytestmark = pytest.mark.usefixtures(
    "close_any_checkpointer_pool_left_open", "close_the_shared_qdrant_read_client_once"
)

_QUERY_TEXT = "irrelevant -- embeddings are pinned below, not derived from this text"
QUERY_DENSE = _onehot(0)
# Exactly orthogonal to QUERY_DENSE (a different single axis) -- cosine
# is exactly 0.0, comfortably below the default 0.20 threshold, not a
# near-miss.
_ORTHOGONAL_DENSE = _onehot(5)


def _chunk(document_id: str, text: str) -> dict:
    return {
        "document_id": document_id,
        "text": text,
        "source_url": f"https://tenant.example/{document_id}",
        "heading_path": [],
        "score": 0.5,
    }


# --- Pure unit tests: _gate_node / _route_after_gate ------------------------


def test_gate_zero_chunks_routes_to_fallback_shape(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    state = {"retrieved_chunks": [], "best_dense_score": None}
    result = graph_module._gate_node(state)
    assert result == {"can_answer": False, "cited_chunk_labels": [], "citations": []}
    assert graph_module._route_after_gate({**state, **result}) == "no_answer_fallback"


def test_gate_below_threshold_routes_to_fallback_shape(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    threshold = get_settings().gate_relevance_threshold
    state = {
        "retrieved_chunks": [_chunk("doc-a", "Some text.")],
        "best_dense_score": threshold - 0.01,
    }
    result = graph_module._gate_node(state)
    assert result == {"can_answer": False, "cited_chunk_labels": [], "citations": []}
    assert graph_module._route_after_gate({**state, **result}) == "no_answer_fallback"


def test_gate_above_threshold_passes_through(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    threshold = get_settings().gate_relevance_threshold
    state = {
        "retrieved_chunks": [_chunk("doc-a", "Some text.")],
        "best_dense_score": threshold + 0.01,
    }
    result = graph_module._gate_node(state)
    assert result == {"can_answer": True}
    assert graph_module._route_after_gate({**state, **result}) == "answered"


def test_gate_exactly_at_threshold_passes_through(monkeypatch):
    # >=, not >: a score equal to the threshold is a PASS, confirming the
    # comparison operator rather than assuming it.
    set_valid_env(monkeypatch, VALID_ENV)
    threshold = get_settings().gate_relevance_threshold
    state = {
        "retrieved_chunks": [_chunk("doc-a", "Some text.")],
        "best_dense_score": threshold,
    }
    result = graph_module._gate_node(state)
    assert result == {"can_answer": True}


def test_gate_none_score_with_nonempty_chunks_routes_to_fallback_shape(monkeypatch):
    # Defensive: retrieve() only returns None when there is truly nothing
    # to score, which in practice coincides with zero chunks -- but the
    # gate itself must not crash or wrongly pass a turn through if this
    # pair were ever inconsistent (e.g. a future bug in retrieve() itself).
    set_valid_env(monkeypatch, VALID_ENV)
    state = {"retrieved_chunks": [_chunk("doc-a", "Some text.")], "best_dense_score": None}
    result = graph_module._gate_node(state)
    assert result == {"can_answer": False, "cited_chunk_labels": [], "citations": []}


def test_gate_logs_decision_without_query_or_chunk_text(caplog, monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    caplog.set_level(logging.INFO, logger=graph_module.logger.name)
    state = {
        "retrieved_chunks": [_chunk("doc-a", "a distinctive chunk sentence")],
        "best_dense_score": 0.9,
    }
    graph_module._gate_node(state)

    gate_records = [r for r in caplog.records if "gate decision" in r.getMessage()]
    assert len(gate_records) == 1
    message = gate_records[0].getMessage()
    assert "distinctive chunk sentence" not in message
    assert "passed=True" in message
    assert "best_dense_score=0.9" in message
    assert "chunk_count=1" in message


# --- Live, through the real graph/retrieve()/Qdrant, deterministic fakes ----


async def _seed_one_point(client, name: str, tenant_id: uuid.UUID, dense_vector: list[float]):
    await qdrant.ensure_collection(client, name)
    document_id = uuid.uuid4()
    await client.upsert(
        collection_name=name,
        points=[
            PointStruct(
                id=str(uuid.uuid4()),
                vector={"dense": dense_vector, "sparse": QUERY_SPARSE},
                payload={
                    "client_id": str(tenant_id),
                    "doc_id": str(document_id),
                    "text": "Zebras are black and white striped.",
                    "source_url": "https://tenant.example/doc",
                    "heading_path": [],
                },
            ),
        ],
        wait=True,
    )
    return document_id


def _patch_fake_answer_model(monkeypatch) -> None:
    chunks = build_fake_answer_arg_chunks(
        can_answer=True, used_chunk_ids=["c1"], answer="A fixed test answer."
    )
    fake = FakeAnswerToolChatModel(arg_chunks=chunks)
    monkeypatch.setattr(graph_module, "get_chat_model", lambda: fake)
    monkeypatch.setattr(graph_module, "get_stream_writer", lambda: (lambda event: None))


def _patch_chat_model_must_never_be_called(monkeypatch) -> None:
    def _never_called():
        raise AssertionError(
            "get_chat_model() must never be called when the gate routes to the fallback "
            "-- the whole point of the gate is skipping the AI call entirely"
        )

    monkeypatch.setattr(graph_module, "get_chat_model", _never_called)


def _patch_retrieve_scoped_to_collection(monkeypatch, collection_name: str) -> None:
    # The real retrieve node, unchanged, EXCEPT pointed at a disposable
    # per-test collection instead of the real app.qdrant.COLLECTION_NAME
    # (matching test_retrieval_service.py/test_retrieval_isolation.py's
    # own collection_name-override convention) -- everything else (tenant
    # identity from config, chunk_from_scored_point(), best_dense_score)
    # is the REAL production code path, not a fake.
    async def _node(state, config):
        tenant_id = context_module.tenant_id_from_config(config)
        query = graph_module._latest_human_message_text(state["messages"])
        result = await service_module.retrieve(tenant_id, query, collection_name=collection_name)
        return {
            "retrieved_chunks": [graph_module.chunk_from_scored_point(p) for p in result.points],
            "best_dense_score": result.best_dense_score,
        }

    monkeypatch.setattr(graph_module, "_retrieve_node", _node)


async def test_below_threshold_reaches_fallback_with_zero_model_calls(monkeypatch):
    _pin_query_embeddings(monkeypatch)
    _patch_chat_model_must_never_be_called(monkeypatch)
    url, key = require_test_qdrant()
    client = qdrant.build_qdrant_client(url, key)
    name = f"gate_below_{uuid.uuid4().hex[:8]}"
    tenant_id = uuid.uuid4()
    try:
        await _seed_one_point(client, name, tenant_id, _ORTHOGONAL_DENSE)
        _patch_retrieve_scoped_to_collection(monkeypatch, name)
        graph = await _fresh_real_graph(monkeypatch)
        identity = _identity(tenant_id)
        conversation_id = uuid.uuid4()

        with pytest.raises(NotImplementedError, match="Step 3.4.e"):
            await _collect(
                run_turn(graph, identity, conversation_id, {"messages": [_QUERY_TEXT]})
            )

        snapshot = await read_state(graph, identity, conversation_id)
        assert snapshot.values["can_answer"] is False
        assert snapshot.values["best_dense_score"] == pytest.approx(0.0)
    finally:
        if await client.collection_exists(name):
            await client.delete_collection(name)
        await client.close()


async def test_above_threshold_reaches_the_answer_node(monkeypatch):
    _pin_query_embeddings(monkeypatch)
    _patch_fake_answer_model(monkeypatch)
    url, key = require_test_qdrant()
    client = qdrant.build_qdrant_client(url, key)
    name = f"gate_above_{uuid.uuid4().hex[:8]}"
    tenant_id = uuid.uuid4()
    try:
        await _seed_one_point(client, name, tenant_id, QUERY_DENSE)
        _patch_retrieve_scoped_to_collection(monkeypatch, name)
        graph = await _fresh_real_graph(monkeypatch)
        identity = _identity(tenant_id)
        conversation_id = uuid.uuid4()

        # The full retrieve -> gate -> answer path, end to end, through
        # the real guarded entry point -- "the full run_turn() path still
        # works end to end" against the REAL retrieve(), not a fake one.
        results = await _collect(
            run_turn(graph, identity, conversation_id, {"messages": [_QUERY_TEXT]})
        )
        assert results

        snapshot = await read_state(graph, identity, conversation_id)
        assert snapshot.values["can_answer"] is True
        assert snapshot.values["best_dense_score"] == pytest.approx(1.0)
    finally:
        if await client.collection_exists(name):
            await client.delete_collection(name)
        await client.close()


async def test_zero_chunks_routes_to_fallback_through_the_real_graph(monkeypatch):
    _pin_query_embeddings(monkeypatch)
    _patch_chat_model_must_never_be_called(monkeypatch)
    url, key = require_test_qdrant()
    client = qdrant.build_qdrant_client(url, key)
    name = f"gate_empty_{uuid.uuid4().hex[:8]}"
    tenant_id = uuid.uuid4()
    try:
        await qdrant.ensure_collection(client, name)  # an empty collection, no points at all
        _patch_retrieve_scoped_to_collection(monkeypatch, name)
        graph = await _fresh_real_graph(monkeypatch)
        identity = _identity(tenant_id)
        conversation_id = uuid.uuid4()

        with pytest.raises(NotImplementedError, match="Step 3.4.e"):
            await _collect(
                run_turn(graph, identity, conversation_id, {"messages": [_QUERY_TEXT]})
            )

        snapshot = await read_state(graph, identity, conversation_id)
        assert snapshot.values["retrieved_chunks"] == []
        assert snapshot.values["best_dense_score"] is None
        assert snapshot.values["can_answer"] is False
    finally:
        if await client.collection_exists(name):
            await client.delete_collection(name)
        await client.close()
