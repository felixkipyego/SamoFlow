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
# Task 3.3.a [SECURITY]: `_build_thread_config()` below -- a COMPLETED
# Step 3.2 file -- is modified here, per direct instruction, to also
# carry `tenant_id` in `configurable`, so graph nodes (built from Step
# 3.3.b onward) can reach it via `app.agent.context.tenant_id_from_
# config()`, the one new accessor that module exists for. See that
# function's own docstring, and `_build_thread_config()`'s own docstring
# below, for the full "config, not state" and "raw uuid.UUID, never
# str()" reasoning -- both confirmed live, not assumed.
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
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, StateSnapshot

from app.agent.context import build_thread_id
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
#
# Task 4.1: widened from the single "updates" string to a LIST
# (["custom", "updates"]) -- the real `/chat` endpoint is this constant's
# first real consumer, and it needs BOTH channels: "custom" carries
# `_answer_node`'s own real token-by-token writer events (the ONLY
# source of real streamed answer text, per that node's own docstring and
# PROJECT_SPEC.md's own §9 marker), while "updates" still carries each
# node's own full return dict -- needed for the FIXED-reply nodes
# (guardrail/no_answer_fallback/dependency_failure/fallback/coordinator),
# none of which call the stream writer at all, so "custom" alone would
# surface nothing for those paths. Confirmed live, against the installed
# langgraph, before relying on it: a LIST stream_mode changes astream()'s
# own yielded shape from a bare chunk to a `(mode_name, chunk)` tuple --
# a real, deliberate shape change this constant's own callers (`run_turn`/
# `resume_turn`) now propagate unchanged, same as every other
# "whatever the graph itself yields" passthrough already documented
# below. No prefix check is added for either channel: neither "custom"
# (an arbitrary dict the node itself wrote, with no config/thread
# metadata at all) nor "updates" (a per-node return dict, same today as
# before this change) carries anything resembling the "configurable"-
# keyed config shape the comment above was written to anticipate -- this
# was confirmed by direct inspection of both channels' real chunk shapes
# before relying on it, not assumed from the comment's own old wording.
# "messages" mode is deliberately NOT added -- it would carry the
# answer node's own raw tool-call JSON fragments (including
# `used_chunk_ids`, which must never reach a visitor), exactly what this
# module's header comment and `_answer_node`'s own docstring already warn
# against forwarding; Step 4.1's own decision (PROJECT_SPEC.md) is to
# never subscribe to it at all, not merely to filter it after the fact.
_STREAM_MODE = ["custom", "updates"]


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


def _build_thread_config(
    identity: VerifiedIdentity,
    conversation_id: uuid.UUID,
    *,
    quota_exhausted: bool = False,
    support_text: str | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> RunnableConfig:
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

    Task 3.3.a [SECURITY]: also carries `tenant_id` (`identity.tenant_id`
    -- the SAME value already embedded in `thread_id`) in `configurable`,
    so graph nodes can read it via `app.agent.context.
    tenant_id_from_config()` -- never `vid`, nothing needs it yet (rule
    11). Config, not state: rebuilt server-side on every call, so a
    resume (which carries no caller-supplied input at all) still
    re-derives `tenant_id` from the CURRENT call's own `identity`, never a
    value some prior turn happened to store in the checkpoint. Stored as
    the raw `uuid.UUID` object, never `str(tenant_id)` -- confirmed live
    that this is what keeps it out of LangGraph's own persisted
    checkpoint metadata (see `app/agent/context.py`'s own header comment
    for the full finding).

    Task 4.3: also carries `quota_exhausted` -- a plain `bool` decided by
    THIS function's own caller (`run_turn()` below, itself called by
    `app/chat/routes.py`'s request handler, which already has a real DB
    session open to look up the tenant's plan/usage BEFORE the graph
    ever runs) and read back by `route()`'s own `_quota_exhausted()`
    predicate via `app.agent.context.quota_exhausted_from_config()`.
    Defaults to `False` so every pre-Task-4.3 caller (every test
    building a config without this parameter) is unaffected. A resume
    never recomputes this (resume_turn()/read_state() don't accept it) --
    quota is only ever relevant to routing a brand NEW turn's input, and
    a resume by definition continues a turn already past that decision.

    Step 6.2: also carries `support_text` -- the tenant's own rendered
    support-details fallback text (`app.agent.graph.render_support_
    details_text()`, applied to `tenant.config` by THIS function's own
    caller, `run_turn()` below, which already has it from `app/chat/
    routes.py`), read back by the three fallback nodes via `app.agent.
    context.support_text_from_config()`. Defaults to `None` for the
    identical "every pre-Step-6.2 caller unaffected" reason as `quota_
    exhausted` above; also not recomputed on resume, for the same
    reasoning.

    [SECURITY] Stored wrapped in a single-key dict (`{"text": ...}`),
    NEVER a bare `str` -- confirmed live, not assumed: `langgraph.
    checkpoint.base.get_checkpoint_metadata()` copies every
    `configurable` entry whose value is `str`/`int`/`bool`/`float`
    into the checkpoint's own PERSISTED metadata (the same mechanism
    `app/agent/context.py`'s own header comment documents for
    `tenant_id`) -- `EXCLUDED_METADATA_KEYS` is a fixed library
    constant this project cannot extend, so a bare `support_text` str
    would leak a tenant's own email/phone/contact-link into a database
    column nothing else expects to hold tenant contact PII. A dict
    value matches neither `isinstance` check, so it is silently
    skipped by that same function -- confirmed live against the real
    `get_checkpoint_metadata()`, not merely reasoned about. `quota_
    exhausted` (a bare `bool`) is NOT similarly guarded and DOES leak
    into persisted metadata today -- a pre-existing Task 4.3 gap, not
    introduced here, left unfixed as out of this task's own scope
    (non-sensitive, a boolean flag) but flagged in the register.

    [SECURITY] Part D: also carries `stop_requested` -- a zero-argument
    callable (never a plain `bool`), because unlike every other key
    here it must report a LIVE signal that can change mid-turn, after
    this config was already built and handed to `graph.astream()`. See
    `app.agent.context.stop_requested_from_config()`'s own docstring for
    why a callable needs no `{"text": ...}`-style wrapping guard the way
    `support_text` does. `None` by default -- every pre-Part-D caller
    unaffected, identical reasoning to `quota_exhausted`/`support_text`
    above. Not recomputed on resume, for the same reasoning those two
    give: a resume continues a turn already past its own answer step.
    """
    if not isinstance(identity.tenant_id, uuid.UUID):
        raise ValueError("identity.tenant_id must be a uuid.UUID")
    if not isinstance(identity.vid, uuid.UUID):
        raise ValueError("identity.vid must be a uuid.UUID")
    if not isinstance(conversation_id, uuid.UUID):
        raise ValueError("conversation_id must be a uuid.UUID")

    thread_id = build_thread_id(identity.tenant_id, identity.vid, conversation_id)
    return {
        "configurable": {
            "thread_id": thread_id,
            "tenant_id": identity.tenant_id,
            "quota_exhausted": quota_exhausted,
            # Wrapped, not a bare str -- see this function's own
            # docstring for why (checkpoint-metadata leak guard).
            "support_text": {"text": support_text},
            # Bare callable, deliberately NOT wrapped -- see this
            # function's own docstring, Part D paragraph.
            "stop_requested": stop_requested,
        }
    }


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
    *,
    quota_exhausted: bool = False,
    support_text: str | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> AsyncIterator[Any]:
    """Runs one turn (`graph.astream`), yielding whatever the graph itself
    yields unchanged. `input` is forwarded to `astream()` exactly as
    given, never inspected for `thread_id`/`configurable`-shaped keys --
    those keys have no special meaning to this function at all; only the
    config this function builds itself, from `identity`/`conversation_id`,
    ever determines which thread a run executes against.

    Task 4.3: `quota_exhausted` is this turn's OWN caller-decided quota
    verdict (`app/chat/routes.py`, which already resolved the tenant's
    plan/usage with a real DB session before calling this) -- forwarded
    straight into `_build_thread_config()`, never computed here.

    Step 6.2: `support_text` is the SAME caller's own rendered support-
    details fallback text (`app.agent.graph.render_support_details_
    text()`, applied to the tenant's own `config` dict), forwarded
    identically.

    Part D: `stop_requested` is the SAME caller's own zero-argument
    callable closing over its in-process "has this visitor asked to
    stop" set (`app/chat/routes.py`'s own `_get_stop_requests()`),
    forwarded identically -- see `_build_thread_config()`'s own
    docstring for why this one, alone, is a callable rather than a
    plain value.
    """
    config = _build_thread_config(
        identity,
        conversation_id,
        quota_exhausted=quota_exhausted,
        support_text=support_text,
        stop_requested=stop_requested,
    )
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
