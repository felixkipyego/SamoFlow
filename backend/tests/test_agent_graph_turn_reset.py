# backend/tests/test_agent_graph_turn_reset.py
# Task 3.4.e [SECURITY-adjacent]: `_reset_turn_state_node` -- every
# per-turn field in `AgentState` reset to its fresh-turn default, in ONE
# place, run unconditionally before this turn's own routing decision. See
# that node's own header comment (app/agent/graph.py) for the full
# reasoning and the bug class this closes.
#
# Split, matching this project's own established pattern: pure, offline
# unit tests first (the node itself, and a structural meta-test that a
# future per-turn field addition cannot silently skip this node), then
# the real cross-turn proof through the real graph/checkpointer, with
# fakes only (no real API calls).
import uuid

import pytest
from langchain_core.messages import AIMessage

from app.agent import checkpointer
from app.agent import graph as graph_module
from app.agent.entrypoint import read_state, run_turn
from app.agent.graph import AgentState, build_graph
from app.auth.dependencies import VerifiedIdentity
from tests.conftest import (
    FakeAnswerToolChatModel,
    build_fake_answer_arg_chunks,
    reset_checkpoint_env,
)

pytestmark = pytest.mark.usefixtures("close_any_checkpointer_pool_left_open")

_ORG = "https://tenant.example"


def _identity() -> VerifiedIdentity:
    return VerifiedIdentity(tenant_id=uuid.uuid4(), vid=uuid.uuid4(), org=_ORG)


async def _collect(aiter):
    return [x async for x in aiter]


def _chunk(document_id: str, text: str) -> dict:
    return {
        "document_id": document_id,
        "text": text,
        "source_url": f"https://tenant.example/{document_id}",
        "heading_path": [],
        "score": 0.5,
    }


# --- Pure unit tests ---------------------------------------------------------


def test_reset_turn_state_node_returns_every_fields_fresh_default():
    result = graph_module._reset_turn_state_node({})
    assert result == {
        "retrieved_chunks": [],
        "best_dense_score": None,
        "can_answer": False,
        "cited_chunk_labels": [],
        "citations": [],
        "dependency_failed": False,
        "prepared_query": "",
        "resolved_model": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "answer_stopped": False,
    }


def test_reset_turn_state_node_returns_fresh_objects_each_call():
    # Never a shared, reused mutable list -- two separate calls' own
    # returned lists must not be the SAME object (no risk of one call's
    # own value aliasing another's).
    first = graph_module._reset_turn_state_node({})
    second = graph_module._reset_turn_state_node({})
    assert first["retrieved_chunks"] is not second["retrieved_chunks"]
    assert first["citations"] is not second["citations"]


def test_reset_turn_state_node_covers_every_per_turn_agentstate_field():
    # STRUCTURAL guard against the exact bug class this node exists to
    # close: if a future task adds a new per-turn field to AgentState
    # without also adding it to this node's own returned defaults, this
    # test fails immediately, rather than silently reintroducing a
    # cross-turn leak someone has to rediscover by hand.
    per_turn_fields = set(AgentState.__annotations__.keys()) - {"messages"}
    reset_fields = set(graph_module._reset_turn_state_node({}).keys())
    assert reset_fields == per_turn_fields


# --- Live, through the real graph/checkpointer, fakes only -----------------


async def _fresh_real_graph(monkeypatch):
    await reset_checkpoint_env(monkeypatch)
    await checkpointer.open_checkpointer_pool()
    cp = checkpointer.get_checkpointer()
    await cp.setup()
    return build_graph(cp)


async def test_per_turn_state_does_not_leak_across_turns_on_the_same_thread(monkeypatch):
    """The exact scenario named by direct instruction: turn 1 answers a
    question with real citations; turn 2, same thread, is gated out
    (nothing relevant retrieved this time) -- turn 2's own final state
    must hold NONE of turn 1's values (no citations, no cited ids, no
    retrieved chunks, no can_answer=true carried over), and the chat
    model must never be invoked for turn 2 at all.
    """
    call_sequence: list[int] = []

    async def _fake_retrieve_node(state, config):
        call_sequence.append(1)
        if len(call_sequence) == 1:
            return {
                "retrieved_chunks": [_chunk("doc-a", "Zebras are black and white striped.")],
                "best_dense_score": 0.9,
            }
        return {"retrieved_chunks": [], "best_dense_score": None}

    monkeypatch.setattr(graph_module, "_retrieve_node", _fake_retrieve_node)

    chunks = build_fake_answer_arg_chunks(
        can_answer=True, used_chunk_ids=["c1"], answer="Zebras are striped."
    )
    fake_model = FakeAnswerToolChatModel(arg_chunks=chunks)
    model_call_count = {"n": 0}

    def _counting_get_chat_model():
        model_call_count["n"] += 1
        return fake_model

    monkeypatch.setattr(graph_module, "get_chat_model", _counting_get_chat_model)
    monkeypatch.setattr(graph_module, "get_stream_writer", lambda: (lambda event: None))

    graph = await _fresh_real_graph(monkeypatch)
    identity = _identity()
    conversation_id = uuid.uuid4()

    # Turn 1: answers, with real citations.
    results1 = await _collect(
        run_turn(graph, identity, conversation_id, {"messages": ["What do zebras look like?"]})
    )
    assert results1
    snapshot1 = await read_state(graph, identity, conversation_id)
    assert snapshot1.values["can_answer"] is True
    assert snapshot1.values["citations"] == [
        {"url": "https://tenant.example/doc-a", "heading_path": []}
    ]
    assert snapshot1.values["cited_chunk_labels"] == ["c1"]
    assert snapshot1.values["retrieved_chunks"] != []
    assert snapshot1.values["best_dense_score"] == 0.9

    # Turn 2: SAME thread, gated out (the fake retrieve node now returns
    # nothing) -- reaches the real fixed no-answer reply, not the fake
    # model at all.
    results2 = await _collect(
        run_turn(graph, identity, conversation_id, {"messages": ["asdkjf nonsense gibberish"]})
    )
    assert results2

    snapshot2 = await read_state(graph, identity, conversation_id)
    assert snapshot2.values["citations"] == []
    assert snapshot2.values["cited_chunk_labels"] == []
    assert snapshot2.values["retrieved_chunks"] == []
    assert snapshot2.values["best_dense_score"] is None
    assert snapshot2.values["can_answer"] is False
    assert snapshot2.values["messages"][-1].content == graph_module._sanitize_answer_text(
        graph_module._NO_ANSWER_FALLBACK_TEXT
    )
    # messages is the ONLY cross-turn field -- turn 1's own AIMessage
    # must still be present, not wiped by the reset.
    assert any(
        isinstance(m, AIMessage) and m.content == "Zebras are striped."
        for m in snapshot2.values["messages"]
    )

    # The chat model was invoked exactly once -- turn 1, never turn 2.
    assert model_call_count["n"] == 1
