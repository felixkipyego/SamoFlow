# backend/app/ingest/extract_text.py
# Task 2.4.f: TXT/MD extraction primitives -- shared logic (Step 2.4
# decision (b)'s own precedent), called by Step 2.7's upload adapter, not
# duplicated or rebuilt there. Both reuse 2.4.b's own ExtractedContent/
# ContentBlock shape unchanged.
#
# TWO SEPARATE FUNCTIONS (extract_text/extract_markdown), not one function
# with a format flag: matching this project's own established per-format
# naming precedent (extract_html, extract_pdf, extract_docx each get a
# dedicated top-level function). A format-flag function would reintroduce
# exactly the kind of caller-side format branching separate functions
# exist to avoid -- the caller already knows which format it has.
#
# extract_text() (.txt): PDF's own flat shape -- plain text has no
# structure at all, so no heading_path, no title, and ALL content
# flattens into exactly one ContentBlock (0 if empty), the same "flatten
# everything, no internal block division" choice 2.4.d made for PDF's
# own page boundaries, applied here to the complete absence of any
# structure signal whatsoever.
#
# extract_markdown() (.md): HTML's/DOCX's own richer multi-block shape --
# Markdown genuinely has heading structure via ATX syntax (# through
# ######). heading_path uses the IDENTICAL document-order stack algorithm
# already proven twice (extract_html.py, extract_docx.py): a new heading
# pops every stacked entry at its own level or deeper, then pushes
# itself; a block's own heading_path is the stack's texts at that moment.
# No DOM-sibling reasoning needed here either (same reason DOCX didn't
# need it) -- Markdown source lines are already a flat, ordered sequence.
#
# Heading detection: HAND-ROLLED, confirmed still adequate on close
# inspection, not a full Markdown parser library (no new dependency,
# matching the Step 2.4 decision entry) -- but a bare per-line regex
# ALONE is insufficient and would be a real bug, not merely incomplete:
# a "#" at the start of a line INSIDE a fenced code block (``` or ~~~)
# must not be read as a heading. The fix is still small -- one boolean
# "currently inside a fence" flag toggled on fence-delimiter lines, not
# a parser -- so the no-new-dependency decision holds with that one
# addition, not overridden. The inline-code-span case (`` `#not a
# heading` ``) and the missing-space case ("#hashtag") both need NO
# special handling at all: requiring the regex to match from the START
# of the line already rejects both (a code-span line starts with a
# backtick, not "#"; "#hashtag" has no space after its hash run) --
# confirmed live, not assumed, against all three cases before writing
# this module.
#
# Fence delimiters and blank lines both act as paragraph/block
# boundaries, flushing whatever text has accumulated so far into its own
# ContentBlock; a fenced block's own content becomes its own block too
# (kept together, not glued to surrounding prose) -- its content is
# preserved as ordinary body text, not discarded, with only the fence
# delimiter lines themselves excluded (pure syntax, not content). Its
# own heading_path is whatever the stack is at that point, same as any
# other block.
#
# Title: the FIRST level-1 ("# ") heading encountered, if any -- a
# common real-world convention (README.md's own H1 is routinely treated
# as the page title). Unlike HTML/PDF/DOCX, where title metadata lives
# in a field structurally separate from any body heading, Markdown's
# title IS also a real, visible body heading -- so, deliberately
# DIFFERENT from the other three extractors, its own text is NOT
# excluded from word_count: a reader genuinely sees it on the page,
# unlike an HTML <title> tag, PDF's /Info/Title, or DOCX's core_
# properties.title, none of which ever render in any of those formats'
# own body content. It still participates normally in the heading_path
# stack for everything that follows it, exactly like any other heading.
#
# Encoding: bytes input is decoded strictly as UTF-8; a decoding failure
# (a real possibility for a user-uploaded file in some other legacy
# encoding) is left to raise UnicodeDecodeError UNCAUGHT, not silently
# retried against a permissive fallback like latin-1. latin-1 can decode
# any byte sequence at all without ever raising -- which is exactly the
# problem: a silent fallback would "succeed" on genuinely wrong-encoding
# input and quietly ingest mojibake into the knowledge base with nothing
# to signal the mistake, worse than a loud, specific, immediately-
# actionable failure (the same "fail cleanly, don't paper over it"
# philosophy extract_pdf.py/extract_docx.py already apply to malformed
# bytes). str input is passed through with no decoding at all.
import re

from app.ingest.extract_html import (
    ContentBlock,
    ExtractedContent,
    current_heading_path,
    push_heading,
)

_ATX_HEADING_RE = re.compile(r"^(#{1,6}) (.*)$")
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})")


def _as_text(content: bytes | str) -> str:
    return content.decode("utf-8") if isinstance(content, bytes) else content


def extract_text(content: bytes | str) -> ExtractedContent:
    text = _as_text(content).strip()
    blocks: tuple[ContentBlock, ...] = ()
    if text:
        blocks = (ContentBlock(heading_path=(), text=text),)
    return ExtractedContent(title=None, blocks=blocks, word_count=len(text.split()))


def extract_markdown(content: bytes | str) -> ExtractedContent:
    text = _as_text(content)

    title: str | None = None
    blocks: list[ContentBlock] = []
    heading_stack: list[tuple[int, str]] = []
    all_text_parts: list[str] = []
    current_block_lines: list[str] = []
    in_fence = False

    def flush() -> None:
        if current_block_lines:
            block_text = "\n".join(current_block_lines).strip()
            if block_text:
                blocks.append(
                    ContentBlock(heading_path=current_heading_path(heading_stack), text=block_text)
                )
                all_text_parts.append(block_text)
        current_block_lines.clear()

    for line in text.splitlines():
        if _FENCE_RE.match(line):
            flush()
            in_fence = not in_fence
            continue

        if in_fence:
            current_block_lines.append(line)
            continue

        if not line.strip():
            flush()
            continue

        heading_match = _ATX_HEADING_RE.match(line)
        if heading_match:
            flush()
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2).strip()
            heading_stack = push_heading(heading_stack, level, heading_text)
            all_text_parts.append(heading_text)
            if level == 1 and title is None:
                title = heading_text
            continue

        current_block_lines.append(line)

    flush()

    word_count = len(" ".join(all_text_parts).split())
    return ExtractedContent(title=title, blocks=tuple(blocks), word_count=word_count)
