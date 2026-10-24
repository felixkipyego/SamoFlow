# backend/tests/test_pillow_reachability_guard.py
# 2026-10-24 (Felix's decision, D-pillow-audit-ignores): pillow stays at
# 12.3.0 with its known findings ignored by ID in `make audit` and
# .grype.yaml, because pillow 13 cannot be installed (every fastembed
# release declares pillow<13.0). That exception is only safe while pillow
# stays UNREACHABLE from tenant input. These guards keep that claim true:
#   1. nothing in backend/app imports PIL;
#   2. extract_pdf() -- the one parser tenant PDFs reach -- works with PIL
#      import-blocked, on a PDF that really embeds a JPEG (pypdf hands
#      image streams to Pillow only when images are decoded);
#   3. the only fastembed model constructed anywhere is the text-only
#      SparseTextEmbedding("Qdrant/bm25") -- an image model (or any new
#      fastembed model type) would put pillow on a live path.
# If one of these must change (OCR, image uploads, an image model), that
# feature is BLOCKED until pillow is upgraded and the ignores removed --
# never weaken a guard here to make it pass.
import ast
import io
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

APP_DIR = REPO_ROOT / "backend" / "app"
# Everywhere a fastembed model could be constructed in shipped or operator-
# run code. backend/tests is deliberately excluded: tests may reference
# fastembed to test this guard itself.
FASTEMBED_SCAN_ROOTS = (APP_DIR, REPO_ROOT / "backend" / "scripts", REPO_ROOT / "evals")
ALLOWED_FASTEMBED_NAMES = {"SparseTextEmbedding"}
ALLOWED_SPARSE_MODEL = "Qdrant/bm25"
# The one module-level constant the real call site passes (app/ingest/
# embedding.py); its own value is asserted below.
ALLOWED_SPARSE_MODEL_NAMES = {"SPARSE_EMBEDDING_MODEL"}


def _python_files(*roots: Path) -> list[Path]:
    return sorted(p for root in roots if root.exists() for p in root.rglob("*.py"))


def _pil_imports(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.split(".")[0] == "PIL"]
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "PIL":
            found.append(node.module)
        elif isinstance(node, ast.Call):
            # importlib.import_module("PIL...") / __import__("PIL...")
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in {"import_module", "__import__"} and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and str(arg.value).split(".")[0] == "PIL":
                    found.append(str(arg.value))
    return found


def _fastembed_violations(source: str) -> list[str]:
    violations = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "fastembed":
                    violations.append(f"`import {alias.name}` (import named classes instead)")
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "fastembed":
            for alias in node.names:
                if alias.name not in ALLOWED_FASTEMBED_NAMES:
                    violations.append(f"`from {node.module} import {alias.name}`")
        elif isinstance(node, ast.Call) and getattr(node.func, "id", None) == "SparseTextEmbedding":
            args = [kw.value for kw in node.keywords if kw.arg == "model_name"] + node.args[:1]
            ok = len(args) == 1 and (
                (isinstance(args[0], ast.Constant) and args[0].value == ALLOWED_SPARSE_MODEL)
                or (isinstance(args[0], ast.Name) and args[0].id in ALLOWED_SPARSE_MODEL_NAMES)
            )
            if not ok:
                violations.append("SparseTextEmbedding(...) with a model other than Qdrant/bm25")
    return violations


# --- 1. no PIL import under backend/app -------------------------------------


def test_nothing_under_backend_app_imports_pil():
    offenders = {
        str(path.relative_to(REPO_ROOT)): imports
        for path in _python_files(APP_DIR)
        if (imports := _pil_imports(path.read_text()))
    }
    assert not offenders, (
        f"backend/app imports Pillow: {offenders}. Pillow 12.3.0 carries "
        "ignored, unpatched findings (D-pillow-audit-ignores): any image-"
        "decoding feature is BLOCKED until pillow is upgraded."
    )


@pytest.mark.parametrize(
    "source",
    [
        "import PIL",
        "import PIL.Image",
        "from PIL import Image",
        "from PIL.Image import open as o",
        "import importlib\nimportlib.import_module('PIL.Image')",
        "__import__('PIL')",
    ],
)
def test_pil_import_detector_catches_every_import_form(source):
    assert _pil_imports(source)


def test_pil_import_detector_ignores_lookalikes():
    assert _pil_imports("import PILLOW_X\nfrom pillowtalk import x\nx = 'PIL'") == []


# --- 2. extract_pdf() with PIL import-blocked -------------------------------


def _jpeg_bytes() -> bytes:
    from PIL import Image  # the TEST builds the fixture; app code never does

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(buf, "JPEG")
    return buf.getvalue()


def _pdf_with_text_and_jpeg(jpeg: bytes) -> bytes:
    content = (
        b"BT /F1 12 Tf 72 720 Td (Page with a JPEG image) Tj ET "
        b"q 64 0 0 64 72 600 cm /Im1 Do Q"
    )
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> /XObject << /Im1 6 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /XObject /Subtype /Image /Width 8 /Height 8 /ColorSpace /DeviceRGB "
        b"/BitsPerComponent 8 /Filter /DCTDecode /Length %d >>\nstream\n" % len(jpeg)
        + jpeg
        + b"\nendstream",
    ]
    out = b"%PDF-1.7\n"
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    trailer = f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
    out += (trailer + f"startxref\n{xref}\n%%EOF\n").encode()
    return out


# Runs in a FRESH interpreter so nothing earlier in the test session (e.g.
# fastembed, which imports PIL at its own import time) has already loaded
# PIL into sys.modules or into pypdf's own module globals.
_BLOCKED_PIL_SCRIPT = """
import sys
sys.modules["PIL"] = None  # any `import PIL...` now raises ImportError
from pathlib import Path
import pypdf
from app.ingest.extract_pdf import extract_pdf

data = Path(sys.argv[1]).read_bytes()
content = extract_pdf(data)
print("TEXT:" + " ".join(block.text for block in content.blocks))
try:
    pypdf.PdfReader(sys.argv[1]).pages[0].images[0].image
    print("CONTROL:decoded")
except ImportError:
    print("CONTROL:ImportError")
"""


def test_extract_pdf_works_with_pil_import_blocked(tmp_path):
    pdf = tmp_path / "with_jpeg.pdf"
    pdf.write_bytes(_pdf_with_text_and_jpeg(_jpeg_bytes()))
    result = subprocess.run(  # noqa: S603 (fixed args: sys.executable + a script + a path)
        [sys.executable, "-c", _BLOCKED_PIL_SCRIPT, str(pdf)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        cwd=REPO_ROOT / "backend",
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert "TEXT:Page with a JPEG image" in lines
    # Non-vacuous: the SAME PDF's image really needs Pillow to decode, so the
    # block was real and extract_pdf() genuinely never reached it.
    assert "CONTROL:ImportError" in lines


# --- 3. the only fastembed model is SparseTextEmbedding("Qdrant/bm25") ------


def test_the_sparse_model_constant_is_qdrant_bm25():
    from app.ingest.embedding import SPARSE_EMBEDDING_MODEL

    assert SPARSE_EMBEDDING_MODEL == ALLOWED_SPARSE_MODEL


def test_only_sparse_bm25_fastembed_model_is_used_anywhere():
    offenders = {
        str(path.relative_to(REPO_ROOT)): v
        for path in _python_files(*FASTEMBED_SCAN_ROOTS)
        if (v := _fastembed_violations(path.read_text()))
    }
    assert not offenders, (
        f"new fastembed usage {offenders}: only SparseTextEmbedding('Qdrant/bm25') "
        "is allowed while pillow 12.3.0's findings are ignored (D-pillow-audit-ignores)."
    )


def test_the_real_call_site_is_actually_scanned():
    # Non-vacuous: the guard above must be looking at the file that really
    # constructs the model.
    source = (APP_DIR / "ingest" / "embedding.py").read_text()
    assert "SparseTextEmbedding(model_name=SPARSE_EMBEDDING_MODEL)" in source
    assert _fastembed_violations(source) == []


@pytest.mark.parametrize(
    "source",
    [
        "from fastembed import ImageEmbedding",
        "from fastembed import TextEmbedding",
        "from fastembed import LateInteractionTextEmbedding",
        "from fastembed.image import ImageEmbedding",
        "import fastembed",
        "SparseTextEmbedding(model_name='prithivida/Splade_PP_en_v1')",
        "SparseTextEmbedding('Qdrant/bm42-all-minilm-l6-v2-attentions')",
        "SparseTextEmbedding(model_name=SOME_OTHER_NAME)",
        "SparseTextEmbedding()",
    ],
)
def test_fastembed_detector_rejects_any_other_model_type(source):
    assert _fastembed_violations(source)
