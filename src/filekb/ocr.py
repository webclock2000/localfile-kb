"""OCR module — Apple Vision text recognition.

Uses macOS Vision framework (VNRecognizeTextRequest) for high-accuracy
text extraction from images and image-based PDFs. Neural Engine accelerated.

This module is macOS-only. On other platforms it raises ImportError.
"""

from __future__ import annotations

import logging
import platform
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ============================================================================
# Platform check
# ============================================================================


def is_apple_silicon() -> bool:
    """Check if running on Apple Silicon Mac."""
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def is_available() -> bool:
    """Check if Apple Vision OCR is available."""
    if not is_apple_silicon():
        return False
    try:
        import Quartz  # noqa: F401
        import Vision  # noqa: F401

        return True
    except ImportError:
        return False


# ============================================================================
# Vision-backed OCR
# ============================================================================


def recognize_text(
    image_path: str | Path,
    languages: tuple[str, ...] = ("zh-Hans", "zh-Hant", "en"),
    min_confidence: float = 0.3,
) -> str:
    """Extract text from an image using Apple Vision OCR.

    Args:
        image_path: Path to the image file (supports PNG, JPEG, TIFF, etc.).
        languages: Ordered language codes for recognition.
                   Default: Simplified Chinese → Traditional Chinese → English.
        min_confidence: Minimum recognition confidence (0.0-1.0).

    Returns:
        Recognized text, or empty string if nothing found.

    Raises:
        ImportError: If pyobjc Vision/Quartz not installed (non-macOS).
        OSError: If image file cannot be read.
    """
    if not is_available():
        raise ImportError(
            "Apple Vision OCR requires macOS with pyobjc-framework-Vision installed. "
            "Install with: pip install pyobjc-framework-Vision pyobjc-framework-Quartz"
        )

    import Quartz
    import Vision

    image_path = str(Path(image_path).expanduser().resolve())

    # Load image via Core Graphics
    image_url = Quartz.CFURLCreateFromFileSystemRepresentation(
        None,
        image_path.encode("utf-8"),
        len(image_path.encode("utf-8")),
        False,
    )
    if image_url is None:
        raise OSError(f"Cannot create CFURL for: {image_path}")

    image_source = Quartz.CGImageSourceCreateWithURL(image_url, None)
    if image_source is None:
        raise OSError(f"Cannot create image source for: {image_path}")

    cg_image = Quartz.CGImageSourceCreateImageAtIndex(image_source, 0, None)
    if cg_image is None:
        raise OSError(f"Cannot decode image: {image_path}")

    # Create Vision request
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(
        cg_image, None
    )

    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setRecognitionLanguages_(list(languages))
    request.setUsesLanguageCorrection_(True)

    # Run recognition
    success = handler.performRequests_error_([request], None)
    if not success:
        return ""

    # Collect results
    results: list[str] = []
    observations = request.results()
    if observations is None:
        return ""

    for obs in observations:
        top = obs.topCandidates_(1)
        if top is None or len(top) == 0:
            continue
        candidate = top[0]
        confidence = float(candidate.confidence())
        if confidence < min_confidence:
            continue
        results.append(str(candidate.string()))

    text = "\n".join(results)
    if text.strip():
        logger.debug(
            "OCR extracted %d chars from %s (confidence threshold: %.2f)",
            len(text), Path(image_path).name, min_confidence,
        )

    return text


def recognize_pdf_page(
    pdf_path: str | Path,
    page_index: int = 0,
    dpi: int = 200,
    languages: tuple[str, ...] = ("zh-Hans", "zh-Hant", "en"),
    min_confidence: float = 0.3,
) -> str:
    """OCR a single page of a PDF by rendering it to an image first.

    Useful for image-based (scanned) PDFs where text extraction fails.

    Args:
        pdf_path: Path to PDF file.
        page_index: Zero-based page number.
        dpi: Rendering resolution for PDF → image conversion.
        languages: Recognition languages.
        min_confidence: Minimum confidence threshold.

    Returns:
        Recognized text from the page.
    """
    import Quartz
    import tempfile

    pdf_path = str(Path(pdf_path).expanduser().resolve())
    pdf_url = Quartz.CFURLCreateFromFileSystemRepresentation(
        None,
        pdf_path.encode("utf-8"),
        len(pdf_path.encode("utf-8")),
        False,
    )
    pdf_doc = Quartz.CGPDFDocumentCreateWithURL(pdf_url)
    if pdf_doc is None:
        raise OSError(f"Cannot open PDF: {pdf_path}")

    page = Quartz.CGPDFDocumentGetPage(pdf_doc, page_index + 1)
    if page is None:
        return ""

    # Render page to image at specified DPI
    rect = Quartz.CGPDFPageGetBoxRect(page, Quartz.kCGPDFMediaBox)
    scale = dpi / 72.0
    width = int(rect.size.width * scale)
    height = int(rect.size.height * scale)

    # Use a temporary PNG for the rendered page
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        color_space = Quartz.CGColorSpaceCreateDeviceRGB()
        ctx = Quartz.CGBitmapContextCreate(
            None, width, height, 8, width * 4,
            color_space, Quartz.kCGImageAlphaPremultipliedFirst,
        )
        if ctx is None:
            return ""

        Quartz.CGContextSetRGBFillColor(ctx, 1, 1, 1, 1)
        Quartz.CGContextFillRect(ctx, Quartz.CGRectMake(0, 0, width, height))
        Quartz.CGContextScaleCTM(ctx, scale, scale)
        Quartz.CGContextDrawPDFPage(ctx, page)

        rendered = Quartz.CGBitmapContextCreateImage(ctx)
        if rendered is None:
            return ""

        # Save to temp file
        dest_url = Quartz.CFURLCreateFromFileSystemRepresentation(
            None,
            tmp_path.encode("utf-8"),
            len(tmp_path.encode("utf-8")),
            False,
        )
        dest = Quartz.CGImageDestinationCreateWithURL(dest_url, "public.png", 1, None)
        Quartz.CGImageDestinationAddImage(dest, rendered, None)
        Quartz.CGImageDestinationFinalize(dest)

        # OCR the rendered page
        return recognize_text(tmp_path, languages=languages, min_confidence=min_confidence)
    finally:
        try:
            Path(tmp_path).unlink()
        except OSError:
            pass
