# backend/app/agent/context.py
# Task 3.3.a [SECURITY]: the one accessor graph nodes use to read
# `tenant_id` out of the LangGraph config. `app/agent/entrypoint.py`'s own
# `_build_thread_config()` (Task 3.2.c, extended at 3.3.a) is the only
# place that CONSTRUCTS the `configurable` dict carrying it; this module
# is the only OTHER place allowed to subscript one to read it back out --
# `tests/test_agent_entrypoint_guard.py`'s own new rule 4 confines any
# `x["configurable"]` subscript to exactly these two files.
#
# Config, not state [SECURITY] (Step 3.3's own design decision, recorded
# in PROJECT_SPEC.md): `tenant_id` travels through the LangGraph
# *config*, rebuilt server-side on every single call by the entry point,
# never through graph *state* (which persists in the checkpoint across
# turns and resumes, and could be silently overwritten by any node
# returning a same-named field). A resume carries no caller-supplied
# input at all -- a state-based `tenant_id` would have to be read back
# from whatever a PRIOR turn happened to store, not re-derived from the
# current call's own `VerifiedIdentity`. docs/SPEC.md §4.5's own "[tools]
# come from server-set config" states the identical pattern for the
# closely-related tool case.
#
# Stored as a raw `uuid.UUID`, never `str(tenant_id)` -- confirmed live,
# Task 3.3.a, not assumed: `langgraph.checkpoint.base.
# get_checkpoint_metadata()` copies every `configurable` entry whose
# value is `str`/`int`/`bool`/`float` into the checkpoint's own PERSISTED
# metadata (the real `metadata` JSONB column in Postgres), unless the key
# is in its own `EXCLUDED_METADATA_KEYS` (`thread_id`/`checkpoint_ns`/
# `checkpoint_id`/etc. -- `tenant_id` is not one of them). Proven live by
# writing both forms to a real test-db and reading the stored row back
# directly: a raw `uuid.UUID` value left no trace in `metadata` at all
# (the type simply does not match that filter's own str/int/bool/float
# check); `str(tenant_id)` was copied in and persisted verbatim.
# `_build_thread_config()` must keep storing the raw `uuid.UUID` for this
# reason -- it is not a convenience choice, it is what keeps `tenant_id`
# out of the checkpoint's own stored metadata.
#
# Senior engineer judgment (CLAUDE.md): this is a DELIBERATE DEPENDENCY on
# a specific piece of current LangGraph behavior (get_checkpoint_
# metadata()'s own str/int/bool/float filter), not a guaranteed language-
# or spec-level contract -- a future LangGraph version could change what
# it copies into metadata. Accepted, not treated as a defect, because the
# dependency is guarded: `tests/test_agent_entrypoint.py`'s own
# `test_tenant_id_is_never_persisted_in_checkpoint_metadata` reads the
# real stored Postgres row directly and fails loudly if that filtering
# behavior ever changes -- proven live, not assumed, by temporarily
# reverting this function to `str(identity.tenant_id)` and confirming
# that exact test fails (`AssertionError: assert 'tenant_id' not in
# {...'tenant_id': '...'}`), then restoring. A silent behavior change on a
# future LangGraph upgrade would be caught by CI, not discovered later as
# a leaked `tenant_id` in production checkpoint metadata.
import uuid
from collections.abc import Mapping
from typing import Any


def build_thread_id(tenant_id: uuid.UUID, vid: uuid.UUID, conversation_id: uuid.UUID) -> str:
    """Task 4.2: the ONE place `thread_id`'s own exact string format
    (docs/SPEC.md §4.5) is defined. `_build_thread_config()` (app/agent/
    entrypoint.py) calls this to build the real LangGraph config; the
    nightly retention-sweep job handler (`handle_retention_sweep()`,
    app/ingest/job_handlers.py) calls the SAME function to address a
    checkpoint thread for deletion (`AsyncPostgresSaver.adelete_thread()`)
    -- a second real caller, extracted here rather than hand-duplicating
    the exact `f"{a}:{b}:{c}"` format a second time. Does NOT repeat
    `_build_thread_config()`'s own delimiter-injection `isinstance`
    checks: both of this function's own callers already hold real
    `uuid.UUID` values read directly from the database, never from an
    untrusted request body the way `_build_thread_config()`'s own
    `identity`/`conversation_id` parameters structurally can (that
    module's own guarded construction path is where that check belongs,
    and still applies there unchanged).
    """
    return f"{tenant_id}:{vid}:{conversation_id}"


def tenant_id_from_config(config: Mapping[str, Any]) -> uuid.UUID:
    """The ONLY way a graph node may read `tenant_id`. Validates presence
    and type, raising a clear `ValueError` otherwise -- never returns a
    value that isn't a real `uuid.UUID`, the same discipline
    `_build_thread_config()` itself already applies on the way in.
    """
    try:
        tenant_id = config["configurable"]["tenant_id"]
    except KeyError:
        tenant_id = None
    if not isinstance(tenant_id, uuid.UUID):
        raise ValueError(
            "config['configurable']['tenant_id'] is missing or not a uuid.UUID "
            f"(got {tenant_id!r}) -- this config was not built by "
            "app.agent.entrypoint._build_thread_config()"
        )
    return tenant_id


def quota_exhausted_from_config(config: Mapping[str, Any]) -> bool:
    """Task 4.3: the ONE way `route()`'s own `_quota_exhausted()` predicate
    (app/agent/graph.py) may read the pre-computed quota verdict out of
    the LangGraph config. Decided BEFORE the graph ever runs, by `app/
    chat/routes.py`'s own request handler (which already has a real DB
    session open, from resolving `identity`/the conversation/the lease,
    same connection-hold discipline as every other pre-stream check) --
    never re-derived inside the graph itself, matching `tenant_id_from_
    config()`'s own identical reasoning: a graph predicate has no DB
    session of its own, and re-deriving a quota verdict mid-graph would
    mean either giving a predicate its own ad hoc session (a new,
    unwanted pattern) or passing a session through config (a much larger
    departure from this project's existing design). Defaults to `False`
    (never exhausted) when absent -- matches `_build_thread_config()`'s
    own `quota_exhausted` parameter default, so every EXISTING caller
    that predates Task 4.3 (every test building a config by hand) keeps
    working unchanged.
    """
    return bool(config.get("configurable", {}).get("quota_exhausted", False))
