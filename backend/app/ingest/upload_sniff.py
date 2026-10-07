# backend/app/ingest/upload_sniff.py
# Task 2.7.a [SECURITY]: content-based file-type detection for the upload
# adapter (docs/SPEC.md §5.6: "content type checked by content, not just
# extension") -- this step's own primary new attack surface, per the Step
# 2.7 breakdown decision entry: every prior adapter's content either came
# from a domain-verification-gated fetch (urls/crawl) or was parsed by the
# stdlib alone (txt/md); an upload is fully tenant-controlled bytes
# claiming to be a PDF or DOCX, and a renamed/relabeled file must not be
# able to reach the wrong parser.
#
# Hand-rolled magic-byte/structural checks, not a new dependency
# (python-magic considered, deliberately not chosen -- see the Step 2.7
# breakdown decision (c): escalate only if this proves genuinely
# inadequate during real use, not speculatively now, matching the
# stdlib-robotparser-then-protego-once-proven-necessary precedent at
# 2.6.e).
#
# PDF: the `%PDF-` prefix (every real PDF starts with this; pypdf itself
# relies on it being present).
#
# DOCX: the ZIP local-file-header magic bytes (`PK\x03\x04`) are NOT
# sufficient on their own -- confirmed live against this project's own
# `tests/fixtures/docx/nested_headings.docx`: a DOCX IS a ZIP archive, but
# the converse is false -- any arbitrary ZIP file (a renamed .zip full of
# unrelated files) also starts with those same four bytes. The real
# distinguishing signal is DOCX's own internal structure: a valid
# Word-produced .docx always contains a `word/document.xml` member
# (confirmed live: `nested_headings.docx` has exactly this, among 17
# total archive members). So this function opens the archive and checks
# for that member specifically, treating a structurally-valid ZIP that
# lacks it as "unknown", not "docx" -- and a corrupt/truncated ZIP
# (`zipfile.BadZipFile`) the same way, not as a crash.
#
# TXT vs MD: investigated live, confirmed NOT distinguishable by content
# alone. Markdown is plain UTF-8 text with no mandatory syntax at all --
# a .md file containing zero "#"/"*"/etc. markers (pure prose, one
# paragraph) is byte-for-byte indistinguishable from a .txt file with the
# same prose; there is no required signature the way PDF/DOCX each have
# one. The ONE place this function trusts the caller-supplied filename's
# own extension -- stated plainly, not buried: once content has already
# been positively confirmed to be plain, null-byte-free, valid-UTF-8 text
# (i.e. NOT a mis-labeled PDF/DOCX -- that determination is made by
# content alone, above, and is authoritative), the filename's extension
# only ever picks between two formats that are both "safe, harmless plain
# text" either way. This is acceptable precisely because docs/SPEC.md
# §5.6's own security intent ("content type checked by content, not just
# extension") is about PDF/DOCX impersonation -- a binary-content risk a
# parser could be exploited through -- not about telling two kinds of
# inert prose apart, which carries no such risk either way.
#
# Task 2.7.b [SECURITY] -- the resource-exhaustion investigation 2.7.a
# deferred to this task (its own now-resolved marker): confirmed LIVE,
# not assumed, that python-docx has ZERO protection against a zip-bomb-
# shaped `.docx` -- `_ZipPkgReader.blob_for()` calls plain
# `zipfile.ZipFile.read(membername)`, decompressing a member fully into
# memory with no size check of its own. Constructed a real PoC: a single
# member whose TRUE decompressed size is 200MB compresses to ~204KB on
# disk -- comfortably under `upload_max_size_bytes` (20MB) above, so that
# check alone does NOT catch this; python-docx's `Document()` would
# decompress the full 200MB (or far more, for a more extreme ratio)
# before anything else ever looks at it. pypdf, by contrast, needed NO
# new mitigation here -- confirmed live (see the Step 2.7.b decision log
# entry for the full writeup) that it already bounds BOTH known PDF
# pathologies internally: a circular page-tree reference raises
# `PdfReadError("Detected cyclic page references.")`
# (`page_tree_maximum_depth`, default 100), and ANY zlib-compressed
# stream decompressing past `Configuration.zlib_maximum_output_length`
# (default 75MB) raises `LimitReachedError` -- both real `PyPdfError`
# subclasses, both proven end-to-end through this project's own
# extract_pdf() with real constructed PDFs, not just read about.
#
# Mitigation chosen: check every ZIP member's own DECLARED uncompressed
# size (`zipfile.ZipInfo.file_size`, read from the central directory,
# itself requiring zero decompression) against a sane ceiling, in the
# SAME already-open archive this function uses to confirm `word/
# document.xml`'s presence -- no second zip-open, no new dependency,
# matching the task's own suggested design exactly. A docx-shaped file
# that fails this check returns "unknown", the SAME vocabulary already
# used for "a ZIP without word/document.xml" and "a corrupt ZIP" --
# deliberately not a new exception type: this function's own existing
# contract (classify, never raise) stays exactly as it was, and "unknown"
# already means "do not treat this as a usable docx," which is precisely
# what a bomb-shaped file is. `max_docx_part_size_bytes` is a REQUIRED
# keyword argument, not an optional one with a hidden default -- matching
# upload_storage.save()'s own `max_size_bytes` precedent (2.7.a): the
# safety check cannot be silently skipped by a caller who forgets to pass
# it.
#
# A theoretical residual gap, considered and NOT closed here, recorded
# rather than silently assumed away: this checks the DECLARED file_size
# field, which a sufficiently adversarial zip could, in principle, make
# inconsistent with the member's real compressed stream (lie about the
# size while keeping a self-consistent CRC for a larger real payload).
# Investigated live: Python's own zipfile DOES still catch many such
# inconsistencies via its own CRC-32 validation during read() -- but only
# AFTER decompressing, which would not prevent the resource cost. Closing
# this fully would need a bounded, incremental decompressing read that
# ignores declared metadata entirely (mirroring pypdf's own
# `max_length=`-bounded zlib approach) -- a materially more complex,
# custom mechanism for a materially more sophisticated attack than the
# classic zip-bomb this task investigated. Declared-size checking is the
# standard, well-known mitigation for the risk actually being defended
# against here (rule 11); going further now would be exactly the kind of
# custom security scheme rule 11 says to avoid building speculatively.
# Revisit if ever shown to matter in practice.
import zipfile
from io import BytesIO
from typing import Literal

UploadKind = Literal["pdf", "docx", "txt", "md", "unknown"]

_PDF_PREFIX = b"%PDF-"
_ZIP_PREFIX = b"PK\x03\x04"
_DOCX_MARKER_MEMBER = "word/document.xml"


def sniff_file_type(data: bytes, filename: str, *, max_docx_part_size_bytes: int) -> UploadKind:
    """Determines the real type of `data` from its own bytes -- `filename`
    is consulted ONLY to break the txt/md tie once content has already
    confirmed the bytes are plain text (see this module's own header
    comment); it is never trusted for pdf/docx. `max_docx_part_size_bytes`
    bounds a docx candidate's own internal ZIP members (the zip-bomb
    defense, also described in the header comment) -- required, not
    optional, so this safety check cannot be silently skipped; ignored
    for every other candidate type."""
    if data.startswith(_PDF_PREFIX):
        return "pdf"

    if data.startswith(_ZIP_PREFIX):
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                if _DOCX_MARKER_MEMBER not in archive.namelist():
                    return "unknown"
                if any(
                    member.file_size > max_docx_part_size_bytes
                    for member in archive.infolist()
                ):
                    return "unknown"
                return "docx"
        except zipfile.BadZipFile:
            return "unknown"

    if b"\x00" in data:
        return "unknown"
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return "unknown"

    return "md" if filename.lower().endswith(".md") else "txt"
