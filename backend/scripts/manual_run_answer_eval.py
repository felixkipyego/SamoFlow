#!/usr/bin/env python3
# backend/scripts/manual_run_answer_eval.py
# Task 3.7.d [SECURITY]: the end-to-end answer eval over the golden set
# -- routing, can_answer, citations, groundedness (calibrated LLM judge)
# and injection resistance. A MANUAL, OPT-IN script making REAL LLM
# calls (real chat-model completions, real dense embeddings, real
# groundedness-judge calls) -- NEVER run by pytest/CI (not under tests/,
# no test_ prefix, not imported by anything except this file's own
# tests, which exercise only the offline-safe cost-estimate/--yes gate).
#
# Run it by hand:
#   cd backend
#   make test-db      # a real Postgres, for the real checkpointer
#   make test-qdrant  # a real Qdrant, for the real retrieve()
#   export TEST_DATABASE_URL=postgresql+psycopg://widgetplatform_owner:change-me-owner@127.0.0.1:55432/widgetplatform_test
#   export TEST_DATABASE_URL_APP=postgresql+psycopg://widgetplatform_app:change-me-app@127.0.0.1:55432/widgetplatform_test
#   export TEST_QDRANT_URL=http://127.0.0.1:56333
#   export TEST_QDRANT_API_KEY=...
#   export TEST_QDRANT_READ_ONLY_API_KEY=...
#   export OPENAI_API_KEY=sk-...                      # a REAL key
#   python scripts/manual_run_answer_eval.py --yes
#
# Crash recovery (Task 3.7.e, item 1b): if a run was killed mid-way (never
# reaching its own `finally` cleanup below), run with --sweep instead --
# deletes every leftover eval-tenant Qdrant point and checkpointer thread,
# reports what it removed, makes no real LLM calls, needs no --yes:
#   python scripts/manual_run_answer_eval.py --sweep
#
# Every non-greeting item goes through the real guarded entry point
# (`run_turn()`, app/agent/entrypoint.py) against a real checkpointer and
# a real Qdrant, seeded from the SAME frozen synthetic corpus 3.7.c
# already built -- `seed_frozen_corpus()` (scripts/retrieval_eval.py) is
# imported and reused directly, never duplicated. Unlike 3.7.c's own
# retrieval-only eval, this CANNOT use a disposable, uniquely-named
# collection: `_retrieve_node` (app/agent/graph.py) calls `retrieve(
# tenant_id, query)` with no `collection_name` override at all (confirmed
# by reading it directly) -- the real graph always queries the real
# default `COLLECTION_NAME`. So this script seeds a disposable TENANT
# inside that real, shared collection instead, and tears down by tenant-
# filtered delete, never `client.delete_collection()` (which the Qdrant
# read-path guard forbids outright, for exactly the reason it would be
# wrong here: that collection is shared).
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

from scripts._shared import point_test_env_at_env_vars

# Deliberately imported BEFORE the env-pointing guard below -- this
# sub-module (scripts/answer_eval_cost.py) only ever imports app.evals.
# golden_set, which touches no Settings/Qdrant/provider SDK at import
# time (confirmed by reading it directly), so it needs none of the
# TEST_QDRANT_*/TEST_DATABASE_URL dance every other import in this file
# does. Split out specifically so it stays offline-testable with zero
# env vars required at all (see tests/test_manual_run_answer_eval.py).
from scripts.answer_eval_cost import (
    INJECTION_REPEATS,
    estimate_cost,
    parse_args,
    require_confirmation,
)


def _point_env_at_test_services() -> None:
    import os
    from urllib.parse import urlsplit

    point_test_env_at_env_vars()  # Qdrant half (shared helper)
    # [SECURITY] L2 (Phase 7 role split, 2026-10-18): TEST_DATABASE_URL is
    # the OWNER role (run_checkpointer_setup()'s own real DDL below);
    # TEST_DATABASE_URL_APP is the APP role (every other live app-code
    # path this script exercises -- open_checkpointer_pool(), the real
    # graph/retrieve() call). Both required now, matching backend/tests/
    # conftest.py's own require_test_database()/require_test_database_app()
    # split.
    required = ("TEST_DATABASE_URL", "TEST_DATABASE_URL_APP")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise SystemExit(f"Missing required env var(s): {', '.join(missing)}.")
    for name in required:
        host = urlsplit(os.environ[name]).hostname
        if host not in {"localhost", "127.0.0.1"}:
            raise SystemExit(
                f"{name} host {host!r} is not localhost/127.0.0.1 -- refusing to run "
                "against a non-local Postgres instance."
            )
    os.environ["DATABASE_URL_MIGRATE"] = os.environ["TEST_DATABASE_URL"]
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL_APP"]


_point_env_at_test_services()  # MUST run before any `app.*` import below

import psycopg  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from qdrant_client import AsyncQdrantClient  # noqa: E402

from app import qdrant  # noqa: E402
from app.agent import checkpointer  # noqa: E402
from app.agent.entrypoint import read_state, run_turn  # noqa: E402
from app.agent.graph import Citation, RetrievedChunk, build_graph  # noqa: E402
from app.auth.dependencies import VerifiedIdentity  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.evals.answer_eval_metrics import (  # noqa: E402
    TurnOutcome,
    can_answer_confusion_by_category,
    can_answer_confusion_matrix,
    citation_hit_rate,
    false_negative_items,
    false_positive_items,
    group_by_category,
    infer_actual_route,
    route_confusion_matrix,
    stopping_stage_breakdown,
    zero_citation_violations,
)
from app.evals.eval_summary import (  # noqa: E402
    format_delta_table,
    load_previous_summary,
    write_summary,
)
from app.evals.golden_set import (  # noqa: E402
    DEFAULT_ITEMS_PATH,
    DEFAULT_SYNTHETIC_CORPUS_PATH,
    GoldenSet,
    GoldenSetItem,
    load_golden_set,
)
from scripts.groundedness_judge import (  # noqa: E402
    CalibrationRow,
    build_judge,
    generate_corrupted_answer,
    judge_turn,
    prune_unlabelled_calibration_files,
    render_context,
    write_key_file,
    write_labelling_csv,
)
from scripts.injection_detectors import CANARIES, detect_injection_leak  # noqa: E402
from scripts.retrieval_eval import (  # noqa: E402
    eval_marker_filter,
    load_and_check_frozen_corpus,
    seed_frozen_corpus,
)

CALIBRATION_TOTAL = 30
CALIBRATION_CORRUPTED = 8
_RESULTS_DIR = Path(__file__).resolve().parents[2] / "evals" / "golden_set" / "results"
# Item 3 (Task 3.7.e): the one COMMITTED summary path this entry point
# owns -- see the `summary`/`previous_summary` block in main() below.
DEFAULT_SUMMARY_PATH = _RESULTS_DIR.parent / "summary_answers.json"
# Task 3.7.e: a small, gitignored-by-directory (evals/golden_set/results/
# is wholesale gitignored, see .gitignore) tracking file -- the ONLY
# durable record of a tenant_id a normal run is still using, written at
# the START of main() below and cleared on CLEAN exit (inside `finally`,
# after cleanup actually succeeds). A crash (SIGKILL, OOM, power loss --
# anything that skips `finally` entirely) leaves the tenant_id behind
# here for `--sweep` to recover checkpointer threads by. The Qdrant side
# of a crash is recovered differently (eval_marker_filter(), independent
# of this file's own survival) -- see run_sweep()'s own docstring.
_TRACKING_FILE = _RESULTS_DIR / ".active_eval_tenants.json"


def _read_tracked_tenants() -> list[str]:
    if not _TRACKING_FILE.exists():
        return []
    return json.loads(_TRACKING_FILE.read_text(encoding="utf-8"))


def _add_tracked_tenant(tenant_id: uuid.UUID) -> None:
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    tenants = _read_tracked_tenants()
    tenants.append(str(tenant_id))
    _TRACKING_FILE.write_text(json.dumps(tenants), encoding="utf-8")


def _remove_tracked_tenant(tenant_id: uuid.UUID) -> None:
    remaining = [t for t in _read_tracked_tenants() if t != str(tenant_id)]
    if remaining:
        _TRACKING_FILE.write_text(json.dumps(remaining), encoding="utf-8")
    elif _TRACKING_FILE.exists():
        _TRACKING_FILE.unlink()


def _require_dev_or_test_environment(settings: Settings) -> None:
    # Item 1c, per direct instruction: "Use the project's existing
    # environment setting if there is one" -- Settings.app_env
    # (app/config.py) IS that existing setting (Literal["development",
    # "test", "production"], confirmed by reading it directly), so this
    # reuses it rather than inventing a second, parallel environment
    # concept. This script seeds/deletes real tenant data in the SHARED
    # production Qdrant collection and makes real paid LLM calls -- a
    # production APP_ENV must refuse outright, not just warn.
    if settings.app_env not in {"development", "test"}:
        raise SystemExit(
            f"APP_ENV={settings.app_env!r} -- this script seeds and deletes real tenant "
            "data in the shared Qdrant collection and makes real, paid LLM calls. It "
            "refuses to run unless Settings.app_env is 'development' or 'test'."
        )


# --- per-item real run ------------------------------------------------------


def _citation_urls(citations: tuple[Citation, ...]) -> frozenset[str]:
    return frozenset(c["url"] for c in citations)


def _as_retrieved_chunks(texts: tuple[str, ...]) -> list[RetrievedChunk]:
    """Rebuilds a minimal `RetrievedChunk` list from just the cited TEXT
    -- the judge's own prompt (via `render_context()`) only ever uses
    `text`, matching `_build_messages()`'s own documented "deliberately
    does NOT include source_url/heading_path/document_id" design; the
    other fields are structurally required by the TypedDict but never
    read for this purpose.
    """
    return [
        {"document_id": "x", "text": text, "source_url": None, "heading_path": [], "score": 0.0}
        for text in texts
    ]


async def _run_one_turn(
    graph, identity: VerifiedIdentity, conversation_id: uuid.UUID, message: str
) -> dict:
    async for _ in run_turn(graph, identity, conversation_id, {"messages": [message]}):
        pass
    snapshot = await read_state(graph, identity, conversation_id)
    return snapshot.values


def _final_ai_text(values: dict) -> str:
    for message in reversed(values["messages"]):
        if isinstance(message, AIMessage):
            return str(message.content)
    return ""


def _build_outcome(item: GoldenSetItem, values: dict, item_id_suffix: str = "") -> TurnOutcome:
    final_text = _final_ai_text(values)
    can_answer = bool(values.get("can_answer", False))
    citations = tuple(values.get("citations") or ())
    retrieved_chunks: list[RetrievedChunk] = values.get("retrieved_chunks") or []
    return TurnOutcome(
        item_id=item.id + item_id_suffix,
        category=item.category,
        expected_route=item.expected_route,
        expected_can_answer=item.expected_can_answer,
        expected_evidence_passages=tuple(s.evidence_passage for s in item.expected_sources),
        actual_route=infer_actual_route(final_text, can_answer),
        actual_can_answer=can_answer,
        cited_chunk_labels=tuple(values.get("cited_chunk_labels") or ()),
        citations=citations,
        retrieved_chunk_texts=tuple(c["text"] for c in retrieved_chunks),
        final_text=final_text,
        best_dense_score=values.get("best_dense_score"),
    )


async def run_answer_eval(golden_set: GoldenSet, graph, tenant_id: uuid.UUID) -> dict:
    """Runs every non-greeting, non-escalation item through the real
    `run_turn()` -- greeting items too, actually (cheap, no LLM call,
    and `expected_route="greeting"` is only provable by actually routing
    one through). Returns a dict with every raw ingredient the caller
    needs: `outcomes` (one per item, or per repeat for injection),
    `thread_ids` (for cleanup), and `injection_raw_outputs` (for the
    detector pass).
    """
    outcomes: list[TurnOutcome] = []
    thread_ids: list[str] = []
    injection_results: dict[str, list[tuple[str, str, frozenset[str]]]] = {}

    for item in golden_set.items:
        if item.category == "injection":
            injection_results[item.id] = []
            for repeat in range(INJECTION_REPEATS):
                vid = uuid.uuid4()
                conversation_id = uuid.uuid4()
                identity = VerifiedIdentity(tenant_id=tenant_id, vid=vid, org="https://eval.example")
                thread_ids.append(f"{tenant_id}:{vid}:{conversation_id}")
                values = await _run_one_turn(graph, identity, conversation_id, item.question)
                outcome = _build_outcome(item, values, item_id_suffix=f"#{repeat}")
                outcomes.append(outcome)
                # Both slots get the SAME text, confirmed necessary, not
                # an oversight: `_answer_node`'s own docstring states the
                # sanitised output is "accumulated into the exact same
                # variable that is both streamed... and persisted" --
                # the real guarded entry point (`run_turn()`/`read_
                # state()`) structurally never exposes a pre-sanitisation
                # value anywhere outside that node's own closure. A real
                # finding, recorded plainly in this task's own report:
                # this is a GOOD security property (no raw-text leak path
                # exists at all), but it does mean a live run through the
                # real entry point cannot exercise a genuine raw-vs-
                # sanitised distinction the way the offline detector unit
                # tests (hand-built strings) deliberately do.
                injection_results[item.id].append(
                    (outcome.final_text, outcome.final_text, _citation_urls(outcome.citations))
                )
            continue

        vid = uuid.uuid4()
        conversation_id = uuid.uuid4()
        identity = VerifiedIdentity(tenant_id=tenant_id, vid=vid, org="https://eval.example")
        thread_ids.append(f"{tenant_id}:{vid}:{conversation_id}")

        for prior in item.prior_turns:
            await _run_one_turn(graph, identity, conversation_id, prior)

        values = await _run_one_turn(graph, identity, conversation_id, item.question)
        outcomes.append(_build_outcome(item, values))

    return {
        "outcomes": outcomes,
        "thread_ids": thread_ids,
        "injection_results": injection_results,
    }


# --- injection detection pass ------------------------------------------------


def run_injection_detectors(
    injection_results: dict[str, list[tuple[str, str, frozenset[str]]]],
) -> dict[str, bool]:
    """ANY single repeat succeeding (leaking) fails that item -- per
    direct instruction. Returns item_id -> passed (True = held).
    """
    passed_by_item: dict[str, bool] = {}
    for item_id, repeats in injection_results.items():
        item_passed = True
        for raw, sanitized, citation_urls in repeats:
            result = detect_injection_leak(
                raw_output=raw, sanitized_output=sanitized, citation_urls=citation_urls,
                canaries=CANARIES,
            )
            if not result.passed:
                item_passed = False
        passed_by_item[item_id] = item_passed
    return passed_by_item


# --- calibration set selection ------------------------------------------------


def select_calibration_rows(
    outcomes: list[TurnOutcome], golden_set: GoldenSet
) -> list[tuple[TurnOutcome, str, str]]:
    """Picks CALIBRATION_TOTAL rows: CALIBRATION_CORRUPTED corrupted
    variants of real can_answer=true answers, the rest real (uncorrupted)
    answers -- returns (outcome, answer_text_to_judge, origin) triples.
    Excludes injection-category outcomes (the judge reading poisoned
    context is a SEPARATE, already-named risk, not the calibration set's
    own job to characterize).
    """
    items_by_id = {item.id: item for item in golden_set.items}
    candidates = [
        o
        for o in outcomes
        if o.actual_can_answer and items_by_id[o.item_id.split("#")[0]].category != "injection"
    ]
    real_count = max(CALIBRATION_TOTAL - CALIBRATION_CORRUPTED, 0)
    rows: list[tuple[TurnOutcome, str, str]] = []
    for outcome in candidates[:real_count]:
        rows.append((outcome, outcome.final_text, "real"))
    corrupted_candidates = candidates[real_count : real_count + CALIBRATION_CORRUPTED]
    for variant, outcome in enumerate(corrupted_candidates):
        corrupted_text, kind = generate_corrupted_answer(outcome.final_text, variant)
        rows.append((outcome, corrupted_text, f"corrupted:{kind}"))
    return rows


# --- --sweep: crash recovery --------------------------------------------------


async def run_sweep() -> None:
    """Recovers leftover eval data from a run that was killed mid-way
    (SIGKILL, OOM, power loss -- anything that skips the `finally` block
    in `main()` below entirely, so the normal cleanup never ran). Two
    independent recovery paths, on purpose:

      - Qdrant points: ANY point carrying EVAL_MARKER_FIELD, across
        every tenant_id, found via `eval_marker_filter()`
        (scripts/retrieval_eval.py) -- robust even if the tracking file
        below was ALSO lost (e.g. the whole machine died, not just this
        process), since it needs no durable state of its own at all.
      - Checkpointer threads: LangGraph's own checkpoint rows have no
        payload-marker equivalent, so these are recovered from
        `_TRACKING_FILE` instead -- the durable record of every
        tenant_id a run had started but not yet cleanly finished. Each
        tracked tenant_id's own thread_ids are recovered with a raw
        `thread_id LIKE '<tenant_id>:%'` query (thread_ids are always
        built as f"{tenant_id}:{vid}:{conversation_id}" in
        `run_answer_eval()` above), the same raw-psycopg-against-
        checkpoint-tables precedent `tests/conftest.py`'s own
        `reset_checkpoint_env()` already established.

    Reports counts for both and makes no real LLM calls.
    """
    settings = get_settings()
    _require_dev_or_test_environment(settings)

    qdrant_client: AsyncQdrantClient = qdrant.build_qdrant_client(
        settings.qdrant_url, settings.qdrant_api_key_str()
    )
    try:
        marker_filter = eval_marker_filter()
        count_result = await qdrant_client.count(
            collection_name=qdrant.COLLECTION_NAME, count_filter=marker_filter, exact=True
        )
        removed_points = count_result.count
        if removed_points:
            await qdrant_client.delete(
                collection_name=qdrant.COLLECTION_NAME, points_selector=marker_filter
            )
    finally:
        await qdrant_client.close()

    removed_threads = 0
    tracked_tenants = _read_tracked_tenants()
    if tracked_tenants:
        await checkpointer.run_checkpointer_setup()
        await checkpointer.open_checkpointer_pool()
        cp = checkpointer.get_checkpointer()
        try:
            conninfo = checkpointer.checkpointer_conninfo(settings.database_url_str())
            async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
                for tenant_id_str in tracked_tenants:
                    cursor = await conn.execute(
                        "SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id LIKE %s",
                        (f"{tenant_id_str}:%",),
                    )
                    rows = await cursor.fetchall()
                    for (thread_id,) in rows:
                        await cp.adelete_thread(thread_id)
                        removed_threads += 1
        finally:
            await checkpointer.close_checkpointer_pool()
        if _TRACKING_FILE.exists():
            _TRACKING_FILE.unlink()

    print(
        f"--sweep: removed {removed_points} eval-marked Qdrant point(s) and "
        f"{removed_threads} checkpointer thread(s) tracked against "
        f"{len(tracked_tenants)} crashed-run tenant(s)."
    )


# --- main orchestration -------------------------------------------------------


async def main() -> None:
    args = parse_args(sys.argv[1:])
    if args.sweep:
        await run_sweep()
        return

    golden_set = load_golden_set(DEFAULT_ITEMS_PATH)
    estimate = estimate_cost(golden_set)
    require_confirmation(estimate, args.yes)

    settings = get_settings()
    _require_dev_or_test_environment(settings)
    corpus = load_and_check_frozen_corpus(DEFAULT_SYNTHETIC_CORPUS_PATH)
    qdrant_client: AsyncQdrantClient = qdrant.build_qdrant_client(
        settings.qdrant_url, settings.qdrant_api_key_str()
    )
    tenant_id = uuid.uuid4()
    _add_tracked_tenant(tenant_id)

    await checkpointer.run_checkpointer_setup()
    await checkpointer.open_checkpointer_pool()
    cp = checkpointer.get_checkpointer()
    graph = build_graph(cp)
    thread_ids: list[str] = []

    try:
        print(f"Seeding frozen corpus into tenant {tenant_id} ({qdrant.COLLECTION_NAME})...")
        await seed_frozen_corpus(qdrant_client, qdrant.COLLECTION_NAME, tenant_id, corpus)

        print("Running every golden-set item through the real guarded entry point...")
        run_result = await run_answer_eval(golden_set, graph, tenant_id)
        thread_ids.extend(run_result["thread_ids"])
        outcomes: list[TurnOutcome] = run_result["outcomes"]

        print("Running deterministic injection detectors...")
        injection_passed = run_injection_detectors(run_result["injection_results"])
        injection_failures = [item_id for item_id, ok in injection_passed.items() if not ok]

        print("Running the groundedness judge on can_answer=true, non-injection turns...")
        judge = build_judge(settings.openai_api_key_str())
        items_by_id = {item.id: item for item in golden_set.items}
        groundedness_by_item: dict[str, str] = {}
        for outcome in outcomes:
            base_id = outcome.item_id.split("#")[0]
            if not outcome.actual_can_answer or items_by_id[base_id].category == "injection":
                continue
            score = await judge_turn(
                judge,
                _as_retrieved_chunks(outcome.retrieved_chunk_texts),
                items_by_id[base_id].question,
                outcome.final_text,
            )
            groundedness_by_item[outcome.item_id] = score.label or "unknown"

        print("Exporting the calibration set...")
        calibration_rows = select_calibration_rows(outcomes, golden_set)
        rows = []
        for index, (outcome, answer_text, origin) in enumerate(calibration_rows):
            base_id = outcome.item_id.split("#")[0]
            question = items_by_id[base_id].question
            context_chunks = _as_retrieved_chunks(outcome.retrieved_chunk_texts)
            score = await judge_turn(judge, context_chunks, question, answer_text)
            rows.append(
                CalibrationRow(
                    row_id=f"row-{index:02d}",
                    question=question,
                    context=render_context(context_chunks, question),
                    answer=answer_text,
                    origin=origin,
                    judge_label=score.label or "unknown",
                )
            )

        _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        # Task 3.7.g, item 4: prune stale UNLABELLED calibration files
        # before writing this run's own new one, so they don't pile up
        # forever -- never touches a file with a real filled-in label
        # (Felix's own work).
        pruned = prune_unlabelled_calibration_files(_RESULTS_DIR)
        if pruned:
            print(f"Pruned {len(pruned)} stale unlabelled calibration file(s): {pruned}")
        run_stamp = time.strftime("%Y%m%d-%H%M%S")
        labelling_path = _RESULTS_DIR / f"calibration_{run_stamp}.csv"
        key_path = _RESULTS_DIR / f"calibration_{run_stamp}.key.json"
        write_labelling_csv(rows, labelling_path)
        write_key_file(rows, key_path)

        # --- deterministic metrics, printed as tables -------------------
        print("\n=== Route confusion matrix (expected -> actual) ===")
        print(json.dumps(route_confusion_matrix(outcomes), indent=2))

        # Task 3.7.f, item 0: the route confusion matrix's own "answer"
        # row conflates a real can_answer false negative with a correct
        # related_unanswerable refusal (both render the identical fixed
        # reply text) -- stopping_stage_breakdown() separates them by
        # WHERE the turn actually stopped, per category.
        print("\n=== Stopping stage per category (router/gate/can_answer_false/answered) ===")
        stage_breakdown = stopping_stage_breakdown(outcomes, settings.gate_relevance_threshold)
        print(json.dumps(stage_breakdown, indent=2, sort_keys=True))

        print("\n=== can_answer confusion matrix ===")
        confusion = can_answer_confusion_matrix(outcomes)
        print(
            f"TP={confusion.true_positive} FP={confusion.false_positive} "
            f"TN={confusion.true_negative} FN={confusion.false_negative} "
            f"accuracy={confusion.accuracy:.3f}"
        )

        print("\n=== can_answer confusion matrix PER CATEGORY ===")
        confusion_by_category = can_answer_confusion_by_category(outcomes)
        confusion_by_category_summary: dict[str, dict] = {}
        for category in sorted(confusion_by_category):
            cat_confusion = confusion_by_category[category]
            confusion_by_category_summary[category] = {
                "tp": cat_confusion.true_positive, "fp": cat_confusion.false_positive,
                "tn": cat_confusion.true_negative, "fn": cat_confusion.false_negative,
            }
            print(
                f"  {category}: TP={cat_confusion.true_positive} "
                f"FP={cat_confusion.false_positive} TN={cat_confusion.true_negative} "
                f"FN={cat_confusion.false_negative}"
            )

        print("\n=== can_answer FALSE POSITIVES (expected false, got true) ===")
        for outcome in false_positive_items(outcomes):
            base_id = outcome.item_id.split("#")[0]
            question = items_by_id[base_id].question
            print(f"  [{outcome.item_id}] ({outcome.category}) question: {question!r}")
            print(
                f"    can_answer={outcome.actual_can_answer} "
                f"cited={list(outcome.cited_chunk_labels)}"
            )
            print(f"    answer: {outcome.final_text!r}")

        print("\n=== can_answer FALSE NEGATIVES (expected true, got false) ===")
        for outcome in false_negative_items(outcomes):
            base_id = outcome.item_id.split("#")[0]
            question = items_by_id[base_id].question
            prompted_labels = [f"c{i + 1}" for i in range(len(outcome.retrieved_chunk_texts))]
            print(f"  [{outcome.item_id}] ({outcome.category}) question: {question!r}")
            print(
                f"    can_answer={outcome.actual_can_answer} "
                f"prompted_chunks={prompted_labels} cited={list(outcome.cited_chunk_labels)}"
            )
            print(f"    answer: {outcome.final_text!r}")

        print("\n=== Citation hit rate per category ===")
        citation_hit_rate_by_category: dict[str, float] = {}
        for category, cat_outcomes in group_by_category(outcomes).items():
            applicable = [o for o in cat_outcomes if o.actual_can_answer]
            if not applicable:
                print(f"  {category}: n/a (no can_answer=true turns)")
                continue
            citation_hit_rate_by_category[category] = citation_hit_rate(applicable)
            print(f"  {category}: {citation_hit_rate(applicable):.3f} (n={len(applicable)})")

        print("\n=== Zero-citation rule violations ===")
        violations = zero_citation_violations(outcomes)
        print(violations if violations else "none")

        print("\n=== Injection resistance (zero tolerance) ===")
        for item_id, ok in injection_passed.items():
            print(f"  {item_id}: {'HELD' if ok else 'FAILED'}")
        print(f"  {len(injection_failures)} of {len(injection_passed)} items FAILED")

        print("\n=== Groundedness judge (UNCALIBRATED until Felix labels) ===")
        labels = list(groundedness_by_item.values())
        grounded_share = (
            labels.count("grounded") / len(labels) if labels else float("nan")
        )
        print(f"  grounded share: {grounded_share:.3f} (n={len(labels)})")
        print(f"  Calibration export: {labelling_path}")
        print(f"  Calibration key (do not share with the labeller): {key_path}")

        summary = {
            "route_confusion": route_confusion_matrix(outcomes),
            "stopping_stage": stage_breakdown,
            "can_answer_confusion": {
                "tp": confusion.true_positive, "fp": confusion.false_positive,
                "tn": confusion.true_negative, "fn": confusion.false_negative,
                "accuracy": confusion.accuracy,
            },
            "can_answer_confusion_by_category": confusion_by_category_summary,
            "citation_hit_rate": citation_hit_rate_by_category,
            "zero_citation_violation_count": len(violations),
            "injection_passed": injection_passed,
            "groundedness_uncalibrated_grounded_share": grounded_share,
        }

        results_path = _RESULTS_DIR / f"run_{run_stamp}.json"
        results_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"\nFull results written to {results_path}")

        # Item 3 (Task 3.7.e): the one COMMITTED (never gitignored) copy of
        # this same metrics-only summary -- always this exact path, so a
        # `git diff` on it shows the run-over-run change, and a delta
        # table against whatever was previously committed there prints
        # immediately, matching the eval rule's own "paste both delta
        # tables" requirement.
        previous_summary = load_previous_summary(DEFAULT_SUMMARY_PATH)
        print("\n=== Delta vs. previously committed summary ===")
        print(format_delta_table(previous_summary, summary))
        write_summary(DEFAULT_SUMMARY_PATH, summary)
        print(
            f"Committed summary written to {DEFAULT_SUMMARY_PATH} -- "
            "commit it to track this run."
        )

        if injection_failures:
            print(
                f"\nSECURITY FINDING: {len(injection_failures)} injection item(s) leaked: "
                f"{injection_failures}"
            )
    finally:
        print(f"\nCleaning up: {len(thread_ids)} checkpointer thread(s), 1 tenant's own points...")
        await qdrant_client.delete(
            collection_name=qdrant.COLLECTION_NAME, points_selector=qdrant.tenant_filter(tenant_id)
        )
        for thread_id in thread_ids:
            await cp.adelete_thread(thread_id)
        await qdrant_client.close()
        await checkpointer.close_checkpointer_pool()
        # Only cleared once cleanup above has actually run -- a crash
        # BEFORE this point leaves the tenant_id tracked, exactly so
        # `--sweep` can find it later (see run_sweep()'s own docstring).
        _remove_tracked_tenant(tenant_id)


if __name__ == "__main__":
    asyncio.run(main())
