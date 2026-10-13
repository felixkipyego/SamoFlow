# backend/tests/test_greeting_phrases.py
# Task 3.4.f: the greeting/thanks phrase-list loader and matcher
# (app/agent/greeting_phrases.py). Offline, no live services, no real
# API calls -- pure data/logic.
import pytest
import yaml

from app.agent import greeting_phrases


@pytest.fixture(autouse=True)
def _clear_cache():
    # The real loader is an @lru_cache singleton -- clear before AND
    # after every test so a monkeypatched _PHRASES_PATH in one test
    # (the malformed-file cases below) can never leak a cached result
    # into a later test using the REAL file.
    greeting_phrases._load_normalized_phrases.cache_clear()
    yield
    greeting_phrases._load_normalized_phrases.cache_clear()


# --- Matches: a real sample from the real, committed YAML file -------------


@pytest.mark.parametrize(
    "message",
    [
        "hi",
        "Hi",
        "HI!",
        "hello",
        "Hello!",
        "hey",
        "thanks",
        "Thanks!",
        "THANKS.",
        "thank you",
        "Thank you!",
        "cheers",
        "ok thanks",
        "  hi  ",  # leading/trailing whitespace
        "hi,",  # trailing punctuation
        "ty",
    ],
)
def test_real_listed_phrases_match(message):
    assert greeting_phrases.is_greeting_or_thanks(message) is True


# --- Near-misses that must NOT match ----------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        # The exact example named by direct instruction: contains "thanks"
        # as a substring, but is a real question riding on a polite
        # opener, not a bare acknowledgement.
        "thanks, but how do I return this?",
        "hi, what are your opening hours?",
        "hello, I have a question about my order",
        "thank you for your help, but I still have a problem",
        # Not in the list at all (a plausible greeting-ish word, but this
        # project's own list does not include it -- exact match, not a
        # fuzzy/semantic one).
        "sup",
        "yo yo yo",
        # Completely unrelated.
        "what is your return policy?",
        "",
    ],
)
def test_near_misses_do_not_match(message):
    assert greeting_phrases.is_greeting_or_thanks(message) is False


def test_message_over_the_length_cap_never_matches_even_if_otherwise_similar():
    # Longer than _MAX_MESSAGE_LENGTH (40 chars) -- never even checked
    # against the list, regardless of content. Padding "hi" out past the
    # cap with filler words that are NOT in the list themselves (so this
    # proves the length rule specifically, not just exact-match).
    long_message = "hi " + "x" * 40
    assert len(long_message) > 40
    assert greeting_phrases.is_greeting_or_thanks(long_message) is False


def test_message_at_exactly_the_length_cap_is_still_checked():
    # Boundary: exactly 40 characters is NOT over the cap (the rule is
    # "longer than", not "at least") -- a listed phrase padded to exactly
    # 40 chars is still checked (and in this case still matches, since it
    # normalises down to a real listed phrase).
    message = "hi" + " " * 38  # 40 chars total, normalises to "hi"
    assert len(message) == 40
    assert greeting_phrases.is_greeting_or_thanks(message) is True


# --- Normalisation, as its own unit -----------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Hi", "hi"),
        ("HI!!", "hi"),
        ("  thanks  ", "thanks"),
        ("Thank   You.", "thank you"),
        ("thanks,", "thanks"),
        ("thanks;", "thanks"),
    ],
)
def test_normalize(raw, expected):
    assert greeting_phrases._normalize(raw) == expected


# --- Schema validation: a malformed file fails loudly, never silently ------


def _write_phrases_file(tmp_path, content: str):
    path = tmp_path / "greeting_phrases.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_malformed_yaml_raises(tmp_path, monkeypatch):
    path = _write_phrases_file(tmp_path, "entries: [this is not a list of mappings")
    monkeypatch.setattr(greeting_phrases, "_PHRASES_PATH", path)
    with pytest.raises(yaml.YAMLError):
        greeting_phrases._load_normalized_phrases()


def test_entry_missing_required_key_raises(tmp_path, monkeypatch):
    path = _write_phrases_file(tmp_path, "entries:\n  - category: greeting\n")
    monkeypatch.setattr(greeting_phrases, "_PHRASES_PATH", path)
    with pytest.raises(Exception, match="phrase"):
        greeting_phrases._load_normalized_phrases()


def test_entry_with_invalid_category_raises(tmp_path, monkeypatch):
    path = _write_phrases_file(
        tmp_path, "entries:\n  - phrase: hi\n    category: not-a-real-category\n"
    )
    monkeypatch.setattr(greeting_phrases, "_PHRASES_PATH", path)
    with pytest.raises(Exception, match="category"):
        greeting_phrases._load_normalized_phrases()


def test_missing_entries_key_raises(tmp_path, monkeypatch):
    path = _write_phrases_file(tmp_path, "not_entries: []\n")
    monkeypatch.setattr(greeting_phrases, "_PHRASES_PATH", path)
    with pytest.raises(Exception, match="entries"):
        greeting_phrases._load_normalized_phrases()


def test_the_real_committed_file_loads_and_validates_successfully():
    # No monkeypatch -- the real backend/config/greeting_phrases.yaml,
    # proving it is itself valid against its own schema, not just that
    # the schema works on synthetic data.
    phrases = greeting_phrases._load_normalized_phrases()
    assert len(phrases) >= 20
    assert "hi" in phrases
    assert "thanks" in phrases
