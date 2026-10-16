# backend/app/agent/escalation_phrases.py
# Step 4.4: docs/SPEC.md §6.2 Rule 3 ("Escalation intent -> Coordinator")
# and §7's own trigger-phrase mechanism. Loads app/agent/phrases/
# escalation_phrases.yaml, the same `@lru_cache` singleton / schema-
# validated-at-load shape as app/agent/greeting_phrases.py's own
# is_greeting_or_thanks() (Task 3.4.f) -- a malformed file fails loudly
# (ValidationError/YAMLError/re.error), never silently degrading to
# "nothing matches" (which would silently disable escalation detection
# in production, not crash -- the same reasoning greeting_phrases.py's
# own header comment already gives for its own file).
#
# UNLIKE greeting_phrases.yaml's exact-whole-message match: escalation
# intent must be found EMBEDDED in a real, possibly long sentence, so
# matching here is substring/pattern-based, not exact-equality -- see
# phrases/escalation_phrases.yaml's own header comment for the full
# phrase-vs-pattern design, and this module's own MATCHING RULE below.
#
# [SECURITY]: this whole module is a PURE, RULES-ONLY text matcher --
# no LLM call, no semantic interpretation of the message at all (docs/
# SPEC.md §6.2: "Routing (rules, no LLM hop)"). This is a structural,
# not merely incidental, defense against prompt injection: a visitor
# message cannot "instruct" a plain `re.search()`/substring check to do
# anything other than literally match or not match -- there is no
# instruction-following step here for injected text to hijack, in
# either direction (forcing a false match, or suppressing a true one).
# See tests/test_escalation_phrases.py's own injection-focused tests for
# the live proof, not just this reasoning.
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Literal

import yaml
from pydantic import BaseModel, model_validator

# NOT Path(__file__).resolve().parents[N] or Path.cwd(): both are real
# production bugs, found live inside the actual built image (2026-10-16)
# -- see app/agent/greeting_phrases.py's own header comment for the
# full reasoning (identical here). `importlib.resources` resolves a
# package's own data file through Python's real import machinery
# instead of hand-computed path arithmetic, so it is correct for an
# editable install, a non-editable install and the built image alike --
# the yaml file now lives INSIDE the `app` package (app/agent/phrases/)
# specifically so it is real package data, not a sibling directory a
# wheel build has no reason to include.
_PHRASES_RESOURCE = resources.files("app.agent").joinpath(
    "phrases", "escalation_phrases.yaml"
)

Category = Literal["asks_for_person", "complaint", "negative"]


class _PhraseEntry(BaseModel):
    phrase: str | None = None
    pattern: str | None = None
    category: Category
    golden_ref: str

    @model_validator(mode="after")
    def _exactly_one_of_phrase_or_pattern(self) -> "_PhraseEntry":
        if (self.phrase is None) == (self.pattern is None):
            raise ValueError(
                f"entry (category={self.category!r}, golden_ref={self.golden_ref!r}) "
                "must set exactly one of phrase/pattern, not both and not neither"
            )
        if self.pattern is not None:
            # Fails loudly AT LOAD TIME on a malformed regex -- never at
            # match time, deep inside a real request. re.error (in
            # Python 3.12, NOT a ValueError subclass -- confirmed live,
            # not assumed) must be re-raised as ValueError, the only
            # exception type pydantic's own @model_validator converts
            # into a real ValidationError.
            try:
                re.compile(self.pattern, re.IGNORECASE)
            except re.error as exc:
                raise ValueError(f"invalid regex pattern {self.pattern!r}: {exc}") from exc
        return self


class _PhraseFile(BaseModel):
    entries: list[_PhraseEntry]


@dataclass(frozen=True)
class _CompiledEntry:
    matcher: re.Pattern
    category: Category


def _compile_entry(entry: _PhraseEntry) -> _CompiledEntry:
    if entry.phrase is not None:
        # Word-boundaried, case-insensitive substring match -- the
        # phrase is treated as a single literal unit (re.escape), so
        # "speak to a human" cannot itself be read as a regex even
        # though it is compiled as one internally. Matches
        # greeting_phrases.py's own "never a bare substring/'contains'
        # check" discipline in spirit, adapted here to find a phrase
        # EMBEDDED in a longer real sentence rather than requiring the
        # whole message to equal it.
        matcher = re.compile(rf"\b{re.escape(entry.phrase)}\b", re.IGNORECASE)
    elif entry.pattern is not None:
        matcher = re.compile(entry.pattern, re.IGNORECASE)
    else:
        # Unreachable: _PhraseEntry's own validator guarantees exactly
        # one of phrase/pattern is set.
        raise ValueError(f"entry (category={entry.category!r}) has neither phrase nor pattern")
    return _CompiledEntry(matcher=matcher, category=entry.category)


@lru_cache
def _load_compiled_entries() -> tuple[_CompiledEntry, ...]:
    raw = yaml.safe_load(_PHRASES_RESOURCE.read_text(encoding="utf-8"))
    parsed = _PhraseFile.model_validate(raw)
    return tuple(_compile_entry(entry) for entry in parsed.entries)


def escalation_phrase_category(message: str) -> Category | None:
    """The category of the FIRST entry (file order) whose phrase/pattern
    is found anywhere in `message` -- `re.search()`, never `fullmatch()`:
    this is "does this phrase/pattern appear in the message", not "is
    the whole message exactly this phrase" (phrases/escalation_phrases.
    yaml's own header comment, and the module
    docstring above, both state why this differs from greeting_phrases.
    py's own exact-match rule). Returns `None` for a message matching
    none of this file's entries -- the normal case for most messages.

    Reads ONLY the literal text passed in -- never retrieved/RAG corpus
    content (callers must never pass anything but the visitor's own
    latest message; see app/agent/graph.py's own `_is_escalation_intent()`
    call site and PROJECT_SPEC.md's own adversarial-pass test proving
    corpus content embedding a trigger phrase never reaches this
    function at all).
    """
    for entry in _load_compiled_entries():
        if entry.matcher.search(message):
            return entry.category
    return None
