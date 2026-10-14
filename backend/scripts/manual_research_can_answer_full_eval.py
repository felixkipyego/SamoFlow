#!/usr/bin/env python3
# backend/scripts/manual_research_can_answer_full_eval.py
# Task 3.7.g (opt-in, real OpenAI + real Qdrant; NEVER run by pytest/CI
# -- not under tests/, no test_ prefix, not imported by anything).
#
# The corrective lesson from Task 3.7.f, now written into PROJECT_
# SPEC.md's own eval rule: research on prompts/schemas/models runs on
# the FULL golden set, N>=3, never a hand-picked subset of failing
# items -- a subset hides a regression in whatever categories it left
# out, exactly what happened at 3.7.f (a variant that looked clean on a
# 31-item research set silently broke 9 previously-100% items at full
# scale).
#
# Every non-greeting item's own retrieval runs ONCE (real retrieve(),
# real OpenAI query embedding) and is reused across all 4 variants x 3
# repeats -- isolating the answer-step variable, matching 3.7.f's own
# precedent. The real gate (`app.agent.graph._gate_passes()`, the SAME
# function `_gate_node` itself calls) is evaluated once per item from
# that one retrieval: a gate failure short-circuits can_answer=False
# for EVERY variant with NO model call, matching production exactly
# (the gate never varies by answer-prompt/model, so paying for 4
# variants' worth of model calls on an already-gated-out item would
# only inflate cost for no signal). Greeting items (15) are handled
# once, not x3 -- `is_greeting_or_thanks()` is a deterministic phrase
# match with no model call and no stochastic behavior to even measure.
#
# Run it by hand:
#   cd backend
#   make test-qdrant
#   export TEST_QDRANT_URL=http://127.0.0.1:<port>
#   export TEST_QDRANT_API_KEY=...
#   export TEST_QDRANT_READ_ONLY_API_KEY=...
#   export OPENAI_API_KEY=sk-...
#   python scripts/manual_research_can_answer_full_eval.py [a] [b] [c] [d]
#
# Cost estimate (real token counts, reported per-run by this script
# too -- this is a pre-run estimate only): ~264 real gpt-4o-mini calls
# each for (a)/(b) and ~264 real gpt-4o calls each for (c)/(d) (91
# non-greeting items x 3 repeats, minus the ~3 off_topic items the gate
# shortcuts for free) -- published OpenAI pricing (gpt-4o-mini $0.15 /
# $0.60 per 1M input/output tokens; gpt-4o $2.50 / $10.00 per 1M)
# estimates roughly $0.07 total for (a)+(b) and roughly $1.20 total for
# (c)+(d) -- a few dollars for all four variants together, run once.
# Cleans up its own seeded tenant's points in a `finally`.
import asyncio
import sys
import time

from scripts._shared import point_test_env_at_env_vars

point_test_env_at_env_vars()  # MUST run before any `app.*` import below

import uuid  # noqa: E402

from pydantic import BaseModel, Field  # noqa: E402

from app import qdrant  # noqa: E402
from app.agent.graph import _SYSTEM_INSTRUCTION as SYSTEM_A  # noqa: E402
from app.agent.graph import Answer, _gate_passes, _sanitize_answer_text  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.evals.golden_set import (  # noqa: E402
    DEFAULT_ITEMS_PATH,
    DEFAULT_SYNTHETIC_CORPUS_PATH,
    load_golden_set,
)
from app.retrieval.service import chunk_from_scored_point, retrieve  # noqa: E402
from scripts._can_answer_research import (  # noqa: E402
    call_variant,
    median_and_range,
    percentile,
)
from scripts.injection_detectors import CANARIES, detect_injection_leak  # noqa: E402
from scripts.retrieval_eval import (  # noqa: E402
    build_prepared_query,
    load_and_check_frozen_corpus,
    seed_frozen_corpus,
)

_N_REPEATS = 3
_GPT4O_MINI = "gpt-4o-mini"
_GPT4O = "gpt-4o"  # next tier up, same provider/family -- see this task's own report
# Published OpenAI pricing, per 1M tokens (input, output) -- stated
# plainly as the pricing source used, not assumed silently.
_PRICE_PER_MILLION = {
    _GPT4O_MINI: (0.15, 0.60),
    _GPT4O: (2.50, 10.00),
}


# --- (b)/(d) prompt: explicit multi-chunk synthesis permission, one
# neutral-domain (library/airline, NOT the shipping/returns/warranty
# synthetic corpus) few-shot pair demonstrating both directions -------------
_FEWSHOT_TEXT = (
    " For example: given a DATA block stating a library membership costs $40 and includes "
    "borrowing at all branches, and a second DATA block stating an active membership can be "
    "renewed online up to 30 days before it expires (a lapsed one must be renewed in person), "
    "the question 'If my membership is still active, can I renew it from home?' has "
    "can_answer=true -- both facts are explicitly stated and directly combine to answer it. By "
    "contrast, given a DATA block stating checked baggage up to 23kg is included and a second "
    "bag costs an extra fee, the question 'Is there an extra fee for an oversized bag over "
    "23kg?' has can_answer=false -- the text states a weight limit and a second-bag fee, but "
    "never states whether an oversized bag has its own fee, and guessing would not be grounded "
    "in the DATA blocks."
)
_SYNTHESIS_SUFFIX = (
    " can_answer may be true even when the answer requires combining facts from several DATA "
    "blocks, as long as each fact is explicitly stated somewhere in them. can_answer is false "
    "when the specific fact the question asks about is not stated, even if related facts are "
    "present."
) + _FEWSHOT_TEXT

SYSTEM_B = SYSTEM_A.rsplit(" You must call", 1)[0] + _SYNTHESIS_SUFFIX


class StrictAnswer(BaseModel):
    can_answer: bool = Field(
        description=(
            "True if the DATA blocks (possibly combined across several blocks, each fact "
            "explicitly stated) answer this exact question. False if the specific fact asked "
            "about is not stated, even if related facts are present."
        )
    )
    answer: str = Field(
        description="The answer, grounded only in the DATA blocks. Empty if can_answer is false."
    )
    used_chunk_ids: list[str] = Field(
        description="Labels (e.g. 'c1') of DATA blocks actually used. Empty if can_answer is false."
    )


VARIANTS: dict[str, tuple[str, type[BaseModel], str]] = {
    "(a) current": (SYSTEM_A, Answer, _GPT4O_MINI),
    "(b) synthesis-prompt": (SYSTEM_B, StrictAnswer, _GPT4O_MINI),
    "(c) next-tier model": (SYSTEM_A, Answer, _GPT4O),
    "(d) (b)+(c)": (SYSTEM_B, StrictAnswer, _GPT4O),
}


async def main() -> None:
    requested = {arg.strip("()") for arg in sys.argv[1:]}
    variants = {k: v for k, v in VARIANTS.items() if k[1] in requested} if requested else VARIANTS

    golden_set = load_golden_set(DEFAULT_ITEMS_PATH)
    settings = get_settings()
    threshold = settings.gate_relevance_threshold
    corpus = load_and_check_frozen_corpus(DEFAULT_SYNTHETIC_CORPUS_PATH)

    client = qdrant.build_qdrant_client(settings.qdrant_url, settings.qdrant_api_key_str())
    tenant_id = uuid.uuid4()

    try:
        print(f"Seeding frozen corpus into tenant {tenant_id} ({qdrant.COLLECTION_NAME})...")
        await seed_frozen_corpus(client, qdrant.COLLECTION_NAME, tenant_id, corpus)

        non_greeting = [item for item in golden_set.items if item.category != "greeting"]
        greeting_items = [item for item in golden_set.items if item.category == "greeting"]
        print(f"Retrieving (once) for {len(non_greeting)} non-greeting items...")
        retrieved_by_item: dict[str, dict] = {}
        for item in non_greeting:
            # Task 3.7.g fix, found live during this task's own first run
            # (not shipped uncaught): `follow_up` items carry `prior_
            # turns` and need the SAME joined query `_prepare_node`
            # builds in production -- both for retrieval AND for what
            # the answer model itself is asked (`_answer_node` reads
            # `state["prepared_query"]`, never the raw question, since
            # Task 3.7.a's own referent fix). Using `item.question` raw
            # for a follow_up item reproduces the EXACT referent-
            # ambiguity bug that fix closed, confirmed live: the first
            # run of this script showed follow_up FN=7/15 under variant
            # (a), the unmodified baseline -- a previously-always-0
            # number -- immediately traced to this, not a real model
            # regression. `build_prepared_query()` is the REAL
            # `_prepare_node` logic, imported, never reimplemented, so
            # a non-follow_up item's own "nothing to join" branch is
            # unaffected (returns the raw question unchanged).
            prepared_query = build_prepared_query(item)
            start = time.perf_counter()
            result = await retrieve(tenant_id, prepared_query)
            retrieval_ms = (time.perf_counter() - start) * 1000
            chunks = [chunk_from_scored_point(p) for p in result.points]
            gate_passed = _gate_passes(len(chunks), result.best_dense_score, threshold)
            retrieved_by_item[item.id] = {
                "chunks": chunks, "score": result.best_dense_score,
                "retrieval_ms": retrieval_ms, "gate_passed": gate_passed,
                "prepared_query": prepared_query,
            }
        gate_shortcut_count = sum(1 for v in retrieved_by_item.values() if not v["gate_passed"])
        print(f"  {gate_shortcut_count} item(s) gate-shortcut (no model call, any variant).")

        for label, (system_text, schema_cls, model_name) in variants.items():
            print(f"\n=== Running variant {label} (model={model_name}) ===")
            per_run_outcomes: list[list[dict]] = []
            latencies: list[float] = []
            total_input_tokens = 0
            total_output_tokens = 0
            total_model_calls = 0
            injection_failures: set[str] = set()

            for _repeat in range(_N_REPEATS):
                outcomes = []
                for item in non_greeting:
                    info = retrieved_by_item[item.id]
                    if not info["gate_passed"]:
                        outcomes.append(
                            {
                                "item": item, "can_answer": False, "answer": "",
                                "citation_urls": frozenset(),
                            }
                        )
                        continue
                    result = await call_variant(
                        system_text, schema_cls, model_name, settings.chat_model_temperature,
                        info["chunks"], info["prepared_query"],
                    )
                    total_model_calls += 1
                    total_input_tokens += result["input_tokens"]
                    total_output_tokens += result["output_tokens"]
                    if result["first_token_ms"] is not None:
                        latencies.append(result["first_token_ms"])
                    citation_urls = frozenset(
                        c["source_url"] for c in info["chunks"] if c["source_url"]
                    )
                    outcomes.append(
                        {
                            "item": item, "can_answer": result["can_answer"],
                            "answer": result["answer"], "citation_urls": citation_urls,
                        }
                    )
                    if item.category == "injection":
                        raw = result["answer"]
                        leak = detect_injection_leak(
                            raw_output=raw, sanitized_output=_sanitize_answer_text(raw),
                            citation_urls=citation_urls, canaries=CANARIES,
                        )
                        if not leak.passed:
                            injection_failures.add(item.id)
                for item in greeting_items:
                    outcomes.append(
                        {
                            "item": item, "can_answer": False, "answer": "",
                            "citation_urls": frozenset(),
                        }
                    )
                per_run_outcomes.append(outcomes)

            # --- per-category FP/FN, median + range across the 3 runs ---
            categories = sorted({item.category for item in golden_set.items})
            fp_by_run: dict[str, list[int]] = {c: [] for c in categories}
            fn_by_run: dict[str, list[int]] = {c: [] for c in categories}
            for outcomes in per_run_outcomes:
                by_cat: dict[str, list[tuple[bool, bool]]] = {c: [] for c in categories}
                for o in outcomes:
                    pair = (o["item"].expected_can_answer, o["can_answer"])
                    by_cat[o["item"].category].append(pair)
                for category, pairs in by_cat.items():
                    fp = sum(1 for exp, act in pairs if not exp and act)
                    fn = sum(1 for exp, act in pairs if exp and not act)
                    fp_by_run[category].append(fp)
                    fn_by_run[category].append(fn)

            print("  Per-category FP (median, range) / FN (median, range):")
            total_fp_runs = [sum(fp_by_run[c][r] for c in categories) for r in range(_N_REPEATS)]
            total_fn_runs = [sum(fn_by_run[c][r] for c in categories) for r in range(_N_REPEATS)]
            for category in categories:
                print(
                    f"    {category}: FP={median_and_range(fp_by_run[category])}  "
                    f"FN={median_and_range(fn_by_run[category])}"
                )
            total_fp_str = median_and_range(total_fp_runs)
            total_fn_str = median_and_range(total_fn_runs)
            print(f"  TOTAL: FP={total_fp_str}  FN={total_fn_str}")

            n_injection = sum(1 for i in golden_set.items if i.category == "injection")
            print(
                f"  Injection held: {n_injection - len(injection_failures)}/{n_injection} "
                f"(failed: {sorted(injection_failures) if injection_failures else 'none'})"
            )

            if latencies:
                p50 = percentile(latencies, 50)
                p95 = percentile(latencies, 95)
                print(
                    f"  first_token_ms: p50={p50:.0f} p95={p95:.0f} (n={len(latencies)}, "
                    f"budget: p50<1500 p95<3000) "
                    f"{'PASS' if p50 < 1500 and p95 < 3000 else 'MISS'}"
                )
            else:
                print("  first_token_ms: n/a")

            in_price, out_price = _PRICE_PER_MILLION[model_name]
            in_cost = total_input_tokens / 1_000_000 * in_price
            out_cost = total_output_tokens / 1_000_000 * out_price
            total_cost = in_cost + out_cost
            # One run's own turn count -- includes free gate-shortcut/
            # greeting turns, matching real production traffic composition.
            turns_this_run = len(non_greeting) + len(greeting_items)
            cost_per_1000 = (total_cost / turns_this_run) * 1000 if turns_this_run else 0.0
            print(
                f"  Cost: {total_model_calls} real model call(s) across {_N_REPEATS} runs, "
                f"{total_input_tokens} input + {total_output_tokens} output tokens, "
                f"${total_cost:.4f} total -- ${cost_per_1000:.4f} per 1000 turns "
                f"(avg per run of {turns_this_run} turns)"
            )
    finally:
        await client.delete(
            collection_name=qdrant.COLLECTION_NAME, points_selector=qdrant.tenant_filter(tenant_id)
        )
        await client.close()
        print(f"\nCleaned up: deleted tenant {tenant_id}'s own points.")


if __name__ == "__main__":
    asyncio.run(main())
