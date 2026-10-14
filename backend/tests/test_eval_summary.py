# backend/tests/test_eval_summary.py
# Task 3.7.e, item 3: the shared delta-table/summary-file machinery behind
# all three eval entry points -- fully offline, pure dict/JSON logic.
import json

from app.evals.eval_summary import (
    flatten_summary,
    format_delta_table,
    load_previous_summary,
    write_summary,
)


def test_load_previous_summary_returns_none_when_no_file_exists(tmp_path):
    assert load_previous_summary(tmp_path / "missing.json") is None


def test_write_and_load_summary_round_trips(tmp_path):
    path = tmp_path / "nested" / "summary.json"
    write_summary(path, {"a": 1, "b": {"c": 0.5}})
    assert load_previous_summary(path) == {"a": 1, "b": {"c": 0.5}}


def test_flatten_summary_builds_dotted_paths():
    nested = {"recall_at_k": {"answerable": {"1": 0.8, "3": 0.9}}, "mrr": {"answerable": 0.85}}
    assert flatten_summary(nested) == {
        "recall_at_k.answerable.1": 0.8,
        "recall_at_k.answerable.3": 0.9,
        "mrr.answerable": 0.85,
    }


def test_flatten_summary_handles_a_flat_dict():
    assert flatten_summary({"x": 1, "y": None}) == {"x": 1, "y": None}


def test_format_delta_table_first_run_has_no_previous():
    table = format_delta_table(None, {"metric_a": 0.9})
    assert "metric_a" in table
    assert "(first run)" in table


def test_format_delta_table_computes_a_numeric_delta():
    table = format_delta_table({"metric_a": 0.80}, {"metric_a": 0.85})
    assert "+0.0500" in table


def test_format_delta_table_flags_a_new_metric():
    table = format_delta_table({"metric_a": 0.8}, {"metric_a": 0.8, "metric_b": 0.5})
    assert "(new metric)" in table


def test_format_delta_table_flags_a_removed_metric():
    table = format_delta_table({"metric_a": 0.8, "metric_b": 0.5}, {"metric_a": 0.8})
    assert "metric_b" in table
    assert "(removed)" in table


def test_format_delta_table_handles_non_numeric_values():
    table = format_delta_table({"label": "grounded"}, {"label": "not_grounded"})
    assert "n/a" in table
    assert "grounded" in table
    assert "not_grounded" in table


def test_format_delta_table_ignores_bool_as_numeric():
    # bool is a subclass of int in Python -- a False/True metric must not
    # be diffed as if it were 0/1, which would silently misreport it.
    table = format_delta_table({"flag": False}, {"flag": True})
    assert "n/a" in table


def test_summary_json_round_trips_through_write_and_format(tmp_path):
    path = tmp_path / "summary.json"
    write_summary(path, {"a": 1})
    raw = path.read_text(encoding="utf-8")
    assert json.loads(raw) == {"a": 1}
    assert raw.endswith("\n")
