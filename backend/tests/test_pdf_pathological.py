# backend/tests/test_pdf_pathological.py
# 2026-10-24 (pypdf 6.19.0 -> 6.21.2, L2 pre-PR): hostile/malformed PDFs a
# tenant could upload must end as a clean outcome -- ingest_upload()'s own
# "failed" status (the PDF extractor raised a PyPdfError, the one exception
# family app/ingest/upload_adapter.py's _DISPATCH expects for "pdf") or an
# empty extraction -- never an uncaught exception or a hang. Matters now
# because pypdf 6.20-6.21.2 added new work limits (per-stream filter count
# and decoding work, xref-table size, cyclic page trees): a limit that
# raised something OTHER than a PyPdfError would crash the ingest job
# instead of failing the one upload. Every case below was observed live on
# 6.21.2 first, then encoded here.
import time
import uuid

import pytest

from app.ingest import upload_storage
from app.ingest.extract_pdf import extract_pdf
from app.ingest.upload_adapter import ingest_upload
from tests.fixtures.pdf.generate_fixtures import build_pdf

# Generous: every case finishes in milliseconds on 6.21.2; this only has to
# catch a hang-shaped regression, not measure performance.
_MAX_SECONDS = 5.0


def _assemble(objects: list[bytes], *, size: int | None = None) -> bytes:
    # A minimal PDF with a correct xref table, built from raw object bodies
    # (object 1 is the root). `size` overrides the trailer's /Size.
    out = b"%PDF-1.7\n"
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    trailer_size = size if size is not None else len(objects) + 1
    out += f"trailer\n<< /Size {trailer_size} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


_CATALOG = b"<< /Type /Catalog /Pages 2 0 R >>"

# Each must end as ingest_upload()'s own status="failed", with the pypdf
# exception type named in the reason.
FAILING_CASES = {
    "truncated": build_pdf(["hello world", "second page"])[:300],
    "garbage_after_header": b"%PDF-1.7\n" + bytes(range(256)) * 40,
    "cyclic_page_tree": _assemble([_CATALOG, b"<< /Type /Pages /Kids [2 0 R] /Count 1 >>"]),
    "deep_nesting": _assemble(
        [
            b"<< /Type /Catalog /Pages 2 0 R /X " + b"[" * 20000 + b"]" * 20000 + b" >>",
            b"<< /Type /Pages /Kids [] /Count 0 >>",
        ]
    ),
}

# Each parses without raising and yields no text -- the same outcome as the
# committed no_text.pdf fixture, handled by the existing empty-content path.
EMPTY_CASES = {
    "huge_xref_size": _assemble([_CATALOG, b"<< /Type /Pages /Kids [] /Count 0 >>"], size=10**9),
    "filter_bomb": _assemble(
        [
            _CATALOG,
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 10 10] /Contents 4 0 R >>",
            b"<< /Length 4 /Filter [" + b"/FlateDecode " * 200 + b"] >>\nstream\nabcd\nendstream",
        ]
    ),
}


@pytest.mark.parametrize("name", sorted(FAILING_CASES))
async def test_pathological_pdf_is_a_clean_failed_ingest(tmp_path, name):
    upload_id = upload_storage.save(
        FAILING_CASES[name], storage_dir=tmp_path, max_size_bytes=50_000_000
    )
    started = time.monotonic()
    # No database/Qdrant handles: a "failed" extraction returns before
    # ingest_upload() ever touches either (its own early return), so passing
    # None here also proves no partial write was attempted.
    result = await ingest_upload(
        None,
        None,
        "unused",
        tenant_id=uuid.uuid4(),
        source_id=uuid.uuid4(),
        upload_id=upload_id,
        original_filename=f"{name}.pdf",
        storage_dir=tmp_path,
        max_docx_part_size_bytes=10_000_000,
    )
    assert time.monotonic() - started < _MAX_SECONDS
    assert result.status == "failed"
    assert result.reason.split(":")[0] in {"PdfReadError", "PdfStreamError"}


@pytest.mark.parametrize("name", sorted(EMPTY_CASES))
def test_pathological_pdf_without_text_extracts_empty_without_raising(name):
    started = time.monotonic()
    content = extract_pdf(EMPTY_CASES[name])
    assert time.monotonic() - started < _MAX_SECONDS
    assert content.blocks == ()
