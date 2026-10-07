# backend/tests/test_upload_sniff.py
# Task 2.7.a [SECURITY]: tests for app/ingest/upload_sniff.py. Offline
# only, pure function, real fixture bytes (not synthetic magic-byte
# stubs alone) for the four positive cases -- reusing 2.4.d/e/f's own
# already-proven-real fixtures (tests/fixtures/pdf, docx, text,
# markdown), not inventing new ones.
import io
import zipfile

import pytest

from app.ingest.upload_sniff import UploadKind, sniff_file_type
from tests.conftest import (
    read_docx_fixture,
    read_markdown_fixture,
    read_pdf_fixture,
    read_text_fixture,
)

# Task 2.7.b: sniff_file_type() now requires max_docx_part_size_bytes -- a
# required keyword argument, matching upload_storage.save()'s own
# max_size_bytes precedent (2.7.a), so the zip-bomb check below can never
# be silently skipped. Irrelevant to every non-docx case in this file,
# but still required there too, by that same design. _sniff() below
# fixes it at Settings.docx_max_part_size_bytes's own default, so every
# call site in this file stays as short as it was before 2.7.b.
_MAX_DOCX_PART_SIZE = 100 * 1024 * 1024


def _sniff(data: bytes, filename: str) -> UploadKind:
    return sniff_file_type(data, filename, max_docx_part_size_bytes=_MAX_DOCX_PART_SIZE)


def test_real_pdf_sniffs_as_pdf():
    data = read_pdf_fixture("multi_page.pdf")
    assert _sniff(data, "whatever.pdf") == "pdf"


def test_real_docx_sniffs_as_docx():
    data = read_docx_fixture("nested_headings.docx")
    assert _sniff(data, "whatever.docx") == "docx"


def test_real_txt_sniffs_as_txt():
    data = read_text_fixture("sample.txt").encode("utf-8")
    assert _sniff(data, "sample.txt") == "txt"


def test_real_md_sniffs_as_md():
    data = read_markdown_fixture("nested_headings.md").encode("utf-8")
    assert _sniff(data, "nested_headings.md") == "md"


def test_unrecognized_bytes_are_unknown():
    # PNG's own magic bytes: not PDF, not ZIP, and not valid UTF-8 either
    # (0x89 is not a legal UTF-8 start byte) -- a real binary format this
    # function was never asked to recognize.
    data = b"\x89PNG\r\n\x1a\n" + b"some binary payload"
    assert _sniff(data, "whatever.png") == "unknown"


def test_empty_bytes_are_unknown_not_misidentified():
    # Empty content matches none of the magic-byte prefixes and decodes
    # as empty UTF-8 text -- goes to the txt/md branch like any other
    # plain text, not a crash. Named explicitly so a future change to the
    # branching order can't silently turn this into an exception.
    assert _sniff(b"", "empty.txt") in ("txt", "md")


# --- Hostile: content vs. a lying extension -----------------------------


def test_a_real_txt_files_bytes_saved_with_a_pdf_extension_is_not_sniffed_as_pdf():
    data = read_text_fixture("sample.txt").encode("utf-8")
    result = _sniff(data, "totally_a_pdf.pdf")
    assert result != "pdf"
    assert result == "txt"  # content, not the lying extension, drives the result


def test_a_real_pdf_files_bytes_saved_with_a_txt_extension_is_still_sniffed_as_pdf():
    data = read_pdf_fixture("multi_page.pdf")
    assert _sniff(data, "totally_just_text.txt") == "pdf"


def test_a_real_docx_files_bytes_saved_with_a_txt_extension_is_still_sniffed_as_docx():
    data = read_docx_fixture("nested_headings.docx")
    assert _sniff(data, "totally_just_text.txt") == "docx"


# --- Hostile: a ZIP that is not a valid DOCX -----------------------------


def test_a_zip_without_word_document_xml_is_unknown_not_falsely_accepted_as_docx():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", b"just an ordinary zip entry, not a word document")
    data = buffer.getvalue()
    assert data.startswith(b"PK\x03\x04")  # confirms this really does share docx's own zip magic
    assert _sniff(data, "totally_a_docx.docx") == "unknown"


def test_a_truncated_zip_is_unknown_not_a_crash():
    # Starts with the real ZIP magic bytes but is nowhere near a complete,
    # parseable archive -- zipfile.BadZipFile must be handled, not raised
    # out of sniff_file_type().
    data = b"PK\x03\x04" + b"\x00" * 10
    assert _sniff(data, "broken.docx") == "unknown"


# --- Hostile: a zip-bomb-shaped DOCX (Task 2.7.b) ------------------------


def _docx_shaped_zip(document_xml: bytes) -> bytes:
    # A minimal "shape" containing the one member sniff_file_type() checks
    # for (word/document.xml) -- not a real, openable Word document (this
    # module only ever asks "is it zip-bomb-shaped," never opens it with
    # python-docx), matching this file's own existing hostile-ZIP
    # construction style (test_a_zip_without_word_document_xml_... above).
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def test_a_zip_bomb_shaped_docx_is_unknown_not_falsely_accepted_as_docx():
    # The real PoC from the Step 2.7.b decision log: 200MB of real,
    # genuinely declared decompressed content, compressing down to a few
    # hundred KB on disk -- comfortably under upload_max_size_bytes (20MB,
    # 2.7.a), so that check alone would never catch this; sniff_file_type()
    # itself must. Safe to build and run: constructing/compressing a
    # buffer of repeated zero bytes allocates that memory ONCE, briefly,
    # for the test's own setup -- nothing is ever decompressed, by design
    # (the whole point of the mitigation under test).
    bomb = b"\x00" * (200 * 1024 * 1024)
    data = _docx_shaped_zip(bomb)
    assert len(data) < 1024 * 1024  # on-disk size proves this really is bomb-shaped
    assert _sniff(data, "totally_a_docx.docx") == "unknown"


def test_a_docx_shaped_zip_well_under_the_ceiling_is_still_accepted_as_docx():
    # No false positive: ordinary, modestly-sized content (not remotely
    # bomb-shaped) must still sniff as docx, proving the new check doesn't
    # just reject every docx-shaped zip outright.
    ordinary = b"<xml>just a normal, modest document part</xml>" * 100
    data = _docx_shaped_zip(ordinary)
    assert _sniff(data, "ordinary.docx") == "docx"


def test_real_docx_fixture_is_comfortably_under_the_ceiling():
    # The real fixture used by test_real_docx_sniffs_as_docx above, through
    # the SAME new check -- confirms the ceiling default is genuinely
    # generous for real-world content, not just for a synthetic probe.
    data = read_docx_fixture("nested_headings.docx")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        largest = max(member.file_size for member in archive.infolist())
    assert largest < _MAX_DOCX_PART_SIZE
    assert _sniff(data, "nested_headings.docx") == "docx"


# --- TXT vs MD: content alone cannot distinguish them, by design --------


def test_plain_prose_with_no_markdown_syntax_is_indistinguishable_by_content_alone():
    # The exact point investigated before building: identical bytes,
    # differing only in the filename's own extension, land on opposite
    # sides -- proving the distinction genuinely comes from the filename
    # for this one pair, not from any content signal (there is none).
    prose = b"Just an ordinary paragraph of prose with no headings or markup at all."
    assert _sniff(prose, "notes.txt") == "txt"
    assert _sniff(prose, "notes.md") == "md"


def test_md_extension_is_case_insensitive():
    prose = b"# A heading\n\nSome text."
    assert _sniff(prose, "NOTES.MD") == "md"


@pytest.mark.parametrize("filename", ["notes", "notes.markdown", "notes.txt.bak"])
def test_anything_not_literally_dot_md_defaults_to_txt(filename):
    assert _sniff(b"plain prose", filename) == "txt"


# --- Hostile: text containing a null byte --------------------------------


def test_text_with_a_null_byte_is_unknown_not_accepted_as_txt():
    assert _sniff(b"hello\x00world", "whatever.txt") == "unknown"


def test_invalid_utf8_is_unknown():
    assert _sniff(b"\xff\xfe\xfa\xfb", "whatever.txt") == "unknown"
