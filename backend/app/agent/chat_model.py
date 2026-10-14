# backend/app/agent/chat_model.py
# Task 3.4.a [SECURITY]: the ONE module allowed to construct a real chat
# model -- docs/SPEC.md §6.3's own "Models" bullet ("per-tenant config via
# `init_chat_model`, with `.with_fallbacks()` to a secondary provider").
# `tests/test_chat_model_guard.py`'s own AST guard confines both
# `init_chat_model`/provider-class construction AND the explicit `api_key=`
# keyword every such call must carry to this file alone.
#
# `init_chat_model`, not the `openai` SDK directly (this project's own
# established embedding-side pattern, `app/ingest/embedding.py`) [Step 3.4
# research, confirmed live before choosing]: LangGraph's `stream_mode=
# "messages"` (docs/SPEC.md §10's own streaming shape -- `messages` becomes
# `token`) only emits chunks for calls that go through a real
# `langchain_core.language_models.BaseChatModel` subclass's own callback
# wiring (`on_llm_new_token`); a bare SDK call inside a graph node would
# never surface through that stream mode at all. `init_chat_model` is the
# one factory docs/SPEC.md names directly, and lives in the separate
# `langchain` package (confirmed live: NOT exposed by `langchain-core`
# alone, already installed transitively via `langgraph`).
#
# Primary OpenAI, secondary Anthropic -- ONLY behind a TWO-KEY switch
# (Q2, code-enforced per a direct PART 0 follow-up to Task 3.4.b, not
# just a documented precondition): `ANTHROPIC_API_KEY` must be set AND
# `Settings.chat_model_secondary_verified` must be `True`. The key alone
# is NOT enough -- confirmed live (the Anthropic streaming/field-order
# check, `backend/scripts/manual_check_chat_model_streaming.py`) that
# field-order preservation is an OpenAI-specific "Structured Outputs"
# guarantee, never independently verified for Anthropic before this
# follow-up; a key present with the flag still `False` (the default)
# means "configured, but not yet verified" -- primary-only, with its own
# distinct startup warning naming the manual script to run. `Settings.
# anthropic_api_key` is deliberately OPTIONAL (see that field's own
# header comment, `app/config.py`) -- a deployment that never configures
# a secondary provider must still start and serve real answers through
# the primary alone, not fail startup for an opt-in feature.
#
# `api_key=` ALWAYS passed explicitly, from `get_settings()`'s own guarded
# accessors, never omitted [SECURITY, confirmed live, not assumed]: both
# `ChatOpenAI`/`ChatAnthropic` (what `init_chat_model` constructs under the
# hood) fall back to reading the provider's own raw environment variable
# (`OPENAI_API_KEY`/`ANTHROPIC_API_KEY`) directly when `api_key` is
# omitted -- proven live, before writing this module, by constructing
# `ChatOpenAI()` with no `api_key` kwarg while `OPENAI_API_KEY` was set in
# the process environment: it succeeded silently, using the raw env value,
# completely bypassing `Settings`/the config guard's own allow-list
# mechanism. Since `OPENAI_API_KEY` genuinely IS present in this
# project's own real environment (required by `Settings` itself), an
# omitted `api_key=` kwarg here would work by accident, not fail loudly --
# exactly the silent-bypass risk `tests/test_chat_model_guard.py`'s own
# explicit-kwarg rule exists to catch statically, since no runtime
# behavior difference exists between "passed explicitly" and "silently
# fell back to the env var" when that env var happens to be populated.
#
# Task 3.4.b: `max_retries=0` on BOTH models, explicit (never the
# installed SDK default of 2) -- `.with_fallbacks()` only engages once the
# PRIMARY has exhausted its own retries, confirmed live before this
# change: a primary pointed at an unreachable address, default
# `max_retries=2`, took **2.597s** before the fallback chain was even
# reached; with `max_retries=0` on both, the identical failure surfaced in
# **0.008s** -- a ~325x difference. This matches this module's own design
# intent directly (PROJECT_SPEC.md's own Q8 decision): the SECONDARY
# provider IS the retry; a primary-side SDK retry would just add a second,
# redundant delay layer in front of it.
#
# `timeout=settings.chat_model_timeout_seconds` on both, explicit --
# confirmed live (`app/config.py`'s own field comment has the full
# experiment) that httpx's own `read` timeout this maps to bounds the GAP
# BETWEEN reads, not the total response duration, so a long, genuinely-
# streaming answer is not truncated just for taking a while in total.
#
# Task 3.4.b [SECURITY-adjacent]: event-loop binding, checked live, not
# assumed, because `get_checkpointer()` (`app/agent/checkpointer.py`)
# deliberately avoids caching for exactly this class of hazard.
# CONFIRMED DIFFERENT HERE: neither `httpx.AsyncClient.__init__` nor
# `openai`'s/`anthropic`'s own base client calls `asyncio.get_running_
# loop()`/`get_event_loop()` at CONSTRUCTION time (grepped both SDKs'
# `_base_client.py` directly -- the one hit in each is inside a `__del__`-
# time cleanup path, not construction), unlike `AsyncPostgresSaver.
# __init__()`'s own confirmed `self.loop = asyncio.get_running_loop()`
# store. A real, DIFFERENT cross-loop hazard exists anyway, found live:
# reusing the SAME cached model across REPEATED, SEPARATELY-CLOSED event
# loops (`asyncio.run()` called more than once) -- after a loop closes
# with a live HTTP keep-alive connection still in the pool -- raises
# `RuntimeError: Event loop is closed` on the FIRST reuse in the next
# loop, then self-heals (the dead connection is evicted, a fresh one
# opens bound to the new loop, which then itself goes stale once THAT
# loop closes) -- confirmed 100% reproducible, alternating success/
# failure on every repeated `asyncio.run()` call reusing one instance.
# Root cause: HTTP keep-alive, not loop storage -- a different mechanism
# from `AsyncPostgresSaver`'s, but the same hazard CLASS.
#
# NOT a production risk: a real FastAPI/uvicorn process has exactly one
# event loop for its entire lifetime (already established at the Step 3.3
# breakdown's own `app/main.py` marker) -- this boundary is never crossed
# there. It IS a real risk for THIS PROJECT'S OWN TEST SUITE specifically,
# which is pytest-asyncio's own per-test-function event-loop churn --
# matching the identical test-harness hazard `get_checkpointer()`'s own
# non-caching design exists to avoid, just triggered differently here
# (keep-alive reuse, not loop storage). `tests/conftest.py`'s
# `close_chat_model_cache_between_tests` fixture (module-scoped, opt-in)
# exists for exactly this: any test module making a REAL (or realistically
# connection-establishing) call through `get_chat_model()` must use it, so
# no test accidentally inherits a live-connection-bearing cached instance
# from a DIFFERENT, already-closed test's own event loop.
# `test_get_chat_model_cached_instance_is_unsafe_to_reuse_across_closed_
# event_loops_once_a_real_connection_is_established` (`tests/test_chat_
# model.py`) is the live regression proof itself -- makes a REAL network
# attempt (to a real, routable TEST-NET-3 address that simply never
# responds, so no actual provider is ever contacted and no cost is
# incurred) specifically to exercise the keep-alive-connection code path,
# not merely the "never connects at all" path my own first, too-shallow
# check used.
import logging
from functools import lru_cache

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable

from app.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def get_chat_model() -> Runnable:
    """The one chat-model entry point for the rest of this codebase -- a
    lazy `@lru_cache` singleton, matching `_get_client()`'s own established
    pattern (`app/ingest/embedding.py`) and `get_qdrant_client()`'s own:
    built once, on first real use, from `get_settings()`, never at import.

    Returns a plain `Runnable` (not `BaseChatModel`): when a secondary
    provider is configured, `.with_fallbacks()` returns a
    `RunnableWithFallbacks`, a different, wrapping type -- callers must
    treat the return value as "some Runnable both construction paths
    support" (`.invoke()`/`.ainvoke()`/`.astream()`/`.with_structured_
    output()`, all of which `RunnableWithFallbacks` forwards), never
    assume the narrower `BaseChatModel` interface.
    """
    settings = get_settings()
    timeout = settings.chat_model_timeout_seconds
    # Task 3.7.i: `None` (never a per-model-name branch here) means this
    # model rejects a temperature override -- omit the kwarg entirely
    # rather than pass `None` through to `init_chat_model()`. A single
    # declarative Settings value per model choice, not special-cased
    # per model in this function.
    temperature_kwargs = (
        {} if settings.chat_model_temperature is None
        else {"temperature": settings.chat_model_temperature}
    )
    # Task 3.7.i: a real reasoning-effort model (e.g. the shipped
    # "gpt-6-luna") needs its own lowest-cost setting requested
    # explicitly -- a plain, optional Settings value, never a per-
    # model-name branch here either. OpenAI-specific (the param name
    # itself is that provider's own), so passed to the primary only,
    # never forwarded to a configured Anthropic secondary below.
    reasoning_kwargs = (
        {} if settings.chat_model_reasoning_effort is None
        else {"reasoning_effort": settings.chat_model_reasoning_effort}
    )
    primary: BaseChatModel = init_chat_model(
        settings.chat_model_primary,
        model_provider="openai",
        api_key=settings.openai_api_key_str(),
        max_retries=0,
        timeout=timeout,
        **temperature_kwargs,
        **reasoning_kwargs,
    )

    secondary_key = settings.anthropic_api_key_str()
    if secondary_key is None:
        logger.warning(
            "ANTHROPIC_API_KEY is not set -- the chat-model fallback chain is "
            "primary-only (OpenAI). A primary-provider outage has no fallback "
            "today; see PROJECT_SPEC.md's own Phase-7-owned Open marker for the "
            "related embedding-fallback gap."
        )
        return primary

    if not settings.chat_model_secondary_verified:
        logger.warning(
            "ANTHROPIC_API_KEY is set but CHAT_MODEL_SECONDARY_VERIFIED is not "
            "true -- the chat-model fallback chain is primary-only (OpenAI). "
            "Run backend/scripts/manual_check_chat_model_streaming.py against "
            "the real Anthropic API first, then set CHAT_MODEL_SECONDARY_"
            "VERIFIED=true, before relying on Anthropic as a real fallback."
        )
        return primary

    secondary: BaseChatModel = init_chat_model(
        settings.chat_model_secondary,
        model_provider="anthropic",
        api_key=secondary_key,
        max_retries=0,
        timeout=timeout,
        **temperature_kwargs,
    )
    return primary.with_fallbacks([secondary])
