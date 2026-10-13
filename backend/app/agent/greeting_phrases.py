# backend/app/agent/greeting_phrases.py
# Task 3.4.f: docs/SPEC.md §6.2 Rule 2, the GREETING/THANKS half only
# (unsafe/abusive input is deliberately DEFERRED for the pilot, an
# explicit dated deviation decided at Task 3.4.g -- see PROJECT_SPEC.md's
# own §9 marker, owner Step 6.4, and app/agent/graph.py's own
# _is_unsafe_or_abusive() for the full reasoning).
#
# Loads backend/config/greeting_phrases.yaml -- a data file, not Python,
# mirroring the shape docs/SPEC.md §7 describes for the sibling
# escalation trigger-phrase file (not yet built, Step 4.4): one entry per
# phrase, a category, loaded once (an `@lru_cache` singleton, this
# project's own established lazy-load-once convention -- `get_settings()`
# /`get_qdrant_client()`/`_get_sparse_model()`), VALIDATED AGAINST A
# SCHEMA (two small pydantic models below) -- a malformed file fails
# loudly (a real `ValidationError`/`yaml.YAMLError`), never silently
# degrading to "nothing matches" (which would be a silent, hard-to-notice
# quality regression in production: every greeting/thanks message would
# then route to real retrieval instead of the free, instant Guardrail
# reply -- a correctness problem, not a crash, exactly the kind of
# failure that should be loud, not swallowed).
#
# MATCHING RULE, stated plainly (the length rule the task itself asked be
# stated explicitly, not left implicit):
#   1. A message longer than `_MAX_MESSAGE_LENGTH` (40 characters) is
#      NEVER checked against this list at all -- a cheap, explicit
#      short-circuit, not merely an emergent property of exact matching
#      below (stated as its own named rule so it is independently
#      testable, not just "whatever exact-match happens to reject").
#   2. The message is NORMALISED -- lowercased, trailing punctuation
#      (!.?,;:) stripped, internal whitespace collapsed to single spaces,
#      leading/trailing whitespace trimmed.
#   3. The normalised message must EXACTLY equal one of the normalised
#      phrases in the list -- never a substring/"contains" match. This is
#      the one rule that makes "thanks, but how do I return this?" NOT
#      match "thanks": that message is 38 characters (under the length
#      cap) but normalises to the full sentence, not to the bare word
#      "thanks" -- exact-match correctly rejects it, where a substring
#      check would have wrongly accepted it (the real bug this design
#      exists to avoid, not a hypothetical one).
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

# `parents[2]`: this file lives at app/agent/greeting_phrases.py, so
# parents[0]=app/agent, parents[1]=app, parents[2]=the backend root --
# correct both in a local checkout (backend/) and inside the built image
# (WORKDIR /app, confirmed against the Dockerfile's own new `COPY
# --chmod=0644 config/ ./config/` line, Task 3.4.f).
_PHRASES_PATH = Path(__file__).resolve().parents[2] / "config" / "greeting_phrases.yaml"
_MAX_MESSAGE_LENGTH = 40
_TRAILING_PUNCTUATION = re.compile(r"[!.?,;:]+$")
_INTERNAL_WHITESPACE = re.compile(r"\s+")


class _PhraseEntry(BaseModel):
    phrase: str
    category: Literal["greeting", "thanks"]


class _PhraseFile(BaseModel):
    entries: list[_PhraseEntry]


def _normalize(text: str) -> str:
    text = text.strip().lower()
    text = _TRAILING_PUNCTUATION.sub("", text)
    text = _INTERNAL_WHITESPACE.sub(" ", text)
    return text.strip()


@lru_cache
def _load_normalized_phrases() -> frozenset[str]:
    raw = yaml.safe_load(_PHRASES_PATH.read_text(encoding="utf-8"))
    parsed = _PhraseFile.model_validate(raw)
    return frozenset(_normalize(entry.phrase) for entry in parsed.entries)


def is_greeting_or_thanks(message: str) -> bool:
    """True only for a SHORT message that, once normalised, exactly
    matches one of `greeting_phrases.yaml`'s own listed phrases -- see
    this module's own header comment for the full three-part rule.
    """
    if len(message) > _MAX_MESSAGE_LENGTH:
        return False
    return _normalize(message) in _load_normalized_phrases()
