# backend/tests/test_extract_pdf.py
# Task 2.4.d: tests for extract_pdf() (backend/app/ingest/extract_pdf.py).
# Offline, no test database, no network -- a pure function over PDF
# bytes. Against real fixture PDF files under tests/fixtures/pdf/ (not
# synthetic byte strings), matching this project's own "test against
# real extracted output" discipline. Both fixtures are hand-constructed,
# minimal-but-valid PDFs (see PROJECT_SPEC.md's own Task 2.4.d decision
# entry for why: no PDF-WRITING library is a dependency anywhere in this
# project, and adding one solely to generate a test fixture would be
# disproportionate -- a small script computed the exact byte offsets a
# correct xref table needs, rather than hand-counting them).
from pathlib import Path

import pytest
from pypdf.errors import PyPdfError

from app.ingest.extract_pdf import extract_pdf

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "pdf"


def _load(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


def test_extracts_known_text_content_correctly():
    content = extract_pdf(_load("multi_page.pdf"))
    assert len(content.blocks) == 1
    assert content.blocks[0].heading_path == ()
    assert "This is the first page of the fixture document." in content.blocks[0].text
    assert "This is the second page, with different content entirely." in content.blocks[0].text
    assert content.title is None


def test_word_count_and_is_nearly_empty_work_against_real_extracted_text():
    substantial = extract_pdf(_load("multi_page.pdf"))
    assert substantial.word_count == 80
    assert substantial.is_nearly_empty is False

    empty = extract_pdf(_load("no_text.pdf"))
    assert empty.word_count == 0
    assert empty.is_nearly_empty is True


def test_multiple_pages_are_flattened_into_a_single_block():
    # Task 2.4.d, point 3: the chosen design -- ALL pages flatten into
    # ONE ContentBlock per document, never one block per page -- a page
    # boundary is a print-layout artifact, not semantic structure, the
    # same reasoning the flat-heading_path decision already applies to
    # font size. Confirms content from BOTH pages is present in that one
    # block, correctly joined (no word from the end of page one running
    # into the first word of page two with no separating whitespace).
    content = extract_pdf(_load("multi_page.pdf"))
    assert len(content.blocks) == 1
    text = content.blocks[0].text
    assert "document. It contains" in text  # end of page 1, intact
    assert "entirely. It also" in text  # end of page 2, intact
    assert "document.It" not in text  # never glued together at the page break


def test_malformed_pdf_fails_cleanly_with_a_specific_exception():
    # Task 2.4.d, point 4(d): a genuinely truncated, structurally broken
    # PDF -- not just empty bytes -- cut from a real, known-good fixture
    # partway through its own object stream. Confirmed live (not
    # assumed): pypdf raises its own specific, documented exception type
    # (a PyPdfError subclass, pypdf.errors.PdfStreamError in practice),
    # never an unhandled low-level crash (IndexError, UnicodeDecodeError,
    # etc.). extract_pdf() deliberately does not catch or re-wrap this --
    # see extract_pdf.py's own header comment for why.
    valid_bytes = _load("multi_page.pdf")
    truncated = valid_bytes[: len(valid_bytes) // 2]
    with pytest.raises(PyPdfError):
        extract_pdf(truncated)


def test_pdf_with_no_extractable_text_is_handled_gracefully_not_an_exception():
    # Task 2.4.d, point 4(e): no_text.pdf's own single page draws a
    # filled rectangle only -- zero text-showing operators -- the same
    # end result pypdf's extract_text() produces for a true scanned-
    # image-only page, without needing to embed real raster image data
    # (impractical to construct reliably by hand; this is the smallest
    # adequate proxy with an identical observable outcome).
    content = extract_pdf(_load("no_text.pdf"))
    assert content.blocks == ()
    assert content.word_count == 0
    assert content.is_nearly_empty is True
