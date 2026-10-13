#!/usr/bin/env python3
# backend/scripts/manual_research_moderation_endpoint.py
# Task 3.4.f RESEARCH ONLY (no unsafe/abusive detection is built by this
# script or by this task -- see PROJECT_SPEC.md's own §9 marker, owner
# "3.4.f's own follow-up"): a MANUAL, OPT-IN script making a few REAL
# OpenAI moderation-endpoint calls -- NEVER run by pytest/CI (not under
# tests/, no test_ prefix, not imported by anything). Run it by hand:
#
#   cd backend && python scripts/manual_research_moderation_endpoint.py
#
# Needs a real OPENAI_API_KEY in the environment. The moderation endpoint
# itself is FREE (confirmed live via OpenAI's own published pricing/docs
# at this task's own research time -- no per-token cost, unlike a real
# chat-model or embedding call), so this script's own real calls cost
# nothing beyond the handful of HTTP round trips they make.
#
# What this answers, that reasoning alone cannot: the REAL, measured
# latency of a REAL moderation call (docs/SPEC.md §6.2's own routing
# rules are meant to be "rules, no LLM hop" -- fast and deterministic;
# this is the one number that proves or disproves whether a moderation
# call fits that design goal, not an assumption either way).
import asyncio
import statistics
import time

from openai import AsyncOpenAI

_SAMPLES = [
    "What are your opening hours?",
    "I will find you and hurt you if this is not fixed",
    "thanks so much, appreciate the help",
    "this product is garbage and so are you",
    "can you help me reset my password",
]


async def main() -> None:
    client = AsyncOpenAI()
    latencies_ms: list[float] = []
    print("Task 3.4.f research: OpenAI moderation endpoint, real calls\n")
    for text in _SAMPLES:
        start = time.perf_counter()
        response = await client.moderations.create(model="omni-moderation-latest", input=text)
        elapsed_ms = (time.perf_counter() - start) * 1000
        latencies_ms.append(elapsed_ms)
        result = response.results[0]
        flagged_categories = [
            name for name, value in result.categories.model_dump().items() if value
        ]
        print(
            f"  {elapsed_ms:6.0f}ms  flagged={result.flagged!s:5}  "
            f"categories={flagged_categories}  :: {text!r}"
        )

    print(
        f"\n  latency: min={min(latencies_ms):.0f}ms max={max(latencies_ms):.0f}ms "
        f"mean={statistics.mean(latencies_ms):.0f}ms"
    )
    print(
        "\n  Compare against docs/SPEC.md §6.2's own 'rules, no LLM hop' design intent "
        "for ROUTING decisions, and §17's own time-to-first-token budgets (p50 < 1.5s, "
        "p95 < 3s) -- see PROJECT_SPEC.md's own Task 3.4.f research entry for the full "
        "recommendation this measurement feeds into."
    )


if __name__ == "__main__":
    asyncio.run(main())
