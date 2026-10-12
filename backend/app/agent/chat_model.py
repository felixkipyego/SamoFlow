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
# Primary OpenAI, secondary Anthropic -- ONLY if `ANTHROPIC_API_KEY` is set
# (Q2). `Settings.anthropic_api_key` is deliberately OPTIONAL (see that
# field's own header comment, `app/config.py`) -- a deployment that never
# configures a secondary provider must still start and serve real answers
# through the primary alone, not fail startup for an opt-in feature. When
# unset, `get_chat_model()` logs a WARNING naming the fallback chain as
# primary-only, so this is a visible, deliberate state, not a silently
# degraded one.
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
    primary: BaseChatModel = init_chat_model(
        settings.chat_model_primary,
        model_provider="openai",
        api_key=settings.openai_api_key_str(),
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

    secondary: BaseChatModel = init_chat_model(
        settings.chat_model_secondary,
        model_provider="anthropic",
        api_key=secondary_key,
    )
    return primary.with_fallbacks([secondary])
