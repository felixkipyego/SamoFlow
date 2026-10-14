# backend/tests/test_groundedness_judge.py
# Task 3.7.d [SECURITY]: the corruption generator and the calibration
# export/import round-trip (scripts/groundedness_judge.py) -- offline
# only, no real LLM/judge call anywhere in this file.
import json

import pytest

from scripts.groundedness_judge import (
    CalibrationRow,
    corrupt_invented_policy,
    corrupt_swapped_fact,
    corrupt_wrong_number,
    generate_corrupted_answer,
    import_calibration_labels,
    render_context,
    write_key_file,
    write_labelling_csv,
)


def _chunk(text: str) -> dict:
    return {
        "document_id": "doc-a", "text": text,
        "source_url": "https://eval.example/doc-a", "heading_path": ["Section"], "score": 0.9,
    }


# --- render_context (delimited the same way as the real answer node) -------


def test_render_context_reuses_the_real_delimiter_scheme():
    context = render_context([_chunk("Shipping is $4.99.")], "how much is shipping")
    assert "[[DATA:c1]]" in context
    assert "[[/DATA]]" in context
    assert "Shipping is $4.99." in context
    assert "how much is shipping" in context


# --- corruption generator -----------------------------------------------


def test_corrupt_wrong_number_shifts_the_first_number():
    result = corrupt_wrong_number("It costs $14.99 and takes 5 days.")
    assert result == "It costs $21.99 and takes 5 days."


def test_corrupt_wrong_number_returns_none_without_a_number():
    assert corrupt_wrong_number("No numbers here at all.") is None


def test_corrupt_swapped_fact_replaces_a_known_pair():
    result = corrupt_swapped_fact("You can return it within 30 days of delivery.")
    assert result == "You can return it within 14 days of delivery."


def test_corrupt_swapped_fact_returns_none_without_a_known_pair():
    assert corrupt_swapped_fact("Nothing matches any known fact pair.") is None


def test_corrupt_invented_policy_always_appends_a_fabricated_clause():
    result = corrupt_invented_policy("Shipping is free over $50.")
    assert result.startswith("Shipping is free over $50.")
    assert len(result) > len("Shipping is free over $50.")


def test_corrupt_invented_policy_variants_differ():
    a = corrupt_invented_policy("Same base text.", variant=0)
    b = corrupt_invented_policy("Same base text.", variant=1)
    assert a != b


def test_generate_corrupted_answer_uses_wrong_number_when_no_swap_pair_matches():
    # $25 deliberately matches none of _SWAP_PAIRS, unlike $14.99/$4.99.
    corrupted, kind = generate_corrupted_answer("It costs $25 total.")
    assert kind == "wrong_number"
    assert corrupted != "It costs $25 total."


def test_generate_corrupted_answer_prefers_swapped_fact_over_wrong_number():
    # "$14.99" is BOTH a known swap pair AND contains digits -- confirmed
    # live, swapped_fact must win this ordering, or it becomes
    # unreachable for every answer a swap pair could also apply to.
    corrupted, kind = generate_corrupted_answer("It costs $14.99.")
    assert kind == "swapped_fact"
    assert corrupted == "It costs $39.99."


def test_generate_corrupted_answer_uses_swapped_fact_for_a_known_pair():
    corrupted, kind = generate_corrupted_answer("Returns are accepted within 30 days.")
    assert kind == "swapped_fact"
    assert "14 days" in corrupted


def test_generate_corrupted_answer_falls_back_to_invented_policy_as_a_last_resort():
    corrupted, kind = generate_corrupted_answer("Nothing numeric or matchable here.")
    assert kind == "invented_policy"
    assert corrupted != "Nothing numeric or matchable here."


def test_generate_corrupted_answer_always_actually_changes_the_text():
    # Every real answer this project's own synthetic corpus could
    # produce has SOME number, known fact pair, or at minimum gets the
    # fallback clause -- the generator must never silently return the
    # input unchanged.
    for answer in [
        "Standard shipping takes 5 to 7 business days.",
        "Returns are accepted within 30 days of delivery.",
        "We accept Visa, Mastercard, and American Express.",
    ]:
        corrupted, _ = generate_corrupted_answer(answer)
        assert corrupted != answer


# --- calibration export / import round-trip ---------------------------------


def _rows() -> list[CalibrationRow]:
    return [
        CalibrationRow(
            row_id="r1", question="q1", context="ctx1", answer="a1",
            origin="real", judge_label="grounded",
        ),
        CalibrationRow(
            row_id="r2", question="q2", context="ctx2", answer="a2 (corrupted)",
            origin="corrupted:wrong_number", judge_label="not_grounded",
        ),
        CalibrationRow(
            row_id="r3", question="q3", context="ctx3", answer="a3 (corrupted)",
            origin="corrupted:invented_policy", judge_label="grounded",  # judge MISSED this one
        ),
    ]


def test_write_labelling_csv_never_leaks_origin_or_judge_label(tmp_path):
    path = tmp_path / "labelling.csv"
    write_labelling_csv(_rows(), path)
    content = path.read_text(encoding="utf-8")
    assert "origin" not in content
    assert "judge_label" not in content
    assert "grounded" not in content  # the judge's own real/corrupted labels are absent
    assert "row_id,question,context,answer,label" in content


def test_write_labelling_csv_refuses_to_overwrite_a_filled_in_label(tmp_path):
    # Task 3.7.e, item 1d: the live guarantee -- Felix is labelling the
    # CURRENT file; a second write to that exact path must never destroy
    # his work, even though normal operation (a new timestamped filename
    # per run) never actually reaches this path in practice.
    path = tmp_path / "labelling.csv"
    write_labelling_csv(_rows(), path)
    content = path.read_text(encoding="utf-8").replace("r1,q1,ctx1,a1,", "r1,q1,ctx1,a1,grounded")
    path.write_text(content, encoding="utf-8")

    with pytest.raises(FileExistsError, match="already contains at least one filled-in label"):
        write_labelling_csv(_rows(), path)

    # The file on disk is untouched -- the refusal happens BEFORE any
    # write, not a partial overwrite.
    assert "r1,q1,ctx1,a1,grounded" in path.read_text(encoding="utf-8")


def test_write_labelling_csv_allows_overwriting_an_all_blank_file(tmp_path):
    # A file that was exported but never touched by Felix (every `label`
    # still blank) is NOT protected -- re-running against the same path
    # in that state is harmless, matching the guard's own documented
    # scope (filled-in labels only, not existence alone).
    path = tmp_path / "labelling.csv"
    write_labelling_csv(_rows(), path)
    write_labelling_csv(_rows(), path)  # must not raise
    assert "row_id,question,context,answer,label" in path.read_text(encoding="utf-8")


def test_write_key_file_round_trips_origin_and_judge_label(tmp_path):
    path = tmp_path / "key.json"
    write_key_file(_rows(), path)
    key = json.loads(path.read_text(encoding="utf-8"))
    assert key["r1"] == {"origin": "real", "judge_label": "grounded"}
    assert key["r2"]["origin"] == "corrupted:wrong_number"


def test_import_requires_felix_to_have_labelled_something(tmp_path):
    labelling_path = tmp_path / "labelling.csv"
    key_path = tmp_path / "key.json"
    write_labelling_csv(_rows(), labelling_path)
    write_key_file(_rows(), key_path)
    with pytest.raises(ValueError, match="not labelled"):
        import_calibration_labels(labelling_path, key_path)


def _label_csv(path, row_id_to_label: dict[str, str]) -> None:
    import csv

    with path.open() as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        if row["row_id"] in row_id_to_label:
            row["label"] = row_id_to_label[row["row_id"]]
    fieldnames = ["row_id", "question", "context", "answer", "label"]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_import_computes_agreement_confusion_and_corrupted_recall(tmp_path):
    labelling_path = tmp_path / "labelling.csv"
    key_path = tmp_path / "key.json"
    rows = _rows()
    write_labelling_csv(rows, labelling_path)
    write_key_file(rows, key_path)

    # Felix labels all 3: agrees with the judge on r1 and r2, disagrees
    # on r3 (he correctly catches the invented-policy corruption the
    # judge missed).
    _label_csv(
        labelling_path,
        {"r1": "grounded", "r2": "not_grounded", "r3": "not_grounded"},
    )

    result = import_calibration_labels(labelling_path, key_path)
    assert result.total_rows == 3
    assert result.labelled_rows == 3
    assert result.agreement_rate == pytest.approx(2 / 3)
    assert result.confusion_matrix["grounded"] == {"grounded": 1}
    assert result.confusion_matrix["not_grounded"] == {"not_grounded": 1, "grounded": 1}
    # 2 corrupted rows total (r2, r3); judge only got r2 right -> 1/2.
    assert result.corrupted_recall == pytest.approx(0.5)


def test_import_skips_blank_labels_in_agreement_but_not_in_corrupted_recall(tmp_path):
    labelling_path = tmp_path / "labelling.csv"
    key_path = tmp_path / "key.json"
    rows = _rows()
    write_labelling_csv(rows, labelling_path)
    write_key_file(rows, key_path)

    # Felix only labels r1 -- r2/r3 stay blank.
    _label_csv(labelling_path, {"r1": "grounded"})

    result = import_calibration_labels(labelling_path, key_path)
    assert result.labelled_rows == 1
    assert result.agreement_rate == 1.0
    # corrupted_recall is still computed from the key file alone (r2,
    # r3), independent of how many Felix has gotten to so far.
    assert result.corrupted_recall == pytest.approx(0.5)


def test_import_raises_if_no_corrupted_rows_exist(tmp_path):
    labelling_path = tmp_path / "labelling.csv"
    key_path = tmp_path / "key.json"
    rows = [
        CalibrationRow(
            row_id="r1", question="q", context="c", answer="a",
            origin="real", judge_label="grounded",
        )
    ]
    write_labelling_csv(rows, labelling_path)
    write_key_file(rows, key_path)
    _label_csv(labelling_path, {"r1": "grounded"})
    with pytest.raises(ValueError, match="no corrupted"):
        import_calibration_labels(labelling_path, key_path)


# --- CLI entry point (`make eval-calibrate`, item 3) ------------------------


def test_find_latest_labelling_csv_picks_the_newest_timestamp(tmp_path):
    from scripts.groundedness_judge import find_latest_labelling_csv

    (tmp_path / "calibration_20260101-090000.csv").write_text("x", encoding="utf-8")
    newest = tmp_path / "calibration_20260102-090000.csv"
    newest.write_text("x", encoding="utf-8")
    assert find_latest_labelling_csv(tmp_path) == newest


def test_find_latest_labelling_csv_raises_a_clear_error_when_none_exist(tmp_path):
    from scripts.groundedness_judge import find_latest_labelling_csv

    with pytest.raises(SystemExit, match="make eval-answers"):
        find_latest_labelling_csv(tmp_path)


def test_eval_calibrate_main_writes_a_committed_summary_and_prints_a_delta_table(
    tmp_path, monkeypatch, capsys
):
    from scripts import groundedness_judge as G

    labelling_path = tmp_path / "calibration_20260101-090000.csv"
    key_path = tmp_path / "calibration_20260101-090000.key.json"
    rows = _rows()
    write_labelling_csv(rows, labelling_path)
    write_key_file(rows, key_path)
    _label_csv(labelling_path, {"r1": "grounded", "r2": "not_grounded", "r3": "not_grounded"})

    summary_path = tmp_path / "summary_calibration.json"
    monkeypatch.setattr(G, "DEFAULT_SUMMARY_PATH", summary_path)

    G.main([str(labelling_path)])
    first_run_output = capsys.readouterr().out
    assert "(first run)" in first_run_output

    written = json.loads(summary_path.read_text(encoding="utf-8"))
    assert written["total_rows"] == 3
    assert written["labelled_rows"] == 3
    assert written["agreement_rate"] == pytest.approx(2 / 3)

    # A second run against the SAME (unchanged) data prints a delta
    # against the summary the first run just committed, not "(first
    # run)" again -- the numbers are identical, so every delta is +0.
    G.main([str(labelling_path)])
    second_run_output = capsys.readouterr().out
    assert "(first run)" not in second_run_output
    assert "agreement_rate" in second_run_output
