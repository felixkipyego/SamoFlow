# backend/tests/test_extract_text.py
# Task 2.4.f: tests for extract_text()/extract_markdown() (backend/app/
# ingest/extract_text.py). Offline, no test database, no network -- pure
# functions over bytes or str. Against real fixture files under
# tests/fixtures/text/ and tests/fixtures/markdown/, matching this
# project's own "test against real extracted output" discipline -- the
# one exception is the non-UTF-8 test, which uses an inline bytes
# literal deliberately (the point of that test IS one specific, narrow
# byte sequence, best read directly rather than via an opaque fixture
# file whose own raw bytes wouldn't render meaningfully anyway).
import pytest

from app.ingest.chunking import chunk_text, compute_content_hash
from app.ingest.extract_text import extract_markdown, extract_text
from tests.conftest import read_markdown_fixture, read_text_fixture


def test_plain_text_produces_flat_shape_with_correct_word_count():
    content = extract_text(read_text_fixture("sample.txt"))
    assert content.title is None
    assert len(content.blocks) == 1
    assert content.blocks[0].heading_path == ()
    assert content.word_count == 69
    assert content.is_nearly_empty is False


def test_markdown_heading_path_reflects_nested_heading_levels():
    # Task 2.4.f, point 2: the SAME document-order stack algorithm as
    # extract_html.py's/extract_docx.py's own -- a paragraph under a
    # level-3 heading under a level-2 under a level-1 gets the full
    # 3-element path, and a later sibling level-2 heading correctly ends
    # both the earlier level-2 and level-3.
    content = extract_markdown(read_markdown_fixture("nested_headings.md"))
    paths = [block.heading_path for block in content.blocks]
    assert paths == [
        ("Top Section",),
        ("Top Section", "Subsection"),
        ("Top Section", "Subsection"),  # the fenced code block, same stack
        ("Top Section", "Subsection", "Sub-subsection"),
        ("Top Section", "Second Subsection"),
    ]


def test_markdown_hostile_hash_cases_are_never_treated_as_headings():
    # Task 2.4.f, point 3: all three real-world edge cases, proven
    # against the real fixture, not assumed from the regex alone.
    content = extract_markdown(read_markdown_fixture("nested_headings.md"))
    full_text = " ".join(block.text for block in content.blocks)

    # (i) a "#" inside a fenced code block -- preserved as real content,
    # never promoted to a heading (no block has heading_path == () or a
    # heading_path containing "this hash is inside a fenced code block").
    assert "this hash is inside a fenced code block" in full_text
    assert not any(
        "fenced code block" in " ".join(path) for path in (b.heading_path for b in content.blocks)
    )

    # (ii) a "#" inside an inline code span -- stays literal text.
    assert "`#not a heading`" in full_text

    # (iii) a "#" with no following space -- stays literal text.
    assert "#hashtag" in full_text

    # None of the three produced a spurious heading: exactly the 4 real
    # headings appear across the whole document's own heading_paths.
    all_heading_names = {name for block in content.blocks for name in block.heading_path}
    assert all_heading_names == {"Top Section", "Subsection", "Sub-subsection", "Second Subsection"}


def test_markdown_title_is_the_first_level_one_heading():
    content = extract_markdown(read_markdown_fixture("nested_headings.md"))
    assert content.title == "Top Section"


def test_markdown_word_count_and_is_nearly_empty_work_correctly():
    content = extract_markdown(read_markdown_fixture("nested_headings.md"))
    assert content.word_count == 125
    assert content.is_nearly_empty is False


def test_non_utf8_bytes_fail_cleanly_with_unicode_decode_error():
    # Task 2.4.f, point 4: strict UTF-8, no silent fallback -- a real
    # possibility for a user-uploaded file saved in some other legacy
    # encoding. 0xe9 alone is not valid UTF-8 (it starts a 2-byte
    # sequence with no continuation byte following).
    bad_bytes = b"caf\xe9 with an invalid utf-8 byte"
    with pytest.raises(UnicodeDecodeError):
        extract_text(bad_bytes)
    with pytest.raises(UnicodeDecodeError):
        extract_markdown(bad_bytes)


def test_chunks_real_text_and_markdown_extracted_content_end_to_end():
    # Task 2.4.f, point 6(e): chunk_text() proven against both real TXT
    # and real MD extracted content from the start -- the same
    # discipline established for PDF and DOCX; this coverage gap must
    # not appear a third time.
    txt_content = extract_text(read_text_fixture("sample.txt"))
    txt_chunks = chunk_text(txt_content)
    assert len(txt_chunks) == 1
    assert txt_chunks[0].heading_path == ()
    assert txt_chunks[0].chunk_index == 0

    md_content = extract_markdown(read_markdown_fixture("nested_headings.md"))
    md_chunks = chunk_text(md_content)
    assert len(md_chunks) == len(md_content.blocks)
    assert [c.chunk_index for c in md_chunks] == list(range(len(md_chunks)))
    for chunk, block in zip(md_chunks, md_content.blocks, strict=True):
        assert chunk.heading_path == block.heading_path
        assert chunk.text == block.text

    assert compute_content_hash(txt_content) == compute_content_hash(txt_content)
    assert compute_content_hash(md_content) == compute_content_hash(md_content)
