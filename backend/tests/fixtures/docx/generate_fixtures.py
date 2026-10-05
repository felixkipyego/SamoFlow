# backend/tests/fixtures/docx/generate_fixtures.py
# Task 2.4.e: regenerates nested_headings.docx and nearly_empty.docx from
# scratch. NOT a test -- pytest's own default python_files pattern
# (test_*.py / *_test.py, see backend/pyproject.toml's [tool.pytest]
# table) never matches this filename, confirmed live, so it is never
# collected. Matches the discipline established for the PDF fixtures
# (backend/tests/fixtures/pdf/generate_fixtures.py).
#
# Considerably simpler than the PDF generator: python-docx can WRITE a
# valid DOCX directly (unlike pypdf, which has no real document-writing
# API), so no hand-rolled byte-offset construction is needed here.
#
# Run from anywhere: `python backend/tests/fixtures/docx/generate_fixtures.py`.
# Overwrites both .docx files in this same directory; run
# `python -m pytest backend/tests/test_extract_docx.py backend/tests/test_chunking.py`
# afterward to confirm nothing changed.
import io
from pathlib import Path

from docx import Document


def build_nested_headings_docx() -> bytes:
    document = Document()
    document.core_properties.title = "Nested Headings Fixture"

    document.add_heading("Top Section", level=1)
    document.add_paragraph(
        "Intro paragraph directly under the top-level section heading only, "
        "written with enough real words to make this fixture comfortably "
        "substantial rather than borderline nearly-empty."
    )
    document.add_heading("Subsection", level=2)
    document.add_paragraph(
        "Paragraph under the subsection, nested below the top section, also "
        "carrying a genuine sentence of its own real content for the word "
        "count to add up across the whole document."
    )
    document.add_heading("Sub-subsection", level=3)
    document.add_paragraph(
        "Paragraph under the deepest heading, nested under all three levels, "
        "completing the structure a heading_path test needs to assert "
        "against with real, known values."
    )
    document.add_heading("Second Subsection", level=2)
    document.add_paragraph(
        "A new level-2 heading ends the previous level-2 and level-3 "
        "headings both, starting a fresh subsection with its own final "
        "paragraph of real content here."
    )

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def build_nearly_empty_docx() -> bytes:
    document = Document()
    document.add_paragraph("Just a few words here, nothing more.")

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


if __name__ == "__main__":
    here = Path(__file__).resolve().parent

    nested = build_nested_headings_docx()
    (here / "nested_headings.docx").write_bytes(nested)

    nearly_empty = build_nearly_empty_docx()
    (here / "nearly_empty.docx").write_bytes(nearly_empty)

    print(f"nested_headings.docx: {len(nested)} bytes")
    print(f"nearly_empty.docx: {len(nearly_empty)} bytes")
