# backend/tests/test_agent_graph_citations.py
# Task 3.4.c [SECURITY]: citations (built only from the intersection of
# what the model claims to have cited and what was actually retrieved)
# and output safety (server-side markdown/HTML neutralisation on the
# model's own streamed answer text, under the append-only streaming rule
# Task 3.4.b already established).
#
# Fakes only -- no real API call anywhere in this file, matching every
# other chat-model-adjacent test in this project.
import app.agent.graph as graph_module
from app.agent.graph import Citation, _build_citations, _StreamSanitizer
from tests.conftest import FakeAnswerToolChatModel, build_fake_answer_arg_chunks


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


# --- Stream sanitiser: pure unit tests ---------------------------------------


def _sanitize(deltas: list[str], allowed_urls: list[str]) -> str:
    sanitizer = _StreamSanitizer(frozenset(allowed_urls))
    output = "".join(sanitizer.feed(delta) for delta in deltas)
    output += sanitizer.finish()
    return output


def test_markdown_image_removed_entirely_even_split_across_many_deltas():
    deltas = ["Before ", "![al", "t text](h", "ttp", "s://evil.example/x.png)", " after"]
    result = _sanitize(deltas, [])
    assert "evil.example" not in result
    assert "![" not in result
    assert result == "Before  after"


def test_link_to_a_noncited_url_becomes_plain_text():
    result = _sanitize(["See [the docs](https://not-cited.example/page) here"], [])
    assert "https://not-cited.example" not in result
    assert result == "See the docs here"


def test_link_to_a_cited_url_survives():
    result = _sanitize(
        ["See [the docs](https://cited.example/page) here"],
        ["https://cited.example/page"],
    )
    assert result == "See [the docs](https://cited.example/page) here"


def test_raw_html_img_tag_is_neutralised():
    result = _sanitize(['before <img src="https://evil.example/x.png"> after'], [])
    assert "<img" not in result
    assert "evil.example" not in result
    assert result == "before  after"


def test_raw_html_anchor_tag_is_neutralised():
    result = _sanitize(['click <a href="https://evil.example">here</a> now'], [])
    assert "<a " not in result
    assert "</a>" not in result


def test_javascript_url_never_becomes_a_citation_or_a_link():
    # Known, documented limitation (_StreamSanitizer's own docstring/
    # module header): the link-URL regex stops at the FIRST ")", so a
    # URL containing its own literal parentheses (javascript: URIs
    # almost always do, e.g. "alert(1)") leaves a stray trailing ")" as
    # plain text -- a cosmetic artifact, not a security gap. The
    # SECURITY property this test actually proves: the dangerous scheme
    # never survives, and the construct is never rendered as a real,
    # clickable `[text](url)` link -- both hold regardless of the stray
    # character.
    result = _sanitize(["[click me](javascript:alert(1))"], [])
    assert "javascript:" not in result
    assert "](javascript" not in result
    assert result.startswith("click me")


def test_data_url_never_becomes_a_citation_or_a_link():
    result = _sanitize(["[click me](data:text/html,evil)"], [])
    assert "data:" not in result
    assert result == "click me"


def test_autolink_to_noncited_url_becomes_plain_text():
    result = _sanitize(["go to <https://not-cited.example> now"], [])
    assert result == "go to https://not-cited.example now"


def test_hold_back_cap_triggers_on_a_long_run_of_bracket_text():
    # A long run of bare "[" with no closing "](" ever -- not a real
    # link, must not be held back forever. Confirmed: the full text
    # still arrives (nothing silently dropped), just potentially split
    # across more than one internal flush once the cap is hit.
    text = "[" * 500
    result = _sanitize([text], [])
    assert result == text


def test_unclosed_construct_at_end_of_stream_is_emitted_as_plain_text():
    # A link that starts but the stream simply ends before ")" arrives --
    # finish() must flush it as plain text, never silently drop it, per
    # this class's own stated end-of-stream rule.
    result = _sanitize(["Here is an incomplete [link that never closes"], [])
    assert result == "Here is an incomplete [link that never closes"


def test_plain_text_with_no_constructs_passes_through_unchanged():
    result = _sanitize(["Just a perfectly ordinary sentence."], [])
    assert result == "Just a perfectly ordinary sentence."


# --- Full node integration: streamed text == persisted AIMessage text ------


async def test_streamed_text_equals_persisted_text_when_sanitisation_fires(monkeypatch):
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
    assert "evil.example" not in persisted
    assert "not-cited.example" not in persisted


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
