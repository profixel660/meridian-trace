"""PDF text ingestion via pdfplumber.

OCR fallback (tesseract via pdf2image + pytesseract) is wired as an optional
extra. Default path is text-only extraction via pdfplumber; when extracted
text is suspiciously thin relative to page count, the ingester automatically
rasterises pages and runs Tesseract OCR. OCR can be disabled at runtime by
setting MERIDIAN_OCR_DISABLED=1.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pdfplumber

from meridian.ingest._types import _Chunk, _Extracted

# Heuristic: average characters per page below this triggers OCR fallback.
_OCR_HEURISTIC_MIN_CHARS_PER_PAGE = 40

# Env var to opt out of the OCR fallback path entirely (returns the thin
# text-extraction result instead of attempting to rasterise + OCR).
_OCR_DISABLED_ENV_VAR = "MERIDIAN_OCR_DISABLED"


def _ocr_disabled() -> bool:
    return os.environ.get(_OCR_DISABLED_ENV_VAR, "").strip() == "1"


def _run_ocr(path: Path, page_count: int) -> _Extracted:
    """Rasterise + OCR every page of the PDF and return a populated _Extracted.

    Raises whatever pdf2image / pytesseract raise on missing binaries; the
    caller is responsible for catching and degrading gracefully.
    """
    # Imports are local so the OCR optional-extra is only required when this
    # branch is actually exercised.
    import pytesseract  # type: ignore[import-not-found]
    from pdf2image import convert_from_path  # type: ignore[import-not-found]

    images = convert_from_path(str(path))

    chunks: list[_Chunk] = []
    page_texts: list[str] = []
    for index, image in enumerate(images, start=1):
        page_text = pytesseract.image_to_string(image) or ""
        page_texts.append(page_text)
        chunks.append(
            _Chunk(
                chunk_kind="pdf_page",
                locator={"page": index, "source": "ocr"},
                text=page_text,
            )
        )

    full_text = "\n\n".join(page_texts)
    metadata: dict[str, Any] = {
        "page_count": page_count,
        "extraction_method": "pdf_ocr",
        "fallback_triggered_from_text_extraction": True,
        "ocr_engine": "tesseract",
    }
    return _Extracted(
        extraction_method="pdf_ocr",
        text=full_text,
        chunks=chunks,
        metadata=metadata,
    )


def extract(path: Path) -> _Extracted:
    """Extract text + per-page chunks. Returns _Extracted (compatible with dispatcher).

    If pdfplumber yields suspiciously little text (avg chars/page below
    `_OCR_HEURISTIC_MIN_CHARS_PER_PAGE`) and the file has at least one page,
    automatically fall back to OCR via pdf2image + pytesseract. The OCR path
    can be disabled by setting `MERIDIAN_OCR_DISABLED=1` in the environment.
    """
    chunks: list[_Chunk] = []
    full_text_parts: list[str] = []

    with pdfplumber.open(str(path)) as pdf:
        page_count = len(pdf.pages)
        for index, page in enumerate(pdf.pages, start=1):
            page_text = page.extract_text(layout=True) or ""
            full_text_parts.append(page_text)
            chunks.append(
                _Chunk(
                    chunk_kind="pdf_page",
                    locator={"page": index},
                    text=page_text,
                )
            )

    full_text = "\n\n".join(full_text_parts)
    avg_chars = len(full_text) / page_count if page_count else 0
    needs_ocr = avg_chars < _OCR_HEURISTIC_MIN_CHARS_PER_PAGE and page_count > 0

    metadata: dict[str, Any] = {
        "page_count": page_count,
        "avg_chars_per_page": round(avg_chars, 1),
        "needs_ocr_hint": needs_ocr,
    }

    text_only_result = _Extracted(
        extraction_method="pdf_text",
        text=full_text,
        chunks=chunks,
        metadata=metadata,
    )

    if not needs_ocr:
        return text_only_result

    if _ocr_disabled():
        metadata["ocr_skipped_reason"] = (
            f"OCR fallback disabled via {_OCR_DISABLED_ENV_VAR}=1"
        )
        return text_only_result

    # OCR fallback path: rasterise + tesseract, returns extraction_method='pdf_ocr'.
    try:
        ocr_result = _run_ocr(path, page_count)
        assert ocr_result.extraction_method == "pdf_ocr"
        return ocr_result
    except Exception as exc:  # noqa: BLE001 - we want to degrade on ANY OCR failure
        # Most commonly: pytesseract.TesseractNotFoundError, or pdf2image's
        # PDFInfoNotInstalledError / PDFPageCountError when Poppler is missing.
        # Also covers ImportError if the `ocr` extra wasn't installed.
        metadata["ocr_skipped_reason"] = f"{type(exc).__name__}: {exc}"
        return text_only_result


__all__ = ["extract"]
