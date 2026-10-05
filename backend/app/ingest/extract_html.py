# backend/app/ingest/extract_html.py
# Task 2.4.b: HTML extraction primitive -- shared logic (Step 2.4 decision
# (b)), called by Step 2.6's URL/crawl adapters, not duplicated or
# rebuilt there. A pure function over an HTML string: no network, no
# database, no Qdrant -- matching 2.3's own "shared library with no real
# caller yet" precedent.
#
# heading_path algorithm, researched and confirmed live (not assumed):
# h1-h6 do NOT nest inside each other in the DOM the way a <div> would --
# an <h3> is a SIBLING of its "parent" <h2> in document order, never its
# child. So heading_path cannot be read off DOM ancestry; it is computed
# by walking headings and paragraphs in DOCUMENT ORDER while maintaining
# a stack of "currently open" headings keyed by level: encountering a
# heading pops every stacked heading at its own level or deeper (a new h2
# ends any open h3+ AND any previous h2), then pushes itself. A <p>'s own
# heading_path is just the stack's texts, outermost first, at the moment
# that <p> is reached -- e.g. an h3 nested under an h2 under an h1
# produces ["Top Section", "Subsection", "Sub-subsection"] for any <p>
# that follows it, until the next heading of level <= 3 changes the
# stack. `BeautifulSoup.find_all([...])` on a single call already returns
# matches in document order, which is exactly what this needs -- no
# separate ancestry walk required.
from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag

# Step 2.4 decision (d): under 50 words of extracted (post-stripping)
# text, not raw bytes/characters -- a module constant, not a Settings
# field, matching DENSE_VECTOR_SIZE's own established precedent (nothing
# else needs this configurable yet).
NEARLY_EMPTY_WORD_THRESHOLD = 50

_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")


@dataclass(frozen=True)
class ContentBlock:
    # heading_path: the breadcrumb of currently-open ancestor headings in
    # DOCUMENT ORDER (outermost first), not DOM ancestry -- see this
    # module's own header comment for why and how it's computed.
    heading_path: tuple[str, ...]
    text: str


@dataclass(frozen=True)
class ExtractedContent:
    title: str | None
    blocks: tuple[ContentBlock, ...]
    # Word count over ALL visible extracted text (post script/style/
    # hidden-element stripping), not just the sum of blocks' own text --
    # a page could carry real content entirely inside headings with no
    # <p> tags at all, which should still count toward "nearly empty."
    # Deliberately excludes the <title> tag's own text: that's page
    # metadata, not body content.
    word_count: int

    @property
    def is_nearly_empty(self) -> bool:
        return self.word_count < NEARLY_EMPTY_WORD_THRESHOLD


# Duplication check after 2.4.e/f/g: the document-order heading-stack
# maintenance logic below -- pop every stacked entry at the new
# heading's own level or deeper, then push it -- was implemented
# identically three times (here, extract_docx.py, extract_text.py) once
# Markdown became the 3rd occurrence, crossing this project's own
# extraction threshold. Shared here, where the algorithm originated;
# the three callers' own OUTER traversal loops (BeautifulSoup tags,
# python-docx paragraphs, raw text lines) stay separate on purpose --
# different iteration shapes don't share a meaningful abstraction, only
# this tuple-level bookkeeping does.
def push_heading(stack: list[tuple[int, str]], level: int, text: str) -> list[tuple[int, str]]:
    return [*(h for h in stack if h[0] < level), (level, text)]


def current_heading_path(stack: list[tuple[int, str]]) -> tuple[str, ...]:
    return tuple(text for _, text in stack)


def _is_hidden(tag: Tag) -> bool:
    # Step 2.4.b decision: covers the four hiding mechanisms a real
    # tenant page is realistically likely to use, each a cheap, direct
    # attribute check -- no CSS cascade or selector matching involved.
    # Deliberately NOT covered: hiding via a CSS class matched by a rule
    # in an external or <style>-block stylesheet (e.g. class="hidden"
    # with ".hidden { display: none }" defined elsewhere) -- correctly
    # detecting that needs a real CSS selector engine evaluated against
    # the DOM, a different order of complexity than a "primitive"
    # warrants (rule 11). Flagged as an Open marker for whoever first
    # hits a real page that needs it.
    style = tag.get("style", "")
    style_normalized = style.lower().replace(" ", "")
    if "display:none" in style_normalized:
        return True
    if "visibility:hidden" in style_normalized:
        return True
    if tag.has_attr("hidden"):
        return True
    if tag.get("aria-hidden", "").lower() == "true":
        return True
    return False


def extract_html(html: str) -> ExtractedContent:
    soup = BeautifulSoup(html, "lxml")

    # <script>/<style> removed ENTIRELY -- tag AND contents. .decompose()
    # destroys the whole subtree outright; unlike just removing the tag
    # (which could leave its text content behind) or .clear() (which
    # would leave an empty-but-present tag), neither the JS nor the CSS
    # text can appear anywhere in the extracted output afterward.
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()

    # Hidden elements: matches are fully materialized into a list FIRST,
    # then decomposed -- mutating the tree while still consuming
    # find_all()'s own result risks exactly the "live view" hazard
    # BeautifulSoup warns about. Confirmed live: decompose() on an
    # ancestor also marks every already-collected descendant tag's own
    # `.decomposed` True and clears its `.attrs` to None -- so each tag
    # must be skipped once `.decomposed`, or the attribute checks in
    # `_is_hidden()` raise AttributeError on a descendant whose ancestor
    # was just removed.
    for tag in list(soup.find_all(True)):
        if tag.decomposed:
            continue
        if _is_hidden(tag):
            tag.decompose()

    title_tag = soup.title
    title = title_tag.get_text(strip=True) if title_tag else None

    # lxml always wraps parsed HTML in <html><body>...</body></html>,
    # confirmed live even for bare fragments with neither tag present --
    # `or soup` is a harmless fallback, never actually exercised.
    body = soup.body or soup

    blocks: list[ContentBlock] = []
    heading_stack: list[tuple[int, str]] = []
    for tag in body.find_all([*_HEADING_TAGS, "p"]):
        if tag.name in _HEADING_TAGS:
            level = int(tag.name[1])
            heading_stack = push_heading(heading_stack, level, tag.get_text(strip=True))
            continue
        text = tag.get_text(strip=True)
        if not text:
            continue
        blocks.append(ContentBlock(heading_path=current_heading_path(heading_stack), text=text))

    word_count = len(body.get_text(separator=" ", strip=True).split())

    return ExtractedContent(title=title, blocks=tuple(blocks), word_count=word_count)
