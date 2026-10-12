# backend/app/agent/entrypoint.py
# Task 3.2.c [SECURITY]: the one guarded entry point (docs/SPEC.md §4.5 --
# "only one function may call graph.astream, aget_state or Command(resume=
# ...). It builds the config itself and asserts the thread prefix matches
# the caller"). This module is the ONLY place in this codebase allowed to
# call those LangGraph run/state methods on a real graph -- enforced by
# this module's own AST guard, tests/test_agent_entrypoint_guard.py, built
# alongside it (not deferred to Step 3.3).
#
# No graph, router or specialist is built here (Step 3.3's own job) --
# `graph` is a dependency-injected parameter (Task 3.2.a/3.2.c's own
# decision: a real graph object, passed in, never constructed by this
# module), tested against a trivial throwaway graph built only for this
# task's own tests.
#
# Three thin public functions (run_turn/resume_turn/read_state), not one
# function with an operation discriminator -- each operation has a
# naturally different payload shape (an arbitrary graph input dict, a
# resume value, nothing), and a discriminator function would need an
# untyped/Union payload parameter plus an internal if/elif dispatch for no
# real benefit: the invariant that actually matters -- the LangGraph
# config dict is built in exactly ONE place, and no path bypasses it -- is
# identically satisfied either way, since all three route through
# `_build_thread_config()` below and nothing else ever constructs one.
#
# Signature discipline (docs/SPEC.md §4.5, §21): every public function
# below takes exactly `(graph, identity, conversation_id, ...)` -- never a
# `thread_id`, `config`, `configurable`, `checkpoint_id`, or a `**kwargs`
# passthrough that could carry any of those in from outside. `identity` is
# `VerifiedIdentity` (`app/auth/dependencies.py`, Task 1.4.h) specifically
# -- never re-derived from a request body/query field, matching
# `retrieve()`'s own identical obligation (Task 3.1.c). `conversation_id`
# is typed `uuid.UUID`, not `str`.
#
# `conversation_id` MUST already be verified, by this function's own
# caller, to belong to `identity.tenant_id` (and, once Step 4.2 lands, to
# `identity.vid` too -- `get_conversation_by_id()` is tenant-scoped only
# today, confirmed by reading it directly; see this task's own Open
# marker). This module has no way to verify where its own `conversation_id`
# argument came from, the identical trust boundary `retrieve()`'s own
# docstring already states for `client_id` -- the caller (Step 4.1's real
# `/chat` endpoint) carries that obligation, not this module.
import uuid
from collections.abc import AsyncIterator, Mapping
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, StateSnapshot

from app.auth.dependencies import VerifiedIdentity

# Duplication check after 3.2.b/c/d (2026-11-02), finding C.1: pinned
# explicitly rather than left at astream()'s own implicit default --
# confirmed live, by comparing all three options directly against the
# installed langgraph, that leaving stream_mode unset resolves to this
# exact "updates" shape (CompiledStateGraph's own default resolution,
# distinct from the plain Pregel-level "values" default), not something
# that could silently drift. Exposing stream_mode as a parameter on
# run_turn()/resume_turn() would require adding a prefix check on
# streamed chunks too (today, only read_state()'s own aget_state()-
# returned config is checked, confirmed empirically to never carry a
# "configurable" key under this stream mode) -- not built, so the
# parameter must not exist either; test_run_turn_and_resume_turn_do_not_
# expose_stream_mode (tests/test_agent_entrypoint.py) asserts this
# directly against both signatures.
_STREAM_MODE = "updates"


class NoResumableInterruptError(Exception):
    """Raised by resume_turn() (Task 3.2.b/c/d duplication check, finding
    C.2) when the thread has no pending interrupt to resume -- confirmed
    live, against the real installed langgraph, that Command(resume=...)
    on a thread with no pending interrupt does NOT raise on its own: a
    never-started thread silently restarts the graph from scratch (the
    resume value is discarded, the graph re-hits the same interrupt
    point); an already-fully-completed thread silently no-ops (zero
    chunks, zero state change). Neither surfaces as an error without this
    explicit check.
    """


def _build_thread_config(identity: VerifiedIdentity, conversation_id: uuid.UUID) -> RunnableConfig:
    """The ONE place a LangGraph config dict is ever constructed in this
    codebase -- every public function below routes through this, nothing
    else does.

    Delimiter-injection guard [SECURITY]: `thread_id` is built as
    `f"{tenant_id}:{vid}:{conversation_id}"` (docs/SPEC.md §4.5) --
    `tenant_id` and `conversation_id` are both `uuid.UUID` by this
    function's own parameter types, and a UUID's `str()` form is always
    exactly 32 hex digits plus 4 literal hyphens (confirmed by the `uuid`
    module's own fixed canonical formatting), so neither can ever contain
    a `:`. `VerifiedIdentity.vid` is ALSO typed `uuid.UUID` (confirmed by
    reading `app/auth/dependencies.py` directly) -- but a Python dataclass
    never enforces its own field types at construction, so a caller could
    still hand this function a `VerifiedIdentity` whose `vid` was built
    from an untrusted string despite the type hint. Checked explicitly
    here, not merely assumed from the annotation: a `vid` (or `tenant_id`)
    that is not a real `uuid.UUID` instance is rejected outright, before
    any string is ever built -- a visitor id containing `:` could
    otherwise forge a `thread_id` colliding with another conversation's or
    visitor's own prefix.
    """
    if not isinstance(identity.tenant_id, uuid.UUID):
        raise ValueError("identity.tenant_id must be a uuid.UUID")
    if not isinstance(identity.vid, uuid.UUID):
        raise ValueError("identity.vid must be a uuid.UUID")
    if not isinstance(conversation_id, uuid.UUID):
        raise ValueError("conversation_id must be a uuid.UUID")

    thread_id = f"{identity.tenant_id}:{identity.vid}:{conversation_id}"
    return {"configurable": {"thread_id": thread_id}}


def _assert_thread_prefix_matches(config: Mapping[str, Any], identity: VerifiedIdentity) -> None:
    """Non-tautological prefix assertion (docs/SPEC.md §4.5's own "asserts
    the thread prefix matches the caller"): checks a config the GRAPH
    handed back (e.g. `aget_state()`'s own returned `StateSnapshot.config`)
    against `identity`'s own expected prefix -- not merely re-checking the
    config this module just built itself, which would prove nothing. A
    correctly-behaving checkpointer/graph will never actually produce a
    mismatch; this exists as a defense-in-depth backstop, tested directly
    against a deliberately-constructed mismatched config (a real graph
    cannot be made to produce one to test against).
    """
    expected_prefix = f"{identity.tenant_id}:{identity.vid}:"
    actual = config.get("configurable", {}).get("thread_id", "")
    if not actual.startswith(expected_prefix):
        raise RuntimeError(
            f"thread prefix mismatch: expected a thread_id starting with "
            f"{expected_prefix!r}, got {actual!r}"
        )


async def _read_verified_state(
    graph: CompiledStateGraph,
    identity: VerifiedIdentity,
    conversation_id: uuid.UUID,
) -> StateSnapshot:
    """Shared by read_state() and resume_turn() (Task 3.2.b/c/d duplication
    check, finding C.2) -- builds the config, reads state via
    `aget_state()`, and asserts the returned config's own thread prefix
    before returning it. Extracted so both operations that need a
    verified current snapshot share the identical sequence, rather than
    `resume_turn()`'s own pending-interrupt check duplicating
    `read_state()`'s three-line body.
    """
    config = _build_thread_config(identity, conversation_id)
    snapshot = await graph.aget_state(config)
    _assert_thread_prefix_matches(snapshot.config, identity)
    return snapshot


async def run_turn(
    graph: CompiledStateGraph,
    identity: VerifiedIdentity,
    conversation_id: uuid.UUID,
    input: Any,  # noqa: A002 (matches astream()'s own `input` parameter name)
) -> AsyncIterator[Any]:
    """Runs one turn (`graph.astream`), yielding whatever the graph itself
    yields unchanged. `input` is forwarded to `astream()` exactly as
    given, never inspected for `thread_id`/`configurable`-shaped keys --
    those keys have no special meaning to this function at all; only the
    config this function builds itself, from `identity`/`conversation_id`,
    ever determines which thread a run executes against.
    """
    config = _build_thread_config(identity, conversation_id)
    async for chunk in graph.astream(input, config, stream_mode=_STREAM_MODE):
        yield chunk


async def resume_turn(
    graph: CompiledStateGraph,
    identity: VerifiedIdentity,
    conversation_id: uuid.UUID,
    resume_value: Any,
) -> AsyncIterator[Any]:
    """Resumes an interrupted turn on the correct thread via
    `Command(resume=resume_value)` -- the construction this module's own
    AST guard confines to this file alone.

    First reads the current state through `_read_verified_state()` (the
    same config builder, with its prefix assertion applied) and raises
    `NoResumableInterruptError` if there is no pending interrupt
    (`snapshot.next` empty) -- see that exception's own docstring for why
    this check exists: confirmed live that LangGraph itself does not
    raise on a resume with nothing to resume. **Known limitation,
    recorded as an Open marker (owner Step 4.1), not fixed here**:
    concurrent duplicate resumes on the same thread can both pass this
    check before either one actually applies -- per-conversation turn
    serialization is the `/chat` endpoint's own job, not this function's.
    """
    snapshot = await _read_verified_state(graph, identity, conversation_id)
    if not snapshot.next:
        raise NoResumableInterruptError(
            f"no pending interrupt to resume for this thread (next={snapshot.next!r})"
        )
    config = _build_thread_config(identity, conversation_id)
    async for chunk in graph.astream(
        Command(resume=resume_value), config, stream_mode=_STREAM_MODE
    ):
        yield chunk


async def read_state(
    graph: CompiledStateGraph,
    identity: VerifiedIdentity,
    conversation_id: uuid.UUID,
) -> StateSnapshot:
    """Reads the current state (`graph.aget_state`) for one thread --
    `_read_verified_state()` re-checks the SNAPSHOT'S OWN config
    (something the graph/checkpointer handed back, not this function's own
    input) before returning it.
    """
    return await _read_verified_state(graph, identity, conversation_id)
