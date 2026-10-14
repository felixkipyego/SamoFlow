# backend/scripts/_can_answer_research.py
# Task 3.7.h, item 0 (duplication check, due at n=4, run before any new
# code this task): `call_variant()`/`_model()`/`_percentile()` had been
# copy-pasted, near-byte-identical in substance, into TWO separate
# research scripts (manual_research_can_answer_variants.py, Task 3.7.f,
# the original; manual_research_can_answer_full_eval.py, Task 3.7.g, a
# strict superset adding usage_metadata accumulation) -- past this
# project's own established 3rd-occurrence extraction threshold, and
# this task was about to write a THIRD near-identical copy to run
# candidate models over the full set. Extracted here instead, matching
# `scripts/_shared.py`'s own precedent (a leading-underscore MODULE
# name, plain public function names inside it) rather than one more
# independently-maintained copy -- the real drift risk this avoids is
# not hypothetical: 3.7.g's own first draft already diverged from
# 3.7.f's version in a way that mattered (it dropped the `prepared_
# query`/`build_prepared_query()` join step for follow_up items, a bug
# found live during that task). A single shared copy cannot silently
# diverge like that a second time.
#
# Mirrors `_shared.py`'s own "no app.* import before env-pointing"
# discipline is NOT needed here -- this module imports `app.agent.graph`
# directly (for `_build_messages`), so every CALLER must itself already
# have called `point_test_env_at_env_vars()` before importing this
# module, exactly as every caller already does today (confirmed by
# reading both existing callers).
import os
import statistics
import time

from langchain.chat_models import init_chat_model
from langchain_core.messages import SystemMessage
from langchain_core.utils.json import parse_partial_json
from pydantic import BaseModel

from app.agent.graph import _build_messages


def build_model(model_name: str, temperature: float | None, *, timeout: float = 30.0, **kwargs):
    """The ONE place every can_answer research script constructs a real
    chat model -- note this is research tooling (`backend/scripts/`),
    NOT production code, so `tests/test_chat_model_guard.py`'s own AST
    guard (which confines REAL model construction to `app/agent/
    chat_model.py`) does not and should not apply here; confirmed by
    reading that guard directly, it only scans `backend/app/`.

    `temperature=None` OMITS the kwarg entirely rather than passing
    `None` through -- Task 3.7.h found LIVE, real, not assumed: newer
    OpenAI models (confirmed on `gpt-6.1-sol` and `o3`) reject
    `temperature=0.0` outright ("Only the default (1) value is
    supported"), so a candidate that cannot run at this project's own
    `chat_model_temperature=0.0` must be called with no override at
    all to be measured as it would actually have to run in production.
    `**kwargs` passes through model-specific extras (e.g.
    `reasoning_effort="low"` for a reasoning model).
    """
    extra = {} if temperature is None else {"temperature": temperature}
    return init_chat_model(
        model_name, model_provider="openai", api_key=os.environ["OPENAI_API_KEY"],
        max_retries=0, timeout=timeout, **extra, **kwargs,
    )


async def call_variant(
    system_text: str, schema_cls: type[BaseModel], model_name: str, temperature: float | None,
    retrieved_chunks, question: str, *, timeout: float = 30.0, **model_kwargs,
) -> dict:
    """One real streaming call -- the SAME `.astream()`/`parse_partial_
    json()` mechanism `_answer_node` itself uses, so `first_token_ms`
    measures the real production quantity (time to the first visible
    answer-text delta, after can_answer resolves true), never an
    approximation via `.ainvoke()`. Raw-chunk accumulation (`+=`)
    recovers real `usage_metadata` (input/output token counts) for a
    real, per-call cost estimate -- confirmed live, before relying on
    it, that accumulating `AIMessageChunk`s via `+` yields a merged
    chunk whose own `usage_metadata` is the real, complete call total,
    both with and without `bind_tools()`.

    Returns `can_answer`/`answer`/`used_chunk_ids` plus whatever extra
    fields the schema itself declares (e.g. a `quote` field) via a
    plain `.get()` with no fallback assumption about which fields exist
    -- callers that need a schema-specific field read it from the
    returned dict's own keys, resolved by `parse_partial_json()`, not a
    second, hand-maintained field list here.
    """
    base_messages = _build_messages(retrieved_chunks, question)
    messages = [SystemMessage(content=system_text), base_messages[1]]
    model = build_model(model_name, temperature, timeout=timeout, **model_kwargs)
    bound = model.bind_tools([schema_cls], tool_choice=schema_cls.__name__)

    start = time.perf_counter()
    accumulated_args = ""
    first_token_ms: float | None = None
    can_answer_known = False
    can_answer_value = False
    merged = None
    async for chunk in bound.astream(messages):
        merged = chunk if merged is None else merged + chunk
        tool_call_chunks = chunk.tool_call_chunks
        if not tool_call_chunks or not tool_call_chunks[0].get("args"):
            continue
        accumulated_args += tool_call_chunks[0]["args"]
        partial = parse_partial_json(accumulated_args) or {}
        if not can_answer_known and "can_answer" in partial:
            can_answer_known = True
            can_answer_value = bool(partial["can_answer"])
        if (
            first_token_ms is None
            and can_answer_known
            and can_answer_value
            and partial.get("answer")
        ):
            first_token_ms = (time.perf_counter() - start) * 1000

    final = parse_partial_json(accumulated_args) or {}
    usage = merged.usage_metadata if merged is not None else None
    return {
        "can_answer": bool(final.get("can_answer", False)),
        "answer": final.get("answer") or "",
        "quote": final.get("quote") or "",
        "used_chunk_ids": list(final.get("used_chunk_ids") or []),
        "first_token_ms": first_token_ms,
        "input_tokens": usage["input_tokens"] if usage else 0,
        "output_tokens": usage["output_tokens"] if usage else 0,
    }


def percentile(values: list[float], p: float) -> float:
    return statistics.quantiles(values, n=100, method="inclusive")[int(p) - 1]


def median_and_range(values: list[int]) -> str:
    if not values:
        return "0 (0-0)"
    return f"{statistics.median(values):.0f} ({min(values)}-{max(values)})"
