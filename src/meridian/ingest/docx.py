"""DOCX ingestion via python-docx.

Chunk granularity: one chunk per heading-level section (split on paragraphs whose
style name begins with "Heading"). If the document has no headings at all we fall
back to fixed-size paragraph windows so we don't end up with a single giant chunk.

Tables are rendered inline as joined cell text within the section they appear in
(v1 keeps it simple; structured table extraction can come later if needed).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from docx import Document
from docx.document import Document as _DocxDocument
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from meridian.ingest._types import _Chunk, _Extracted

# Fallback window size when the document contains no headings — keeps chunks
# small enough to be useful without exploding chunk counts on long docs.
_FALLBACK_PARAGRAPHS_PER_CHUNK = 50


def _is_heading(paragraph: Paragraph) -> bool:
    style = paragraph.style
    name = getattr(style, "name", "") or ""
    return name.startswith("Heading")


def _table_to_text(table: Table) -> str:
    """Render a table as line-per-row, ' | '-joined cells. v1 keeps formatting flat."""
    lines: list[str] = []
    for row in table.rows:
        cells = [cell.text.strip() for cell in row.cells]
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def _iter_block_items(document: _DocxDocument):
    """Yield paragraphs and tables in document order.

    python-docx exposes paragraphs and tables as separate collections; to keep
    table content adjacent to the surrounding prose we walk the underlying XML.
    """
    body = document.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield Table(child, document)


def extract(path: Path) -> _Extracted:
    """Extract text + per-section chunks from a .docx file."""
    document = Document(str(path))

    # First pass: linearise the document into "blocks" of (kind, text, is_heading).
    # paragraph_index tracks paragraph position (tables don't increment it) so the
    # locator's paragraph_index_start/end refer to paragraph ordinals only.
    blocks: list[dict[str, Any]] = []
    paragraph_index = 0
    for item in _iter_block_items(document):
        if isinstance(item, Paragraph):
            text = item.text or ""
            blocks.append(
                {
                    "kind": "paragraph",
                    "text": text,
                    "is_heading": _is_heading(item),
                    "heading_text": text if _is_heading(item) else None,
                    "paragraph_index": paragraph_index,
                }
            )
            paragraph_index += 1
        elif isinstance(item, Table):
            blocks.append(
                {
                    "kind": "table",
                    "text": _table_to_text(item),
                    "is_heading": False,
                    "heading_text": None,
                    "paragraph_index": None,
                }
            )

    paragraph_count = paragraph_index
    chunks: list[_Chunk] = []
    full_text_parts: list[str] = []

    has_headings = any(b["is_heading"] for b in blocks)

    if has_headings:
        # Section-based chunking: each Heading-styled paragraph starts a new section.
        # A leading non-heading prelude becomes its own "(prelude)" chunk.
        current_heading: str | None = None
        current_lines: list[str] = []
        current_para_start: int | None = None
        current_para_end: int | None = None

        def flush():
            if not current_lines and current_heading is None:
                return
            heading = current_heading or "(prelude)"
            section_text = "\n".join(current_lines).strip()
            if not section_text:
                return
            # chunk_kind: schema's chunk_kind enum has no docx-specific value,
            # so we reuse 'pdf_section' as the closest semantic match for a
            # heading-bounded section of prose. Spec: do not invent enum values.
            chunks.append(
                _Chunk(
                    chunk_kind="pdf_section",
                    locator={
                        "section_heading": heading,
                        "paragraph_index_start": current_para_start
                        if current_para_start is not None
                        else 0,
                        "paragraph_index_end": current_para_end
                        if current_para_end is not None
                        else (current_para_start or 0),
                    },
                    text=section_text,
                )
            )

        for block in blocks:
            if block["kind"] == "paragraph" and block["is_heading"]:
                flush()
                current_heading = block["heading_text"] or ""
                current_lines = [current_heading]
                current_para_start = block["paragraph_index"]
                current_para_end = block["paragraph_index"]
                full_text_parts.append(current_heading)
            else:
                text = block["text"]
                if text:
                    current_lines.append(text)
                    full_text_parts.append(text)
                if block["kind"] == "paragraph":
                    if current_para_start is None:
                        current_para_start = block["paragraph_index"]
                    current_para_end = block["paragraph_index"]
        flush()
    else:
        # Fallback: fixed paragraph windows. Tables fall into whichever window
        # their preceding paragraphs belong to.
        window_lines: list[str] = []
        window_para_start: int | None = None
        window_para_end: int | None = None
        window_paragraph_count = 0

        def flush_window():
            if not window_lines:
                return
            chunks.append(
                _Chunk(
                    chunk_kind="pdf_section",
                    locator={
                        "section_heading": "(no headings)",
                        "paragraph_index_start": window_para_start or 0,
                        "paragraph_index_end": window_para_end
                        if window_para_end is not None
                        else (window_para_start or 0),
                    },
                    text="\n".join(window_lines).strip(),
                )
            )

        for block in blocks:
            text = block["text"]
            if text:
                window_lines.append(text)
                full_text_parts.append(text)
            if block["kind"] == "paragraph":
                if window_para_start is None:
                    window_para_start = block["paragraph_index"]
                window_para_end = block["paragraph_index"]
                window_paragraph_count += 1
                if window_paragraph_count >= _FALLBACK_PARAGRAPHS_PER_CHUNK:
                    flush_window()
                    window_lines = []
                    window_para_start = None
                    window_para_end = None
                    window_paragraph_count = 0
        flush_window()

    full_text = "\n".join(full_text_parts)
    metadata: dict[str, Any] = {
        "paragraph_count": paragraph_count,
        "section_count": len(chunks),
        "extraction_method": "docx_parsed",
    }

    return _Extracted(
        extraction_method="docx_parsed",
        text=full_text,
        chunks=chunks,
        metadata=metadata,
    )


__all__ = ["extract"]
