"""Haiku-tier per-chunk triage pass (CONTEXT.md §13).

For text_spec / drawing / demarcation paths, run a cheap Haiku call per chunk
to decide whether the chunk likely contains deliverable content. Mark the
chunk row with `triage_marked_for_extraction` + reasoning. Downstream
extractors filter to triaged-keep chunks before sending to Sonnet.

BOD sources skip triage — every BOD row is already a candidate.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from meridian.db.connection import transaction
from meridian.llm.client import call_llm
from meridian.logging import get_logger
from meridian.prompts.loader import (
    extract_prompt_body,
    load_prompt,
    render_prompt,
)

_log = get_logger("meridian.extract.triage")


def _utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

_TRIAGE_PROMPT_FILENAME = "PROMPT_V1_triage.md"

_SYSTEM_PROMPT = (
    "You are a fast triage classifier for construction project document chunks. "
    "Return ONLY a single JSON object. Begin with `{`, end with `}`. "
    "No preamble. No markdown fencing."
)

# Don't waste a triage call on chunks below this length — almost certainly noise
# (a page footer, blank page artefact, etc.).
_MIN_CHUNK_CHARS_TO_TRIAGE = 40

# Cap text sent to triage. Haiku is cheap but we don't need full pages — first
# 2-3k chars is enough to classify "is this content-bearing".
_MAX_CHUNK_CHARS_PER_TRIAGE = 4000


@dataclass
class TriageResult:
    source_id: str
    triaged: int
    kept: int
    skipped: int
    auto_kept_short: int  # chunks too small to triage; defaulted to keep=true
    skipped_due_to_bod_path: bool


def run_triage_for_source(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    job_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> TriageResult:
    """Triage every un-triaged chunk for the source. Idempotent — already-triaged chunks are skipped."""
    # provider/model intentionally optional — call_llm resolves via the
    # per-purpose routing chain (purpose="triage"). Default route is
    # claude-haiku-4-5-20251001 per DEFAULT_PURPOSE_ROUTING; users can override
    # to local (e.g. ollama/qwen2.5:14b) via project routing or env vars.

    source_row = conn.execute(
        "SELECT * FROM source_document WHERE id = ?", (source_id,)
    ).fetchone()
    if source_row is None:
        raise ValueError(f"source_document not found: {source_id}")

    _log.info(
        "triage.start",
        source_id=source_id,
        filename=source_row["filename"],
        job_id=job_id,
        extraction_path=source_row["extraction_path"],
    )

    # BOD rows are already candidates per the disposition rule — no triage value.
    if source_row["extraction_path"] == "bod_import":
        _log.info(
            "triage.finish",
            source_id=source_id,
            filename=source_row["filename"],
            skipped_due_to_bod_path=True,
            kept=0,
            skipped=0,
        )
        return TriageResult(
            source_id=source_id,
            triaged=0,
            kept=0,
            skipped=0,
            auto_kept_short=0,
            skipped_due_to_bod_path=True,
        )

    # Chunk-level resume (schema v5): any chunk left in 'in_progress' from a
    # prior interrupted job is an orphaned mid-write — surface it via
    # structured logging then reset to 'pending' so we re-process it.
    orphan_rows = conn.execute(
        """
        SELECT id, extraction_job_id, extraction_started_at
        FROM source_document_chunk
        WHERE source_id = ? AND extraction_status = 'in_progress'
        """,
        (source_id,),
    ).fetchall()
    for orphan in orphan_rows:
        _log.warning(
            "triage.chunk.orphan_in_progress",
            source_id=source_id,
            chunk_id=orphan["id"],
            prior_job_id=orphan["extraction_job_id"],
            prior_started_at=orphan["extraction_started_at"],
            current_job_id=job_id,
        )
    if orphan_rows:
        with transaction(conn):
            conn.execute(
                """
                UPDATE source_document_chunk
                SET extraction_status = 'pending',
                    extraction_started_at = NULL,
                    extraction_finished_at = NULL,
                    extraction_job_id = NULL
                WHERE source_id = ? AND extraction_status = 'in_progress'
                """,
                (source_id,),
            )

    chunks = conn.execute(
        """
        SELECT id, chunk_kind, locator, text, triage_marked_for_extraction,
               extraction_status
        FROM source_document_chunk
        WHERE source_id = ?
        ORDER BY id
        """,
        (source_id,),
    ).fetchall()

    triaged = 0
    kept = 0
    skipped = 0
    auto_kept_short = 0

    body = extract_prompt_body(load_prompt(_TRIAGE_PROMPT_FILENAME))

    for chunk in chunks:
        # Chunk-level resume gate: skip chunks that already reached a terminal
        # extraction state for any prior job. Falls back to the legacy
        # triage_marked_for_extraction signal so DBs that pre-date v5 (or rows
        # that the migration backfill didn't cover) are still respected.
        if chunk["extraction_status"] in ("extracted", "skipped"):
            if chunk["triage_marked_for_extraction"] == 0:
                skipped += 1
            else:
                kept += 1
            continue
        if (
            chunk["extraction_status"] == "pending"
            and chunk["triage_marked_for_extraction"] is not None
        ):
            # Pre-v5 rows whose triage decision was set but extraction_status
            # never updated (unlikely after migration backfill, but defensive).
            if chunk["triage_marked_for_extraction"] == 1:
                kept += 1
            else:
                skipped += 1
            continue

        text = (chunk["text"] or "").strip()
        chunk_id = chunk["id"]

        # Mark in_progress before any work so a crash mid-call leaves a
        # detectable orphan (the resume path resets it next run).
        with transaction(conn):
            conn.execute(
                """
                UPDATE source_document_chunk
                SET extraction_status = 'in_progress',
                    extraction_started_at = ?,
                    extraction_job_id = ?
                WHERE id = ?
                """,
                (_utcnow(), job_id, chunk_id),
            )

        if len(text) < _MIN_CHUNK_CHARS_TO_TRIAGE:
            with transaction(conn):
                conn.execute(
                    """
                    UPDATE source_document_chunk
                    SET triage_marked_for_extraction = 1,
                        triage_reason = 'auto-kept (chunk too small to triage)',
                        extraction_status = 'extracted',
                        extraction_finished_at = ?
                    WHERE id = ?
                    """,
                    (_utcnow(), chunk_id),
                )
            _log.info(
                "triage.chunk.auto_kept",
                source_id=source_id,
                chunk_id=chunk_id,
                job_id=job_id,
                reason="too_short",
            )
            auto_kept_short += 1
            kept += 1
            continue

        excerpt = text[:_MAX_CHUNK_CHARS_PER_TRIAGE]
        user_prompt = render_prompt(
            body,
            {
                "filename": source_row["filename"],
                "chunk_kind": chunk["chunk_kind"],
                "chunk_locator": chunk["locator"],
                "chunk_text": excerpt,
            },
        )
        try:
            call = call_llm(
                conn,
                purpose="triage",
                provider=provider,
                model=model,
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                prompt_version_ref="triage_v1.0",
                job_id=job_id,
                max_tokens=1024,
            )
        except RuntimeError as exc:
            # Triage is best-effort — on failure default to keep=true so we don't
            # silently drop content. Record the failure in triage_reason and
            # finalise the chunk's extraction state so resume doesn't retry.
            with transaction(conn):
                conn.execute(
                    """
                    UPDATE source_document_chunk
                    SET triage_marked_for_extraction = 1,
                        triage_reason = ?,
                        extraction_status = 'extracted',
                        extraction_finished_at = ?
                    WHERE id = ?
                    """,
                    (
                        f"auto-kept after triage failure: {exc}"[:500],
                        _utcnow(),
                        chunk_id,
                    ),
                )
            _log.warning(
                "triage.chunk.fail_auto_kept",
                source_id=source_id,
                chunk_id=chunk_id,
                job_id=job_id,
                error=str(exc)[:200],
            )
            kept += 1
            triaged += 1
            continue

        parsed = call.parsed_json if isinstance(call.parsed_json, dict) else None
        if parsed is None:
            keep_flag = True
            reason = "auto-kept (triage returned non-object JSON; bias to keep)"
        else:
            keep_flag = bool(parsed.get("keep", True))
            reason = (
                str(parsed.get("reason", "")).strip()
                or "(no reason provided by triage)"
            )

        terminal_status = "extracted" if keep_flag else "skipped"
        with transaction(conn):
            conn.execute(
                """
                UPDATE source_document_chunk
                SET triage_marked_for_extraction = ?,
                    triage_reason = ?,
                    extraction_status = ?,
                    extraction_finished_at = ?
                WHERE id = ?
                """,
                (
                    1 if keep_flag else 0,
                    reason[:500],
                    terminal_status,
                    _utcnow(),
                    chunk_id,
                ),
            )
        _log.info(
            "triage.chunk.decided",
            source_id=source_id,
            chunk_id=chunk_id,
            job_id=job_id,
            keep=keep_flag,
            extraction_status=terminal_status,
        )
        triaged += 1
        if keep_flag:
            kept += 1
        else:
            skipped += 1

    _log.info(
        "triage.finish",
        source_id=source_id,
        filename=source_row["filename"],
        job_id=job_id,
        triaged=triaged,
        kept=kept,
        skipped=skipped,
        auto_kept_short=auto_kept_short,
    )
    return TriageResult(
        source_id=source_id,
        triaged=triaged,
        kept=kept,
        skipped=skipped,
        auto_kept_short=auto_kept_short,
        skipped_due_to_bod_path=False,
    )


def assemble_triaged_text(
    conn: sqlite3.Connection,
    *,
    source_id: str,
) -> str:
    """Concatenate text from chunks marked keep=true. Returns full source text if no triage decisions exist."""
    chunks = conn.execute(
        """
        SELECT chunk_kind, locator, text, triage_marked_for_extraction
        FROM source_document_chunk
        WHERE source_id = ?
        ORDER BY id
        """,
        (source_id,),
    ).fetchall()
    if not chunks:
        # No chunking; fall back to full text.
        row = conn.execute(
            "SELECT text FROM source_document_text WHERE source_id = ?", (source_id,)
        ).fetchone()
        return (row["text"] if row else "") or ""

    any_triaged = any(c["triage_marked_for_extraction"] is not None for c in chunks)
    if not any_triaged:
        # No triage run yet — return full concatenation.
        return "\n\n".join(c["text"] or "" for c in chunks)

    parts: list[str] = []
    for chunk in chunks:
        if chunk["triage_marked_for_extraction"] == 1:
            locator = chunk["locator"]
            try:
                loc = json.loads(locator)
                header = f"[{chunk['chunk_kind']} {loc}]"
            except json.JSONDecodeError:
                header = f"[{chunk['chunk_kind']}]"
            parts.append(f"{header}\n{chunk['text'] or ''}")
    return "\n\n".join(parts)


__all__ = ["TriageResult", "assemble_triaged_text", "run_triage_for_source"]
