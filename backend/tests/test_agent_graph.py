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
import uuid

import pytest

from app.agent import checkpointer
from app.agent import graph as graph_module
from app.agent.entrypoint import run_turn
from app.agent.graph import build_graph, route
from app.auth.dependencies import VerifiedIdentity
from tests.conftest import reset_checkpoint_env

pytestmark = pytest.mark.usefixtures("close_any_checkpointer_pool_left_open")

_ORG = "https://tenant.example"


def _identity() -> VerifiedIdentity:
    return VerifiedIdentity(tenant_id=uuid.uuid4(), vid=uuid.uuid4(), org=_ORG)


async def _collect(aiter):
    return [x async for x in aiter]


# --- route() as a pure function ---------------------------------------------


def test_route_returns_retrieve_when_no_rule_matches():
    assert route({}) == "retrieve"


def test_route_rule_1_quota_exhausted_wins(monkeypatch):
    monkeypatch.setattr(graph_module, "_quota_exhausted", lambda state: True)
    assert route({}) == "fallback"


def test_route_rule_2_greeting_thanks_or_unsafe_wins(monkeypatch):
    monkeypatch.setattr(graph_module, "_is_greeting_thanks_or_unsafe", lambda state: True)
    assert route({}) == "guardrail"


def test_route_rule_3_escalation_intent_wins(monkeypatch):
    monkeypatch.setattr(graph_module, "_is_escalation_intent", lambda state: True)
    assert route({}) == "coordinator"


def test_route_precedence_rule_1_beats_rule_3_when_both_match(monkeypatch):
    # docs/SPEC.md §6.2's own enumerated order -- rule 1 (quota) is
    # checked before rule 3 (escalation intent), so it wins when both
    # predicates would otherwise match.
    monkeypatch.setattr(graph_module, "_quota_exhausted", lambda state: True)
    monkeypatch.setattr(graph_module, "_is_escalation_intent", lambda state: True)
    assert route({}) == "fallback"


def test_route_precedence_rule_2_beats_rule_3_when_both_match(monkeypatch):
    monkeypatch.setattr(graph_module, "_is_greeting_thanks_or_unsafe", lambda state: True)
    monkeypatch.setattr(graph_module, "_is_escalation_intent", lambda state: True)
    assert route({}) == "guardrail"


# --- destination node stubs, called directly (gate_answer is otherwise
# unreachable until Task 3.3.c makes the retrieve node real) ----------------


def test_fallback_node_names_its_destination_and_owning_step():
    with pytest.raises(NotImplementedError, match="Step 4.3"):
        graph_module._fallback_node({})


def test_guardrail_node_names_its_destination_and_owning_step():
    with pytest.raises(NotImplementedError, match="Step 3.4"):
        graph_module._guardrail_node({})


def test_coordinator_node_names_its_destination_and_owning_step():
    with pytest.raises(NotImplementedError, match="Step 4.4"):
        graph_module._coordinator_node({})


def test_gate_answer_node_names_its_destination_and_owning_step():
    with pytest.raises(NotImplementedError, match="Step 3.4"):
        graph_module._gate_answer_node({})


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


async def test_run_turn_reaches_the_guardrail_stub_when_greeting_thanks_or_unsafe(monkeypatch):
    monkeypatch.setattr(graph_module, "_is_greeting_thanks_or_unsafe", lambda state: True)
    graph = await _fresh_real_graph(monkeypatch)
    identity = _identity()
    with pytest.raises(NotImplementedError, match="Step 3.4"):
        await _collect(run_turn(graph, identity, uuid.uuid4(), {"messages": ["hi there"]}))


async def test_run_turn_reaches_the_coordinator_stub_when_escalation_intent(monkeypatch):
    monkeypatch.setattr(graph_module, "_is_escalation_intent", lambda state: True)
    graph = await _fresh_real_graph(monkeypatch)
    identity = _identity()
    with pytest.raises(NotImplementedError, match="Step 4.4"):
        await _collect(
            run_turn(graph, identity, uuid.uuid4(), {"messages": ["I want to talk to a person"]})
        )
