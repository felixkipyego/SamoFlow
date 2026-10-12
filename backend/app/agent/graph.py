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
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.context import tenant_id_from_config
from app.retrieval.service import RetrievedChunk, chunk_from_scored_point, retrieve


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


def _latest_human_message_text(messages: list[BaseMessage]) -> str:
    """Task 3.3.c: the query text for the retrieve node below -- the
    latest `HumanMessage`'s own content, walked backward since `prepare`
    (Step 3.5, not yet inserted) will eventually rewrite follow-ups but
    nothing here assumes that has happened.

    Fails loudly (a plain `ValueError`, never silently empty chunks) on
    three distinct malformed-input shapes, each named explicitly rather
    than lumped into one generic message -- decided deliberately, not
    assumed, because none of these three are docs/SPEC.md §6.3's own "no
    relevant answer" case (that is a well-formed query that genuinely
    matches nothing in the tenant's content -- Step 3.4's gate node owns
    that, not this function). These three are INPUT-SHAPE problems from
    this node's own caller, not content-relevance problems:
      - no `HumanMessage` at all: nothing upstream guarantees one exists
        yet (`prepare` isn't built, and nothing validates this graph's own
        `input` today) -- a caller/upstream-contract violation, not a
        visitor-facing scenario.
      - empty/whitespace-only content: a degenerate input that would
        still mechanically "work" (embed an empty string, query Qdrant)
        but wastes a real, paid OpenAI call on garbage and produces a
        meaningless ranking -- caught before that call, not after.
      - non-`str` content (LangChain messages can carry a list of
        multimodal content blocks): docs/SPEC.md's own v1 scope is
        English text only, no multimodal input anywhere in the spec --
        building real block-text-extraction now would be speculative
        (rule 11); this fails clearly instead of silently guessing which
        block is "the text".
    """
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            content = message.content
            if not isinstance(content, str):
                raise ValueError(
                    "the latest HumanMessage's content is not a plain string "
                    f"(got {type(content).__name__}) -- multimodal content blocks "
                    "are not supported"
                )
            query = content.strip()
            if not query:
                raise ValueError("the latest HumanMessage's content is empty or whitespace-only")
            return query
    raise ValueError("no HumanMessage found in state['messages']")


async def _retrieve_node(state: AgentState, config: RunnableConfig) -> dict:
    """The real Retrieval Specialist's own search step (Task 3.3.c),
    calling `retrieve()` (Task 3.1.c) for real. `tenant_id` comes ONLY
    from `tenant_id_from_config(config)` -- the one accessor (Task
    3.3.a) -- no other source of tenant identity exists anywhere in this
    function, matching `retrieve()`'s own identical obligation for
    `client_id`.

    Production lens, confirmed live, not assumed, stated plainly rather
    than silently absorbed: if Qdrant or the OpenAI embedding call is down
    or slow during this call, nothing here catches it -- the exception
    propagates uncaught through this node, through `astream()`, to this
    graph's own caller. Confirmed live: Qdrant's own client default
    timeout is 5s (`AsyncQdrantRemote.DEFAULT_GRPC_TIMEOUT`); OpenAI's own
    default is far longer (`openai._constants.DEFAULT_TIMEOUT`, `read=600`
    seconds), with the SDK's own built-in retry (`embed_dense()`'s own
    header comment: 2 retries) potentially compounding that further on a
    slow-but-not-fully-down provider -- a visitor-facing turn could hang
    for minutes in the worst case, today, with no visitor-facing fallback
    behavior decided. See PROJECT_SPEC.md's own two §9 markers (Task
    3.3.d split the original single "owner undecided" one in two, with
    concrete owners) -- not decided or built here, deliberately: this
    node's own job is the search call itself, not a retry/circuit-breaker
    policy spanning arbitrary future nodes.
    """
    tenant_id = tenant_id_from_config(config)
    query = _latest_human_message_text(state["messages"])
    points = await retrieve(tenant_id, query)
    return {"retrieved_chunks": [chunk_from_scored_point(point) for point in points]}


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
