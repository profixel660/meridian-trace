"""Top-level ingestion entry point: hash, dispatch by mime, persist source + chunks."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import sqlite3
import sys
import uuid
from dataclasses import dataclass, field
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

# --------------------------------------------------------------------------
# Folder-walk support (round-18 / Stream A): canonical supported-extension
# set, and a directory walker that produces the manifest the
# /setup/import-folder/scan endpoint returns.
#
# The extension set is the SOURCE OF TRUTH for "what the dispatcher knows
# how to ingest" — kept in sync with the mime-type branches in
# ingest_file() below. New formats added there must add their extension(s)
# here too, otherwise the scan call will silently route them to ``skipped``.
# --------------------------------------------------------------------------

# Map "kind" → set of extensions (lowercase, with leading dot). The kind
# becomes the bucket key in WalkResult.files_by_kind.
SUPPORTED_EXTENSIONS_BY_KIND: dict[str, frozenset[str]] = {
    "pdf":  frozenset({".pdf"}),
    "docx": frozenset({".docx"}),
    "xlsx": frozenset({".xlsx"}),
    "dwg":  frozenset({".dwg"}),
    "eml":  frozenset({".eml"}),
    "msg":  frozenset({".msg"}),
}

# Flat extension → kind lookup. Built once at import.
_EXT_TO_KIND: dict[str, str] = {
    ext: kind
    for kind, exts in SUPPORTED_EXTENSIONS_BY_KIND.items()
    for ext in exts
}

# Files we never want to ingest, regardless of extension. Case-insensitive
# basename match.
_SYSTEM_FILE_NAMES: frozenset[str] = frozenset({
    "thumbs.db",
    ".ds_store",
    "desktop.ini",
})

# Directory names that are pruned during the walk (skipped wholesale, not
# even reported in ``skipped``). Match on basename anywhere in the tree.
_PRUNED_DIR_NAMES: frozenset[str] = frozenset({
    ".git",
    "node_modules",
    "__pycache__",
    "_meridian",
})


@dataclass
class WalkSkip:
    """One ``skipped`` entry in the WalkResult manifest."""

    path: str
    reason: str  # "unsupported_extension" | "access_denied" | "hidden_or_system"


@dataclass
class WalkResult:
    """Manifest produced by :func:`walk_directory`.

    ``files_by_kind`` always contains every key in
    :data:`SUPPORTED_EXTENSIONS_BY_KIND` (empty list when no files of that
    kind were found) so consumers can render a stable layout.
    """

    folder_path: str
    folder_name: str
    files_by_kind: dict[str, list[str]] = field(default_factory=dict)
    skipped: list[WalkSkip] = field(default_factory=list)
    total_ingestable: int = 0


def _is_hidden(path: Path) -> bool:
    """Cross-platform hidden-file detection.

    POSIX: leading-dot filename. Windows: leading-dot filename OR the
    FILE_ATTRIBUTE_HIDDEN bit on the file's attributes (set by Explorer
    via "Properties → Hidden", and on auto-generated junk like
    ``$RECYCLE.BIN``). The Windows attribute check is best-effort: a
    failed os.stat falls back to the dot-prefix rule only.
    """
    name = path.name
    if name.startswith(".") and name not in {".", ".."}:
        return True
    if sys.platform == "win32":
        try:
            attrs = os.stat(path).st_file_attributes  # type: ignore[attr-defined]
        except (OSError, AttributeError):
            return False
        # FILE_ATTRIBUTE_HIDDEN = 0x2, FILE_ATTRIBUTE_SYSTEM = 0x4
        return bool(attrs & 0x2) or bool(attrs & 0x4)
    return False


def walk_directory(folder: Path) -> WalkResult:
    """Walk ``folder`` recursively and bucket files by ingest-kind.

    Pure scan — no DB writes, no LLM calls, no ``ingest_file`` invocation.
    Reusable beyond the wizard (e.g. CLI ``meridian projects scan``).

    Skip rules:
      * Hidden files (leading dot OR Windows hidden attribute).
      * System files: ``Thumbs.db``, ``.DS_Store``, ``desktop.ini`` (case-insensitive).
      * Files inside ``.git``, ``node_modules``, ``__pycache__``, ``_meridian``
        (dirs pruned wholesale; not even reported as skipped — consistent with
        the spirit of "these are operator infrastructure, not project content").
      * Files where ``os.access(path, os.R_OK)`` is False → recorded as
        ``access_denied`` so the user can see what they need to unblock.
      * Files with an extension not in :data:`SUPPORTED_EXTENSIONS_BY_KIND`
        → recorded as ``unsupported_extension`` so the user understands why
        their .txt / .docm / .png files didn't show up.

    Args:
        folder: directory to walk. Must exist and be a directory; callers
            are responsible for the precondition check (the wizard endpoint
            translates a missing/non-dir path into HTTP 400).

    Returns:
        :class:`WalkResult` — ready to serialise as the
        ``/setup/import-folder/scan`` response body.
    """
    folder = folder.resolve()
    files_by_kind: dict[str, list[str]] = {
        kind: [] for kind in SUPPORTED_EXTENSIONS_BY_KIND
    }
    skipped: list[WalkSkip] = []
    total_ingestable = 0

    # os.walk is dramatically faster than Path.rglob on large trees and
    # gives us the in-place dirnames mutation we need for pruning.
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
        # Prune infrastructure dirs in-place so os.walk doesn't descend.
        dirnames[:] = [d for d in dirnames if d not in _PRUNED_DIR_NAMES]

        # Also prune hidden directories on POSIX (e.g. .venv, .idea). On
        # Windows hidden-attribute-based pruning would require an os.stat
        # per directory; the explicit _PRUNED_DIR_NAMES set already covers
        # the noise generators we care about.
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]

        for fname in filenames:
            path = Path(dirpath) / fname

            # System-file skip (Thumbs.db / .DS_Store / desktop.ini)
            if fname.lower() in _SYSTEM_FILE_NAMES:
                skipped.append(WalkSkip(path=str(path), reason="hidden_or_system"))
                continue

            # Hidden file skip
            if _is_hidden(path):
                skipped.append(WalkSkip(path=str(path), reason="hidden_or_system"))
                continue

            # Read-permission check. os.access returns False for files
            # readable by no-one OR for files we lack permission for; in
            # both cases the user benefits from seeing the path.
            try:
                readable = os.access(path, os.R_OK)
            except OSError:
                readable = False
            if not readable:
                skipped.append(WalkSkip(path=str(path), reason="access_denied"))
                continue

            ext = path.suffix.lower()
            kind = _EXT_TO_KIND.get(ext)
            if kind is None:
                skipped.append(
                    WalkSkip(path=str(path), reason="unsupported_extension")
                )
                continue

            files_by_kind[kind].append(str(path))
            total_ingestable += 1

    return WalkResult(
        folder_path=str(folder),
        folder_name=folder.name,
        files_by_kind=files_by_kind,
        skipped=skipped,
        total_ingestable=total_ingestable,
    )


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


__all__ = [
    "SUPPORTED_EXTENSIONS_BY_KIND",
    "IngestResult",
    "WalkResult",
    "WalkSkip",
    "ingest_file",
    "walk_directory",
]
