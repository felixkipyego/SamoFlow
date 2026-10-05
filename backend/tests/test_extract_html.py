# backend/tests/test_extract_html.py
# Task 2.4.b: tests for extract_html() (backend/app/ingest/extract_html.py).
# Offline only, no test database, no network -- a pure function over an
# HTML string. Against real fixture HTML files under tests/fixtures/html/
# (not inline strings), matching this project's own "test against real
# extracted output, not only synthetic text" discipline.
from app.ingest.extract_html import NEARLY_EMPTY_WORD_THRESHOLD, ContentBlock, extract_html
from tests.conftest import read_html_fixture


def test_nested_headings_produce_the_correct_heading_path_per_paragraph():
    # Task 2.4.b: heading_path is a document-order breadcrumb, not DOM
    # ancestry (h1-h6 are siblings in the DOM, never nested in each
    # other) -- an h3 under an h2 under an h1 must produce a 3-element
    # path, and a later sibling h2 must end both the earlier h2 and h3.
    content = extract_html(read_html_fixture("nested_headings.html"))
    paths = [block.heading_path for block in content.blocks]
    assert paths == [
        ("Top Section",),
        ("Top Section", "Subsection"),
        ("Top Section", "Subsection", "Sub-subsection"),
        ("Top Section", "Second Subsection"),
    ]


def test_script_and_style_contents_never_appear_anywhere_in_extracted_output():
    # THE HOSTILE CASE: proves <script>/<style> are removed ENTIRELY --
    # tag AND contents -- not just that the tags themselves are gone.
    # Every "SECRET_*_MARKER" string below lives only inside a <script>
    # or <style> block in the fixture; none must survive anywhere in the
    # title or any block's text.
    content = extract_html(read_html_fixture("hostile.html"))
    full_text = (content.title or "") + " " + " ".join(b.text for b in content.blocks)
    assert "SECRET_SCRIPT_MARKER" not in full_text
    assert "SECRET_STYLE_MARKER" not in full_text
    assert "alert(" not in full_text
    assert "color: red" not in full_text
    assert content.title == "Hostile Fixture"
    assert content.blocks == (
        ContentBlock(
            heading_path=("Real Heading",),
            text="This is a normal, visible paragraph that should survive extraction untouched.",
        ),
    )


def test_hidden_elements_are_excluded_by_every_covered_mechanism():
    # Task 2.4.b: covers style="display:none" (with and without a space
    # after the colon), style="visibility:hidden", the hidden attribute,
    # aria-hidden="true", and a hidden ANCESTOR (nested content excluded
    # via its parent, not matched directly itself) -- see extract_html.py's
    # own _is_hidden() for which subset is covered and why.
    content = extract_html(read_html_fixture("hidden_elements.html"))
    texts = [block.text for block in content.blocks]
    assert texts == [
        "This visible paragraph should appear in the extracted output.",
        "This closing visible paragraph should also appear in the extracted output.",
    ]
    full_text = " ".join(texts)
    assert "SECRET_HIDDEN" not in full_text


def test_nearly_empty_page_is_flagged():
    content = extract_html(read_html_fixture("nearly_empty.html"))
    assert content.word_count < NEARLY_EMPTY_WORD_THRESHOLD
    assert content.is_nearly_empty is True


def test_substantial_page_is_not_flagged_as_nearly_empty():
    content = extract_html(read_html_fixture("substantial.html"))
    assert content.word_count >= NEARLY_EMPTY_WORD_THRESHOLD
    assert content.is_nearly_empty is False


def test_page_with_no_title_tag_returns_none_instead_of_raising():
    content = extract_html(read_html_fixture("no_title.html"))
    assert content.title is None
    assert content.blocks  # the rest of the page still extracts normally


def test_malformed_html_is_handled_by_bs4s_own_error_tolerance_without_crashing():
    # Task 2.4.b: confirmed live, not assumed -- BeautifulSoup/lxml's
    # standard error tolerance for unclosed tags (<p>, a stray <b>, a
    # <div><span> with no closing tags at all) recovers a sensible tree
    # with no exception raised, rather than this function needing its
    # own try/except around the parse.
    content = extract_html(read_html_fixture("malformed.html"))
    assert content.title == "Malformed Fixture"
    assert len(content.blocks) == 2
    assert all(block.heading_path == ("Unclosed Heading",) for block in content.blocks)
