"""Per-document quality scan (CONTEXT.md §8). Sets class / state / extraction_path."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from meridian.config import settings
from meridian.db.connection import transaction
from meridian.llm.client import call_llm
from meridian.prompts.loader import (
    QUALITY_SCAN_PROMPT_FILENAME,
    extract_prompt_body,
    load_prompt,
    render_prompt,
)

# Cap how much source text we send to the scan call. The scan only needs
# enough context to classify; full-doc context would be wasteful.
_QUALITY_SCAN_EXCERPT_CHARS = 18000

_SYSTEM_PROMPT = (
    "You are a careful classifier for construction project documents. "
    "Return ONLY valid JSON matching the requested schema, with no commentary."
)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    return str(uuid.uuid4())


def _build_prompt(*, source_row: sqlite3.Row, source_text: str) -> str:
    body = extract_prompt_body(load_prompt(QUALITY_SCAN_PROMPT_FILENAME))
    excerpt = source_text[:_QUALITY_SCAN_EXCERPT_CHARS]
    if len(source_text) > _QUALITY_SCAN_EXCERPT_CHARS:
        excerpt += "\n\n[... truncated ...]"
    return render_prompt(
        body,
        {
            "filename": source_row["filename"],
            "mime_type": source_row["mime_type"],
            "size_bytes": source_row["size_bytes"],
            "relative_path": source_row["relative_path"],
            "max_chars": _QUALITY_SCAN_EXCERPT_CHARS,
            "source_text_excerpt": excerpt,
        },
    )


def run_quality_scan(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    job_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Run the LLM quality scan, persist results, update source_document fields. Returns parsed scan."""
    # provider/model intentionally optional — when omitted, call_llm resolves
    # via the per-purpose routing chain (env > project > settings > defaults).

    source_row = conn.execute(
        "SELECT * FROM source_document WHERE id = ?", (source_id,)
    ).fetchone()
    if source_row is None:
        raise ValueError(f"source_document not found: {source_id}")

    text_row = conn.execute(
        "SELECT text FROM source_document_text WHERE source_id = ?", (source_id,)
    ).fetchone()
    source_text = text_row["text"] if text_row else ""

    user_prompt = _build_prompt(source_row=source_row, source_text=source_text)

    call = call_llm(
        conn,
        purpose="quality_scan",
        provider=provider,
        model=model,
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        prompt_version_ref=f"quality_scan_{settings.prompt_quality_scan_version}",
        job_id=job_id,
    )
    if not isinstance(call.parsed_json, dict):
        raise RuntimeError(
            f"Quality scan returned non-object JSON for source {source_id} "
            f"(call {call.id}). Raw response stored in llm_call.response_text."
        )

    parsed = call.parsed_json
    scan_id = _new_id()
    now = _now()
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO document_quality_scan (
                id, source_id, llm_call_id, scan_quality, markups_present,
                illegible_regions, mismatched_references, template_detected,
                proposed_class, proposed_state, proposed_revision,
                summary, scanned_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scan_id,
                source_id,
                call.id,
                parsed.get("scan_quality", "clean"),
                int(bool(parsed.get("markups_present"))),
                json.dumps(parsed.get("illegible_regions") or []),
                json.dumps(parsed.get("mismatched_references") or []),
                int(bool(parsed.get("is_template"))),
                parsed.get("document_class"),
                parsed.get("document_state"),
                parsed.get("revision"),
                parsed.get("summary", ""),
                now,
            ),
        )

        # Update the source row with the scan's classifications.
        conn.execute(
            """
            UPDATE source_document
            SET document_class            = ?,
                document_state            = ?,
                revision                  = ?,
                revision_detected_via     = ?,
                is_template               = ?,
                is_demarcation_schedule   = ?,
                quality_scan_id           = ?,
                extraction_path           = ?
            WHERE id = ?
            """,
            (
                parsed.get("document_class"),
                parsed.get("document_state"),
                parsed.get("revision"),
                parsed.get("revision_detected_via"),
                int(bool(parsed.get("is_template"))),
                int(bool(parsed.get("is_demarcation_schedule"))),
                scan_id,
                parsed.get("extraction_path", "text_spec"),
                source_id,
            ),
        )

    return parsed


__all__ = ["run_quality_scan"]
