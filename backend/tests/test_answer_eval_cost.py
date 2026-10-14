# backend/tests/test_answer_eval_cost.py
# Task 3.7.d: the cost-estimate + --yes gate (scripts/answer_eval_cost.py)
# -- fully offline, no env vars, no real services. Exercised against the
# REAL committed golden set (106 items -- 16 answerable, 15 everywhere
# else, since Task 3.7.g's own `rel-07` relabelling moved one item from
# related_unanswerable to answerable and backfilled a new `rel-16` to
# keep that category at its own required 15) so the counts are a real,
# meaningful check, not just arithmetic on a fake fixture.
import pytest

from app.evals.golden_set import DEFAULT_ITEMS_PATH, load_golden_set
from scripts.answer_eval_cost import estimate_cost, parse_args, require_confirmation


def test_yes_flag_defaults_to_false():
    args = parse_args([])
    assert args.yes is False


def test_yes_flag_parses():
    args = parse_args(["--yes"])
    assert args.yes is True


def test_sweep_flag_defaults_to_false():
    assert parse_args([]).sweep is False


def test_sweep_flag_parses():
    assert parse_args(["--sweep"]).sweep is True


def test_require_confirmation_raises_without_yes(capsys):
    golden_set = load_golden_set(DEFAULT_ITEMS_PATH)
    estimate = estimate_cost(golden_set)
    with pytest.raises(SystemExit, match="--yes"):
        require_confirmation(estimate, yes=False)
    captured = capsys.readouterr()
    assert "Golden-set items" in captured.out


def test_require_confirmation_passes_with_yes(capsys):
    golden_set = load_golden_set(DEFAULT_ITEMS_PATH)
    estimate = estimate_cost(golden_set)
    require_confirmation(estimate, yes=True)  # must not raise
    captured = capsys.readouterr()
    assert "Golden-set items" in captured.out


def test_estimate_cost_against_the_real_committed_golden_set():
    golden_set = load_golden_set(DEFAULT_ITEMS_PATH)
    estimate = estimate_cost(golden_set)
    assert estimate.item_count == 106
    # 15 off_topic (1 turn each) + 15 injection (3 turns each) +
    # 15 follow_up (2 turns each) + 46 other (answerable(16)/related_
    # unanswerable(15)/multi_source(15), 1 turn each) = 15+45+30+46 = 136.
    assert estimate.retrieval_call_count == 136
    # Same total minus the 15 off_topic turns, which never reach the
    # answer node at all.
    assert estimate.chat_call_count == 121
    # answerable(16) + follow_up(15) + multi_source(15) = 46 -- every
    # item whose own expected_can_answer is true, excluding injection.
    assert estimate.judge_call_count == 46
    assert estimate.estimated_usd > 0


def test_estimate_cost_rejects_nothing_and_is_deterministic():
    golden_set = load_golden_set(DEFAULT_ITEMS_PATH)
    first = estimate_cost(golden_set)
    second = estimate_cost(golden_set)
    assert first == second
