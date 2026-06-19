"""Document parser — multi-format text extraction.

Dispatch table routes each file extension to the appropriate parser.
Priority chain per DEVELOPMENT_V3.md §3.3 (FR-03):

    .md / .txt / .rst   → built-in UTF-8 reader
    .pdf                 → markitdown → Docling fallback → pymupdf
    .docx / .xlsx / .pptx / .html → markitdown
    .py / .js / .ts / .go / .rs   → code structure extractor
    .csv                 → table summarizer
    .json                → truncated raw text

Files > 50 MB are skipped. Unsupported extensions are skipped.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

# Extensions we can parse
TEXT_EXTENSIONS = {".md", ".txt", ".rst", ".log", ".yaml", ".yml", ".toml", ".cfg"}
OFFICE_EXTENSIONS = {".docx", ".xlsx", ".pptx", ".html", ".htm"}
LEGACY_OFFICE_EXTENSIONS = {".doc", ".ppt"}  # Need LibreOffice to convert
LEGACY_XLS = {".xls"}  # markitdown handles these
CODE_EXTENSIONS = {".py", ".js", ".ts", ".go", ".rs", ".java", ".c", ".h", ".cpp", ".hpp"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".bmp", ".gif", ".webp"}
DATA_EXTENSIONS = {".csv", ".json"}
PDF_EXTENSION = ".pdf"

# All supported extensions (images included when OCR available)
SUPPORTED_EXTENSIONS = (
    TEXT_EXTENSIONS | OFFICE_EXTENSIONS | LEGACY_OFFICE_EXTENSIONS
    | LEGACY_XLS | CODE_EXTENSIONS | DATA_EXTENSIONS | IMAGE_EXTENSIONS
    | {PDF_EXTENSION}
)


def parse_file(file_path: str | Path) -> str:
    """Parse a file to plain text.

    Args:
        file_path: Absolute or relative path to the file.

    Returns:
        Extracted plain text content.

    Raises:
        ValueError: If file is too large or extension unsupported.
        FileNotFoundError: If file doesn't exist.
    """
    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not path.is_file():
        raise ValueError(f"Not a file: {path}")

    file_size = path.stat().st_size
    if file_size > MAX_FILE_SIZE:
        raise ValueError(f"File too large ({file_size / 1024 / 1024:.1f} MB > 50 MB): {path}")

    suffix = path.suffix.lower()

    if suffix in TEXT_EXTENSIONS:
        return _parse_text(path)
    elif suffix == ".pdf":
        try:
            text = _parse_pdf(path)
        except ValueError as e:
            logger.debug("PDF parse failed (will try OCR): %s — %s", path.name, e)
            text = ""
        return _ocr_fallback(path, text) if _should_ocr(text) else text
    elif suffix in OFFICE_EXTENSIONS:
        try:
            text = _parse_office(path)
        except ValueError as e:
            logger.debug("Office parse failed (will try OCR): %s — %s", path.name, e)
            text = ""
        return _ocr_fallback(path, text) if _should_ocr(text) else text
    elif suffix in LEGACY_OFFICE_EXTENSIONS:
        try:
            text = _parse_legacy_office(path)
        except ValueError as e:
            logger.debug("Legacy office parse failed (will try OCR): %s — %s", path.name, e)
            text = ""
        return _ocr_fallback(path, text) if _should_ocr(text) else text
    elif suffix in LEGACY_XLS:
        try:
            text = _parse_office(path)
        except ValueError as e:
            logger.debug("XLS parse failed (will try OCR): %s — %s", path.name, e)
            text = ""
        return _ocr_fallback(path, text) if _should_ocr(text) else text
    elif suffix in IMAGE_EXTENSIONS:
        try:
            return _parse_image(path)
        except ValueError as e:
            logger.debug("Image parse failed (will try OCR fallback): %s — %s", path.name, e)
            return _ocr_fallback(path, "")
    elif suffix in CODE_EXTENSIONS:
        return _parse_code(path)
    elif suffix == ".csv":
        return _parse_csv(path)
    elif suffix == ".json":
        return _parse_json(path)
    else:
        raise ValueError(f"Unsupported file type: {suffix}")


# ------------------------------------------------------------------
# Text
# ------------------------------------------------------------------


def _parse_text(path: Path) -> str:
    """Read UTF-8 text files."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Try common fallback encodings
        for enc in ["latin-1", "gbk", "gb2312", "shift-jis"]:
            try:
                return path.read_text(encoding=enc)
            except (UnicodeDecodeError, LookupError):
                continue
        # Last resort
        return path.read_text(encoding="utf-8", errors="replace")


# ------------------------------------------------------------------
# PDF
# ------------------------------------------------------------------


def _parse_pdf(path: Path) -> str:
    """Parse PDF via pymupdf → markitdown → Docling chain.

    pymupdf runs first because it's lightweight and has no GPU dependency.
    Docling is last because its MPS inference can conflict with the LLM
    server and crash the process.
    """
    # ── 1. pymupdf (fitz) — lightweight, text extraction only ──
    try:
        import fitz

        doc = fitz.open(str(path))
        pages = [page.get_text() for page in doc]
        doc.close()
        text = "\n\n".join(pages)
        if text and len(text.strip()) > 50:
            logger.debug("PDF parsed via pymupdf: %s", path.name)
            return text
    except Exception as e:
        logger.debug("pymupdf PDF failed for %s: %s", path.name, e)

    # ── 2. markitdown ──
    try:
        from markitdown import MarkItDown

        md = MarkItDown()
        result = md.convert(str(path))
        text = result.text_content
        if text and len(text.strip()) > 50:
            logger.debug("PDF parsed via markitdown: %s", path.name)
            return text
    except Exception as e:
        logger.debug("markitdown PDF failed for %s: %s", path.name, e)

    # ── 3. Don't use Docling for PDFs ──
    # Docling uses MPS (Apple Silicon GPU) for layout detection, which conflicts
    # with the oMLX LLM server and can crash the entire process (SIGKILL).
    # Instead, return empty — the caller's _ocr_fallback will use Apple Vision
    # OCR (Neural Engine, no GPU conflict) to extract text from image-based PDFs.
    logger.debug("pymupdf+markitdown failed for %s — deferring to OCR", path.name)
    raise ValueError(f"Text extraction failed for: {path.name} (will use OCR)")


# ------------------------------------------------------------------
# Office documents
# ------------------------------------------------------------------


def _parse_office(path: Path) -> str:
    """Parse .docx/.xlsx/.pptx/.html via markitdown."""
    from markitdown import MarkItDown

    md = MarkItDown()
    result = md.convert(str(path))
    text = result.text_content
    if not text or len(text.strip()) < 10:
        logger.debug("markitdown empty/near-empty for %s — will try OCR", path.name)
        return ""  # Return empty so _ocr_fallback can try OCR
    return text


# ------------------------------------------------------------------
# Legacy Office (via LibreOffice)
# ------------------------------------------------------------------

LIBREOFFICE_BIN = "soffice"  # brew-installed LibreOffice wrapper


def _find_libreoffice() -> str | None:
    """Find the LibreOffice binary. Returns path or None."""
    # Check common locations
    candidates = [
        "soffice",
        "libreoffice",
        "/opt/homebrew/bin/soffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ]
    for c in candidates:
        try:
            subprocess.run([c, "--version"], capture_output=True, timeout=5)
            return c
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def _parse_legacy_office(path: Path) -> str:
    """Convert legacy .doc/.ppt to text via LibreOffice headless.

    Creates a temp dir, converts the file to .txt using LibreOffice,
    reads the result, and cleans up.
    """
    lo_bin = _find_libreoffice()
    if lo_bin is None:
        raise ValueError(
            "LibreOffice is required for legacy .doc/.ppt files. "
            "Install with: brew install --cask libreoffice"
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        # Copy file to temp dir (LibreOffice needs write access to the directory)
        import shutil
        tmp_path = Path(tmpdir) / path.name
        shutil.copy2(path, tmp_path)

        # Convert to text via LibreOffice headless
        result = subprocess.run(
            [
                lo_bin, "--headless", "--convert-to", "txt:Text",
                "--outdir", tmpdir, str(tmp_path),
            ],
            capture_output=True, text=True, timeout=120,
        )

        if result.returncode != 0:
            raise ValueError(
                f"LibreOffice conversion failed for {path.name}: {result.stderr[:200]}"
            )

        # Find the output .txt file
        txt_name = path.stem + ".txt"
        txt_path = Path(tmpdir) / txt_name

        if not txt_path.exists():
            logger.debug("LibreOffice produced no .txt for %s — will try OCR", path.name)
            return ""  # Return empty so _ocr_fallback can try OCR

        text = txt_path.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            logger.debug("LibreOffice produced empty text for %s — will try OCR", path.name)
            return ""  # Return empty so _ocr_fallback can try OCR

        logger.debug("Legacy Office converted via LibreOffice: %s → %d chars", path.name, len(text))
        return text


# ------------------------------------------------------------------
# Images (via Apple Vision OCR)
# ------------------------------------------------------------------


def _should_ocr(text: str) -> bool:
    """Check if parsed text is too sparse to be useful — needs OCR.

    Returns True if text is empty or very short (< 50 non-whitespace chars).
    """
    cleaned = "".join(c for c in text if c.isalnum() or c.isspace())
    return len(cleaned.strip()) < 50


def _ocr_fallback(file_path: Path, original_text: str) -> str:
    """Try OCR on a file whose text extraction returned sparse/empty content.

    Strategy depends on file type:
    - Images (.jpg, .png, etc.) → OCR directly
    - Office documents (.ppt, .doc, etc.) → convert to PDF via LibreOffice
      → OCR each page
    - PDFs → OCR each page at configured DPI

    Returns the OCR result if it's substantially better than the original.
    """
    try:
        from filekb.config import Config

        cfg = Config()
        if not cfg.ocr.enabled:
            return original_text

        from filekb.ocr import is_available as ocr_available

        if not ocr_available():
            return original_text

        suffix = file_path.suffix.lower()
        logger.info("OCR fallback: %s (original too sparse: %d chars)",
                    file_path.name, len(original_text.strip()))

        # Image files → OCR directly
        if suffix in IMAGE_EXTENSIONS:
            from filekb.ocr import recognize_text
            ocr_text = recognize_text(
                str(file_path),
                languages=tuple(cfg.ocr.languages),
                min_confidence=cfg.ocr.min_confidence,
            )

        # Office documents → convert to PDF then OCR
        elif suffix in OFFICE_EXTENSIONS | LEGACY_OFFICE_EXTENSIONS:
            ocr_text = _ocr_office_via_pdf(file_path, cfg)

        # PDF → directly OCR each page
        elif suffix == ".pdf":
            ocr_text = _ocr_pdf_pages(file_path, cfg)

        else:
            return original_text

        if ocr_text and len(ocr_text.strip()) > len(original_text.strip()):
            logger.info("OCR improved: %s (%d → %d chars)",
                        file_path.name, len(original_text), len(ocr_text))
            return ocr_text

    except Exception as e:
        logger.warning("OCR fallback failed for %s: %s", file_path.name, e)

    return original_text


def _ocr_office_via_pdf(file_path: Path, cfg: Any) -> str:
    """Convert an Office document to PDF via LibreOffice, then OCR each page."""
    import shutil
    import tempfile

    from filekb.ocr import recognize_pdf_page

    lo_bin = _find_libreoffice()
    if lo_bin is None:
        raise ValueError("LibreOffice required for Office → PDF conversion")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir) / file_path.name
        shutil.copy2(file_path, tmp)

        result = subprocess.run(
            [lo_bin, "--headless", "--convert-to", "pdf", "--outdir", tmpdir, str(tmp)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise ValueError(f"LibreOffice PDF conversion failed: {result.stderr[:200]}")

        pdf_path = Path(tmpdir) / (file_path.stem + ".pdf")
        if not pdf_path.exists():
            raise ValueError("LibreOffice produced no PDF output")

        # OCR each page
        import Quartz
        pdf_url = Quartz.CFURLCreateFromFileSystemRepresentation(
            None, str(pdf_path).encode(), len(str(pdf_path).encode()), False,
        )
        pdf_doc = Quartz.CGPDFDocumentCreateWithURL(pdf_url)
        if pdf_doc is None:
            return ""
        page_count = Quartz.CGPDFDocumentGetNumberOfPages(pdf_doc)

        pages_text: list[str] = []
        for p in range(page_count):
            page_text = recognize_pdf_page(
                pdf_path, page_index=p,
                dpi=cfg.ocr.pdf_dpi,
                languages=tuple(cfg.ocr.languages),
                min_confidence=cfg.ocr.min_confidence,
            )
            if page_text.strip():
                pages_text.append(page_text)

        return "\n\n".join(pages_text)


def _ocr_pdf_pages(file_path: Path, cfg: Any) -> str:
    """OCR each page of an image-based PDF."""
    from filekb.ocr import recognize_pdf_page
    import Quartz

    pdf_url = Quartz.CFURLCreateFromFileSystemRepresentation(
        None, str(file_path).encode(), len(str(file_path).encode()), False,
    )
    pdf_doc = Quartz.CGPDFDocumentCreateWithURL(pdf_url)
    if pdf_doc is None:
        return ""
    page_count = Quartz.CGPDFDocumentGetNumberOfPages(pdf_doc)

    pages_text: list[str] = []
    for p in range(page_count):
        page_text = recognize_pdf_page(
            str(file_path), page_index=p,
            dpi=cfg.ocr.pdf_dpi,
            languages=tuple(cfg.ocr.languages),
            min_confidence=cfg.ocr.min_confidence,
        )
        if page_text.strip():
            pages_text.append(page_text)

    return "\n\n".join(pages_text)


def _parse_image(path: Path) -> str:
    """Parse an image file via Apple Vision OCR.

    If OCR is disabled or unavailable, raises ValueError.
    """
    from filekb.config import Config

    cfg = Config()
    if not cfg.ocr.enabled:
        raise ValueError(
            f"OCR disabled (ocr.enabled=false). Cannot process image: {path.name}"
        )

    from filekb.ocr import is_available as ocr_available, recognize_text

    if not ocr_available():
        raise ValueError(
            "Apple Vision OCR is only available on macOS. "
            "Install with: pip install pyobjc-framework-Vision pyobjc-framework-Quartz"
        )

    text = recognize_text(
        str(path),
        languages=tuple(cfg.ocr.languages),
        min_confidence=cfg.ocr.min_confidence,
    )

    if not text or not text.strip():
        raise ValueError(f"OCR found no text in image: {path.name}")

    logger.debug("Image OCR: %s → %d chars", path.name, len(text))
    return text


# ------------------------------------------------------------------
# Code
# ------------------------------------------------------------------


def _parse_code(path: Path) -> str:
    """Extract docstrings, function/class signatures, and imports from code files.

    For Python files, uses AST. For other languages, uses regex-based extraction.
    Implementation details (function bodies) are excluded per FR-03.
    """
    suffix = path.suffix.lower()
    if suffix == ".py":
        return _parse_python(path)
    else:
        return _parse_generic_code(path)


def _parse_python(path: Path) -> str:
    """Extract structure from Python files using AST."""
    import ast

    source = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # If AST parse fails, return first 200 lines as fallback
        lines = source.splitlines()[:200]
        return "\n".join(lines)

    parts: list[str] = []

    # Module docstring
    if (
        isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
        and isinstance(tree.body[0].value.value, str)
    ):
        parts.append(f'""" {tree.body[0].value.value} """')

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            parts.append(ast.unparse(node))
        elif isinstance(node, ast.FunctionDef):
            # Function signature + docstring only, no body
            sig = _function_signature(node)
            parts.append(sig)
        elif isinstance(node, ast.ClassDef):
            # Class name + bases + docstring
            bases = [ast.unparse(b) for b in node.bases]
            base_str = f"({', '.join(bases)})" if bases else ""
            parts.append(f"\nclass {node.name}{base_str}:")
            doc = ast.get_docstring(node)
            if doc:
                parts.append(f'    """{doc}"""')
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    parts.append("    " + _function_signature(item))

    return "\n".join(parts)


def _function_signature(node: ast.FunctionDef) -> str:
    import ast
    """Extract function signature and docstring."""
    args = []
    for arg in node.args.args:
        arg_str = arg.arg
        if arg.annotation:
            arg_str += f": {ast.unparse(arg.annotation)}"
        args.append(arg_str)
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    decorators = "\n".join(f"@{ast.unparse(d)}" for d in node.decorator_list)
    sig = f"{decorators}\ndef {node.name}({', '.join(args)}){returns}:" if decorators else f"def {node.name}({', '.join(args)}){returns}:"
    doc = ast.get_docstring(node)
    if doc:
        sig += f'\n    """{doc}"""'
    else:
        sig += " ..."
    return sig


def _parse_generic_code(path: Path) -> str:
    """Regex-based structural extraction for non-Python code files."""
    source = path.read_text(encoding="utf-8", errors="replace")
    lines = source.splitlines()

    # Extract: import/include statements, function signatures, comments
    patterns = [
        (r"^(import|from|require|#include|use|package)\s", "import"),
        (r"^(func|fn|function|def|class|interface|type|struct|enum)\s", "decl"),
        (r"^\s*//.*|/\*\*.*\*/|^\s*#.*", "comment"),
    ]

    result: list[str] = []
    for line in lines[:500]:  # Limit to first 500 lines
        for pattern, _kind in patterns:
            if re.match(pattern, line.strip()):
                result.append(line)
                break

    return "\n".join(result)


# ------------------------------------------------------------------
# CSV
# ------------------------------------------------------------------


def _parse_csv(path: Path) -> str:
    """Summarize CSV file: column names + row count + sample rows."""
    import csv

    with open(path, encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        try:
            headers = next(reader)
        except StopIteration:
            return "(empty CSV)"

        rows = list(reader)

    summary = [
        f"CSV file: {path.name}",
        f"Columns ({len(headers)}): {', '.join(headers)}",
        f"Rows: {len(rows)}",
    ]

    if rows:
        summary.append("\nSample (first 10 rows):")
        for i, row in enumerate(rows[:10]):
            summary.append("  " + " | ".join(row))

    return "\n".join(summary)


# ------------------------------------------------------------------
# JSON
# ------------------------------------------------------------------


def _parse_json(path: Path) -> str:
    """Extract truncated text from JSON files."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.warning("Cannot parse JSON %s: %s", path.name, e)
        return path.read_text(encoding="utf-8", errors="replace")[:5000]

    text = json.dumps(data, ensure_ascii=False, indent=2)
    if len(text) > 10000:
        text = text[:10000] + "\n... (truncated)"
    return text


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------


def is_supported(file_path: str | Path) -> bool:
    """Check if a file type is supported for parsing."""
    suffix = Path(file_path).suffix.lower()
    return suffix in SUPPORTED_EXTENSIONS
