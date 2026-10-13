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

_ENV_KEYS_TOUCHED_BY_IMPORT = (
    "TEST_QDRANT_URL", "TEST_QDRANT_API_KEY", "TEST_QDRANT_READ_ONLY_API_KEY",
    "TEST_DATABASE_URL", "QDRANT_URL", "QDRANT_API_KEY", "QDRANT_READ_ONLY_API_KEY",
    "DATABASE_URL",
)
_original_env = {key: os.environ.get(key) for key in _ENV_KEYS_TOUCHED_BY_IMPORT}
os.environ["TEST_QDRANT_URL"] = "http://127.0.0.1:1"
os.environ["TEST_QDRANT_API_KEY"] = "fake-key"
os.environ["TEST_QDRANT_READ_ONLY_API_KEY"] = "fake-readonly-key"
os.environ["TEST_DATABASE_URL"] = "postgresql+psycopg://u:p@127.0.0.1:1/db"

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

from app.evals.golden_set import load_golden_set  # noqa: E402
from scripts import manual_run_answer_eval as M  # noqa: E402

for _key, _value in _original_env.items():
    if _value is None:
        os.environ.pop(_key, None)
    else:
        os.environ[_key] = _value


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
    }
    outcome = M._build_outcome(item, values)
    assert outcome.item_id == "ans-01"
    assert outcome.actual_can_answer is True
    assert outcome.actual_route == "answer"
    assert outcome.cited_chunk_labels == ("c1",)
    assert outcome.retrieved_chunk_texts == ("Standard domestic shipping takes 5 to 7 days.",)


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
