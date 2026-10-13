#!/usr/bin/env python3
# backend/scripts/manual_research_followup_full_turn_latency.py
# Task 3.5.e PART 1 (opt-in, real OpenAI + real Qdrant + real test-db;
# NEVER run by pytest/CI -- not under tests/, no test_ prefix, not
# imported by anything). Measures real follow-up-turn latency through
# the REAL, unmodified graph (run_turn()/read_state()), now that `prepare`
# is a real node (Task 3.5.c, built as JOINING -- see PROJECT_SPEC.md's
# own Step 3.5 decision log, 2026-10-13).
#
# Run it by hand, same prerequisites as manual_research_full_turn_
# latency.py's own header comment (same env-redirect story, same reason
# NOT to reuse tests/conftest.py's reset_checkpoint_env() wholesale):
#
#   cd backend
#   make test-db && make test-qdrant
#   export TEST_DATABASE_URL=postgresql+psycopg://widgetplatform:change-me@127.0.0.1:55432/widgetplatform_test
#   export TEST_QDRANT_URL=http://127.0.0.1:56333
#   export TEST_QDRANT_API_KEY=test-qdrant-key
#   export TEST_QDRANT_READ_ONLY_API_KEY=test-qdrant-readonly-key
#   export OPENAI_API_KEY=sk-...   # a REAL key, plus APP_ENV/JWT_SIGNING_KEY/
#   # DB_CONNECTION_ENCRYPTION_KEY/ADMIN_API_KEY/API_HOST/API_PORT already set
#   python scripts/manual_research_followup_full_turn_latency.py
#
# Cost: ~40 real turns (30 two-turn conversations' own turn 2 + 10
# standalone first turns; each turn is 1 real dense-embedding call + 1
# real gpt-4o-mini call) -- a few cents at published pricing.
#
# Cleans up after itself: seeds into the REAL app.qdrant.COLLECTION_NAME
# on the disposable test-qdrant INSTANCE (matching manual_research_
# full_turn_latency.py's own established precedent -- _retrieve_node
# calls retrieve(tenant_id, query) with no collection_name override, so
# there is no way to point production retrieve() at a throwaway
# collection name); deletes every point it seeded, by tenant_id, in a
# try/finally, regardless of how the run ends.
import asyncio
import statistics
import time
import uuid

import pytest
import tiktoken
from qdrant_client import AsyncQdrantClient

from app import qdrant
from app.agent import checkpointer
from app.agent import graph as graph_module
from app.agent.entrypoint import read_state, run_turn
from app.agent.graph import build_graph
from app.auth.dependencies import VerifiedIdentity
from app.config import get_settings
from app.ingest.qdrant_writer import build_payload, build_point, build_point_id, upsert_points
from app.retrieval import service as service_module
from scripts.manual_research_full_turn_latency import _redirect_test_infra_env
from scripts.manual_research_gate_signal import CORPUS, _topic_slug

_ORG = "https://tenant.example"
_EMBEDDING_VERSION = "followup-latency-research-v1"
_SOURCE_ID = uuid.uuid4()
_N_FOLLOWUP_TURNS = 30
_N_FIRST_TURNS = 10
_CHAT_MODEL_FOR_TOKEN_COUNT = "gpt-4o-mini"  # noqa: S105

# Task 3.5.a's own 10 realistic follow-up pairs (manual_research_followup_
# rewrite.py), carried forward unchanged -- each one genuinely gets
# flagged by needs_join() (confirmed live, Task 3.5.b's own committed
# test suite), so turn 2 in every pair below really does exercise the
# join, not just a hopeful assumption that it would.
_FOLLOWUP_PAIRS = [
    ("How much does the Pro plan cost per month?", "What about Enterprise?"),
    ("What is your return window for a full refund?", "What if the item arrives damaged?"),
    ("Do you ship internationally?", "How long does that take?"),
    ("What are your customer support hours on weekdays?", "What about on weekends?"),
    ("Is annual billing available?", "Which plans support it?"),
    ("How do I return an item?", "What if I don't have a receipt?"),
    ("How much is standard shipping?", "Is it free over a certain amount?"),
    ("Do you offer expedited shipping?", "How much does that cost?"),
    ("What does the Pro plan include?", "And the Enterprise plan?"),
    ("Can I return a final sale item?", "What about a defective one?"),
]

# First-turn-only queries, matching Task 3.4.g's own "A: clearly
# answerable" group -- same 10 queries that produced the already-recorded
# ~1167ms p50 (post Task 3.4.g's own Answer-schema reorder).
_FIRST_TURN_QUERIES = [
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
]


async def _seed_corpus(write_client: AsyncQdrantClient, tenant_id) -> None:
    from app.ingest.embedding import embed_dense, embed_sparse

    await qdrant.ensure_collection(write_client, qdrant.COLLECTION_NAME)
    texts = [text for _, text in CORPUS]
    dense_vectors = await embed_dense(texts)
    sparse_vectors = await embed_sparse(texts)
    points = [
        build_point(
            build_point_id(_SOURCE_ID, i, _EMBEDDING_VERSION),
            build_payload(
                tenant_id=tenant_id,
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
    await upsert_points(write_client, qdrant.COLLECTION_NAME, points)
    print(f"Seeded {len(points)} chunks into {qdrant.COLLECTION_NAME!r} for tenant {tenant_id}.\n")


async def _delete_tenant_points(write_client: AsyncQdrantClient, tenant_id) -> None:
    await write_client.delete(
        collection_name=qdrant.COLLECTION_NAME,
        points_selector=qdrant.tenant_filter(tenant_id),
    )


class _Instrumentation:
    """Same four monkeypatches as manual_research_full_turn_latency.py's
    own class, PLUS a fifth: `_prepare_node` (Task 3.5.c), timed as the
    wall-clock duration of the REAL function call (it is synchronous, no
    I/O -- a direct wrap is exact, no writer/stream-based trick needed
    the way `first_token_ms` needs one).
    """

    def __init__(self):
        self.turn_start = 0.0
        self.prepare_ms: float | None = None
        self.embedding_ms: float | None = None
        self.qdrant_ms: float | None = None
        self.first_token_ms: float | None = None
        self.prompt_tokens: int | None = None
        self._encoding = tiktoken.encoding_for_model(_CHAT_MODEL_FOR_TOKEN_COUNT)
        self._real_prepare_node = graph_module._prepare_node
        self._real_embed_dense = service_module.embed_dense
        self._real_query_batch_points = AsyncQdrantClient.query_batch_points
        self._real_get_stream_writer = graph_module.get_stream_writer
        self._real_build_messages = graph_module._build_messages

    def reset(self):
        self.turn_start = time.perf_counter()
        self.prepare_ms = None
        self.embedding_ms = None
        self.qdrant_ms = None
        self.first_token_ms = None
        self.prompt_tokens = None

    def install(self, monkeypatch):
        def _timed_prepare_node(state):
            start = time.perf_counter()
            result = self._real_prepare_node(state)
            self.prepare_ms = (time.perf_counter() - start) * 1000
            return result

        async def _timed_embed_dense(texts):
            start = time.perf_counter()
            result = await self._real_embed_dense(texts)
            self.embedding_ms = (time.perf_counter() - start) * 1000
            return result

        async def _timed_query_batch_points(client_self, *args, **kwargs):
            start = time.perf_counter()
            result = await self._real_query_batch_points(client_self, *args, **kwargs)
            self.qdrant_ms = (time.perf_counter() - start) * 1000
            return result

        def _instrumented_get_stream_writer():
            real_writer = self._real_get_stream_writer()

            def wrapped(event):
                if self.first_token_ms is None:
                    self.first_token_ms = (time.perf_counter() - self.turn_start) * 1000
                return real_writer(event)

            return wrapped

        def _counted_build_messages(retrieved_chunks, question):
            messages = self._real_build_messages(retrieved_chunks, question)
            full_text = "\n".join(str(m.content) for m in messages)
            self.prompt_tokens = len(self._encoding.encode(full_text))
            return messages

        monkeypatch.setattr(graph_module, "_prepare_node", _timed_prepare_node)
        monkeypatch.setattr(service_module, "embed_dense", _timed_embed_dense)
        monkeypatch.setattr(AsyncQdrantClient, "query_batch_points", _timed_query_batch_points)
        monkeypatch.setattr(graph_module, "get_stream_writer", _instrumented_get_stream_writer)
        monkeypatch.setattr(graph_module, "_build_messages", _counted_build_messages)


async def _run_followup_turns(graph, identity, instrumentation, n: int) -> list[dict]:
    rows = []
    pairs = (_FOLLOWUP_PAIRS * ((n // len(_FOLLOWUP_PAIRS)) + 1))[:n]
    for i, (previous_question, followup) in enumerate(pairs):
        conversation_id = uuid.uuid4()
        # Turn 1: unmeasured, real -- establishes the "previous visitor
        # question" _prepare_node will find on turn 2.
        async for _chunk in run_turn(
            graph, identity, conversation_id, {"messages": [previous_question]}
        ):
            pass

        # Turn 2: the measured, flagged follow-up.
        instrumentation.reset()
        async for _chunk in run_turn(graph, identity, conversation_id, {"messages": [followup]}):
            pass
        total_ms = (time.perf_counter() - instrumentation.turn_start) * 1000

        snapshot = await read_state(graph, identity, conversation_id)
        row = {
            "followup": followup,
            "prepare_ms": instrumentation.prepare_ms,
            "embedding_ms": instrumentation.embedding_ms,
            "qdrant_ms": instrumentation.qdrant_ms,
            "first_token_ms": instrumentation.first_token_ms,
            "total_ms": total_ms,
            "prompt_tokens": instrumentation.prompt_tokens,
            "can_answer": snapshot.values.get("can_answer"),
        }
        rows.append(row)
        ft = row["first_token_ms"]
        print(
            f"  [followup {i + 1}/{n}] total={total_ms:7.0f}ms "
            f"first_token={'n/a' if ft is None else f'{ft:7.0f}ms'} "
            f"prepare={instrumentation.prepare_ms:5.1f}ms "
            f"embed={instrumentation.embedding_ms:6.0f}ms "
            f"qdrant={instrumentation.qdrant_ms:6.0f}ms "
            f"tokens={row['prompt_tokens']:4} can_answer={row['can_answer']}"
        )
    return rows


async def _run_first_turns(graph, identity, instrumentation, n: int) -> list[dict]:
    rows = []
    for i in range(n):
        query = _FIRST_TURN_QUERIES[i % len(_FIRST_TURN_QUERIES)]
        conversation_id = uuid.uuid4()
        instrumentation.reset()
        async for _chunk in run_turn(graph, identity, conversation_id, {"messages": [query]}):
            pass
        total_ms = (time.perf_counter() - instrumentation.turn_start) * 1000
        snapshot = await read_state(graph, identity, conversation_id)
        row = {
            "query": query,
            "prepare_ms": instrumentation.prepare_ms,
            "embedding_ms": instrumentation.embedding_ms,
            "qdrant_ms": instrumentation.qdrant_ms,
            "first_token_ms": instrumentation.first_token_ms,
            "total_ms": total_ms,
            "can_answer": snapshot.values.get("can_answer"),
        }
        rows.append(row)
        ft = row["first_token_ms"]
        print(
            f"  [first-turn {i + 1}/{n}] total={total_ms:7.0f}ms "
            f"first_token={'n/a' if ft is None else f'{ft:7.0f}ms'} "
            f"prepare={instrumentation.prepare_ms:5.1f}ms can_answer={row['can_answer']}"
        )
    return rows


def _percentile(values: list[float], p: float) -> float:
    return statistics.quantiles(values, n=100, method="inclusive")[int(p) - 1]


def _report(label: str, rows: list[dict], p50_target_ms: float, p95_target_ms: float) -> None:
    print(f"\n=== {label}: {len(rows)} turns ===")
    for field in ("prepare_ms", "embedding_ms", "qdrant_ms", "first_token_ms", "total_ms"):
        values = [r[field] for r in rows if r.get(field) is not None]
        if not values:
            continue
        print(
            f"  {field:16}: p50={_percentile(values, 50):7.1f}ms  "
            f"p95={_percentile(values, 95):7.1f}ms  "
            f"min={min(values):7.1f}ms  max={max(values):7.1f}ms"
        )
    answered = sum(1 for r in rows if r["can_answer"])
    print(f"  can_answer=True : {answered}/{len(rows)}")
    first_token_values = [r["first_token_ms"] for r in rows if r["first_token_ms"] is not None]
    if first_token_values:
        p50 = _percentile(first_token_values, 50)
        p95 = _percentile(first_token_values, 95)
        print(
            f"\n  Target: first-token p50 < {p50_target_ms:.0f}ms, "
            f"p95 < {p95_target_ms:.0f}ms -- measured "
            f"p50={p50:.0f}ms ({'PASS' if p50 < p50_target_ms else 'MISS'}), "
            f"p95={p95:.0f}ms ({'PASS' if p95 < p95_target_ms else 'MISS'})"
        )


async def main() -> None:
    monkeypatch = pytest.MonkeyPatch()
    tenant_id = uuid.uuid4()
    write_client = None
    try:
        _redirect_test_infra_env(monkeypatch)
        write_client = qdrant.get_qdrant_client()
        await _seed_corpus(write_client, tenant_id)

        instrumentation = _Instrumentation()
        instrumentation.install(monkeypatch)

        await checkpointer.open_checkpointer_pool()
        cp = checkpointer.get_checkpointer()
        await cp.setup()
        graph = build_graph(cp)
        identity = VerifiedIdentity(tenant_id=tenant_id, vid=uuid.uuid4(), org=_ORG)

        print(f"Running {_N_FOLLOWUP_TURNS} two-turn conversations (measuring turn 2 only)...")
        followup_rows = await _run_followup_turns(
            graph, identity, instrumentation, _N_FOLLOWUP_TURNS
        )
        _report(
            "Follow-up turns (§17 follow-up: p50 < 2000ms, p95 < 3500ms)",
            followup_rows,
            p50_target_ms=2000,
            p95_target_ms=3500,
        )

        print(f"\nRunning {_N_FIRST_TURNS} standalone first turns (confirming p50 unchanged)...")
        first_turn_rows = await _run_first_turns(graph, identity, instrumentation, _N_FIRST_TURNS)
        _report(
            "First turns (§17 first-turn: p50 < 1500ms, p95 < 3000ms)",
            first_turn_rows,
            p50_target_ms=1500,
            p95_target_ms=3000,
        )
    finally:
        if write_client is not None:
            await _delete_tenant_points(write_client, tenant_id)
            print(
                f"\nCleaned up: deleted tenant {tenant_id}'s own points from "
                f"{qdrant.COLLECTION_NAME!r}."
            )
        await checkpointer.close_checkpointer_pool()
        monkeypatch.undo()
        get_settings.cache_clear()


if __name__ == "__main__":
    asyncio.run(main())
