"""Top-level ingestion entry point: hash, dispatch by mime, persist source + chunks."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from meridian.db.connection import transaction
from meridian.ingest import docx as docx_ingest
from meridian.ingest import dwg as dwg_ingest
from meridian.ingest import email as email_ingest
from meridian.ingest import pdf as pdf_ingest
from meridian.ingest import xlsx as xlsx_ingest
from meridian.logging import get_logger

_log = get_logger("meridian.ingest")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    return str(uuid.uuid4())


@dataclass
class IngestResult:
    source_id: str
    filename: str
    content_hash: str
    mime_type: str
    extraction_method: str
    text_length: int
    chunk_count: int
    deduped: bool  # True if the same content_hash was already in the DB


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _detect_mime(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    if guessed:
        return guessed
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if suffix == ".docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if suffix == ".pdf":
        return "application/pdf"
    if suffix == ".dwg":
        return "image/vnd.dwg"
    if suffix == ".eml":
        return "message/rfc822"
    if suffix == ".msg":
        return "application/vnd.ms-outlook"
    return "application/octet-stream"


def ingest_file(
    conn: sqlite3.Connection,
    *,
    file_path: Path,
    project_root: Path,
) -> IngestResult:
    """Hash, dedup, extract text + chunks. Returns the source_id for downstream wiring."""
    file_path = file_path.resolve()
    project_root = project_root.resolve()
    _log.info("ingest.start", filename=file_path.name, path=str(file_path))
    content_hash = _hash_file(file_path)

    existing = conn.execute(
        "SELECT id FROM source_document WHERE content_hash = ?",
        (content_hash,),
    ).fetchone()
    if existing:
        # Content-hash dedup (CONTEXT.md §13). Same bytes already imported.
        meta = conn.execute(
            "SELECT filename FROM source_document WHERE id = ?", (existing["id"],)
        ).fetchone()
        text_row = conn.execute(
            "SELECT extraction_method, length(text) AS n FROM source_document_text WHERE source_id = ?",
            (existing["id"],),
        ).fetchone()
        chunk_n = conn.execute(
            "SELECT COUNT(*) AS n FROM source_document_chunk WHERE source_id = ?",
            (existing["id"],),
        ).fetchone()["n"]
        result = IngestResult(
            source_id=existing["id"],
            filename=meta["filename"],
            content_hash=content_hash,
            mime_type=_detect_mime(file_path),
            extraction_method=text_row["extraction_method"] if text_row else "unknown",
            text_length=text_row["n"] if text_row else 0,
            chunk_count=chunk_n,
            deduped=True,
        )
        _log.info(
            "ingest.finish",
            filename=result.filename,
            source_id=result.source_id,
            content_hash=result.content_hash,
            mime_type=result.mime_type,
            deduped=True,
            chunk_count=result.chunk_count,
        )
        return result

    mime_type = _detect_mime(file_path)
    size_bytes = file_path.stat().st_size

    try:
        relative_path = str(file_path.relative_to(project_root))
    except ValueError:
        relative_path = str(file_path)

    if mime_type == "application/pdf":
        extracted = pdf_ingest.extract(file_path)
    elif mime_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        extracted = xlsx_ingest.extract(file_path)
    elif mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        extracted = docx_ingest.extract(file_path)
    elif mime_type in {"message/rfc822", "application/vnd.ms-outlook"}:
        extracted = email_ingest.extract(file_path)
    elif mime_type in {
        "image/vnd.dwg",
        "application/dwg",
        "application/acad",
        "application/vnd.dwg",
    }:
        extracted = dwg_ingest.extract(file_path)
    else:
        raise NotImplementedError(
            f"No ingester wired for mime_type={mime_type!r} yet. "
            "Supported in this build: PDF, XLSX, DOCX, DWG, EML/MSG."
        )

    source_id = _new_id()
    now = _now()
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO source_document
              (id, filename, relative_path, content_hash, mime_type, size_bytes, imported_at, extraction_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                source_id,
                file_path.name,
                relative_path,
                content_hash,
                mime_type,
                size_bytes,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO source_document_text
              (source_id, extraction_method, extracted_at, text, metadata)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                source_id,
                extracted.extraction_method,
                now,
                extracted.text,
                json.dumps(extracted.metadata),
            ),
        )
        for chunk in extracted.chunks:
            conn.execute(
                """
                INSERT INTO source_document_chunk
                  (id, source_id, chunk_kind, locator, text)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    _new_id(),
                    source_id,
                    chunk.chunk_kind,
                    json.dumps(chunk.locator),
                    chunk.text,
                ),
            )

    result = IngestResult(
        source_id=source_id,
        filename=file_path.name,
        content_hash=content_hash,
        mime_type=mime_type,
        extraction_method=extracted.extraction_method,
        text_length=len(extracted.text),
        chunk_count=len(extracted.chunks),
        deduped=False,
    )
    _log.info(
        "ingest.finish",
        filename=result.filename,
        source_id=result.source_id,
        content_hash=result.content_hash,
        mime_type=result.mime_type,
        deduped=False,
        chunk_count=result.chunk_count,
        text_length=result.text_length,
        extraction_method=result.extraction_method,
    )
    return result


__all__ = ["IngestResult", "ingest_file"]
