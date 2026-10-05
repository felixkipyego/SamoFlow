# backend/tests/fixtures/pdf/generate_fixtures.py
# Task 2.4.d (committed at the duplication check after 2.4.b/c/d, item B1):
# regenerates multi_page.pdf and no_text.pdf from scratch. NOT a test --
# pytest's own default python_files pattern (test_*.py / *_test.py, see
# backend/pyproject.toml's [tool.pytest] table) never matches this
# filename, confirmed live, so it is never collected.
#
# Hand-constructs minimal, valid PDF objects directly (no PDF-writing
# library -- pypdf itself has no real "draw text on a page" API, and
# adding one, e.g. reportlab, solely to generate a test fixture would be
# disproportionate, rule 11). Computes every object's exact byte offset
# programmatically as it builds the file, rather than hand-counting them,
# so the resulting xref table is correct by construction.
#
# Run from anywhere: `python backend/tests/fixtures/pdf/generate_fixtures.py`.
# Overwrites multi_page.pdf/no_text.pdf in this same directory; run
# `python -m pytest backend/tests/test_extract_pdf.py backend/tests/test_chunking.py`
# afterward to confirm nothing changed.
from pathlib import Path


def build_pdf(page_texts: list[str]) -> bytes:
    page_obj_nums = []
    content_obj_nums = []

    # Reserve object numbers up front: 1=Catalog, 2=Pages, then one Page +
    # one Contents stream per page, then the Font last.
    catalog_num = 1
    pages_num = 2
    next_num = 3
    for _ in page_texts:
        page_obj_nums.append(next_num)
        next_num += 1
        content_obj_nums.append(next_num)
        next_num += 1
    font_obj_num = next_num

    kids = " ".join(f"{n} 0 R" for n in page_obj_nums)

    body_by_num: dict[int, bytes] = {}
    body_by_num[catalog_num] = f"<< /Type /Catalog /Pages {pages_num} 0 R >>".encode()
    body_by_num[pages_num] = (
        f"<< /Type /Pages /Kids [{kids}] /Count {len(page_texts)} >>".encode()
    )
    for page_num, content_num, text in zip(
        page_obj_nums, content_obj_nums, page_texts, strict=True
    ):
        body_by_num[page_num] = (
            f"<< /Type /Page /Parent {pages_num} 0 R "
            f"/MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_obj_num} 0 R >> >> "
            f"/Contents {content_num} 0 R >>"
        ).encode()
        escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        stream = f"BT /F1 12 Tf 72 700 Td ({escaped}) Tj ET".encode()
        body_by_num[content_num] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"
        )
    body_by_num[font_obj_num] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    return _assemble(body_by_num, total_objects=font_obj_num, catalog_num=catalog_num)


def build_pdf_no_text(page_count: int = 1) -> bytes:
    # A page with a content stream that draws a filled rectangle only --
    # zero text-showing operators, genuinely no extractable text, the
    # same end result pypdf's extract_text() produces for a true scanned
    # image page, without needing to embed real raster image data.
    catalog_num = 1
    pages_num = 2
    page_nums = []
    content_nums = []
    next_num = 3
    for _ in range(page_count):
        page_nums.append(next_num)
        next_num += 1
        content_nums.append(next_num)
        next_num += 1

    kids = " ".join(f"{n} 0 R" for n in page_nums)
    body_by_num: dict[int, bytes] = {}
    body_by_num[catalog_num] = f"<< /Type /Catalog /Pages {pages_num} 0 R >>".encode()
    body_by_num[pages_num] = f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode()
    for page_num, content_num in zip(page_nums, content_nums, strict=True):
        body_by_num[page_num] = (
            f"<< /Type /Page /Parent {pages_num} 0 R "
            f"/MediaBox [0 0 612 792] /Resources << >> "
            f"/Contents {content_num} 0 R >>"
        ).encode()
        stream = b"1 0 0 rg 100 100 200 200 re f"
        body_by_num[content_num] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"
        )

    return _assemble(body_by_num, total_objects=next_num - 1, catalog_num=catalog_num)


def _assemble(body_by_num: dict[int, bytes], total_objects: int, catalog_num: int) -> bytes:
    out = bytearray()
    out += b"%PDF-1.4\n"
    offsets = [0] * (total_objects + 1)
    for num in range(1, total_objects + 1):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n".encode() + body_by_num[num] + b"\nendobj\n"

    xref_offset = len(out)
    out += f"xref\n0 {total_objects + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for num in range(1, total_objects + 1):
        out += f"{offsets[num]:010d} 00000 n \n".encode()
    out += b"trailer\n"
    out += f"<< /Size {total_objects + 1} /Root {catalog_num} 0 R >>\n".encode()
    out += b"startxref\n"
    out += f"{xref_offset}\n".encode()
    out += b"%%EOF"
    return bytes(out)


if __name__ == "__main__":
    here = Path(__file__).resolve().parent

    multi_page = build_pdf(
        [
            "This is the first page of the fixture document. "
            "It contains several genuine sentences of real content, written "
            "specifically so a test can assert against exact known text. "
            "The page keeps going a little further to add more real words.",
            "This is the second page, with different content entirely. "
            "It also contains multiple real sentences of its own, distinct "
            "from the first page, so a flattening test can confirm content "
            "from both pages is genuinely present in the final result.",
        ]
    )
    (here / "multi_page.pdf").write_bytes(multi_page)

    no_text = build_pdf_no_text(1)
    (here / "no_text.pdf").write_bytes(no_text)

    print(f"multi_page.pdf: {len(multi_page)} bytes")
    print(f"no_text.pdf: {len(no_text)} bytes")
