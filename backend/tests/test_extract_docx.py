# backend/tests/test_extract_docx.py
# Task 2.4.e: tests for extract_docx() (backend/app/ingest/extract_docx.py).
# Offline, no test database, no network -- a pure function over DOCX
# bytes. Against real fixture .docx files under tests/fixtures/docx/
# (not synthetic byte strings), matching this project's own "test
# against real extracted output" discipline. Both fixtures are built via
# python-docx's own document-writing API (see this directory's own
# generate_fixtures.py) -- considerably simpler than the PDF fixtures'
# hand-rolled byte-offset construction, since python-docx can write a
# valid DOCX directly.
from zipfile import BadZipFile

import pytest

from app.ingest.chunking import chunk_text, compute_content_hash
from app.ingest.extract_docx import extract_docx
from tests.conftest import read_docx_fixture


def test_extracts_known_text_content_correctly():
    content = extract_docx(read_docx_fixture("nested_headings.docx"))
    assert len(content.blocks) == 4
    assert "Intro paragraph directly under the top-level section" in content.blocks[0].text
    assert "A new level-2 heading ends the previous" in content.blocks[3].text


def test_heading_path_reflects_nested_heading_levels():
    # Task 2.4.e, point 2: the SAME document-order stack algorithm as
    # extract_html.py's own -- a paragraph under a Heading 3 under a
    # Heading 2 under a Heading 1 gets the full 3-element path, and a
    # later sibling Heading 2 correctly ends both the earlier Heading 2
    # and Heading 3. python-docx's own paragraph order is already flat
    # and sequential, so no DOM-sibling reasoning is needed here the way
    # extract_html.py's own header comment describes for HTML.
    content = extract_docx(read_docx_fixture("nested_headings.docx"))
    paths = [block.heading_path for block in content.blocks]
    assert paths == [
        ("Top Section",),
        ("Top Section", "Subsection"),
        ("Top Section", "Subsection", "Sub-subsection"),
        ("Top Section", "Second Subsection"),
    ]


def test_title_extracted_correctly_when_present():
    content = extract_docx(read_docx_fixture("nested_headings.docx"))
    assert content.title == "Nested Headings Fixture"


def test_title_is_none_not_empty_string_when_absent():
    # Confirmed live: python-docx's own core_properties.title returns ""
    # when unset, not None -- normalized here to match ExtractedContent's
    # established None-means-absent contract (extract_html.py's own
    # behavior for a missing <title> tag).
    content = extract_docx(read_docx_fixture("nearly_empty.docx"))
    assert content.title is None


def test_word_count_and_is_nearly_empty_work_correctly():
    substantial = extract_docx(read_docx_fixture("nested_headings.docx"))
    assert substantial.word_count == 109
    assert substantial.is_nearly_empty is False

    nearly_empty = extract_docx(read_docx_fixture("nearly_empty.docx"))
    assert nearly_empty.word_count == 7
    assert nearly_empty.is_nearly_empty is True


def test_malformed_docx_fails_cleanly_with_a_specific_exception():
    # A DOCX is a ZIP archive -- confirmed live (not assumed) that
    # truncating a real, known-good fixture partway through raises
    # zipfile.BadZipFile, a standard library exception surfaced through
    # python-docx's own ZIP-based parsing, never an unhandled low-level
    # crash. extract_docx() deliberately does not catch or re-wrap this,
    # matching extract_pdf.py's own precedent.
    valid_bytes = read_docx_fixture("nested_headings.docx")
    truncated = valid_bytes[: len(valid_bytes) // 2]
    with pytest.raises(BadZipFile):
        extract_docx(truncated)


def test_chunks_real_docx_extracted_content_end_to_end():
    # Task 2.4.e, point 4(f): proving chunk_text() against real DOCX-
    # extracted content from the start, per the duplication check after
    # 2.4.b/c/d's own finding -- don't let this coverage gap recur for a
    # third format. chunk_text() has no format-specific branching, so
    # this also doubles as confirmation that DOCX's richer, HTML-like
    # multi-block shape chunks exactly like HTML's own does (one chunk
    # per block here, since each block is short).
    content = extract_docx(read_docx_fixture("nested_headings.docx"))
    chunks = chunk_text(content)
    assert len(chunks) == len(content.blocks) == 4
    assert [c.chunk_index for c in chunks] == [0, 1, 2, 3]
    for chunk, block in zip(chunks, content.blocks, strict=True):
        assert chunk.heading_path == block.heading_path
        assert chunk.text == block.text

    # compute_content_hash() also runs cleanly end-to-end against this shape.
    assert compute_content_hash(content) == compute_content_hash(content)
