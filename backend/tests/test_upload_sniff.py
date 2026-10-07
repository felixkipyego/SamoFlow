# backend/tests/test_upload_sniff.py
# Task 2.7.a [SECURITY]: tests for app/ingest/upload_sniff.py. Offline
# only, pure function, real fixture bytes (not synthetic magic-byte
# stubs alone) for the four positive cases -- reusing 2.4.d/e/f's own
# already-proven-real fixtures (tests/fixtures/pdf, docx, text,
# markdown), not inventing new ones.
import io
import zipfile

import pytest

from app.ingest.upload_sniff import sniff_file_type
from tests.conftest import (
    read_docx_fixture,
    read_markdown_fixture,
    read_pdf_fixture,
    read_text_fixture,
)


def test_real_pdf_sniffs_as_pdf():
    data = read_pdf_fixture("multi_page.pdf")
    assert sniff_file_type(data, "whatever.pdf") == "pdf"


def test_real_docx_sniffs_as_docx():
    data = read_docx_fixture("nested_headings.docx")
    assert sniff_file_type(data, "whatever.docx") == "docx"


def test_real_txt_sniffs_as_txt():
    data = read_text_fixture("sample.txt").encode("utf-8")
    assert sniff_file_type(data, "sample.txt") == "txt"


def test_real_md_sniffs_as_md():
    data = read_markdown_fixture("nested_headings.md").encode("utf-8")
    assert sniff_file_type(data, "nested_headings.md") == "md"


def test_unrecognized_bytes_are_unknown():
    # PNG's own magic bytes: not PDF, not ZIP, and not valid UTF-8 either
    # (0x89 is not a legal UTF-8 start byte) -- a real binary format this
    # function was never asked to recognize.
    data = b"\x89PNG\r\n\x1a\n" + b"some binary payload"
    assert sniff_file_type(data, "whatever.png") == "unknown"


def test_empty_bytes_are_unknown_not_misidentified():
    # Empty content matches none of the magic-byte prefixes and decodes
    # as empty UTF-8 text -- goes to the txt/md branch like any other
    # plain text, not a crash. Named explicitly so a future change to the
    # branching order can't silently turn this into an exception.
    assert sniff_file_type(b"", "empty.txt") in ("txt", "md")


# --- Hostile: content vs. a lying extension -----------------------------


def test_a_real_txt_files_bytes_saved_with_a_pdf_extension_is_not_sniffed_as_pdf():
    data = read_text_fixture("sample.txt").encode("utf-8")
    result = sniff_file_type(data, "totally_a_pdf.pdf")
    assert result != "pdf"
    assert result == "txt"  # content, not the lying extension, drives the result


def test_a_real_pdf_files_bytes_saved_with_a_txt_extension_is_still_sniffed_as_pdf():
    data = read_pdf_fixture("multi_page.pdf")
    assert sniff_file_type(data, "totally_just_text.txt") == "pdf"


def test_a_real_docx_files_bytes_saved_with_a_txt_extension_is_still_sniffed_as_docx():
    data = read_docx_fixture("nested_headings.docx")
    assert sniff_file_type(data, "totally_just_text.txt") == "docx"


# --- Hostile: a ZIP that is not a valid DOCX -----------------------------


def test_a_zip_without_word_document_xml_is_unknown_not_falsely_accepted_as_docx():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", b"just an ordinary zip entry, not a word document")
    data = buffer.getvalue()
    assert data.startswith(b"PK\x03\x04")  # confirms this really does share docx's own zip magic
    assert sniff_file_type(data, "totally_a_docx.docx") == "unknown"


def test_a_truncated_zip_is_unknown_not_a_crash():
    # Starts with the real ZIP magic bytes but is nowhere near a complete,
    # parseable archive -- zipfile.BadZipFile must be handled, not raised
    # out of sniff_file_type().
    data = b"PK\x03\x04" + b"\x00" * 10
    assert sniff_file_type(data, "broken.docx") == "unknown"


# --- TXT vs MD: content alone cannot distinguish them, by design --------


def test_plain_prose_with_no_markdown_syntax_is_indistinguishable_by_content_alone():
    # The exact point investigated before building: identical bytes,
    # differing only in the filename's own extension, land on opposite
    # sides -- proving the distinction genuinely comes from the filename
    # for this one pair, not from any content signal (there is none).
    prose = b"Just an ordinary paragraph of prose with no headings or markup at all."
    assert sniff_file_type(prose, "notes.txt") == "txt"
    assert sniff_file_type(prose, "notes.md") == "md"


def test_md_extension_is_case_insensitive():
    prose = b"# A heading\n\nSome text."
    assert sniff_file_type(prose, "NOTES.MD") == "md"


@pytest.mark.parametrize("filename", ["notes", "notes.markdown", "notes.txt.bak"])
def test_anything_not_literally_dot_md_defaults_to_txt(filename):
    assert sniff_file_type(b"plain prose", filename) == "txt"


# --- Hostile: text containing a null byte --------------------------------


def test_text_with_a_null_byte_is_unknown_not_accepted_as_txt():
    assert sniff_file_type(b"hello\x00world", "whatever.txt") == "unknown"


def test_invalid_utf8_is_unknown():
    assert sniff_file_type(b"\xff\xfe\xfa\xfb", "whatever.txt") == "unknown"
