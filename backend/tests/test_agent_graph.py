# backend/tests/test_agent_graph.py
# Task 3.3.b: the rule-based router (pure-function tests) and
# build_graph() (end to end, through the real guarded entry point, with a
# real checkpointer against test-db). Plumbing, not [SECURITY] -- no
# isolation/tenant-identity proof here; that is test_agent_graph_
# retrieve.py's own job (Task 3.3.c), now that the retrieve node is real.
#
# Task 3.3.c: this file's own `test_run_turn_reaches_the_retrieve_stub_
# for_an_ordinary_message` is REMOVED, not updated -- its entire premise
# (retrieve is still a stub raising NotImplementedError) is now false,
# the same situation test_app_skeleton.py's own header comment already
# documents for `build_graph` itself. An ordinary message reaching a
# WORKING retrieve node needs real Qdrant/OpenAI-shaped env (seeded
# content, patch_embed_dense()), which does not belong in this file's own
# plumbing-only suite -- that proof lives in test_agent_graph_retrieve.py
# instead, end to end through run_turn()/read_state().
#
# Task 3.4.d [SECURITY]: this file's own `test_gate_node_is_a_pass_
# through_stub` is REMOVED, not updated -- its entire premise (the gate
# is a pass-through stub) is now false, the same pattern as the 3.3.c
# removal immediately above. The gate's real logic is [SECURITY]
# (a tenant-isolation-sensitive signal, a model-call-cost decision), so
# its own tests live in a new, dedicated file, `test_agent_graph_gate.py`
# -- matching this project's own established pattern of giving a new
# security-relevant mechanism its own test file (e.g. `test_agent_graph_
# citations.py` for Task 3.4.c), not folded into this plumbing-only suite.
#
# Task 3.4.f: `_is_greeting_thanks_or_unsafe` (one combined stub) is
# REPLACED by two separate predicates, `_is_greeting_or_thanks` (real
# now) and `_is_unsafe_or_abusive` (still a stub, research-only this
# task) -- every test below monkeypatching the old combined name is
# updated to the new split names, and `route()`'s own real greeting
# check now reads `state["messages"]`, so tests that used to pass a bare
# `{}` (fine when the predicate never touched state) need a real
# message list instead, UNLESS the predicate itself is monkeypatched
# (in which case `{}` is still fine, the stub never looks at it). This
# file's own `test_guardrail_node_names_its_destination_and_owning_step`/
# `test_no_answer_fallback_node_names_its_destination_and_owning_step`/
# `test_run_turn_reaches_the_guardrail_stub_when_greeting_thanks_or_
# unsafe` are REWRITTEN, not removed -- both nodes are real now, so
# these prove the real fixed replies instead of a raised stub error.
import uuid

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agent import checkpointer
from app.agent import graph as graph_module
from app.agent.entrypoint import read_state, run_turn
from app.agent.graph import build_graph, route
from app.auth.dependencies import VerifiedIdentity
from tests.conftest import reset_checkpoint_env

_ORDINARY_MESSAGE_STATE = {"messages": [HumanMessage(content="What are your hours?")]}

pytestmark = pytest.mark.usefixtures("close_any_checkpointer_pool_left_open")

_ORG = "https://tenant.example"


def _identity() -> VerifiedIdentity:
    return VerifiedIdentity(tenant_id=uuid.uuid4(), vid=uuid.uuid4(), org=_ORG)


async def _collect(aiter):
    return [x async for x in aiter]


# --- route() as a pure function ---------------------------------------------


def test_route_returns_retrieve_when_no_rule_matches():
    assert route(_ORDINARY_MESSAGE_STATE) == "retrieve"


def test_route_rule_1_quota_exhausted_wins(monkeypatch):
    # Quota short-circuits before the real greeting check ever reads
    # state["messages"] -- a bare {} is still fine here.
    monkeypatch.setattr(graph_module, "_quota_exhausted", lambda state: True)
    assert route({}) == "fallback"


def test_route_rule_2_greeting_thanks_or_unsafe_wins(monkeypatch):
    monkeypatch.setattr(graph_module, "_is_greeting_or_thanks", lambda state: True)
    assert route({}) == "guardrail"


def test_route_rule_3_escalation_intent_wins(monkeypatch):
    monkeypatch.setattr(graph_module, "_is_escalation_intent", lambda state: True)
    assert route(_ORDINARY_MESSAGE_STATE) == "coordinator"


def test_route_precedence_rule_1_beats_rule_3_when_both_match(monkeypatch):
    # docs/SPEC.md §6.2's own enumerated order -- rule 1 (quota) is
    # checked before rule 3 (escalation intent), so it wins when both
    # predicates would otherwise match.
    monkeypatch.setattr(graph_module, "_quota_exhausted", lambda state: True)
    monkeypatch.setattr(graph_module, "_is_escalation_intent", lambda state: True)
    assert route({}) == "fallback"


def test_route_precedence_rule_2_beats_rule_3_when_both_match(monkeypatch):
    monkeypatch.setattr(graph_module, "_is_greeting_or_thanks", lambda state: True)
    monkeypatch.setattr(graph_module, "_is_escalation_intent", lambda state: True)
    assert route({}) == "guardrail"


# --- destination node stubs, called directly (no_answer_fallback is
# otherwise unreachable until Task 3.4.b makes the answer node real, and
# gate/answer themselves are no longer stubs as of Task 3.4.b -- see
# test_agent_graph_answer.py for their own real tests) ----------------


def test_fallback_node_names_its_destination_and_owning_step():
    with pytest.raises(NotImplementedError, match="Step 4.3"):
        graph_module._fallback_node({})


def test_guardrail_node_returns_the_real_fixed_sanitized_reply():
    # Task 3.4.f: guardrail is real now -- the stub-error test is
    # REMOVED, not updated (its own premise is now false, matching the
    # 3.3.c/3.4.d removal pattern this file's own header already
    # documents). Compares against a FRESH call to _sanitize_answer_
    # text(), not the raw constant -- this text happens to contain none
    # of that function's own escaped characters today, so the two are
    # currently identical either way, but asserting against the
    # sanitizer's own real output (not just the raw string) is what
    # actually proves this node goes through it, not a coincidence of
    # today's specific wording.
    result = graph_module._guardrail_node({})
    expected_text = graph_module._sanitize_answer_text(graph_module._GUARDRAIL_REPLY_TEXT)
    assert result == {"messages": [AIMessage(content=expected_text)]}


def test_coordinator_node_names_its_destination_and_owning_step():
    with pytest.raises(NotImplementedError, match="Step 4.4"):
        graph_module._coordinator_node({})


def test_no_answer_fallback_node_returns_the_real_fixed_sanitized_reply():
    # Task 3.4.e: no longer a stub -- see test_gate_node_is_a_pass_
    # through_stub's own removal note above for the pattern this follows.
    result = graph_module._no_answer_fallback_node({})
    expected_text = graph_module._sanitize_answer_text(graph_module._NO_ANSWER_FALLBACK_TEXT)
    assert result == {"messages": [AIMessage(content=expected_text)]}


def test_dependency_failure_fallback_node_returns_the_real_fixed_sanitized_reply():
    result = graph_module._dependency_failure_fallback_node({})
    expected_text = graph_module._sanitize_answer_text(graph_module._DEPENDENCY_FAILURE_TEXT)
    assert result == {"messages": [AIMessage(content=expected_text)]}


# --- _retrieve_node's own dependency-failure catch (Task 3.4.e) -------------


async def test_retrieve_node_catches_a_retrieve_failure_and_sets_dependency_failed(
    monkeypatch,
):
    async def _raise(*args, **kwargs):
        raise RuntimeError("scripted dependency failure")

    monkeypatch.setattr(graph_module, "retrieve", _raise)
    config = {"configurable": {"tenant_id": uuid.uuid4()}}
    state = {"messages": [HumanMessage(content="what are your hours")]}

    result = await graph_module._retrieve_node(state, config)

    assert result == {
        "retrieved_chunks": [],
        "best_dense_score": None,
        "dependency_failed": True,
    }


# --- end to end, through the real guarded entry point -----------------------


async def _fresh_real_graph(monkeypatch):
    await reset_checkpoint_env(monkeypatch)
    await checkpointer.open_checkpointer_pool()
    cp = checkpointer.get_checkpointer()
    await cp.setup()
    return build_graph(cp)


async def test_run_turn_reaches_the_fallback_stub_when_quota_exhausted(monkeypatch):
    monkeypatch.setattr(graph_module, "_quota_exhausted", lambda state: True)
    graph = await _fresh_real_graph(monkeypatch)
    identity = _identity()
    with pytest.raises(NotImplementedError, match="Step 4.3"):
        await _collect(run_turn(graph, identity, uuid.uuid4(), {"messages": ["hello"]}))


async def test_run_turn_reaches_the_real_guardrail_reply_for_a_real_greeting(monkeypatch):
    # Task 3.4.f: guardrail is real now -- no monkeypatch needed, "hi" is
    # a real, listed phrase (backend/config/greeting_phrases.yaml) that
    # the real `_is_greeting_or_thanks()` matches through the actual
    # route()/guardrail_node path, end to end.
    graph = await _fresh_real_graph(monkeypatch)
    identity = _identity()
    conversation_id = uuid.uuid4()
    results = await _collect(run_turn(graph, identity, conversation_id, {"messages": ["hi"]}))
    assert results

    snapshot = await read_state(graph, identity, conversation_id)
    messages = snapshot.values["messages"]
    assert isinstance(messages[-1], AIMessage)
    assert messages[-1].content == graph_module._sanitize_answer_text(
        graph_module._GUARDRAIL_REPLY_TEXT
    )


async def test_run_turn_reaches_the_coordinator_stub_when_escalation_intent(monkeypatch):
    monkeypatch.setattr(graph_module, "_is_escalation_intent", lambda state: True)
    graph = await _fresh_real_graph(monkeypatch)
    identity = _identity()
    with pytest.raises(NotImplementedError, match="Step 4.4"):
        await _collect(
            run_turn(graph, identity, uuid.uuid4(), {"messages": ["I want to talk to a person"]})
        )


async def test_run_turn_reaches_the_dependency_failure_reply_when_retrieve_fails(monkeypatch):
    # Task 3.4.e: a real retrieve() failure, end to end through the real
    # graph -- the chat model must never be called (the gate's own
    # zero-chunk/dependency-failed state never reaches the answer node).
    def _never_called():
        raise AssertionError("get_chat_model() must never be called on a dependency failure")

    async def _raise(*args, **kwargs):
        raise RuntimeError("scripted dependency failure")

    monkeypatch.setattr(graph_module, "retrieve", _raise)
    monkeypatch.setattr(graph_module, "get_chat_model", _never_called)
    graph = await _fresh_real_graph(monkeypatch)
    identity = _identity()
    conversation_id = uuid.uuid4()

    results = await _collect(
        run_turn(graph, identity, conversation_id, {"messages": ["what are your hours"]})
    )
    assert results

    snapshot = await read_state(graph, identity, conversation_id)
    assert snapshot.values["dependency_failed"] is True
    assert snapshot.values["can_answer"] is False
    assert snapshot.values["messages"][-1].content == graph_module._sanitize_answer_text(
        graph_module._DEPENDENCY_FAILURE_TEXT
    )
