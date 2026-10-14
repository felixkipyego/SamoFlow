# backend/scripts/answer_eval_cost.py
# Task 3.7.d: the cost-estimate + --yes gate for manual_run_answer_eval.py
# -- split into its own module, deliberately, so it is offline-testable
# with NO env-pointing dance at all: `app.evals.golden_set` touches no
# Settings/Qdrant/provider SDK at import time (confirmed by reading it
# directly -- plain pydantic/yaml), unlike manual_run_answer_eval.py's
# own top-level imports, which DO need TEST_QDRANT_*/TEST_DATABASE_URL
# pointed first. Importing THIS module needs none of that.
import argparse
import sys
from dataclasses import dataclass

from app.evals.golden_set import GoldenSet

INJECTION_REPEATS = 3

# Rough, named, not precision-engineered: deliberately round, order-of-
# magnitude placeholders stated as such, not a claim of billing
# accuracy. `_EST_USD_PER_CHAT_CALL` updated at Task 3.7.g (2026-10-14)
# to track `Settings.chat_model_primary`'s own change ("gpt-4o-mini" ->
# "gpt-4o") -- real measured average from that task's own full-golden-
# set research run ($0.7371 / 264 real gpt-4o calls), not re-derived
# from published per-token pricing a second time. The judge
# (`scripts/groundedness_judge.py`) stays on "gpt-4o-mini" (unchanged,
# cheap) -- it is a SEPARATE model choice from the production primary,
# not re-matched to it automatically; see this task's own §9 marker.
_EST_USD_PER_EMBED_CALL = 0.00002
_EST_USD_PER_CHAT_CALL = 0.0028
_EST_USD_PER_JUDGE_CALL = 0.0004


@dataclass(frozen=True)
class CostEstimate:
    item_count: int
    retrieval_call_count: int
    chat_call_count: int
    judge_call_count: int
    estimated_usd: float


def estimate_cost(golden_set: GoldenSet) -> CostEstimate:
    non_greeting = [item for item in golden_set.items if item.category != "greeting"]
    off_topic = [item for item in non_greeting if item.category == "off_topic"]
    injection = [item for item in non_greeting if item.category == "injection"]
    follow_up = [item for item in non_greeting if item.category == "follow_up"]
    excluded = ("off_topic", "injection", "follow_up")
    other = [item for item in non_greeting if item.category not in excluded]

    # Real turns actually run: off_topic/other get 1 turn each; injection
    # gets INJECTION_REPEATS independent turns; follow_up gets 2 (its own
    # real prior turn, then the real follow-up question).
    total_turns = (
        len(off_topic) + len(injection) * INJECTION_REPEATS + len(follow_up) * 2 + len(other)
    )
    # Every non-greeting turn reaches _retrieve_node (one embedding call);
    # only a turn that clears the gate reaches _answer_node (off_topic
    # turns never do, by design -- that IS the gate's own job).
    retrieval_calls = total_turns
    chat_calls = total_turns - len(off_topic)
    # Judge calls: one per item whose FINAL question is expected to reach
    # a real answer -- injection is deliberately excluded (the judge
    # reads the same poisoned context and is not trusted for those).
    judge_calls = len(
        [
            item
            for item in non_greeting
            if item.expected_can_answer and item.category != "injection"
        ]
    )
    estimated_usd = round(
        retrieval_calls * _EST_USD_PER_EMBED_CALL
        + chat_calls * _EST_USD_PER_CHAT_CALL
        + judge_calls * _EST_USD_PER_JUDGE_CALL,
        3,
    )
    return CostEstimate(
        item_count=len(golden_set.items),
        retrieval_call_count=retrieval_calls,
        chat_call_count=chat_calls,
        judge_call_count=judge_calls,
        estimated_usd=estimated_usd,
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Required to actually make real API calls -- otherwise prints the cost "
        "estimate and exits.",
    )
    # Task 3.7.e, item 1b: a separate, no-real-LLM-calls cleanup mode --
    # deletes every eval-marked Qdrant point (any tenant) and every
    # checkpointer thread left behind by a crashed run, then exits. Lives
    # on this SAME parser (not a second argparse instance) so `--sweep`
    # and `--yes` are mutually visible to argparse's own help text; the
    # caller (manual_run_answer_eval.main()) checks `args.sweep` BEFORE
    # estimating cost/requiring confirmation, since a cleanup pass makes
    # no LLM calls at all and needs neither.
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="Delete all leftover eval-tenant Qdrant points and checkpointer threads from "
        "crashed runs, report what was removed, and exit -- makes no real LLM calls.",
    )
    return parser.parse_args(argv)


def print_cost_estimate(estimate: CostEstimate) -> None:
    print(f"Golden-set items: {estimate.item_count}")
    print(f"Estimated real embedding calls: {estimate.retrieval_call_count}")
    print(f"Estimated real chat-model calls: {estimate.chat_call_count}")
    print(f"Estimated real groundedness-judge calls: {estimate.judge_call_count}")
    print(f"Rough estimated cost: ~${estimate.estimated_usd}")


def require_confirmation(estimate: CostEstimate, yes: bool) -> None:
    print_cost_estimate(estimate)
    if not yes:
        raise SystemExit("Re-run with --yes to actually make these real API calls.")


if __name__ == "__main__":
    # A tiny standalone preview -- "python scripts/answer_eval_cost.py"
    # prints the real cost estimate against the real committed golden
    # set with NO env vars and NO API key needed at all, unlike the full
    # manual_run_answer_eval.py.
    from app.evals.golden_set import DEFAULT_ITEMS_PATH, load_golden_set

    args = parse_args(sys.argv[1:])
    print_cost_estimate(estimate_cost(load_golden_set(DEFAULT_ITEMS_PATH)))
