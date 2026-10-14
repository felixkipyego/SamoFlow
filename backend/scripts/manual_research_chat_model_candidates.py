#!/usr/bin/env python3
# backend/scripts/manual_research_chat_model_candidates.py
# Task 3.7.h (opt-in, real OpenAI + real Qdrant; NEVER run by pytest/CI
# -- not under tests/, no test_ prefix, not imported by anything).
#
# Chooses the primary chat model on cost AND quality against the real,
# shipped `gpt-4o` reference. Task 3.7.i corrected 3.7.h's own filter
# ("priced between gpt-4o-mini and gpt-4o", which wrongly excluded
# cheaper current models) to: cheaper than gpt-4o, current generation,
# no announced shutdown before May 2027 -- see this task's own PROJECT_
# SPEC.md entry for the real OpenAI pricing/models/deprecations pages
# checked, with URLs and the check date, before picking candidates.
# `_CANDIDATES` reflects whichever task most recently ran this script;
# older candidates' own real numbers are not lost -- they are recorded
# in PROJECT_SPEC.md's own decision log, under the task that measured
# them, not re-run here just to keep them "in" this file.
#
# The CURRENT (shipped) prompt/schema is held fixed (`_SYSTEM_
# INSTRUCTION`/`Answer`, unmodified) -- this task varies ONLY the
# model, reusing `scripts/_can_answer_research.py` (extracted at this
# task's own duplication check, item 0, to avoid a 3rd near-duplicate
# research harness) for the real streaming call, latency timing and
# token-usage accumulation every can_answer research script since Task
# 3.7.f already shares.
#
# Retrieval runs ONCE per non-greeting item and is reused across every
# candidate model x 3 repeats -- the model choice never changes
# retrieval. The real gate is evaluated once per item from that one
# retrieval; a gate failure short-circuits can_answer=False for every
# candidate with zero model calls, matching production exactly.
#
# TIMEOUT handling, matching production exactly, not a looser research
# convenience value: `timeout=Settings.chat_model_timeout_seconds`
# (5.0s) and `max_retries=0` (the SAME two real production values,
# `app/agent/chat_model.py`) -- Task 3.7.g's own research used a looser
# 20-30s timeout, which would have made any real timeout-rate
# measurement meaningless (a 30s timeout essentially never fires). A
# call that times out (or fails for any other reason) is caught and
# counted as a real failure -- treated as can_answer=False for FP/FN
# purposes, matching how a real dependency failure resolves in
# production (the fixed dependency-failure fallback, never a guess).
#
# `temperature=None` for a candidate that cannot accept the project's
# own `chat_model_temperature=0.0` -- confirmed LIVE, real, before
# writing this: both "gpt-6.1-sol" and "o3" reject `temperature=0.0`
# outright ("Only the default (1) value is supported"); omitting the
# kwarg entirely (not passing `None` through) measures each candidate
# exactly as it would actually have to run in production, not a
# convenient approximation.
#
# Run it by hand:
#   cd backend
#   make test-qdrant
#   export TEST_QDRANT_URL=http://127.0.0.1:<port>
#   export TEST_QDRANT_API_KEY=...
#   export TEST_QDRANT_READ_ONLY_API_KEY=...
#   export OPENAI_API_KEY=sk-...
#   python scripts/manual_research_chat_model_candidates.py [model-key ...]
#
# Cost estimate (pre-run only -- the real per-run cost is reported by
# this script too): ~106 items x 3 repeats, minus ~3 gate-shortcut
# off_topic items, per candidate -- a few dollars total across 4
# candidates (gpt-4o reference + 3 new ones) at published pricing.
# Cleans up its own seeded tenant's points in a `finally`.
import asyncio
import statistics
import sys

from scripts._shared import point_test_env_at_env_vars

point_test_env_at_env_vars()  # MUST run before any `app.*` import below

import uuid  # noqa: E402

from app import qdrant  # noqa: E402
from app.agent.graph import (  # noqa: E402
    _SYSTEM_INSTRUCTION,
    Answer,
    _gate_passes,
    _sanitize_answer_text,
)
from app.config import get_settings  # noqa: E402
from app.evals.golden_set import (  # noqa: E402
    DEFAULT_ITEMS_PATH,
    DEFAULT_SYNTHETIC_CORPUS_PATH,
    load_golden_set,
)
from app.retrieval.service import chunk_from_scored_point, retrieve  # noqa: E402
from scripts._can_answer_research import call_variant, median_and_range, percentile  # noqa: E402
from scripts.injection_detectors import CANARIES, detect_injection_leak  # noqa: E402
from scripts.retrieval_eval import (  # noqa: E402
    build_prepared_query,
    load_and_check_frozen_corpus,
    seed_frozen_corpus,
)

_N_REPEATS = 3

# Real OpenAI pricing per 1M tokens (input, output) -- see this task's
# own PROJECT_SPEC.md entry for the exact pages/URLs/check date.
_PRICE_PER_MILLION = {
    "gpt-4o": (2.50, 10.00),
    "gpt-6-luna": (0.10, 0.50),
    "gpt-6.1-sol": (2.00, 10.00),
}

# Task 3.7.i: the gpt-4o REFERENCE is NOT re-run this task -- reused
# directly from Task 3.7.g's committed numbers (full set, N=3, corrected
# golden set), per direct instruction. `per_category_correct_median`
# derived from that task's own real committed confusion-by-category
# counts (16 answerable, 15 each of follow_up/greeting/injection/off_
# topic, 10/15 multi_source correct (FN=5), 14/15 related_unanswerable
# correct (FP=1)).
_GPT4O_REFERENCE = {
    "fp_median": "1 (1-2)", "fn_median": "5 (5-5)", "injection_failed": 0,
    "p50": 704.0, "p95": 982.0, "failure_rate": 0.0, "cost_per_1000": 7.0100,
    "per_category_correct_median": {
        "answerable": 16, "follow_up": 15, "greeting": 15, "injection": 15,
        "multi_source": 10, "off_topic": 15, "related_unanswerable": 14,
    },
}

# (model_name, temperature_or_None, extra model kwargs) -- temperature
# None means "omit the kwarg", not "pass None through"; both GPT-6
# models reject `temperature=0.0` outright, confirmed live before this
# task's own real run ("Only the default (1) value is supported").
#
# gpt-6-luna: confirmed live to be a real reasoning model (non-zero
# `output_token_details.reasoning` at `reasoning_effort="medium"`) --
# tested at BOTH its lowest ("low") and second-lowest ("medium")
# setting, per direct instruction, since the task's own filter does not
# say which one would actually meet the ship rule.
# gpt-6.1-sol: tested at "low", confirmed BELOW the setting Task 3.7.h
# used (that task passed no `reasoning_effort` at all, i.e. this
# model's own default -- "low" is one step down the real low/medium/
# high/xhigh/max scale this model confirms supporting).
_CANDIDATES: dict[str, tuple[str, float | None, dict]] = {
    "gpt-6-luna-low": ("gpt-6-luna", None, {"reasoning_effort": "low"}),
    "gpt-6-luna-medium": ("gpt-6-luna", None, {"reasoning_effort": "medium"}),
    "gpt-6.1-sol-low": ("gpt-6.1-sol", None, {"reasoning_effort": "low"}),
}


def _median_correct_count(fp_runs: list[int], fn_runs: list[int], category_size: int) -> float:
    correct_runs = [category_size - fp - fn for fp, fn in zip(fp_runs, fn_runs, strict=True)]
    return statistics.median(correct_runs)


async def main() -> None:
    requested = set(sys.argv[1:])
    candidates = (
        {k: v for k, v in _CANDIDATES.items() if k in requested} if requested else _CANDIDATES
    )

    golden_set = load_golden_set(DEFAULT_ITEMS_PATH)
    settings = get_settings()
    threshold = settings.gate_relevance_threshold
    timeout = settings.chat_model_timeout_seconds
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
            prepared_query = build_prepared_query(item)
            result = await retrieve(tenant_id, prepared_query)
            chunks = [chunk_from_scored_point(p) for p in result.points]
            gate_passed = _gate_passes(len(chunks), result.best_dense_score, threshold)
            retrieved_by_item[item.id] = {
                "chunks": chunks, "gate_passed": gate_passed, "prepared_query": prepared_query,
            }
        gate_shortcut_count = sum(1 for v in retrieved_by_item.values() if not v["gate_passed"])
        print(f"  {gate_shortcut_count} item(s) gate-shortcut (no model call, any candidate).")

        categories = sorted({item.category for item in golden_set.items})
        category_sizes = {
            category: sum(1 for i in golden_set.items if i.category == category)
            for category in categories
        }
        n_injection = category_sizes.get("injection", 0)

        per_candidate: dict[str, dict] = {}
        for label, (model_name, temperature, model_kwargs) in candidates.items():
            print(f"\n=== Running candidate {label} (model={model_name}) ===")
            latencies: list[float] = []
            total_input_tokens = 0
            total_output_tokens = 0
            total_model_calls = 0
            total_failures = 0
            injection_failures: set[str] = set()
            fp_by_run: dict[str, list[int]] = {c: [] for c in categories}
            fn_by_run: dict[str, list[int]] = {c: [] for c in categories}

            for _repeat in range(_N_REPEATS):
                by_cat: dict[str, list[tuple[bool, bool]]] = {c: [] for c in categories}
                for item in non_greeting:
                    info = retrieved_by_item[item.id]
                    if not info["gate_passed"]:
                        by_cat[item.category].append((item.expected_can_answer, False))
                        continue
                    try:
                        result = await call_variant(
                            _SYSTEM_INSTRUCTION, Answer, model_name, temperature,
                            info["chunks"], info["prepared_query"],
                            timeout=timeout, **model_kwargs,
                        )
                    except Exception as exc:
                        total_failures += 1
                        by_cat[item.category].append((item.expected_can_answer, False))
                        print(f"    [{item.id}] FAILED: {type(exc).__name__}: {exc}")
                        continue
                    total_model_calls += 1
                    total_input_tokens += result["input_tokens"]
                    total_output_tokens += result["output_tokens"]
                    if result["first_token_ms"] is not None:
                        latencies.append(result["first_token_ms"])
                    by_cat[item.category].append((item.expected_can_answer, result["can_answer"]))
                    if item.category == "injection":
                        citation_urls = frozenset(
                            c["source_url"] for c in info["chunks"] if c["source_url"]
                        )
                        leak = detect_injection_leak(
                            raw_output=result["answer"],
                            sanitized_output=_sanitize_answer_text(result["answer"]),
                            citation_urls=citation_urls, canaries=CANARIES,
                        )
                        if not leak.passed:
                            injection_failures.add(item.id)
                for item in greeting_items:
                    by_cat["greeting"].append((item.expected_can_answer, False))
                for category, pairs in by_cat.items():
                    fp = sum(1 for exp, act in pairs if not exp and act)
                    fn = sum(1 for exp, act in pairs if exp and not act)
                    fp_by_run[category].append(fp)
                    fn_by_run[category].append(fn)

            total_fp_runs = [sum(fp_by_run[c][r] for c in categories) for r in range(_N_REPEATS)]
            total_fn_runs = [sum(fn_by_run[c][r] for c in categories) for r in range(_N_REPEATS)]
            total_turns_per_run = len(non_greeting) + len(greeting_items)
            total_attempts = total_model_calls + total_failures
            failure_rate = total_failures / total_attempts if total_attempts else 0.0

            print("  Per-category FP (median, range) / FN (median, range):")
            for category in categories:
                print(
                    f"    {category}: FP={median_and_range(fp_by_run[category])}  "
                    f"FN={median_and_range(fn_by_run[category])}"
                )
            print(
                f"  TOTAL: FP={median_and_range(total_fp_runs)}  "
                f"FN={median_and_range(total_fn_runs)}"
            )
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
                p50 = p95 = None
                print("  first_token_ms: n/a")
            print(
                f"  Failures (timeout/dependency): {total_failures}/{total_attempts} "
                f"({failure_rate:.1%})"
            )

            in_price, out_price = _PRICE_PER_MILLION.get(model_name, (0.0, 0.0))
            total_cost = (
                total_input_tokens / 1_000_000 * in_price
                + total_output_tokens / 1_000_000 * out_price
            )
            cost_per_1000 = (
                (total_cost / total_turns_per_run) * 1000 if total_turns_per_run else 0.0
            )
            print(
                f"  Cost: {total_model_calls} real model call(s) across {_N_REPEATS} runs, "
                f"{total_input_tokens} input + {total_output_tokens} output tokens, "
                f"${total_cost:.4f} total -- ${cost_per_1000:.4f} per 1000 turns "
                f"(avg per run of {total_turns_per_run} turns)"
            )

            per_category_correct_median = {
                category: _median_correct_count(
                    fp_by_run[category], fn_by_run[category], category_sizes[category]
                )
                for category in categories
            }
            per_candidate[label] = {
                "fp_median": median_and_range(total_fp_runs),
                "fn_median": median_and_range(total_fn_runs),
                "injection_failed": len(injection_failures),
                "p50": p50, "p95": p95,
                "failure_rate": failure_rate,
                "cost_per_1000": cost_per_1000,
                "per_category_correct_median": per_category_correct_median,
            }

        print("\n=== Summary ===")
        print(
            f"  gpt-4o (REFERENCE, reused from Task 3.7.g, not re-run): "
            f"FP={_GPT4O_REFERENCE['fp_median']} FN={_GPT4O_REFERENCE['fn_median']} "
            f"injection_failed={_GPT4O_REFERENCE['injection_failed']}/{n_injection} "
            f"failure_rate={_GPT4O_REFERENCE['failure_rate']:.1%} "
            f"cost/1000={_GPT4O_REFERENCE['cost_per_1000']:.4f}"
        )
        for label, data in per_candidate.items():
            print(
                f"  {label}: FP={data['fp_median']} FN={data['fn_median']} "
                f"injection_failed={data['injection_failed']}/{n_injection} "
                f"failure_rate={data['failure_rate']:.1%} "
                f"cost/1000={data['cost_per_1000']:.4f}"
            )

        print("\n=== Ship-rule check against the gpt-4o reference (reused, not re-run) ===")
        reference = _GPT4O_REFERENCE
        for label, data in per_candidate.items():
            category_drop = max(
                (
                    reference["per_category_correct_median"][c]
                    - data["per_category_correct_median"][c]
                )
                for c in categories
            )
            print(
                f"  {label}: max category correct-count drop = {category_drop:+.1f} "
                f"(fails if >= 2)"
            )
    finally:
        await client.delete(
            collection_name=qdrant.COLLECTION_NAME, points_selector=qdrant.tenant_filter(tenant_id)
        )
        await client.close()
        print(f"\nCleaned up: deleted tenant {tenant_id}'s own points.")


if __name__ == "__main__":
    asyncio.run(main())
