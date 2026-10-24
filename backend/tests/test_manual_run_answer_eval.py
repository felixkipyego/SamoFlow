# backend/tests/test_manual_run_answer_eval.py
# Task 3.7.d: the pure, offline-testable pieces of manual_run_answer_
# eval.py -- outcome building, injection-detector aggregation, and
# calibration-row selection. No real service, no real LLM call anywhere
# in this file.
#
# manual_run_answer_eval.py itself calls _point_env_at_test_services() at
# IMPORT time (every other manual script's own established convention --
# MUST run before any app.* import). That check only validates presence
# and hostname (never attempts a real connection), so fake-but-localhost
# values let it import safely offline -- but EVERY one of those names
# (both the TEST_-prefixed ones and the plain QDRANT_URL/DATABASE_URL
# ones _point_env_at_test_services() itself copies them into) must be
# restored to their ORIGINAL state immediately after the import
# completes, not left set: confirmed live, a first version of this file
# left them set at module (collection) scope, and since TEST_QDRANT_URL/
# TEST_DATABASE_URL are NOT among `tests/conftest.py`'s own
# `ALL_SETTINGS_VARS` (the per-test `_isolated_env` autouse fixture only
# clears THOSE), the leak persisted for the rest of the pytest session --
# every other live-Qdrant-gated test in the suite then saw a "configured"
# but unreachable test-qdrant and failed for real, instead of correctly
# skipping. The snapshot/restore below runs once, synchronously, at
# import time -- by the time any test function in this file (or any
# other) actually runs, the environment is back to exactly what it was.
import os
import sys
import uuid

import pytest

# [SECURITY] L2 (Phase 7 role split, 2026-10-18): TEST_DATABASE_URL_APP/
# DATABASE_URL_MIGRATE added -- _point_env_at_test_services() now
# requires BOTH TEST_DATABASE_URL (owner) and TEST_DATABASE_URL_APP (app)
# present, and copies them into DATABASE_URL_MIGRATE/DATABASE_URL
# respectively.
_ENV_KEYS_TOUCHED_BY_IMPORT = (
    "TEST_QDRANT_URL", "TEST_QDRANT_API_KEY", "TEST_QDRANT_READ_ONLY_API_KEY",
    "TEST_DATABASE_URL", "TEST_DATABASE_URL_APP", "QDRANT_URL", "QDRANT_API_KEY",
    "QDRANT_READ_ONLY_API_KEY", "DATABASE_URL", "DATABASE_URL_MIGRATE",
)
_original_env = {key: os.environ.get(key) for key in _ENV_KEYS_TOUCHED_BY_IMPORT}
os.environ["TEST_QDRANT_URL"] = "http://127.0.0.1:1"
os.environ["TEST_QDRANT_API_KEY"] = "fake-key"
os.environ["TEST_QDRANT_READ_ONLY_API_KEY"] = "fake-readonly-key"
os.environ["TEST_DATABASE_URL"] = "postgresql+psycopg://u:p@127.0.0.1:1/db"
os.environ["TEST_DATABASE_URL_APP"] = "postgresql+psycopg://u:p@127.0.0.1:1/db"

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

from app.evals.golden_set import load_golden_set  # noqa: E402
from scripts import manual_run_answer_eval as M  # noqa: E402

for _key, _value in _original_env.items():
    if _value is None:
        os.environ.pop(_key, None)
    else:
        os.environ[_key] = _value

# Item 1b's own live tests (below) open/close the real checkpointer pool
# directly (both to seed checkpoints and via run_sweep() itself) -- this
# is the same opt-in safety net test_checkpointer.py's own header comment
# documents, closing any pool a failing assertion left open so it never
# leaks into the next test.
pytestmark = pytest.mark.usefixtures("close_any_checkpointer_pool_left_open")


def _item(golden_set, item_id):
    return next(i for i in golden_set.items if i.id == item_id)


def _real_golden_set():
    from app.evals.golden_set import DEFAULT_ITEMS_PATH

    return load_golden_set(DEFAULT_ITEMS_PATH)


# --- _final_ai_text / _build_outcome ----------------------------------------


def test_final_ai_text_returns_the_last_ai_message():
    values = {
        "messages": [
            HumanMessage(content="q"),
            AIMessage(content="first answer"),
            AIMessage(content="second answer"),
        ]
    }
    assert M._final_ai_text(values) == "second answer"


def test_final_ai_text_empty_when_no_ai_message():
    assert M._final_ai_text({"messages": [HumanMessage(content="q")]}) == ""


def test_build_outcome_captures_can_answer_and_citations():
    golden_set = _real_golden_set()
    item = _item(golden_set, "ans-01")
    values = {
        "messages": [AIMessage(content="Standard domestic shipping takes 5 to 7 days.")],
        "can_answer": True,
        "citations": [{"url": "https://eval.example/shipping-domestic", "heading_path": []}],
        "cited_chunk_labels": ["c1"],
        "retrieved_chunks": [
            {
                "document_id": "x", "text": "Standard domestic shipping takes 5 to 7 days.",
                "source_url": "https://eval.example/shipping-domestic", "heading_path": [],
                "score": 0.9,
            }
        ],
        "best_dense_score": 0.74,
    }
    outcome = M._build_outcome(item, values)
    assert outcome.item_id == "ans-01"
    assert outcome.actual_can_answer is True
    assert outcome.actual_route == "answer"
    assert outcome.cited_chunk_labels == ("c1",)
    assert outcome.retrieved_chunk_texts == ("Standard domestic shipping takes 5 to 7 days.",)
    assert outcome.best_dense_score == 0.74


def test_build_outcome_defaults_best_dense_score_to_none_when_absent():
    golden_set = _real_golden_set()
    item = _item(golden_set, "off-01")
    values = {"messages": [AIMessage(content="some answer")], "can_answer": False}
    outcome = M._build_outcome(item, values)
    assert outcome.best_dense_score is None


def test_build_outcome_appends_the_item_id_suffix():
    golden_set = _real_golden_set()
    item = _item(golden_set, "inj-01")
    values = {"messages": [AIMessage(content="some answer")], "can_answer": True}
    outcome = M._build_outcome(item, values, item_id_suffix="#2")
    assert outcome.item_id == "inj-01#2"


def test_citation_urls_extracts_the_url_field():
    citations = (
        {"url": "https://a.example", "heading_path": []},
        {"url": "https://b.example", "heading_path": []},
    )
    assert M._citation_urls(citations) == frozenset({"https://a.example", "https://b.example"})


def test_as_retrieved_chunks_preserves_text_order():
    chunks = M._as_retrieved_chunks(("first", "second"))
    assert [c["text"] for c in chunks] == ["first", "second"]


# --- run_injection_detectors -------------------------------------------------


def test_run_injection_detectors_passes_a_clean_item():
    results = {
        "inj-01": [
            ("Expedited shipping costs $14.99.", "Expedited shipping costs $14.99.", frozenset()),
        ]
    }
    assert M.run_injection_detectors(results) == {"inj-01": True}


def test_run_injection_detectors_any_leaking_repeat_fails_the_item():
    from scripts.injection_detectors import CANARY_TOKEN

    results = {
        "inj-01": [
            ("clean answer", "clean answer", frozenset()),
            (f"leaked {CANARY_TOKEN}", f"leaked {CANARY_TOKEN}", frozenset()),
            ("clean answer again", "clean answer again", frozenset()),
        ]
    }
    assert M.run_injection_detectors(results) == {"inj-01": False}


def test_run_injection_detectors_handles_multiple_items_independently():
    from scripts.injection_detectors import CANARY_TOKEN

    results = {
        "inj-01": [("clean", "clean", frozenset())],
        "inj-02": [(CANARY_TOKEN, CANARY_TOKEN, frozenset())],
    }
    outcome = M.run_injection_detectors(results)
    assert outcome["inj-01"] is True
    assert outcome["inj-02"] is False


# --- select_calibration_rows --------------------------------------------------


def _outcome(item_id, can_answer=True, text="A real answer."):
    return M.TurnOutcome(
        item_id=item_id, category="answerable", expected_route="answer",
        expected_can_answer=True, expected_evidence_passages=(), actual_route="answer",
        actual_can_answer=can_answer, cited_chunk_labels=(), citations=(),
        retrieved_chunk_texts=(), final_text=text,
    )


def test_select_calibration_rows_excludes_injection_and_non_answered_turns():
    golden_set = _real_golden_set()
    outcomes = [
        _outcome("ans-01"),
        _outcome("inj-01#0"),  # injection -- excluded
        _outcome("off-01", can_answer=False),  # never answered -- excluded
    ]
    rows = M.select_calibration_rows(outcomes, golden_set)
    assert len(rows) == 1
    assert rows[0][0].item_id == "ans-01"
    assert rows[0][2] == "real"


def test_select_calibration_rows_corrupts_the_tail():
    golden_set = _real_golden_set()
    # 35 outcomes against REAL, existing non-injection item ids (ans-*,
    # fu-*, ms-* -- 15 each, 45 available) -- more than CALIBRATION_TOTAL
    # (30), so the selection logic actually has to choose real_count
    # then corrupt the next CALIBRATION_CORRUPTED (8).
    real_ids = (
        [f"ans-{i:02d}" for i in range(1, 16)]
        + [f"fu-{i:02d}" for i in range(1, 16)]
        + [f"ms-{i:02d}" for i in range(1, 6)]
    )
    outcomes = [_outcome(item_id, text=f"Answer for {item_id}.") for item_id in real_ids]
    rows = M.select_calibration_rows(outcomes, golden_set)
    assert len(rows) == M.CALIBRATION_TOTAL
    origins = [origin for _, _, origin in rows]
    assert origins.count("real") == M.CALIBRATION_TOTAL - M.CALIBRATION_CORRUPTED
    corrupted = [r for r in rows if r[2] != "real"]
    assert len(corrupted) == M.CALIBRATION_CORRUPTED
    for outcome, answer_text, origin in corrupted:
        assert origin.startswith("corrupted:")
        assert answer_text != outcome.final_text


# --- _require_dev_or_test_environment (item 1c) ------------------------------


def test_environment_guard_allows_development():
    M._require_dev_or_test_environment(M.Settings.model_construct(app_env="development"))


def test_environment_guard_allows_test():
    M._require_dev_or_test_environment(M.Settings.model_construct(app_env="test"))


def test_environment_guard_refuses_production():
    settings = M.Settings.model_construct(app_env="production")
    with pytest.raises(SystemExit, match="APP_ENV='production'"):
        M._require_dev_or_test_environment(settings)


# --- tracking-file helpers (item 1a/1b: crash-recovery for --sweep) ---------


def test_tracked_tenants_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "_RESULTS_DIR", tmp_path)
    monkeypatch.setattr(M, "_TRACKING_FILE", tmp_path / ".active_eval_tenants.json")

    assert M._read_tracked_tenants() == []

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    M._add_tracked_tenant(tenant_a)
    M._add_tracked_tenant(tenant_b)
    assert set(M._read_tracked_tenants()) == {str(tenant_a), str(tenant_b)}

    M._remove_tracked_tenant(tenant_a)
    assert M._read_tracked_tenants() == [str(tenant_b)]


def test_removing_the_last_tracked_tenant_deletes_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "_RESULTS_DIR", tmp_path)
    monkeypatch.setattr(M, "_TRACKING_FILE", tmp_path / ".active_eval_tenants.json")

    tenant = uuid.uuid4()
    M._add_tracked_tenant(tenant)
    assert M._TRACKING_FILE.exists()

    M._remove_tracked_tenant(tenant)
    assert not M._TRACKING_FILE.exists()


def test_removing_an_untracked_tenant_is_a_harmless_no_op(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "_RESULTS_DIR", tmp_path)
    monkeypatch.setattr(M, "_TRACKING_FILE", tmp_path / ".active_eval_tenants.json")

    M._remove_tracked_tenant(uuid.uuid4())  # no tracking file exists yet
    assert not M._TRACKING_FILE.exists()


# --- run_sweep() (item 1b): live crash-recovery tests, real test-qdrant/db --


async def test_sweep_removes_eval_marked_points_and_leaves_other_tenants_alone(
    tmp_path, monkeypatch
):
    # The Qdrant half of crash recovery: proves `--sweep` finds a point by
    # its payload marker ALONE (no tracking file involved at all for this
    # half -- see run_sweep()'s own docstring) and, critically, does NOT
    # touch another tenant's own unmarked point in the SAME shared
    # collection -- the real precision test item 1b's own instruction
    # names explicitly ("nothing else, including another tenant's
    # points").
    from app.ingest.embedding import EMBEDDING_MODEL, SparseVectorData
    from app.ingest.qdrant_writer import build_payload, build_point, build_point_id, upsert_points
    from scripts.retrieval_eval import EVAL_MARKER_FIELD, EVAL_MARKER_VALUE
    from tests.conftest import require_test_qdrant, set_real_qdrant_env

    monkeypatch.setattr(M, "_RESULTS_DIR", tmp_path)
    monkeypatch.setattr(M, "_TRACKING_FILE", tmp_path / ".active_eval_tenants.json")
    set_real_qdrant_env(monkeypatch)
    url, key = require_test_qdrant()

    def _seed(tenant_id: uuid.UUID, marked: bool):
        document_id = uuid.uuid4()
        payload = build_payload(
            tenant_id=tenant_id, source_id=uuid.uuid4(), source_type="urls",
            document_id=document_id, source_url="https://eval.example/x", file_name=None,
            title="x", heading_path=(), chunk_index=0, content_hash="h",
            embedding_model=EMBEDDING_MODEL, embedding_version="v1", text="t",
        )
        if marked:
            payload[EVAL_MARKER_FIELD] = EVAL_MARKER_VALUE
        point_id = build_point_id(document_id, 0, "v1")
        sparse = SparseVectorData(indices=[1], values=[1.0])
        return build_point(point_id, payload, [0.0] * M.qdrant.DENSE_VECTOR_SIZE, sparse)

    crashed_tenant = uuid.uuid4()
    other_tenant = uuid.uuid4()
    crashed_point = _seed(crashed_tenant, marked=True)
    other_point = _seed(other_tenant, marked=False)

    client = M.qdrant.build_qdrant_client(url, key)
    try:
        await M.qdrant.ensure_collection(client, M.qdrant.COLLECTION_NAME)
        await upsert_points(client, M.qdrant.COLLECTION_NAME, [crashed_point, other_point])

        await M.run_sweep()

        crashed_records, _ = await client.scroll(
            collection_name=M.qdrant.COLLECTION_NAME,
            scroll_filter=M.qdrant.tenant_filter(crashed_tenant), limit=10,
        )
        other_records, _ = await client.scroll(
            collection_name=M.qdrant.COLLECTION_NAME,
            scroll_filter=M.qdrant.tenant_filter(other_tenant), limit=10,
        )
        assert crashed_records == []
        assert len(other_records) == 1
        assert other_records[0].payload["client_id"] == str(other_tenant)
    finally:
        await client.delete(
            collection_name=M.qdrant.COLLECTION_NAME,
            points_selector=M.qdrant.tenant_filter(other_tenant),
        )
        await client.close()


async def test_sweep_recovers_checkpointer_threads_for_tracked_tenants_only(
    tmp_path, monkeypatch
):
    # The checkpointer half: a tenant_id written to the tracking file (the
    # "a crashed run never reached its own finally block" simulation) gets
    # its own thread(s) deleted; an UNTRACKED tenant's thread (standing in
    # for either a live run still in progress, or simply unrelated real
    # data) must survive untouched.
    import psycopg
    from langgraph.checkpoint.base import CheckpointMetadata, empty_checkpoint

    from tests.conftest import (
        require_test_qdrant,
        require_test_qdrant_read_only_key,
        reset_checkpoint_env,
    )

    monkeypatch.setattr(M, "_RESULTS_DIR", tmp_path)
    monkeypatch.setattr(M, "_TRACKING_FILE", tmp_path / ".active_eval_tenants.json")

    qdrant_url, qdrant_key = require_test_qdrant()
    qdrant_read_only_key = require_test_qdrant_read_only_key()
    conninfo = await reset_checkpoint_env(
        monkeypatch,
        QDRANT_URL=qdrant_url, QDRANT_API_KEY=qdrant_key,
        QDRANT_READ_ONLY_API_KEY=qdrant_read_only_key,
    )
    async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
        await M.checkpointer.AsyncPostgresSaver(conn).setup()

    crashed_tenant = uuid.uuid4()
    other_tenant = uuid.uuid4()
    crashed_thread = f"{crashed_tenant}:vid:conv"
    other_thread = f"{other_tenant}:vid:conv"

    await M.checkpointer.open_checkpointer_pool()
    try:
        saver = M.checkpointer.get_checkpointer()
        for thread_id in (crashed_thread, other_thread):
            config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
            checkpoint = empty_checkpoint()
            checkpoint["channel_values"] = {"messages": "hello"}
            await saver.aput(config, checkpoint, CheckpointMetadata(source="input", step=1), {})
    finally:
        await M.checkpointer.close_checkpointer_pool()

    M._add_tracked_tenant(crashed_tenant)  # simulates a run that crashed before cleanup

    await M.run_sweep()

    await M.checkpointer.open_checkpointer_pool()
    try:
        saver = M.checkpointer.get_checkpointer()
        crashed_config = {"configurable": {"thread_id": crashed_thread, "checkpoint_ns": ""}}
        other_config = {"configurable": {"thread_id": other_thread, "checkpoint_ns": ""}}
        assert await saver.aget_tuple(crashed_config) is None
        assert await saver.aget_tuple(other_config) is not None
    finally:
        await M.checkpointer.close_checkpointer_pool()

    assert M._read_tracked_tenants() == []


async def test_main_still_cleans_up_on_a_mid_run_crash(tmp_path, monkeypatch):
    # The real property item 1b's own "cleanup runs in a finally block"
    # instruction depends on: a genuine exception raised deep inside a
    # real run must still trigger cleanup, not just a clean return. A
    # real dry run of `make eval-answers` (3.7.e) hit a genuine OpenAI
    # mid-stream connection error, but the Makefile's own teardown
    # (test-db-down/test-qdrant-down, both `-v`) destroyed the disposable
    # services immediately afterward regardless of the script's own exit
    # code -- so whether main()'s own finally block ran could not be
    # observed from that run's own output alone. This test forces the
    # identical "exception raised inside run_answer_eval()" shape
    # directly, with services left running so cleanup can actually be
    # checked against real Qdrant/Postgres state.
    from tests.conftest import (
        require_test_qdrant,
        require_test_qdrant_read_only_key,
        reset_checkpoint_env,
    )

    monkeypatch.setattr(M, "_RESULTS_DIR", tmp_path)
    monkeypatch.setattr(M, "_TRACKING_FILE", tmp_path / ".active_eval_tenants.json")

    qdrant_url, qdrant_key = require_test_qdrant()
    qdrant_read_only_key = require_test_qdrant_read_only_key()
    await reset_checkpoint_env(
        monkeypatch,
        QDRANT_URL=qdrant_url, QDRANT_API_KEY=qdrant_key,
        QDRANT_READ_ONLY_API_KEY=qdrant_read_only_key,
    )

    fixed_tenant_id = uuid.uuid4()
    monkeypatch.setattr(M.uuid, "uuid4", lambda: fixed_tenant_id)
    monkeypatch.setattr(sys, "argv", ["manual_run_answer_eval.py", "--yes"])

    async def _raise(*args, **kwargs):
        raise RuntimeError("synthetic failure, simulating a real mid-run crash")

    monkeypatch.setattr(M, "run_answer_eval", _raise)

    with pytest.raises(RuntimeError, match="synthetic failure"):
        await M.main()

    # The tenant's own seeded Qdrant points are gone -- finally's cleanup
    # ran despite the exception, not skipped.
    client = M.qdrant.build_qdrant_client(qdrant_url, qdrant_key)
    try:
        records, _ = await client.scroll(
            collection_name=M.qdrant.COLLECTION_NAME,
            scroll_filter=M.qdrant.tenant_filter(fixed_tenant_id), limit=10,
        )
        assert records == []
    finally:
        await client.close()

    # The tracking file no longer lists this tenant -- _remove_tracked_
    # tenant() ran too, inside the SAME finally block, after the real
    # cleanup above.
    assert str(fixed_tenant_id) not in M._read_tracked_tenants()
