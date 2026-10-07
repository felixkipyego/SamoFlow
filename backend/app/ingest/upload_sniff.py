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
import zipfile
from io import BytesIO
from typing import Literal

UploadKind = Literal["pdf", "docx", "txt", "md", "unknown"]

_PDF_PREFIX = b"%PDF-"
_ZIP_PREFIX = b"PK\x03\x04"
_DOCX_MARKER_MEMBER = "word/document.xml"

# TODO(2.7.b): this module's own ZIP check calls only namelist() (reads
# the central directory's metadata) and never decompresses/reads any
# member's actual content, so it is not itself exposed to a
# decompression-bomb-style resource-exhaustion risk, and the overall
# input is already bounded by upload_storage.save()'s own max_size_bytes
# before this function ever sees it. The real investigation -- what
# pypdf/python-docx guard against once they actually PARSE full content
# (2.7.c's job, downstream of sniffing) -- is 2.7.b's, not addressed here.


def sniff_file_type(data: bytes, filename: str) -> UploadKind:
    """Determines the real type of `data` from its own bytes -- `filename`
    is consulted ONLY to break the txt/md tie once content has already
    confirmed the bytes are plain text (see this module's own header
    comment); it is never trusted for pdf/docx."""
    if data.startswith(_PDF_PREFIX):
        return "pdf"

    if data.startswith(_ZIP_PREFIX):
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                if _DOCX_MARKER_MEMBER in archive.namelist():
                    return "docx"
        except zipfile.BadZipFile:
            pass
        return "unknown"

    if b"\x00" in data:
        return "unknown"
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return "unknown"

    return "md" if filename.lower().endswith(".md") else "txt"
