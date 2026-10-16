# backend/app/agent/rewrite_trigger.py
# Task 3.5.b: the "needs join" skip-check. Decided at the Step 3.5
# decision log (2026-10-13, Felix): follow-ups are prepared by JOINING
# the previous visitor question with the current message, never a model
# rewrite, for the pilot (docs/SPEC.md §6.3's own "a small, fast model
# rewrites" is a recorded, dated deviation -- see PROJECT_SPEC.md's own
# decision log and §9 marker, owner Step 3.7, for the full reasoning and
# the real evidence this choice rests on). This module answers ONE
# question: does the current message look like it depends on earlier
# context (a pronoun, a back-reference, or just very short), or does it
# look self-contained? `_prepare_node` (app/agent/graph.py) is the only
# caller -- it only ATTEMPTS a join when this says yes, and only if a
# qualifying previous question exists at all.
#
# Mirrors greeting_phrases.py's own established shape exactly: a YAML
# data file (`rewrite_trigger_phrases.yaml`), loaded once (`@lru_cache`),
# validated against a schema -- a malformed file fails loudly, never
# silently matching nothing (which here would mean EVERY follow-up
# silently loses its own context, a quality regression, not a crash).
#
# MATCHING RULE, stated plainly (deliberately NOT the same rule as
# greeting_phrases.py's own exact-whole-message match):
#   1. The message is normalised the same way (lowercased, trailing
#      punctuation stripped, internal whitespace collapsed).
#   2. Flagged as needing a join if EITHER: a listed phrase appears
#      anywhere in the normalised message as a whole-word/whole-phrase
#      match (never matching inside a larger word, e.g. "this" must
#      never match inside "enthusiastic"), OR the message is
#      `_SHORT_MESSAGE_WORD_THRESHOLD` words or fewer. This is an OR,
#      not an AND -- either condition alone is enough, matching 3.5.a's
#      own research script exactly (the one this module replaces with a
#      real, committed, data-file-driven version).
#   3. Confirmed, by 3.5.a's own real research (10 follow-ups, 15 self-
#      contained questions): this combination is imperfect in BOTH
#      directions (a real, now-quantified ~20% false-positive rate on
#      compound self-contained questions whose own second half refers
#      back to its own first half, e.g. "...and how long does it take?")
#      -- accepted deliberately, per the Step 3.5 decision log's own Q1
#      reasoning: under joining (no model call), a false positive costs
#      only a slightly broader query, never a model-call latency cost.
import re
from functools import lru_cache
from importlib import resources

import yaml
from pydantic import BaseModel

# Same fix, same reasoning, as greeting_phrases.py's own identical line
# -- see that module's own header comment for the full reasoning. The
# yaml file lives INSIDE the `app` package (app/agent/phrases/) so it
# is real package data, resolved through importlib.resources rather
# than hand-computed path arithmetic.
_PHRASES_RESOURCE = resources.files("app.agent").joinpath(
    "phrases", "rewrite_trigger_phrases.yaml"
)
_SHORT_MESSAGE_WORD_THRESHOLD = 4
_TRAILING_PUNCTUATION = re.compile(r"[!.?,;:]+$")
_INTERNAL_WHITESPACE = re.compile(r"\s+")


class _PhraseEntry(BaseModel):
    phrase: str


class _PhraseFile(BaseModel):
    entries: list[_PhraseEntry]


def _normalize(text: str) -> str:
    text = text.strip().lower()
    text = _TRAILING_PUNCTUATION.sub("", text)
    text = _INTERNAL_WHITESPACE.sub(" ", text)
    return text.strip()


@lru_cache
def _load_phrase_patterns() -> tuple[re.Pattern, ...]:
    raw = yaml.safe_load(_PHRASES_RESOURCE.read_text(encoding="utf-8"))
    parsed = _PhraseFile.model_validate(raw)
    return tuple(
        re.compile(r"\b" + re.escape(_normalize(entry.phrase)) + r"\b")
        for entry in parsed.entries
    )


def needs_join(message: str) -> bool:
    """True if `message` looks context-dependent enough that `_prepare_
    node` should attempt joining it with the previous visitor question
    -- either a listed phrase appears anywhere in it, or it is short.
    """
    normalized = _normalize(message)
    if len(normalized.split()) <= _SHORT_MESSAGE_WORD_THRESHOLD:
        return True
    return any(pattern.search(normalized) for pattern in _load_phrase_patterns())
