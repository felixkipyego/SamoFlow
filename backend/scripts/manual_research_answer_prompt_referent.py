#!/usr/bin/env python3
# backend/scripts/manual_research_answer_prompt_referent.py
# Task 3.7.a follow-up [correctness] (opt-in, real OpenAI + real Qdrant;
# NEVER run by pytest/CI -- not under tests/, no test_ prefix, not
# imported by anything). Measures whether the answer model's own
# `can_answer` rate on a joined follow-up improves when it is ASKED the
# prepared (joined) query instead of the bare follow-up message, and/or
# when sampling temperature is pinned to 0 -- the real diagnosis behind
# this: `_answer_node` (app/agent/graph.py) builds its own prompt from
# `_latest_human_message_text(state["messages"])`, the RAW latest
# message, never `state["prepared_query"]` -- confirmed by reading both
# functions directly before writing this script. The joined query is
# used for retrieval only; the answer model never sees it.
#
# Run it by hand, same prerequisites as this project's other real-infra
# research scripts (TEST_QDRANT_URL/TEST_QDRANT_API_KEY/TEST_QDRANT_
# READ_ONLY_API_KEY, a real OPENAI_API_KEY, the usual Settings-required
# vars already in your shell/.env):
#
#   cd backend
#   make test-qdrant
#   export TEST_QDRANT_URL=http://127.0.0.1:<port>
#   export TEST_QDRANT_API_KEY=...
#   export TEST_QDRANT_READ_ONLY_API_KEY=...
#   export OPENAI_API_KEY=sk-...
#   python scripts/manual_research_answer_prompt_referent.py
#
# Cost: ~160 real gpt-4o-mini calls (N=20 x 4 variants for the expedited-
# shipping pair, N=10 x 2 variants x 4 sanity-check pairs) -- a few
# cents. Cleans up its own seeded tenant points in a `finally`.
import asyncio
import os
import uuid

from scripts._shared import point_test_env_at_env_vars

point_test_env_at_env_vars()  # MUST run before any `app.*` import below

from langchain.chat_models import init_chat_model  # noqa: E402

from app import qdrant  # noqa: E402
from app.agent.graph import Answer, _build_messages, chunk_from_scored_point  # noqa: E402
from app.ingest.qdrant_writer import (  # noqa: E402
    build_payload,
    build_point,
    build_point_id,
    upsert_points,
)
from app.retrieval.service import retrieve  # noqa: E402
from scripts.manual_research_gate_signal import CORPUS, _topic_slug  # noqa: E402

_SOURCE_ID = uuid.uuid4()
_EMBEDDING_VERSION = "answer-prompt-referent-v1"
_CHAT_MODEL = "gpt-4o-mini"

_PRIMARY_PAIR = ("Do you offer expedited shipping?", "How much does that cost?")
_SANITY_PAIRS = [
    ("What is your return window for a full refund?", "What if the item arrives damaged?"),
    ("Do you ship internationally?", "How long does that take?"),
    ("What are your customer support hours on weekdays?", "What about on weekends?"),
    ("How do I return an item?", "What if I don't have a receipt?"),
]


async def _seed(write_client, tenant_id) -> None:
    from app.ingest.embedding import embed_dense, embed_sparse

    await qdrant.ensure_collection(write_client, qdrant.COLLECTION_NAME)
    texts = [t for _, t in CORPUS]
    dense = await embed_dense(texts)
    sparse = await embed_sparse(texts)
    points = [
        build_point(
            build_point_id(_SOURCE_ID, i, _EMBEDDING_VERSION),
            build_payload(
                tenant_id=tenant_id, source_id=_SOURCE_ID, source_type="url",
                document_id=_SOURCE_ID, source_url=f"https://acme-gadgets.example/{_topic_slug(topic)}",
                file_name=None, title=topic, heading_path=(topic,), chunk_index=i,
                content_hash=f"hash-{i}", embedding_model="text-embedding-3-small",
                embedding_version=_EMBEDDING_VERSION, text=text,
            ),
            dense[i], sparse[i],
        )
        for i, (topic, text) in enumerate(CORPUS)
    ]
    await upsert_points(write_client, qdrant.COLLECTION_NAME, points)
    print(f"Seeded {len(points)} chunks for tenant {tenant_id}.\n")


def _model(temperature: float | None):
    kwargs = {"max_retries": 0, "timeout": 20.0}
    if temperature is not None:
        kwargs["temperature"] = temperature
    return init_chat_model(
        _CHAT_MODEL, model_provider="openai", api_key=os.environ["OPENAI_API_KEY"], **kwargs
    )


async def _one_answer_call(retrieved_chunks, question: str, temperature: float | None) -> bool:
    messages = _build_messages(retrieved_chunks, question)
    bound = _model(temperature).bind_tools([Answer], tool_choice="Answer")
    response = await bound.ainvoke(messages)
    args = response.tool_calls[0]["args"]
    return bool(args.get("can_answer"))


async def _run_variant(
    retrieved_chunks, question: str, temperature: float | None, n: int
) -> list[bool]:
    results = []
    for _ in range(n):
        results.append(await _one_answer_call(retrieved_chunks, question, temperature))
    return results


def _rate(results: list[bool]) -> str:
    return f"{sum(results)}/{len(results)} ({100 * sum(results) / len(results):.0f}%)"


async def _retrieve_joined(tenant_id, previous_question: str, followup: str):
    joined = f"{previous_question} {followup}"
    result = await retrieve(tenant_id, joined)
    retrieved_chunks = [chunk_from_scored_point(p) for p in result.points]
    return joined, retrieved_chunks, result.best_dense_score


async def main() -> None:
    write_client = qdrant.get_qdrant_client()
    tenant_id = uuid.uuid4()
    try:
        await _seed(write_client, tenant_id)

        print("=== Primary pair: expedited shipping, N=20 per variant ===")
        prev, followup = _PRIMARY_PAIR
        joined, retrieved_chunks, best_dense_score = await _retrieve_joined(
            tenant_id, prev, followup
        )
        print(f"  joined query: {joined!r}")
        print(f"  best_dense_score: {best_dense_score}")
        print(f"  retrieved_chunks[0]: {retrieved_chunks[0]['text'][:80]!r}\n")

        variants = {
            "(a) current: bare followup, provider-default temperature": (followup, None),
            "(b) prepared query, provider-default temperature": (joined, None),
            "(c) bare followup, temperature=0": (followup, 0.0),
            "(d) prepared query, temperature=0": (joined, 0.0),
        }
        primary_results = {}
        for label, (question, temperature) in variants.items():
            results = await _run_variant(retrieved_chunks, question, temperature, 20)
            primary_results[label] = results
            print(f"  {label}: {_rate(results)}")

        print("\n=== Sanity check: 4 other follow-up pairs, (a) vs (d), N=10 per variant ===")
        for prev2, followup2 in _SANITY_PAIRS:
            joined2, chunks2, score2 = await _retrieve_joined(tenant_id, prev2, followup2)
            print(f"\n  pair: {prev2!r} -> {followup2!r}")
            print(f"    joined: {joined2!r}  best_dense_score={score2}")
            a_results = await _run_variant(chunks2, followup2, None, 10)
            d_results = await _run_variant(chunks2, joined2, 0.0, 10)
            print(f"    (a) bare/default-temp:   {_rate(a_results)}")
            print(f"    (d) prepared/temp=0:     {_rate(d_results)}")
    finally:
        await write_client.delete(
            collection_name=qdrant.COLLECTION_NAME,
            points_selector=qdrant.tenant_filter(tenant_id),
        )
        print(f"\nCleaned up: deleted tenant {tenant_id}'s own points.")


if __name__ == "__main__":
    asyncio.run(main())
