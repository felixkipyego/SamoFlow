# backend/tests/test_agent_entrypoint.py
# Task 3.2.c [SECURITY]: live tests for the guarded entry point
# (app/agent/entrypoint.py), real against test-db with a real
# AsyncPostgresSaver from 3.2.b's own pool. No real graph/router/
# specialist is built or used here (Step 3.3's own job) -- a trivial
# throwaway StateGraph, built only for these tests, stands in for it.
#
# The throwaway graph's single node calls interrupt() unconditionally, so
# every ordinary run_turn() call pauses after the input merge (state
# already reflects the incoming message at that point -- LangGraph applies
# channel reducers before a node runs) but before completing -- enough to
# prove thread scoping (tests a-d) without needing every turn to run to
# completion, and enough to give resume_turn() (test e) something real to
# resume.
import operator
import uuid
from typing import Annotated, TypedDict

import psycopg
import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agent import checkpointer
from app.agent.entrypoint import (
    _assert_thread_prefix_matches,
    _build_thread_config,
    read_state,
    resume_turn,
    run_turn,
)
from app.auth.dependencies import VerifiedIdentity
from tests.test_checkpointer import _drop_checkpoint_tables, _set_real_database_env

_ORG = "https://tenant.example"


class _TestState(TypedDict):
    messages: Annotated[list[str], operator.add]
    resumed: Annotated[list[str], operator.add]


def _interruptible_node(state: _TestState) -> dict:
    answer = interrupt("need input")
    return {"resumed": [answer]}


def _build_test_graph(cp):
    builder = StateGraph(_TestState)
    builder.add_node("node", _interruptible_node)
    builder.add_edge(START, "node")
    builder.add_edge("node", END)
    return builder.compile(checkpointer=cp)


@pytest.fixture(autouse=True)
async def _close_any_pool_left_open():
    # Duplicated from test_checkpointer.py's own identical fixture -- a
    # 2nd occurrence now, flagged here (not fixed mid-task) as a
    # duplication-check candidate, matching this project's own established
    # "flag, don't fix mid-task" discipline for a 2nd-3rd occurrence.
    yield
    if checkpointer._checkpointer_pool is not None:
        await checkpointer.close_checkpointer_pool()


async def _fresh_graph(monkeypatch):
    _set_real_database_env(monkeypatch)
    conninfo = checkpointer.checkpointer_conninfo()
    await _drop_checkpoint_tables(conninfo)
    await checkpointer.open_checkpointer_pool()
    cp = checkpointer.get_checkpointer()
    await cp.setup()
    return _build_test_graph(cp)


async def _collect(aiter):
    return [x async for x in aiter]


async def _messages_of(graph, identity: VerifiedIdentity, conversation_id: uuid.UUID) -> list[str]:
    snapshot = await read_state(graph, identity, conversation_id)
    return snapshot.values.get("messages", [])


def _identity(**overrides) -> VerifiedIdentity:
    defaults = {"tenant_id": uuid.uuid4(), "vid": uuid.uuid4(), "org": _ORG}
    return VerifiedIdentity(**{**defaults, **overrides})


# --- (a) real turn persists a checkpoint with the expected thread_id -------


async def test_a_real_turn_persists_a_checkpoint_with_the_expected_thread_id(monkeypatch):
    graph = await _fresh_graph(monkeypatch)
    identity = _identity()
    conversation_id = uuid.uuid4()

    await _collect(run_turn(graph, identity, conversation_id, {"messages": ["hi"]}))

    expected_thread_id = f"{identity.tenant_id}:{identity.vid}:{conversation_id}"
    conninfo = checkpointer.checkpointer_conninfo()
    async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
        row = await (
            await conn.execute(
                "SELECT thread_id FROM checkpoints WHERE thread_id = %s", (expected_thread_id,)
            )
        ).fetchone()

    assert row is not None
    assert row[0] == expected_thread_id


# --- (b) two visitors, same tenant, same conversation_id -> separate threads


async def test_two_visitors_same_tenant_same_conversation_id_get_separate_threads(monkeypatch):
    graph = await _fresh_graph(monkeypatch)
    tenant_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    identity_a = _identity(tenant_id=tenant_id)
    identity_b = _identity(tenant_id=tenant_id)

    await _collect(run_turn(graph, identity_a, conversation_id, {"messages": ["from A"]}))
    await _collect(run_turn(graph, identity_b, conversation_id, {"messages": ["from B"]}))

    messages_b = await _messages_of(graph, identity_b, conversation_id)
    assert messages_b == ["from B"]


# --- (c) two tenants, same vid, same conversation_id -> separate threads ---


async def test_two_tenants_same_vid_same_conversation_id_get_separate_threads(monkeypatch):
    graph = await _fresh_graph(monkeypatch)
    vid = uuid.uuid4()
    conversation_id = uuid.uuid4()
    identity_1 = _identity(vid=vid)
    identity_2 = _identity(vid=vid)

    await _collect(run_turn(graph, identity_1, conversation_id, {"messages": ["tenant 1"]}))
    await _collect(run_turn(graph, identity_2, conversation_id, {"messages": ["tenant 2"]}))

    messages_2 = await _messages_of(graph, identity_2, conversation_id)
    assert messages_2 == ["tenant 2"]


# --- (d) input keys named thread_id/configurable have no effect ------------


async def test_input_keys_named_thread_id_or_configurable_have_no_effect(monkeypatch):
    graph = await _fresh_graph(monkeypatch)
    identity = _identity()
    conversation_id = uuid.uuid4()
    other_conversation_id = uuid.uuid4()

    await _collect(
        run_turn(
            graph,
            identity,
            conversation_id,
            {
                "messages": ["real"],
                "thread_id": "evil",
                "configurable": {"thread_id": str(other_conversation_id)},
            },
        )
    )

    messages = await _messages_of(graph, identity, conversation_id)
    assert messages == ["real"]
    other_messages = await _messages_of(graph, identity, other_conversation_id)
    assert other_messages == []


# --- (e) resume via Command(resume=...) lands on the correct thread --------


async def test_resume_lands_on_the_correct_thread(monkeypatch):
    graph = await _fresh_graph(monkeypatch)
    identity = _identity()
    conversation_id = uuid.uuid4()
    other_conversation_id = uuid.uuid4()

    await _collect(run_turn(graph, identity, conversation_id, {"messages": ["start"]}))
    await _collect(run_turn(graph, identity, other_conversation_id, {"messages": ["other"]}))

    await _collect(resume_turn(graph, identity, conversation_id, "the answer"))

    snapshot = await read_state(graph, identity, conversation_id)
    assert snapshot.values.get("resumed") == ["the answer"]

    other_snapshot = await read_state(graph, identity, other_conversation_id)
    assert other_snapshot.values.get("resumed", []) == []


# --- (f) delimiter-injection rejection --------------------------------------


def test_build_thread_config_rejects_a_non_uuid_vid():
    # Bypasses VerifiedIdentity's own type hint on purpose -- a dataclass
    # never enforces its own field types at construction, so this is a
    # real, reachable shape, not a contrived one.
    bad_identity = VerifiedIdentity(tenant_id=uuid.uuid4(), vid="evil:vid", org=_ORG)
    with pytest.raises(ValueError, match="vid"):
        _build_thread_config(bad_identity, uuid.uuid4())


def test_build_thread_config_rejects_a_non_uuid_conversation_id():
    identity = _identity()
    with pytest.raises(ValueError, match="conversation_id"):
        _build_thread_config(identity, "not-a-uuid")


# --- (g) prefix assertion fires on a mismatched config ----------------------


def test_assert_thread_prefix_matches_raises_on_a_mismatched_config():
    identity = _identity()
    mismatched_config = {
        "configurable": {"thread_id": f"{uuid.uuid4()}:{uuid.uuid4()}:{uuid.uuid4()}"}
    }
    with pytest.raises(RuntimeError, match="thread prefix mismatch"):
        _assert_thread_prefix_matches(mismatched_config, identity)


def test_assert_thread_prefix_matches_passes_for_a_matching_config():
    identity = _identity()
    good_config = _build_thread_config(identity, uuid.uuid4())
    _assert_thread_prefix_matches(good_config, identity)  # must not raise


# --- Layer 3 runtime backstop (test_agent_entrypoint_guard.py's own design
# rests on this) ------------------------------------------------------------


async def test_a_graph_invoked_with_no_thread_config_raises_the_real_checkpointer_error(
    monkeypatch,
):
    # The fact the 3-layer AST guard design in test_agent_entrypoint_guard.py
    # rests on: any code path that bypasses _build_thread_config() has no
    # way to supply a config carrying the real checkpointer keys, and a
    # real compiled graph genuinely refuses to run at all without one --
    # confirmed against the installed langgraph directly (Task 3.2.c), not
    # assumed from a docstring claim. Deliberately calls graph.astream()
    # directly here, bypassing the entry point on purpose, to prove this.
    graph = await _fresh_graph(monkeypatch)
    with pytest.raises(ValueError, match="Checkpointer requires"):
        async for _ in graph.astream({"messages": ["hi"]}):
            pass
