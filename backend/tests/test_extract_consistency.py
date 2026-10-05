# backend/tests/test_extract_consistency.py
# Duplication check after 2.4.e/f/g (item C1): proves the three
# independently-implemented heading algorithms (extract_html.py,
# extract_docx.py, extract_text.py's own extract_markdown()) produce
# COMPARABLE structure for COMPARABLE input, not just that each one
# works correctly in isolation against its own fixture -- the three
# "nested_headings" fixtures happen to already share identical heading
# text ("Top Section" > "Subsection" > "Sub-subsection", then a sibling
# "Second Subsection"), confirmed live before writing this test, making
# a direct cross-format comparison possible with no synthetic content.
from app.ingest.extract_docx import extract_docx
from app.ingest.extract_html import extract_html
from app.ingest.extract_text import extract_markdown
from tests.conftest import read_docx_fixture, read_html_fixture, read_markdown_fixture


def test_html_docx_and_markdown_produce_comparable_heading_paths_for_comparable_content():
    html = extract_html(read_html_fixture("nested_headings.html"))
    docx = extract_docx(read_docx_fixture("nested_headings.docx"))
    md = extract_markdown(read_markdown_fixture("nested_headings.md"))

    expected_paths = [
        ("Top Section",),
        ("Top Section", "Subsection"),
        ("Top Section", "Subsection", "Sub-subsection"),
        ("Top Section", "Second Subsection"),
    ]

    html_paths = [block.heading_path for block in html.blocks]
    docx_paths = [block.heading_path for block in docx.blocks]
    assert html_paths == docx_paths == expected_paths

    # Markdown's own fixture has one extra block HTML/DOCX don't: the
    # fenced code block under "Subsection" (confirmed live when this
    # test was written) -- excluded here by its own known text, not by
    # position, so the comparison stays meaningful even if unrelated
    # prose in the fixture changes order later.
    md_paths = [
        block.heading_path
        for block in md.blocks
        if "fenced code block" not in block.text
    ]
    assert md_paths == expected_paths
