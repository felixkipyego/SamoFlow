#!/usr/bin/env python3
# backend/scripts/manual_check_chat_model_streaming.py
# Task 3.4.b: a MANUAL, OPT-IN script making REAL chat-model API calls --
# NEVER run by pytest/CI (not under tests/, no test_ prefix, not imported
# by anything). Run it by hand:
#
#   cd backend && python scripts/manual_check_chat_model_streaming.py
#
# Needs a real OPENAI_API_KEY in the environment (cents of cost -- one or
# two short completions). If ANTHROPIC_API_KEY is also set, it is checked
# too; if not, that half is skipped with a clear message, not silently.
#
# What this answers, that no fake model ever can (fakes stream whatever
# script they're given -- they cannot show how a REAL provider actually
# chunks a REAL tool call):
#   (1) PROJECT_SPEC.md's own §9 marker: enabling ANTHROPIC_API_KEY in
#       production is conditional on THIS check passing against Anthropic
#       first -- does `tool_call_chunks` actually stream incrementally
#       through `ChatAnthropic` at all, and is the declared `Answer`
#       schema's own field order -- **`can_answer` -> `answer` ->
#       `used_chunk_ids`, REORDERED at the Task 3.4.g latency follow-up
#       (was `can_answer`/`used_chunk_ids`/`answer`)** -- actually
#       preserved the same way it's confirmed live for OpenAI
#       (app/agent/graph.py's own header comment)? Checked here as: zero
#       answer text before `can_answer` resolves (unchanged, still
#       non-negotiable), and `used_chunk_ids` not observed arriving (as a
#       JSON key) before the first visible answer-text delta -- NOT a
#       correctness requirement (the real `_answer_node`'s post-loop
#       citation trigger has no dependency on provider field order at
#       all, confirmed by that task's own adversarial pass), only the
#       signal that this provider would ALSO get the latency benefit the
#       reorder was built for.
#   (2) Real, measured time-to-first-visible-text against docs/SPEC.md
#       §17's own 1.5s/3s time-to-first-token budget, through the SAME
#       incremental-parsing logic the real answer node uses (not a
#       simplified stand-in).
#
# Reuses app.agent.graph's own real Answer schema/_build_messages()/
# parse_partial_json() logic directly -- this script proves the REAL
# production code path against a REAL provider, not a separately
# maintained copy of it that could silently drift out of sync.
import asyncio
import time

from langchain.chat_models import init_chat_model
from langchain_core.utils.json import parse_partial_json

from app.agent.graph import Answer, _build_messages
from app.config import get_settings


async def _probe(provider: str, model_name: str, api_key: str) -> None:
    print(f"\n=== {provider} ({model_name}) ===")
    settings = get_settings()
    model = init_chat_model(
        model_name,
        model_provider=provider,
        api_key=api_key,
        max_retries=0,
        timeout=settings.chat_model_timeout_seconds,
    )
    bound = model.bind_tools([Answer], tool_choice="Answer")
    messages = _build_messages(
        [
            {
                "document_id": "doc-1",
                "text": "The sky is blue today, with scattered clouds near the horizon.",
                "source_url": "https://example.com/sky",
                "heading_path": ["Weather"],
                "score": 0.95,
            }
        ],
        "What color is the sky?",
    )

    start = time.perf_counter()
    accumulated = ""
    emitted = ""
    can_answer_at: float | None = None
    first_text_at: float | None = None
    used_chunk_ids_at: float | None = None
    raw_chunks_seen = 0
    text_before_can_answer_violation = False

    async for chunk in bound.astream(messages):
        tool_call_chunks = chunk.tool_call_chunks
        if not tool_call_chunks or not tool_call_chunks[0].get("args"):
            continue
        raw_chunks_seen += 1
        if raw_chunks_seen <= 5:
            print(f"  raw chunk {raw_chunks_seen}: {tool_call_chunks[0]!r}")
        accumulated += tool_call_chunks[0]["args"]
        partial = parse_partial_json(accumulated) or {}

        if can_answer_at is None and "can_answer" in partial:
            can_answer_at = time.perf_counter() - start
            # The one invariant that cannot change regardless of field
            # order (app/agent/graph.py's own Answer docstring): zero
            # answer-text bytes before can_answer resolves. Still checked
            # here even though the NEW order makes "answer" the very next
            # field -- a provider that front-loads multiple fields into
            # one chunk could in principle violate this.
            if partial.get("answer"):
                text_before_can_answer_violation = True

        if used_chunk_ids_at is None and "used_chunk_ids" in partial:
            used_chunk_ids_at = time.perf_counter() - start

        new_answer = partial.get("answer") or ""
        if len(new_answer) > len(emitted):
            if first_text_at is None:
                first_text_at = time.perf_counter() - start
            emitted = new_answer

    total = time.perf_counter() - start
    final = parse_partial_json(accumulated) or {}

    # Task 3.4.g's own latency follow-up: did this provider ALSO stream
    # used_chunk_ids AFTER answer text started, the way the new declared
    # order asks for? NOT a correctness requirement (_build_citations()'s
    # own post-loop trigger works correctly regardless, confirmed by that
    # task's own adversarial pass) -- only whether this provider gets the
    # SAME latency benefit the reorder was built to produce.
    order_not_honored = (
        used_chunk_ids_at is not None
        and first_text_at is not None
        and used_chunk_ids_at < first_text_at
    )

    print(f"  raw tool_call_chunks observed: {raw_chunks_seen}")
    if can_answer_at:
        print(f"  can_answer known at:    {can_answer_at * 1000:.0f}ms")
    else:
        print("  can_answer: NEVER resolved")
    if first_text_at:
        print(f"  first visible text at:  {first_text_at * 1000:.0f}ms")
    else:
        print("  no answer text streamed")
    if used_chunk_ids_at:
        print(f"  used_chunk_ids key seen at: {used_chunk_ids_at * 1000:.0f}ms")
    print(f"  total time:             {total * 1000:.0f}ms")
    print(f"  final can_answer:       {final.get('can_answer')}")
    print(f"  final used_chunk_ids:   {final.get('used_chunk_ids')}")
    print(f"  final answer:           {final.get('answer')!r}")
    if raw_chunks_seen <= 1:
        print("  *** WARNING: tool_call_chunks did NOT stream incrementally. ***")
    if text_before_can_answer_violation:
        print(
            "  *** WARNING: answer text was already present when can_answer resolved -- "
            "the one non-negotiable invariant is VIOLATED. ***"
        )
    if order_not_honored:
        print(
            "  NOTE: used_chunk_ids appeared before the first visible answer text -- "
            "this provider does not honor the new declared field order. Not a "
            "correctness problem (citations are still built correctly, post-loop), "
            "but this provider will not get the latency benefit the reorder was "
            "built for."
        )
    if provider == "anthropic":
        if raw_chunks_seen > 1 and not text_before_can_answer_violation:
            print(
                "  RESULT: Anthropic streaming check PASSES -- safe to set "
                "CHAT_MODEL_SECONDARY_VERIFIED=true (ANTHROPIC_API_KEY alone is "
                "not enough; app/agent/chat_model.py enforces both)."
            )
            if order_not_honored:
                print(
                    "  (passes correctness; see the field-order NOTE above for the "
                    "latency caveat specific to this provider.)"
                )
        else:
            print(
                "  RESULT: Anthropic streaming check FAILS -- do NOT set "
                "CHAT_MODEL_SECONDARY_VERIFIED=true until this is investigated."
            )


async def main() -> None:
    settings = get_settings()
    await _probe("openai", settings.chat_model_primary, settings.openai_api_key_str())

    anthropic_key = settings.anthropic_api_key_str()
    if anthropic_key is None:
        print("\n=== anthropic ===")
        print("  SKIPPED: ANTHROPIC_API_KEY is not set in this environment.")
        print("  Run this script again with it set before enabling it in production "
              "(see PROJECT_SPEC.md's own §9 marker for this exact requirement).")
        return
    await _probe("anthropic", settings.chat_model_secondary, anthropic_key)


if __name__ == "__main__":
    asyncio.run(main())
