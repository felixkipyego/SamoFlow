# backend/tests/test_chunking.py
# Task 2.4.c: tests for chunk_text()/compute_content_hash() (backend/app/
# ingest/chunking.py). Offline, no test database -- a one-time network
# fetch of the cl100k_base merge-rank file happens on first real call to
# tiktoken.encoding_for_model() (confirmed live, see chunking.py's own
# header comment); everything else here is a pure function over
# 2.4.b's own ExtractedContent. Against real fixture HTML files under
# tests/fixtures/html/ wherever practical, matching this project's own
# "test against real extracted output, not only synthetic text"
# discipline -- the one exception is the whitespace-formatting-
# invariance hash test, which inlines two HTML strings deliberately
# (the point of that test IS the byte-level contrast between them, best
# read directly rather than diffing two opaque fixture files).
import hashlib
from pathlib import Path

from app.ingest.chunking import (
    CHUNK_OVERLAP_TOKENS,
    MAX_CHUNK_TOKENS,
    _get_encoding,
    chunk_text,
    compute_content_hash,
)
from app.ingest.extract_html import ContentBlock, ExtractedContent, extract_html

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "html"


def _extract(name: str) -> ExtractedContent:
    return extract_html((FIXTURES_DIR / name).read_text())


def test_chunking_the_same_real_input_twice_is_byte_identical():
    content = _extract("substantial.html")
    assert chunk_text(content) == chunk_text(content)


def test_overlap_is_genuinely_the_same_64_tokens_not_merely_two_adjacent_chunks():
    # Task 2.4.c, point 5: proven at the TOKEN level, not just "a chunk
    # exists after another chunk" -- a real fixture with one block long
    # enough (549 tokens) to require exactly two chunks under it.
    content = _extract("long_block.html")
    chunks = chunk_text(content)
    assert len(chunks) == 2
    assert chunks[0].chunk_index == 0
    assert chunks[1].chunk_index == 1
    assert chunks[0].heading_path == chunks[1].heading_path == ("A Single Long Section",)

    encoding = _get_encoding()
    first_tokens = encoding.encode(chunks[0].text)
    second_tokens = encoding.encode(chunks[1].text)
    assert len(first_tokens) == MAX_CHUNK_TOKENS
    assert first_tokens[-CHUNK_OVERLAP_TOKENS:] == second_tokens[:CHUNK_OVERLAP_TOKENS]
    # The second chunk is short -- 101 tokens, nowhere near MAX_CHUNK_TOKENS --
    # but its own overlap portion is still the FULL 64 tokens; only the
    # NEW content beyond the overlap is small. This is the real shape of
    # the "short final block" edge case: the overlap itself never
    # shrinks, by construction (chunking.py's own _token_windows() proof).
    assert len(second_tokens) > CHUNK_OVERLAP_TOKENS


def test_short_input_under_512_tokens_produces_exactly_one_chunk_with_no_overlap():
    content = _extract("nearly_empty.html")
    assert len(content.blocks) == 1
    chunks = chunk_text(content)
    assert len(chunks) == 1
    assert chunks[0].chunk_index == 0
    encoding = _get_encoding()
    assert len(encoding.encode(chunks[0].text)) < MAX_CHUNK_TOKENS


def test_multiple_blocks_never_merge_across_a_heading_boundary():
    # Task 2.4.c, point 3: the chosen design -- chunk WITHIN each
    # ContentBlock independently, never flowing text across a block
    # boundary -- verified against a real 4-block, 3-level fixture.
    content = _extract("nested_headings.html")
    chunks = chunk_text(content)
    assert len(chunks) == len(content.blocks)
    for chunk, block in zip(chunks, content.blocks, strict=True):
        assert chunk.heading_path == block.heading_path
        assert chunk.text == block.text


def test_chunk_index_increments_sequentially_from_zero_across_the_whole_document():
    content = _extract("nested_headings.html")
    chunks = chunk_text(content)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_empty_content_produces_zero_chunks_not_an_exception():
    empty = ExtractedContent(title=None, blocks=(), word_count=0)
    assert chunk_text(empty) == []


def test_content_hash_is_deterministic_for_the_same_real_input():
    content = _extract("substantial.html")
    assert compute_content_hash(content) == compute_content_hash(content)


def test_content_hash_differs_for_genuinely_different_content():
    a = _extract("substantial.html")
    b = _extract("nested_headings.html")
    assert compute_content_hash(a) != compute_content_hash(b)


def test_content_hash_is_unaffected_by_inter_tag_whitespace_formatting_noise():
    # Proves the "hash normalized/extracted text, not raw bytes" property
    # has a REAL effect, not just a theoretical one: two HTML sources
    # with identical visible content but different inter-tag whitespace/
    # indentation (the kind of noise a template re-render could
    # introduce with zero actual content change) must hash identically,
    # even though a raw-bytes hash of the same two strings does NOT.
    #
    # Deliberately NOT tested here: whitespace differences WITHIN a
    # single text run (e.g. "hello   world" vs "hello world"). Confirmed
    # live that extract_html()'s own get_text(strip=True) only strips
    # leading/trailing whitespace per text node -- it does not collapse
    # internal runs -- so that specific kind of noise is NOT currently
    # normalized away, and asserting otherwise here would be testing a
    # false premise.
    minified = (
        "<html><head><title>X</title></head>"
        "<body><h1>H</h1><p>para one</p><p>para two</p></body></html>"
    )
    pretty = (
        "<html>\n  <head>\n    <title>X</title>\n  </head>\n  <body>\n"
        "    <h1>H</h1>\n\n    <p>para one</p>\n\n\n    <p>para two</p>\n"
        "  </body>\n</html>"
    )
    content_a = extract_html(minified)
    content_b = extract_html(pretty)
    assert content_a == content_b

    assert compute_content_hash(content_a) == compute_content_hash(content_b)
    assert (
        hashlib.sha256(minified.encode("utf-8")).hexdigest()
        != hashlib.sha256(pretty.encode("utf-8")).hexdigest()
    )


def test_compute_content_hash_excludes_non_content_derived_values():
    # Task 2.4.c: must be deterministic and derived ONLY from title/
    # blocks -- a direct sanity check that the hash really is a pure
    # function of ExtractedContent's own content fields, not of object
    # identity or any hidden timing/id() source: two SEPARATE objects
    # with the same content fields must hash identically.
    content = ExtractedContent(
        title="Same Title",
        blocks=(ContentBlock(heading_path=("H",), text="same text"),),
        word_count=2,
    )
    content_copy = ExtractedContent(
        title="Same Title",
        blocks=(ContentBlock(heading_path=("H",), text="same text"),),
        word_count=2,
    )
    assert content is not content_copy
    assert compute_content_hash(content) == compute_content_hash(content_copy)
