# backend/app/ingest/extract_pdf.py
# Task 2.4.d: PDF extraction primitive -- shared logic (Step 2.4 decision
# (b)'s own precedent for extract_html.py), called by Step 2.7's upload
# adapter, not duplicated or rebuilt there. Reuses 2.4.b's own
# ExtractedContent/ContentBlock shape unchanged, so chunk_text() (2.4.c)
# and any future caller never need format-specific branching -- a PDF's
# own ExtractedContent is structurally identical to an HTML one, just
# with different field values.
#
# heading_path: ALWAYS flat/empty (`()`), for every block, with NO font-
# size/bold/layout heuristic -- matching the Step 2.4 decision entry
# exactly. Do NOT "helpfully" add one later without a real, concrete need
# driving it (rule 11): a font-size heuristic is a genuinely hard,
# failure-prone problem (inconsistent font metadata across PDF
# producers, no standard "this is a heading" signal at all), and nothing
# in this project's own roadmap currently asks for structured PDF
# headings.
#
# title: ALWAYS None. The task-list row's own framing is "text only" --
# PDF metadata's /Info /Title field is frequently absent or unreliable
# across real-world PDF producers, unlike HTML's near-universal <title>
# tag; reading it would add a new, untested surface this primitive does
# not need. Not an oversight -- a deliberate scope match to "text only."
#
# Multi-page handling (Step 2.4.d's own research question, decided):
# flatten ALL pages into exactly ONE ContentBlock per document, not one
# block per page. A page boundary is a PRINT-LAYOUT artifact (where a
# physical sheet happened to end), not semantic document structure --
# the identical reasoning the flat-heading_path decision above already
# applies to font size. Treating a page break as if it were a heading
# boundary would be inventing structure that isn't there. Each page's
# own extracted text is joined with "\n" before building the single
# block, so the last word of one page can never run directly into the
# first word of the next with no separating whitespace.
#
# Malformed input: pypdf's own exceptions (pypdf.errors.PyPdfError and
# its subclasses, e.g. PdfReadError/PdfStreamError) are left to propagate
# UNCAUGHT and unwrapped -- confirmed live these are already clear,
# specific, documented exception types, never a raw crash (IndexError,
# UnicodeDecodeError, etc.). Wrapping them in a second, project-specific
# exception type would add a layer of translation with no real benefit
# (rule 11): there is no existing caller-facing contract here that needs
# a different shape, unlike e.g. app/ingest/repository.py's own
# IntegrityError-to-domain-error translation.
import io

from pypdf import PdfReader

from app.ingest.extract_html import ContentBlock, ExtractedContent


def extract_pdf(pdf_bytes: bytes) -> ExtractedContent:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    page_texts = [page.extract_text() for page in reader.pages]
    full_text = "\n".join(text for text in page_texts if text)

    blocks: tuple[ContentBlock, ...] = ()
    if full_text:
        blocks = (ContentBlock(heading_path=(), text=full_text),)

    return ExtractedContent(title=None, blocks=blocks, word_count=len(full_text.split()))
