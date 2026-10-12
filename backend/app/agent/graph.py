# backend/app/agent/graph.py
# Step 1.1b: whole-application skeleton stub for agent's future graph.
#
# Task 3.3.a: the real state schema -- a `MessagesState` subclass
# (docs/SPEC.md §6.3's own "State: MessagesState with thread_id per
# §4.5"). `retrieved_chunks` is a plain, non-`Annotated` field: LangGraph's
# own default reducer for an un-annotated TypedDict key is OVERWRITE
# (last writer wins), confirmed live, not assumed -- a second run on the
# same thread (sharing one checkpoint) completely replaced a prior run's
# own value for a plain field, never appended or merged. Deliberate: a
# tenant's own retrieved chunks from an earlier turn must never linger in
# a later turn's own checkpoint once that later turn's own retrieve node
# runs again.
#
# Retroactive fix [SECURITY-adjacent], applied per the new standing
# "Senior engineer judgment" rule (CLAUDE.md): this field was originally
# typed `list[ScoredPoint]` (a real `qdrant_client` vendor type), which
# both widened test_qdrant_read_path_guard.py's own CLIENT_ACCESS_ALLOWLIST
# for a bare type-annotation import, and -- confirmed live, the more
# serious problem -- coupled every persisted checkpoint to qdrant-client's
# own internal module path (see app/retrieval/service.py's own
# RetrievedChunk/chunk_from_scored_point() header comment for the full
# live-confirmed finding). Fixed: this module no longer imports
# qdrant_client at all, and is REMOVED from CLIENT_ACCESS_ALLOWLIST again.
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.retrieval.service import RetrievedChunk


class AgentState(MessagesState):
    retrieved_chunks: list[RetrievedChunk]


# Task 3.3.b: the rule-based router (docs/SPEC.md §6.2's own 4 rules,
# checked in this exact order -- rule 1 beats rule 3 if both would match,
# matching the spec's own enumerated order, not an arbitrary one).
# Plumbing, not [SECURITY] -- kept light, no abstraction beyond what these
# four routes need (CLAUDE.md's own "senior engineer judgment": state the
# simplest correct design, justify complexity beyond it -- there is none
# to justify here).
#
# Each predicate is a plain, private module-level function (not a list of
# bound references built once at import time): `route()` below calls each
# by name, so a test can `monkeypatch.setattr(graph, "_quota_exhausted",
# lambda state: True)` and have `route()`'s own next call see it -- a
# function body's bare name reference is resolved against its module's
# globals at CALL time, not captured as a fixed object reference at
# definition time, confirmed by Python's own documented scoping rules (and
# re-confirmed live by the tests this task adds). A pre-built list
# capturing the function OBJECTS directly would not be monkeypatchable
# this way -- this is deliberately simpler, not an oversight.
def _quota_exhausted(state: AgentState) -> bool:
    # TODO(4.3): real quota check once Step 4.3's quota system exists.
    return False


def _is_greeting_thanks_or_unsafe(state: AgentState) -> bool:
    # TODO(3.4): no classification mechanism is specified anywhere in
    # docs/SPEC.md for this rule specifically -- see PROJECT_SPEC.md's own
    # §9 marker (owner Step 3.4, which owns the Guardrail specialist's own
    # real replies).
    return False


def _is_escalation_intent(state: AgentState) -> bool:
    # TODO(4.4): real YAML trigger-phrase check (docs/SPEC.md §7) once
    # Step 4.4 builds it.
    return False


def route(state: AgentState) -> str:
    """docs/SPEC.md §6.2's own routing rules, in order. "Otherwise" (no
    rule matches) routes to the retrieve node -- the start of §6.3's own
    `prepare -> retrieve -> gate -> answer` path (`prepare` itself is Step
    3.5's own node, not yet inserted into this graph).
    """
    if _quota_exhausted(state):
        return "fallback"
    if _is_greeting_thanks_or_unsafe(state):
        return "guardrail"
    if _is_escalation_intent(state):
        return "coordinator"
    return "retrieve"


_ROUTE_PATH_MAP = {
    "fallback": "fallback",
    "guardrail": "guardrail",
    "coordinator": "coordinator",
    "retrieve": "retrieve",
}


def _fallback_node(state: AgentState) -> dict:
    raise NotImplementedError(
        "Fallback & Guardrail specialist (quota-exhausted reply) not built yet (Step 4.3)"
    )


def _guardrail_node(state: AgentState) -> dict:
    raise NotImplementedError(
        "Fallback & Guardrail specialist (greeting/thanks/unsafe reply) not built yet (Step 3.4)"
    )


def _coordinator_node(state: AgentState) -> dict:
    raise NotImplementedError(
        "Escalation / Human Handoff Coordinator not built yet (Step 4.4)"
    )


def _retrieve_node(state: AgentState) -> dict:
    # Becomes real in Task 3.3.c (this same Step) -- the retrieve node
    # calling retrieve() (Task 3.1.c) for real, with tenant_id from
    # app.agent.context.tenant_id_from_config() and the latest human
    # message as the query.
    raise NotImplementedError("retrieve node not built yet (Task 3.3.c)")


def _gate_answer_node(state: AgentState) -> dict:
    raise NotImplementedError("gate/answer node not built yet (Step 3.4)")


def build_graph(checkpointer: AsyncPostgresSaver) -> CompiledStateGraph:
    """Compiles the real `AgentState` graph with the given checkpointer --
    an explicit parameter (Task 3.3's own Q4 decision), not an internal
    `get_checkpointer()` call, matching this project's own established
    "explicit over implicit, testable" convention. Does NOT wire itself
    into `app/main.py` or call `open_checkpointer_pool()`/
    `get_checkpointer()` itself -- that wiring is Step 4.1's own job (see
    PROJECT_SPEC.md's own §9 marker, recorded at the Step 3.3 breakdown).
    """
    builder = StateGraph(AgentState)
    builder.add_node("fallback", _fallback_node)
    builder.add_node("guardrail", _guardrail_node)
    builder.add_node("coordinator", _coordinator_node)
    builder.add_node("retrieve", _retrieve_node)
    builder.add_node("gate_answer", _gate_answer_node)
    builder.add_conditional_edges(START, route, _ROUTE_PATH_MAP)
    builder.add_edge("retrieve", "gate_answer")
    for node in ("fallback", "guardrail", "coordinator", "gate_answer"):
        builder.add_edge(node, END)
    return builder.compile(checkpointer=checkpointer)
