#!/usr/bin/env python3
# backend/scripts/manual_research_followup_rewrite.py
# Task 3.5.a RESEARCH ONLY (no production code built by this script --
# see PROJECT_SPEC.md's own Step 3.5 decision log, Q5/Q6): a MANUAL,
# OPT-IN script making REAL OpenAI calls (cents of cost) against a REAL,
# DISPOSABLE test-qdrant instance -- NEVER run by pytest/CI (not under
# tests/, no test_ prefix, not imported by anything). Run it by hand:
#
#   cd backend
#   export TEST_QDRANT_URL=http://127.0.0.1:<port>   # from `make test-qdrant`
#   export TEST_QDRANT_API_KEY=...
#   export TEST_QDRANT_READ_ONLY_API_KEY=...
#   export OPENAI_API_KEY=sk-...                      # a REAL key
#   python scripts/manual_research_followup_rewrite.py
#
# Three things this answers, each feeding a real Step 3.5 decision:
#   (1) Retrieval quality: for a genuinely context-dependent follow-up,
#       does the right topic survive in the top-k for (a) the raw
#       follow-up alone, (b) the previous question concatenated with the
#       follow-up, or (c) a real model-rewritten standalone question?
#       Decides Q5's fallback choice (raw vs. concatenation) when a
#       rewrite is attempted but fails/times out.
#   (2) Real rewrite-model latency, N>=20 calls, two candidate models --
#       decides Q6's timeout value and informs which model Step 3.5.c
#       should actually configure.
#   (3) The draft skip-check phrase list (Q2) against the same 10
#       follow-ups plus 15 self-contained questions -- false positives
#       and false negatives, decides whether the drafted list is good
#       enough to carry into 3.5.b's own real classifier.
#
# Reuses Task 3.4.d's own CORPUS (manual_research_gate_signal.py,
# imported, not retyped, so the two scripts' own data can never silently
# drift apart) and the real `retrieve()`/`embed_dense()`/`embed_sparse()`
# production code directly -- this script proves against the REAL
# retrieval path, not a separately maintained copy of it.
import asyncio
import time
import uuid

from scripts._shared import point_test_env_at_env_vars

point_test_env_at_env_vars()  # MUST run before any `app.*` import below

from openai import AsyncOpenAI  # noqa: E402

from app.ingest.embedding import embed_dense, embed_sparse  # noqa: E402
from app.ingest.qdrant_writer import (  # noqa: E402
    build_payload,
    build_point,
    build_point_id,
    upsert_points,
)
from app.qdrant import (  # noqa: E402
    COLLECTION_NAME,
    ensure_collection,
    get_qdrant_client,
    tenant_filter,
)
from app.retrieval.service import retrieve as _retrieve  # noqa: E402
from scripts.manual_research_gate_signal import CORPUS, _topic_slug  # noqa: E402


async def retrieve(*args, **kwargs):
    # This script's own retry wrapper, NOT a production pattern: local
    # Docker-port-forwarded test-qdrant has shown intermittent single-
    # request httpx.ConnectTimeout failures during this research (every
    # request that DID reach the server returned 200, confirmed by
    # reading the container's own access log directly -- never a real
    # Qdrant-side error), most likely a stale pooled connection after the
    # longer gap a preceding real OpenAI call leaves. Retried here, in
    # this throwaway research script only, rather than treated as a
    # finding about the real retrieve()/Settings.retrieval timeout design
    # (unrelated to this task's own scope).
    last_exc = None
    for attempt in range(3):
        try:
            return await _retrieve(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            await asyncio.sleep(0.5 * (attempt + 1))
    raise last_exc

_TENANT_ID = uuid.uuid4()
_SOURCE_ID = uuid.uuid4()
_EMBEDDING_VERSION = "followup-rewrite-research-v1"
_REWRITE_SYSTEM_PROMPT = (
    "Given the conversation history below, rewrite the visitor's latest message as "
    "a single, standalone question that could be understood without the history. "
    "Output ONLY the rewritten question, with no extra text, explanation, or "
    "formatting."
)
_REWRITE_MODELS = ("gpt-4o-mini", "gpt-4.1-nano")

# --- (1) Retrieval quality: 10 genuinely context-dependent follow-ups -------
# (previous_question, follow_up, expected_topic) -- expected_topic matches
# one of CORPUS's own 4 topic labels (Pricing/Returns Policy/Shipping/
# Hours & Support). "Correct" below means: at least one top-k chunk is
# from this topic -- a reasonable proxy, not exact-chunk matching, since
# several chunks per topic could legitimately answer a given follow-up.
_FOLLOWUP_PAIRS = [
    ("How much does the Pro plan cost per month?", "What about Enterprise?", "Pricing"),
    (
        "What is your return window for a full refund?",
        "What if the item arrives damaged?",
        "Returns Policy",
    ),
    ("Do you ship internationally?", "How long does that take?", "Shipping"),
    (
        "What are your customer support hours on weekdays?",
        "What about on weekends?",
        "Hours & Support",
    ),
    ("Is annual billing available?", "Which plans support it?", "Pricing"),
    ("How do I return an item?", "What if I don't have a receipt?", "Returns Policy"),
    ("How much is standard shipping?", "Is it free over a certain amount?", "Shipping"),
    ("Do you offer expedited shipping?", "How much does that cost?", "Shipping"),
    ("What does the Pro plan include?", "And the Enterprise plan?", "Pricing"),
    ("Can I return a final sale item?", "What about a defective one?", "Returns Policy"),
]

# --- (3) Draft skip-check phrase list (Q2) ----------------------------------
_REWRITE_TRIGGER_PHRASES = [
    "it", "they", "them", "that", "those", "this", "that one", "the first one",
    "the other one", "what about", "how about", "and the", "and what about",
]
_SHORT_MESSAGE_WORD_THRESHOLD = 4


def _needs_rewrite(message: str) -> bool:
    normalized = message.strip().lower().rstrip("?.!")
    words = normalized.split()
    phrase_hit = any(
        normalized == phrase
        or normalized.startswith(phrase + " ")
        or f" {phrase} " in f" {normalized} "
        for phrase in _REWRITE_TRIGGER_PHRASES
    )
    return phrase_hit or len(words) <= _SHORT_MESSAGE_WORD_THRESHOLD


# 10 follow-ups (should trigger True) + 15 self-contained questions (should
# trigger False) -- the 15 reuses Task 3.4.d's own "A: clearly answerable"
# group (10) plus 5 more self-contained questions.
_SELF_CONTAINED_QUESTIONS = [
    "How much does the Pro plan cost per month?",
    "What is your return window for a full refund?",
    "How long does standard shipping take within the US?",
    "What are your customer support hours on weekdays?",
    "Can I get free shipping, and what's the order threshold?",
    "Is annual billing available, and which plans support it?",
    "What happens if I receive a damaged item?",
    "Do you ship internationally, and how long does it take?",
    "What are your flagship store's opening hours?",
    "Are prices in USD, and do they include sales tax?",
    "What is included in the Enterprise plan?",
    "Can I exchange an item for a different color?",
    "Do you charge for returns on international orders?",
    "What is the phone support number?",
    "Are live chat hours different for Enterprise customers?",
]


async def _seed_corpus(write_client) -> None:
    await ensure_collection(write_client, COLLECTION_NAME)
    texts = [text for _, text in CORPUS]
    dense_vectors = await embed_dense(texts)
    sparse_vectors = await embed_sparse(texts)

    points = [
        build_point(
            build_point_id(_SOURCE_ID, i, _EMBEDDING_VERSION),
            build_payload(
                tenant_id=_TENANT_ID,
                source_id=_SOURCE_ID,
                source_type="url",
                document_id=_SOURCE_ID,
                source_url=f"https://acme-gadgets.example/{_topic_slug(topic)}",
                file_name=None,
                title=topic,
                heading_path=(topic,),
                chunk_index=i,
                content_hash=f"hash-{i}",
                embedding_model="text-embedding-3-small",
                embedding_version=_EMBEDDING_VERSION,
                text=text,
            ),
            dense_vectors[i],
            sparse_vectors[i],
        )
        for i, (topic, text) in enumerate(CORPUS)
    ]
    await upsert_points(write_client, COLLECTION_NAME, points)
    print(f"Seeded {len(points)} chunks into {COLLECTION_NAME!r} for tenant {_TENANT_ID}.\n")


async def _rewrite(model: str, previous_question: str, followup: str) -> tuple[float, str]:
    client = AsyncOpenAI()
    messages = [
        {"role": "system", "content": _REWRITE_SYSTEM_PROMPT},
        {"role": "user", "content": previous_question},
        {"role": "user", "content": followup},
    ]
    start = time.perf_counter()
    response = await client.chat.completions.create(
        model=model, messages=messages, max_tokens=60, temperature=0,
    )
    elapsed = time.perf_counter() - start
    return elapsed, (response.choices[0].message.content or "").strip()


def _topic_in_top_k(points, expected_topic: str) -> bool:
    return any(point.payload.get("title") == expected_topic for point in points)


async def _part1_retrieval_quality() -> dict[str, list[bool]]:
    print("=== Part 1: retrieval quality (raw vs. concatenated vs. rewritten) ===\n")
    results: dict[str, list[bool]] = {"raw": [], "concat": [], "rewritten": []}
    for previous_question, followup, expected_topic in _FOLLOWUP_PAIRS:
        print(f"  prev: {previous_question!r}")
        print(f"  followup: {followup!r}  (expected topic: {expected_topic})")

        raw_result = await retrieve(_TENANT_ID, followup)
        raw_hit = _topic_in_top_k(raw_result.points, expected_topic)
        results["raw"].append(raw_hit)
        print(
            f"    (a) raw:        hit={raw_hit!s:5}  best_dense_score="
            f"{raw_result.best_dense_score}"
        )

        concat_query = f"{previous_question} {followup}"
        concat_result = await retrieve(_TENANT_ID, concat_query)
        concat_hit = _topic_in_top_k(concat_result.points, expected_topic)
        results["concat"].append(concat_hit)
        print(
            f"    (b) concat:     hit={concat_hit!s:5}  best_dense_score="
            f"{concat_result.best_dense_score}  query={concat_query!r}"
        )

        _, rewritten = await _rewrite("gpt-4o-mini", previous_question, followup)
        rewritten_result = await retrieve(_TENANT_ID, rewritten)
        rewritten_hit = _topic_in_top_k(rewritten_result.points, expected_topic)
        results["rewritten"].append(rewritten_hit)
        print(
            f"    (c) rewritten:  hit={rewritten_hit!s:5}  best_dense_score="
            f"{rewritten_result.best_dense_score}  query={rewritten!r}"
        )
        print()

    for label, hits in results.items():
        print(f"  {label:10}: {sum(hits)}/{len(hits)} correct")
    print()
    return results


async def _part2_rewrite_latency() -> None:
    print("=== Part 2: real rewrite-model latency (N>=20 per model) ===\n")
    # 10 follow-up pairs x 2 repeats = 20 calls per model, cycling the
    # same realistic pairs twice rather than inventing low-value filler
    # queries just to pad N.
    cases = (_FOLLOWUP_PAIRS * 2)[:20]
    for model in _REWRITE_MODELS:
        times = []
        for previous_question, followup, _topic in cases:
            elapsed, rewritten = await _rewrite(model, previous_question, followup)
            times.append(elapsed)
        times.sort()
        p50 = times[len(times) // 2]
        p95 = times[int(len(times) * 0.95) - 1]
        trips_1200ms = sum(1 for t in times if t > 1.2)
        print(
            f"  {model:14}: N={len(times)}  p50={p50 * 1000:.0f}ms  p95={p95 * 1000:.0f}ms  "
            f"min={min(times) * 1000:.0f}ms  max={max(times) * 1000:.0f}ms  "
            f"would-trip-1200ms-timeout={trips_1200ms}/{len(times)}"
        )
    print()


def _part3_phrase_list_check() -> None:
    print("=== Part 3: draft skip-check phrase list -- false positives/negatives ===\n")
    false_negatives = []
    for _prev, followup, _topic in _FOLLOWUP_PAIRS:
        if not _needs_rewrite(followup):
            false_negatives.append(followup)
    false_positives = []
    for question in _SELF_CONTAINED_QUESTIONS:
        if _needs_rewrite(question):
            false_positives.append(question)

    print(f"  False negatives (follow-up wrongly judged self-contained): {len(false_negatives)}/10")
    for text in false_negatives:
        print(f"    - {text!r}")
    print(f"  False positives (self-contained wrongly judged needing rewrite): "
          f"{len(false_positives)}/{len(_SELF_CONTAINED_QUESTIONS)}")
    for text in false_positives:
        print(f"    - {text!r}")
    print()


async def main() -> None:
    write_client = get_qdrant_client()
    try:
        await _seed_corpus(write_client)
        await _part1_retrieval_quality()
        await _part2_rewrite_latency()
        _part3_phrase_list_check()
    finally:
        await write_client.delete(
            collection_name=COLLECTION_NAME,
            points_selector=tenant_filter(_TENANT_ID),
        )
        print(f"Cleaned up: deleted tenant {_TENANT_ID}'s own points.")


if __name__ == "__main__":
    asyncio.run(main())
