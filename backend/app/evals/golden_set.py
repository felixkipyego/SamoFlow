# backend/app/evals/golden_set.py
# Task 3.7.b: golden-set item schema + loader, the frozen-corpus schema +
# loader, and evidence-passage -> chunk resolution. No eval runner or
# metrics here (Step 3.7.c onward) -- this module only defines the data
# shapes and proves a committed set actually resolves against a real,
# ingested corpus.
#
# Schema choice, dated deviation from the shape sketched at Task 3.7.a's
# own PART 3 research note (PROJECT_SPEC.md): that note described
# `expected_source_chunks` as "a list[str] of stable chunk identifiers".
# Built differently here, per direct instruction: expected sources are
# referenced by DOCUMENT + a short VERBATIM EVIDENCE PASSAGE, resolved to
# real chunk(s) at load time -- never a stored chunk id. A chunk id baked
# into the golden set would silently go stale the moment the corpus is
# re-chunked (a changed MAX_CHUNK_TOKENS/CHUNK_OVERLAP_TOKENS, a content
# edit that shifts every later chunk_index in that document); a verbatim
# passage re-resolves correctly as long as the actual sentence survives
# somewhere in the re-chunked text, which is the real invariant golden-set
# authors care about ("the chunk that contains this fact"), not a
# resolver-shape implementation detail.
#
# File format: YAML, matching this project's own established house
# convention for every other validated data file (greeting_phrases.yaml,
# rewrite_trigger_phrases.yaml, app/agent/greeting_phrases.py's own
# loader) -- not JSONL, which would be a second format for the same kind
# of "schema-validated list of records" data with no offsetting benefit.
#
# extra="forbid" everywhere (app/auth/schemas.py's own established
# precedent): an unknown field in a hand-edited golden-set item is a
# typo'd key silently ignored, not a loud failure, unless caught here.
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

# backend/app/evals/golden_set.py -> parents[0]=app/evals, [1]=app,
# [2]=backend, [3]=repo root. evals/ is a REPO-ROOT sibling of backend/
# (docs/SPEC.md §21's own layout: "evals/ golden sets, runners") -- pure
# eval/dev-time data, never read by the running production app, so it is
# fine (and correct) that this does not resolve inside the deployed image
# (WORKDIR /app there is backend/; evals/ is never copied in, and nothing
# at request time needs it).
GOLDEN_SET_DIR = Path(__file__).resolve().parents[3] / "evals" / "golden_set"
DEFAULT_ITEMS_PATH = GOLDEN_SET_DIR / "items.yaml"
DEFAULT_SYNTHETIC_CORPUS_PATH = GOLDEN_SET_DIR / "synthetic_corpus.json"

Category = Literal[
    "answerable",
    "related_unanswerable",
    "off_topic",
    "follow_up",
    "multi_source",
    "greeting",
    "injection",
]
# docs/SPEC.md §13's own named categories are answerable, unanswerable,
# follow-ups, escalation triggers and injection attempts. This project's
# own set splits "unanswerable" into two genuinely different mechanisms
# worth measuring separately (off_topic: the GATE itself fails; related_
# unanswerable: the gate passes but the real answer isn't in the text)
# and adds multi_source/greeting, both named explicitly in this task's
# own instruction. escalation triggers are DELIBERATELY ABSENT -- see
# PROJECT_SPEC.md's own §9 marker, owner Step 4.4: no real trigger-phrase
# mechanism exists yet (docs/SPEC.md §7), and faking expected behavior
# for a mechanism that doesn't exist would be a guess dressed up as data.
CATEGORIES: tuple[str, ...] = get_args(Category)

Route = Literal["answer", "gate_fallback", "greeting"]
CorpusName = Literal["synthetic", "company_site"]

_MAX_EVIDENCE_PASSAGE_LENGTH = 300


class ExpectedSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document: str = Field(min_length=1)
    evidence_passage: str = Field(min_length=1, max_length=_MAX_EVIDENCE_PASSAGE_LENGTH)


class GoldenSetItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    category: Category
    corpus: CorpusName
    question: str = Field(min_length=1)
    prior_turns: list[str] = Field(default_factory=list)
    expected_route: Route
    expected_can_answer: bool
    expected_sources: list[ExpectedSource] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_category_shape(self) -> "GoldenSetItem":
        # Cross-field rules tying each category to the real graph's own
        # observable shape (app/agent/graph.py) -- not arbitrary, each
        # one is a direct consequence of how routing/the answer node
        # actually works, so a malformed item (e.g. category="off_topic"
        # but expected_can_answer=True) is a loud, specific failure here,
        # never a silently-wrong fixture discovered later at eval time.
        n_sources = len(self.expected_sources)
        n_docs = len({source.document for source in self.expected_sources})

        def fail(reason: str) -> None:
            raise ValueError(f"item {self.id!r} (category={self.category!r}): {reason}")

        if self.category == "greeting":
            if self.expected_route != "greeting":
                fail("expected_route must be 'greeting'")
            if self.expected_can_answer:
                fail("expected_can_answer must be false (the answer node is never reached)")
            if self.prior_turns:
                fail("prior_turns must be empty (greetings have no corpus/conversation context)")
            if n_sources:
                fail("expected_sources must be empty (no corpus connection)")
        elif self.category == "off_topic":
            if self.expected_route != "gate_fallback":
                fail("expected_route must be 'gate_fallback' (the gate itself must fail)")
            if self.expected_can_answer:
                fail("expected_can_answer must be false (the answer node is never reached)")
            if n_sources:
                fail("expected_sources must be empty (nothing in the corpus is relevant)")
        elif self.category == "related_unanswerable":
            if self.expected_route != "answer":
                fail("expected_route must be 'answer' (the gate passes; the ANSWER declines)")
            if self.expected_can_answer:
                fail("expected_can_answer must be false (this is the unanswerable half)")
            if n_sources != 1:
                fail("expected_sources must name exactly 1 topically-relevant chunk")
        elif self.category == "answerable":
            if self.expected_route != "answer" or not self.expected_can_answer:
                fail("expected_route must be 'answer' and expected_can_answer must be true")
            if self.prior_turns:
                fail("prior_turns must be empty (use category=follow_up for conversation context)")
            if n_sources != 1:
                fail("expected_sources must name exactly 1 source (use multi_source for 2+)")
        elif self.category == "follow_up":
            if self.expected_route != "answer" or not self.expected_can_answer:
                fail("expected_route must be 'answer' and expected_can_answer must be true")
            if not self.prior_turns:
                fail("prior_turns must be non-empty (this category exercises joining)")
            if n_sources != 1:
                fail("expected_sources must name exactly 1 source")
        elif self.category == "multi_source":
            if self.expected_route != "answer" or not self.expected_can_answer:
                fail("expected_route must be 'answer' and expected_can_answer must be true")
            if n_sources < 2 or n_docs < 2:
                fail("expected_sources must span at least 2 distinct documents")
        elif self.category == "injection":
            if self.expected_route != "answer" or not self.expected_can_answer:
                fail("expected_route must be 'answer' and expected_can_answer must be true")
            if n_sources < 1:
                fail("expected_sources must name at least 1 (the compromised) chunk")
        return self


class GoldenSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[GoldenSetItem]

    @model_validator(mode="after")
    def _check_unique_ids(self) -> "GoldenSet":
        ids = [item.id for item in self.items]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate golden-set item id(s): {dupes}")
        return self

    def category_counts(self) -> dict[str, int]:
        counts = dict.fromkeys(CATEGORIES, 0)
        for item in self.items:
            counts[item.category] += 1
        return counts


def load_golden_set(path: Path = DEFAULT_ITEMS_PATH) -> GoldenSet:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return GoldenSet.model_validate(raw)


class FrozenChunk(BaseModel):
    """One real chunk of a frozen corpus snapshot -- produced ONCE, by
    running real source pages through the real ingestion path (extract_*
    -> chunk_text -> embed_dense/embed_sparse, Step 2.4/2.5), then
    committed. Never re-chunked or re-embedded by this loader; see
    `backend/scripts/manual_build_golden_set_synthetic_corpus.py` for the
    one-time generation step.
    """

    model_config = ConfigDict(extra="forbid")

    document: str
    chunk_index: int
    heading_path: list[str]
    text: str
    content_hash: str
    embedding_model: str
    embedding_version: str
    dense_vector: list[float]
    sparse_indices: list[int]
    sparse_values: list[float]


class FrozenCorpus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    corpus: CorpusName
    # The full, ordered list of document identifiers this corpus actually
    # contains -- lets "cover every document at least once" (this task's
    # own item-writing rule) be checked mechanically against real data,
    # not just asserted in prose.
    documents: list[str]
    chunks: list[FrozenChunk]


def load_frozen_corpus(path: Path) -> FrozenCorpus:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return FrozenCorpus.model_validate(raw)


@dataclass(frozen=True)
class ResolvedSource:
    document: str
    evidence_passage: str
    chunk_indices: tuple[int, ...]


class EvidencePassageNotFoundError(ValueError):
    """Raised when a golden-set item's evidence_passage does not appear
    verbatim in any chunk of its named document within the given frozen
    corpus -- the "fails loudly" half of this task's own requirement.
    """


def resolve_item_sources(item: GoldenSetItem, corpus: FrozenCorpus) -> list[ResolvedSource]:
    resolved: list[ResolvedSource] = []
    for source in item.expected_sources:
        matches = tuple(
            chunk.chunk_index
            for chunk in corpus.chunks
            if chunk.document == source.document and source.evidence_passage in chunk.text
        )
        if not matches:
            raise EvidencePassageNotFoundError(
                f"item {item.id!r}: evidence passage {source.evidence_passage!r} not found "
                f"in any chunk of document {source.document!r} in the {corpus.corpus!r} corpus"
            )
        resolved.append(
            ResolvedSource(
                document=source.document, evidence_passage=source.evidence_passage,
                chunk_indices=matches,
            )
        )
    return resolved


def resolve_golden_set(
    golden_set: GoldenSet, corpora: dict[str, FrozenCorpus]
) -> dict[str, list[ResolvedSource]]:
    """Resolves every item whose own `corpus` has a loaded `FrozenCorpus`
    in `corpora`. An item referencing a corpus NOT present in `corpora`
    (e.g. "company_site" before Felix's own site is crawled, Task 3.7.b's
    own explicit scope limit) is skipped, not failed -- only a corpus
    that genuinely exists can be resolved against.
    """
    resolved: dict[str, list[ResolvedSource]] = {}
    for item in golden_set.items:
        corpus = corpora.get(item.corpus)
        if corpus is None:
            continue
        resolved[item.id] = resolve_item_sources(item, corpus)
    return resolved
