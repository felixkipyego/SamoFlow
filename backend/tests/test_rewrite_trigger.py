# backend/tests/test_rewrite_trigger.py
# Task 3.5.b: the "needs join" phrase-list loader and matcher
# (app/agent/rewrite_trigger.py). Offline, no live services, no real API
# calls -- pure data/logic, mirroring test_greeting_phrases.py's own
# structure.
import pytest
import yaml

from app.agent import rewrite_trigger


@pytest.fixture(autouse=True)
def _clear_cache():
    rewrite_trigger._load_phrase_patterns.cache_clear()
    yield
    rewrite_trigger._load_phrase_patterns.cache_clear()


# --- Matches: a phrase appearing anywhere, or a short message ---------------


@pytest.mark.parametrize(
    "message",
    [
        "What about Enterprise?",
        "How about the Pro plan?",
        "And the Enterprise plan?",
        "What if the item arrives damaged?",
        "What if I don't have a receipt?",
        "How long does that take?",
        "What about on weekends?",
        "Which plans support it?",
        "is it free",  # short (3 words) -- length rule alone
        "and that",  # short AND phrase match
    ],
)
def test_context_dependent_messages_are_flagged(message):
    assert rewrite_trigger.needs_join(message) is True


# --- Near-misses: genuinely self-contained, not flagged ---------------------


@pytest.mark.parametrize(
    "message",
    [
        "How much does the Pro plan cost per month?",
        "What is your return window for a full refund?",
        "How long does standard shipping take within the US?",
        "What are your customer support hours on weekdays?",
        "What are your flagship store's opening hours?",
    ],
)
def test_self_contained_questions_mostly_not_flagged(message):
    assert rewrite_trigger.needs_join(message) is False


# --- Normalisation (reused pattern from greeting_phrases) -------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("What About Enterprise?", "what about enterprise"),
        ("  How about the Pro plan?  ", "how about the pro plan"),
    ],
)
def test_normalize(raw, expected):
    assert rewrite_trigger._normalize(raw) == expected


def test_phrase_match_is_whole_word_not_a_substring():
    # "this" must never match inside "enthusiastic" -- confirms the
    # \b-bounded regex, not a naive substring check.
    assert rewrite_trigger.needs_join("We are very enthusiastic about gadgets today") is False


# --- Schema validation: a malformed file fails loudly, never silently ------


def _write_phrases_file(tmp_path, content: str):
    path = tmp_path / "rewrite_trigger_phrases.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_malformed_yaml_raises(tmp_path, monkeypatch):
    path = _write_phrases_file(tmp_path, "entries: [this is not a list of mappings")
    monkeypatch.setattr(rewrite_trigger, "_PHRASES_RESOURCE", path)
    with pytest.raises(yaml.YAMLError):
        rewrite_trigger._load_phrase_patterns()


def test_entry_missing_required_key_raises(tmp_path, monkeypatch):
    path = _write_phrases_file(tmp_path, "entries:\n  - not_phrase: it\n")
    monkeypatch.setattr(rewrite_trigger, "_PHRASES_RESOURCE", path)
    with pytest.raises(Exception, match="phrase"):
        rewrite_trigger._load_phrase_patterns()


def test_missing_entries_key_raises(tmp_path, monkeypatch):
    path = _write_phrases_file(tmp_path, "not_entries: []\n")
    monkeypatch.setattr(rewrite_trigger, "_PHRASES_RESOURCE", path)
    with pytest.raises(Exception, match="entries"):
        rewrite_trigger._load_phrase_patterns()


def test_the_real_committed_file_loads_and_validates_successfully():
    # No monkeypatch -- the real app/agent/phrases/rewrite_trigger_
    # phrases.yaml, proving it is itself valid against its own schema.
    patterns = rewrite_trigger._load_phrase_patterns()
    assert len(patterns) >= 10


# --- Task 3.5.a's own real data, re-checked here as a committed,
# --- regression-proof record of the actual rates (not re-measured,
# --- the SAME 10 follow-ups/15 self-contained questions that task
# --- reported on -- copied, not imported, since manual_research_
# --- followup_rewrite.py is a NEVER-imported opt-in script, matching
# --- this project's own "scripts/ is never imported by anything"
# --- convention). -------------------------------------------------------

_3_5_A_FOLLOWUPS = [
    "What about Enterprise?",
    "What if the item arrives damaged?",
    "How long does that take?",
    "What about on weekends?",
    "Which plans support it?",
    "What if I don't have a receipt?",
    "Is it free over a certain amount?",
    "How much does that cost?",
    "And the Enterprise plan?",
    "What about a defective one?",
]

_3_5_A_SELF_CONTAINED = [
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


def test_3_5_a_followups_rate_recorded():
    flagged = [m for m in _3_5_A_FOLLOWUPS if rewrite_trigger.needs_join(m)]
    # All 10 now flagged -- the "what if" fix (added to the YAML list
    # after 3.5.a found 2 false negatives) resolves both; re-confirmed
    # live here, not assumed.
    assert len(flagged) == 10, f"expected all 10 flagged, got {len(flagged)}: {flagged}"


def test_3_5_a_self_contained_false_positive_rate_is_bounded():
    # Task 3.5.e's own duplication check: CONFIRMED LIVE, not assumed,
    # that this was already a real bound before this edit -- adding an
    # over-broad phrase ("your") to the committed YAML made this
    # assertion fail (6/15, not 3/15), proven by actually adding it,
    # running this test, watching it fail, then reverting. Changed from
    # `== 3` to `<= 3` anyway: an UPPER BOUND is the semantically correct
    # check -- a future narrowing of the phrase list that legitimately
    # REDUCES false positives below 3 is an improvement, not a
    # regression, and must never fail this test the way exact equality
    # would have. 3.5.a's own measured rate (3/15, 20%) is the accepted
    # ceiling (Step 3.5 decision Q1) -- this test's only job is making
    # sure the YAML never silently grows past it.
    flagged = [m for m in _3_5_A_SELF_CONTAINED if rewrite_trigger.needs_join(m)]
    assert len(flagged) <= 3, (
        f"false-positive rate regressed past the accepted 3/15 ceiling: "
        f"got {len(flagged)}: {flagged}"
    )
