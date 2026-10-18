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
import inspect
import operator
import uuid
from typing import Annotated, TypedDict

import psycopg
import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agent import checkpointer
from app.agent.entrypoint import (
    NoResumableInterruptError,
    _assert_thread_prefix_matches,
    _build_thread_config,
    read_state,
    resume_turn,
    run_turn,
)
from app.agent.graph import AgentState
from app.auth.dependencies import VerifiedIdentity
from app.config import get_settings
from tests.conftest import reset_checkpoint_env

pytestmark = pytest.mark.usefixtures("close_any_checkpointer_pool_left_open")

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


async def _fresh_graph(monkeypatch):
    await reset_checkpoint_env(monkeypatch)
    # [SECURITY] L2 (Phase 7 role split, 2026-10-18): setup() is genuine
    # DDL -- run via run_checkpointer_setup() (the OWNER role), never
    # through the live pool below (APP role, no CREATE on the schema;
    # `cp.setup()` through that pool would now fail outright, the exact
    # "runtime DDL trap" this split exists to find and remove).
    await checkpointer.run_checkpointer_setup()
    await checkpointer.open_checkpointer_pool()
    cp = checkpointer.get_checkpointer()
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
    conninfo = checkpointer.checkpointer_conninfo(get_settings().database_url_str())
    async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
        row = await (
            await conn.execute(
                "SELECT thread_id FROM checkpoints WHERE thread_id = %s", (expected_thread_id,)
            )
        ).fetchone()

    assert row is not None
    assert row[0] == expected_thread_id


# --- tenant_id in config [SECURITY] (Task 3.3.a) ---------------------------


def test_build_thread_config_includes_tenant_id_as_a_raw_uuid():
    identity = _identity()
    config = _build_thread_config(identity, uuid.uuid4())
    tenant_id = config["configurable"]["tenant_id"]
    assert tenant_id == identity.tenant_id
    assert isinstance(tenant_id, uuid.UUID)
    assert not isinstance(tenant_id, str)


async def test_tenant_id_is_never_persisted_in_checkpoint_metadata(monkeypatch):
    # The live finding this design rests on (Task 3.3.a, confirmed by
    # reading langgraph.checkpoint.base.get_checkpoint_metadata() directly
    # before building anything): a raw uuid.UUID value in `configurable`
    # is silently skipped by that function's own str/int/bool/float
    # filter, so it never reaches the checkpoint's own PERSISTED metadata
    # column -- a real regression guard, not just a one-off manual check,
    # against someone later "helpfully" storing str(tenant_id) instead.
    graph = await _fresh_graph(monkeypatch)
    identity = _identity()
    conversation_id = uuid.uuid4()

    await _collect(run_turn(graph, identity, conversation_id, {"messages": ["hi"]}))

    expected_thread_id = f"{identity.tenant_id}:{identity.vid}:{conversation_id}"
    conninfo = checkpointer.checkpointer_conninfo(get_settings().database_url_str())
    async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
        row = await (
            await conn.execute(
                "SELECT metadata FROM checkpoints WHERE thread_id = %s "
                "ORDER BY checkpoint_id DESC LIMIT 1",
                (expected_thread_id,),
            )
        ).fetchone()

    assert row is not None
    assert "tenant_id" not in row[0]


async def test_support_text_is_never_persisted_in_checkpoint_metadata(monkeypatch):
    # Step 6.2: the identical live finding/regression guard as the
    # tenant_id test directly above, for a NEW, higher-stakes value --
    # support_text can carry a tenant's own real email/phone/contact
    # link. _build_thread_config() wraps it in {"text": ...} for
    # exactly this reason (a bare str would NOT be skipped by
    # get_checkpoint_metadata()'s own filter, confirmed live separately
    # -- see that function's own docstring).
    graph = await _fresh_graph(monkeypatch)
    identity = _identity()
    conversation_id = uuid.uuid4()

    await _collect(
        run_turn(
            graph,
            identity,
            conversation_id,
            {"messages": ["hi"]},
            support_text="Email a-real-tenant-contact@example.com for help.",
        )
    )

    expected_thread_id = f"{identity.tenant_id}:{identity.vid}:{conversation_id}"
    conninfo = checkpointer.checkpointer_conninfo(get_settings().database_url_str())
    async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
        row = await (
            await conn.execute(
                "SELECT metadata FROM checkpoints WHERE thread_id = %s "
                "ORDER BY checkpoint_id DESC LIMIT 1",
                (expected_thread_id,),
            )
        ).fetchone()

    assert row is not None
    assert "support_text" not in row[0]
    assert "a-real-tenant-contact@example.com" not in str(row[0])


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
    # Also re-proves, incidentally, that resume_turn()'s own new pending-
    # interrupt pre-check (duplication check after 3.2.b/c/d, finding
    # C.2) does not falsely reject a real, genuinely-pending interrupt --
    # this test already exercises exactly that case.
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


async def test_resume_on_a_never_started_thread_raises(monkeypatch):
    # Confirmed live at the duplication check after 3.2.b/c/d (finding
    # C.2): without this pre-check, LangGraph itself does not raise here
    # -- it silently restarts the graph from scratch and re-hits the same
    # interrupt, discarding the resume value with no error at all.
    graph = await _fresh_graph(monkeypatch)
    identity = _identity()
    conversation_id = uuid.uuid4()

    with pytest.raises(NoResumableInterruptError):
        await _collect(resume_turn(graph, identity, conversation_id, "early answer"))


async def test_resume_on_an_already_completed_thread_raises(monkeypatch):
    # Confirmed live: without this pre-check, LangGraph itself silently
    # no-ops here -- zero chunks, zero state change, no error.
    graph = await _fresh_graph(monkeypatch)
    identity = _identity()
    conversation_id = uuid.uuid4()

    await _collect(run_turn(graph, identity, conversation_id, {"messages": ["start"]}))
    await _collect(resume_turn(graph, identity, conversation_id, "first answer"))

    with pytest.raises(NoResumableInterruptError):
        await _collect(resume_turn(graph, identity, conversation_id, "second answer"))


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


# --- stream_mode is pinned, never caller-controllable (duplication check
# after 3.2.b/c/d, finding C.1) -----------------------------------------------


def test_run_turn_and_resume_turn_do_not_expose_stream_mode():
    # Pinned to an explicit, hardcoded stream_mode (app/agent/entrypoint.py's
    # own _STREAM_MODE) rather than left as a caller-controllable
    # parameter: exposing it would require adding a prefix check on
    # streamed chunks too (today, only read_state()'s own aget_state()-
    # returned config is checked) -- not built, so the parameter must not
    # exist either.
    assert "stream_mode" not in inspect.signature(run_turn).parameters
    assert "stream_mode" not in inspect.signature(resume_turn).parameters


# --- AgentState.retrieved_chunks is plainly serializable [SECURITY-adjacent]
# (Task 3.3.a's own retroactive fix) ------------------------------------


def _agent_state_node(state: AgentState) -> dict:
    return {
        "retrieved_chunks": [
            {
                "document_id": "11111111-1111-1111-1111-111111111111",
                "text": "the retrieved text",
                "source_url": "https://tenant.example/page",
                "heading_path": ["Section"],
                "score": 0.87,
            }
        ]
    }


def _build_agent_state_graph(cp):
    builder = StateGraph(AgentState)
    builder.add_node("node", _agent_state_node)
    builder.add_edge(START, "node")
    builder.add_edge("node", END)
    return builder.compile(checkpointer=cp)


async def test_agent_state_retrieved_chunks_round_trips_with_no_serialization_warning(
    monkeypatch, caplog
):
    # The real fix this task made, proven directly against the REAL
    # AgentState/RetrievedChunk (not a stand-in): a raw qdrant_client.
    # models.ScoredPoint in this same field previously triggered
    # LangGraph's own "Deserializing unregistered type ... This will be
    # blocked in a future version" warning (confirmed live before fixing
    # it) and embedded qdrant_client's own internal module path into the
    # stored checkpoint bytes. A plain RetrievedChunk dict must do neither.
    #
    # Confirmed live, before writing this assertion: that warning is
    # logged (logging.Logger.warning(), langgraph.checkpoint.serde.
    # jsonplus's own _warn_once()), not raised via Python's warnings
    # module -- warnings.catch_warnings() would silently never see it
    # regardless of whether the underlying behavior fired; caplog is the
    # real mechanism this needs.
    await reset_checkpoint_env(monkeypatch)
    await checkpointer.run_checkpointer_setup()
    await checkpointer.open_checkpointer_pool()
    cp = checkpointer.get_checkpointer()
    graph = _build_agent_state_graph(cp)

    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    with caplog.at_level("WARNING"):
        async for _ in graph.astream({"messages": ["hi"]}, config):
            pass

    assert "unregistered type" not in caplog.text

    snapshot = await graph.aget_state(config)
    assert snapshot.values["retrieved_chunks"] == [
        {
            "document_id": "11111111-1111-1111-1111-111111111111",
            "text": "the retrieved text",
            "source_url": "https://tenant.example/page",
            "heading_path": ["Section"],
            "score": 0.87,
        }
    ]
