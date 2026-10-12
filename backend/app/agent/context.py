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
