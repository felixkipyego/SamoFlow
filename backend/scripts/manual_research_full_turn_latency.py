#!/usr/bin/env python3
# backend/scripts/manual_research_full_turn_latency.py
# Task 3.4.g PART 1 (opt-in, real OpenAI + real Qdrant + real test-db;
# NEVER run by pytest/CI -- not under tests/, no test_ prefix, not
# imported by anything). Measures full-turn latency through the REAL,
# UNMODIFIED graph (run_turn()/read_state(), the real retrieve/gate/
# answer nodes) -- no node is stubbed or reimplemented here, matching
# manual_check_chat_model_streaming.py's own "prove the real code path,
# not a separately maintained copy" principle.
#
# Run it by hand (needs your own shell/`.env` to already carry a REAL
# OPENAI_API_KEY plus every other Settings-required var -- APP_ENV,
# JWT_SIGNING_KEY, DB_CONNECTION_ENCRYPTION_KEY, ADMIN_API_KEY,
# API_HOST, API_PORT -- this script redirects ONLY DATABASE_URL/
# QDRANT_URL/QDRANT_API_KEY/QDRANT_READ_ONLY_API_KEY, deliberately
# leaving every other var untouched so your real OPENAI_API_KEY is
# never clobbered by tests/conftest.py's own VALID_ENV fake one -- see
# this script's own _redirect_test_infra_env() for why reset_checkpoint_
# env() itself is NOT reused wholesale):
#
#   cd backend
#   make test-db      # separate terminal, or let it already be running
#   make test-qdrant
#   export TEST_DATABASE_URL=postgresql+psycopg://widgetplatform:change-me@127.0.0.1:55432/widgetplatform_test
#   export TEST_QDRANT_URL=http://127.0.0.1:56333
#   export TEST_QDRANT_API_KEY=test-qdrant-key
#   export TEST_QDRANT_READ_ONLY_API_KEY=test-qdrant-readonly-key
#   export OPENAI_API_KEY=sk-...        # a REAL key
#   # plus APP_ENV/JWT_SIGNING_KEY/DB_CONNECTION_ENCRYPTION_KEY/
#   # ADMIN_API_KEY/API_HOST/API_PORT -- any valid-shaped value, unused
#   # by anything this script exercises.
#   python scripts/manual_research_full_turn_latency.py
#
# Cost: 30 real turns x (1 dense query embedding + 1 real gpt-4o-mini
# call, forced single Answer tool) -- a few cents at published pricing.
#
# Corpus: Task 3.4.d's own CORPUS/QUERY_GROUPS["A: clearly answerable"]
# (manual_research_gate_signal.py) -- IMPORTED, not retyped, so both
# scripts' own data can never silently drift apart. Seeded into the
# REAL app.qdrant.COLLECTION_NAME (matching test_agent_graph_retrieve.py's
# own established precedent, confirmed by reading that file directly:
# _retrieve_node calls retrieve(tenant_id, query) with no collection_
# name override, so there is no way to point production retrieve() at a
# throwaway collection name -- the disposable part is the whole test-
# qdrant INSTANCE, not a collection within it). Cleaned up by tenant_id
# at the end, in a try/finally, matching that same file's own convention
# of not leaving seeded data in COLLECTION_NAME permanently.
import asyncio
import os
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
from scripts.manual_research_gate_signal import CORPUS, QUERY_GROUPS, _topic_slug
from tests.conftest import (
    require_test_database,
    require_test_qdrant,
    require_test_qdrant_read_only_key,
)

_ORG = "https://tenant.example"
_EMBEDDING_VERSION = "latency-research-v1"
_SOURCE_ID = uuid.uuid4()
_N_TURNS = 30
_ANSWER_QUERIES = QUERY_GROUPS["A: clearly answerable"]
# Settings.chat_model_primary's own default -- a model name, not a secret.
_CHAT_MODEL_FOR_TOKEN_COUNT = "gpt-4o-mini"  # noqa: S105


def _redirect_test_infra_env(monkeypatch) -> None:
    # Deliberately NOT tests/conftest.py's own reset_checkpoint_env(): that
    # helper's set_valid_env(monkeypatch, VALID_ENV, ...) call sets EVERY
    # VALID_ENV key, including a FAKE OPENAI_API_KEY ("test-openai-key")
    # -- fine for its own real callers (they stub embed_dense()/the chat
    # model, so no real OpenAI call is ever made), but this script makes
    # REAL OpenAI calls and would silently 401 if its own real key were
    # clobbered this way. Redirects ONLY the three infra vars this
    # script's own disposable test-db/test-qdrant actually need;
    # everything else (OPENAI_API_KEY, JWT_SIGNING_KEY,
    # DB_CONNECTION_ENCRYPTION_KEY, ADMIN_API_KEY, APP_ENV, API_HOST,
    # API_PORT) is read from the real environment, unmodified.
    real_openai_key = os.environ.get("OPENAI_API_KEY")
    if not real_openai_key or real_openai_key == "test-openai-key":  # noqa: S105
        raise SystemExit(
            "OPENAI_API_KEY must be set to a REAL key in your shell before running this "
            "script (this is NOT redirected the way QDRANT_*/DATABASE_URL are -- see this "
            "script's own header comment)."
        )
    database_url = require_test_database()
    qdrant_url, qdrant_api_key = require_test_qdrant()
    qdrant_read_only_key = require_test_qdrant_read_only_key()
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("QDRANT_URL", qdrant_url)
    monkeypatch.setenv("QDRANT_API_KEY", qdrant_api_key)
    monkeypatch.setenv("QDRANT_READ_ONLY_API_KEY", qdrant_read_only_key)
    get_settings.cache_clear()


async def _seed_corpus(write_client: AsyncQdrantClient, tenant_id) -> None:
    await qdrant.ensure_collection(write_client, qdrant.COLLECTION_NAME)
    from app.ingest.embedding import embed_dense, embed_sparse

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
    """Monkeypatches four real call sites, in THIS process only, to
    recover per-turn sub-timings that `run_turn()`'s own yielded chunks
    cannot provide today -- confirmed live, not assumed: `_STREAM_MODE`
    (app/agent/entrypoint.py) is the single string `"updates"`, NOT a
    list including `"custom"`, so `_answer_node`'s own per-token writer
    events (Task 3.4.b) are produced but never surface through run_turn()
    as currently wired (the exact gap the already-recorded §9 marker,
    owner Step 4.1, names). Tapped directly at the writer callback
    instead -- the same kind of direct instrumentation Task 3.4.b's own
    manual script already used for a model-call-only measurement.

    "Time to first VISIBLE answer text" is NOT measured as a separate
    number from "time to first token event": confirmed live by reading
    _answer_node (app/agent/graph.py) directly -- writer({"type":
    "token", ...}) is called exactly once per call site (line 853), only
    ever with a non-empty sanitized_delta (the "ZERO answer-text bytes
    emitted before can_answer is known" rule plus _sanitize_answer_text()
    never producing empty output for non-empty input, Task 3.4.c's own
    finding) -- so the first writer() call always already carries real
    visible text. Reported as one number, with this reasoning stated
    explicitly, rather than silently reporting two identical numbers.
    """

    def __init__(self):
        self.turn_start = 0.0
        self.embedding_ms: float | None = None
        self.qdrant_ms: float | None = None
        self.first_token_ms: float | None = None
        self.prompt_tokens: int | None = None
        self._encoding = tiktoken.encoding_for_model(_CHAT_MODEL_FOR_TOKEN_COUNT)
        self._real_embed_dense = service_module.embed_dense
        self._real_query_batch_points = AsyncQdrantClient.query_batch_points
        self._real_get_stream_writer = graph_module.get_stream_writer
        self._real_build_messages = graph_module._build_messages

    def reset(self):
        self.turn_start = time.perf_counter()
        self.embedding_ms = None
        self.qdrant_ms = None
        self.first_token_ms = None
        self.prompt_tokens = None

    def install(self, monkeypatch):
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

        monkeypatch.setattr(service_module, "embed_dense", _timed_embed_dense)
        monkeypatch.setattr(AsyncQdrantClient, "query_batch_points", _timed_query_batch_points)
        monkeypatch.setattr(graph_module, "get_stream_writer", _instrumented_get_stream_writer)
        monkeypatch.setattr(graph_module, "_build_messages", _counted_build_messages)


async def _run_n_turns(graph, identity, instrumentation, n: int, label: str) -> list[dict]:
    rows = []
    for i in range(n):
        query = _ANSWER_QUERIES[i % len(_ANSWER_QUERIES)]
        conversation_id = uuid.uuid4()
        instrumentation.reset()
        async for _chunk in run_turn(graph, identity, conversation_id, {"messages": [query]}):
            pass
        total_ms = (time.perf_counter() - instrumentation.turn_start) * 1000

        snapshot = await read_state(graph, identity, conversation_id)
        row = {
            "query": query,
            "embedding_ms": instrumentation.embedding_ms,
            "qdrant_ms": instrumentation.qdrant_ms,
            "first_token_ms": instrumentation.first_token_ms,
            "total_ms": total_ms,
            "prompt_tokens": instrumentation.prompt_tokens,
            "can_answer": snapshot.values.get("can_answer"),
            "best_dense_score": snapshot.values.get("best_dense_score"),
        }
        rows.append(row)
        ft = row["first_token_ms"]
        print(
            f"  [{label} {i + 1}/{n}] total={total_ms:7.0f}ms "
            f"first_token={'n/a' if ft is None else f'{ft:7.0f}ms'} "
            f"embed={instrumentation.embedding_ms:6.0f}ms "
            f"qdrant={instrumentation.qdrant_ms:6.0f}ms "
            f"tokens={row['prompt_tokens']:4} can_answer={row['can_answer']}"
        )
    return rows


def _percentile(values: list[float], p: float) -> float:
    return statistics.quantiles(values, n=100, method="inclusive")[int(p) - 1]


def _report(label: str, rows: list[dict]) -> None:
    print(f"\n=== {label}: {len(rows)} turns ===")
    for field in ("embedding_ms", "qdrant_ms", "first_token_ms", "total_ms"):
        values = [r[field] for r in rows if r[field] is not None]
        if not values:
            continue
        print(
            f"  {field:16}: p50={_percentile(values, 50):7.0f}ms  "
            f"p95={_percentile(values, 95):7.0f}ms  "
            f"min={min(values):7.0f}ms  max={max(values):7.0f}ms"
        )
    tokens = [r["prompt_tokens"] for r in rows if r["prompt_tokens"] is not None]
    if tokens:
        print(
            f"  prompt_tokens   : min={min(tokens)} max={max(tokens)} "
            f"mean={statistics.mean(tokens):.0f}"
        )
    answered = sum(1 for r in rows if r["can_answer"])
    print(f"  can_answer=True : {answered}/{len(rows)}")
    first_token_values = [r["first_token_ms"] for r in rows if r["first_token_ms"] is not None]
    if first_token_values:
        p50 = _percentile(first_token_values, 50)
        p95 = _percentile(first_token_values, 95)
        print(
            f"\n  docs/SPEC.md §17: first-token p50 < 1500ms, p95 < 3000ms -- "
            f"measured p50={p50:.0f}ms ({'PASS' if p50 < 1500 else 'MISS'}), "
            f"p95={p95:.0f}ms ({'PASS' if p95 < 3000 else 'MISS'})"
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

        print(f"Running {_N_TURNS} real turns, default Settings.retrieval_k...")
        baseline_rows = await _run_n_turns(graph, identity, instrumentation, _N_TURNS, "baseline")
        _report("Baseline (default retrieval_k)", baseline_rows)

        baseline_first_token = [r["first_token_ms"] for r in baseline_rows if r["first_token_ms"]]
        p50 = _percentile(baseline_first_token, 50) if baseline_first_token else None
        if p50 is not None and p50 >= 1500:
            print(
                "\np50 MISSED the §17 target -- running ONE cheap lever experiment: "
                "retrieval_k 8 -> 4 (smaller prompt), 10 turns, not changing any default."
            )
            monkeypatch.setenv("RETRIEVAL_K", "4")
            get_settings.cache_clear()
            lever_rows = await _run_n_turns(graph, identity, instrumentation, 10, "k=4")
            _report("Lever experiment: retrieval_k=4", lever_rows)
            monkeypatch.delenv("RETRIEVAL_K")
            get_settings.cache_clear()
    finally:
        if write_client is not None:
            await _delete_tenant_points(write_client, tenant_id)
            print(
                f"\nCleaned up: deleted tenant {tenant_id}'s own points from "
                f"{qdrant.COLLECTION_NAME!r}."
            )
        await checkpointer.close_checkpointer_pool()
        monkeypatch.undo()


if __name__ == "__main__":
    asyncio.run(main())
