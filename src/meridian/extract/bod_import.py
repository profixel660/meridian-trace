"""BOD structured-import extractor — calls PROMPT_V1_bod_import, persists results."""

from __future__ import annotations

import sqlite3
from typing import Any

from meridian.config import settings
from meridian.extract.persist import persist_parsed_output
from meridian.llm.client import call_llm
from meridian.prompts.loader import (
    BOD_PROMPT_FILENAME,
    extract_prompt_body,
    load_prompt,
    render_prompt,
)

_SYSTEM_PROMPT = (
    "You are an extractor for construction project deliverables. This is the "
    "BOD structured-import path: every input row is already a candidate. "
    "Your ENTIRE response must be a single valid JSON object matching the "
    "schema in the user prompt. Begin your response with the opening brace `{` "
    "and end with the closing brace `}`. Do NOT include any preamble, plan, "
    "narration, markdown fencing, or commentary. JSON only."
)

# Cap chars per call. BOD inputs tend to be tabular and dense — a single 99-row
# Exhibit fits comfortably under 100k chars including formatting.
_MAX_SOURCE_CHARS = 120_000


def extract_bod_payload(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    extraction_job_id: str,
    provider: str | None = None,
    model: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Run the BOD LLM call only.

    Returns ``(parsed_payload, llm_call_id)``. Does NOT persist — the caller
    is expected to wrap persist + EJS state-transition in a single
    transaction (round-9 §6 Part B).
    """
    # provider/model intentionally optional — call_llm resolves via the
    # per-purpose routing chain when omitted.

    source_row = conn.execute(
        "SELECT * FROM source_document WHERE id = ?", (source_id,)
    ).fetchone()
    if source_row is None:
        raise ValueError(f"source_document not found: {source_id}")

    text_row = conn.execute(
        "SELECT text FROM source_document_text WHERE source_id = ?", (source_id,)
    ).fetchone()
    rows_text = (text_row["text"] if text_row else "") or ""
    if len(rows_text) > _MAX_SOURCE_CHARS:
        rows_text = rows_text[:_MAX_SOURCE_CHARS] + "\n\n[... truncated ...]"

    body = extract_prompt_body(load_prompt(BOD_PROMPT_FILENAME))
    user_prompt = render_prompt(
        body,
        {
            "filename": source_row["filename"],
            "document_state": source_row["document_state"] or "null",
            "revision": source_row["revision"] or "",
            "sheet_name": "(see header lines in input)",
            "column_mapping": "(default)",
            "rows": rows_text,
        },
    )

    call = call_llm(
        conn,
        purpose="extract_bod",
        provider=provider,
        model=model,
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        prompt_version_ref=f"bod_{settings.prompt_bod_version}",
        job_id=extraction_job_id,
        max_tokens=settings.extraction_max_tokens,
    )
    if not isinstance(call.parsed_json, dict):
        raise RuntimeError(
            f"BOD extraction returned non-object JSON for source {source_id} "
            f"(call {call.id})."
        )

    return call.parsed_json, call.id


def run_bod_extraction(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    extraction_job_id: str,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, int]:
    """Run BOD extraction for a single source. Returns persistence counts.

    Backwards-compatible wrapper that calls the LLM and immediately persists
    in a separate transaction. The orchestrator now prefers
    :func:`extract_bod_payload` so it can fold persist + EJS state transition
    into a single atomic commit.
    """
    parsed, call_id = extract_bod_payload(
        conn,
        source_id=source_id,
        extraction_job_id=extraction_job_id,
        provider=provider,
        model=model,
    )
    return persist_parsed_output(
        conn,
        parsed=parsed,
        source_id=source_id,
        extraction_job_id=extraction_job_id,
        llm_call_id=call_id,
    )


__all__ = ["extract_bod_payload", "run_bod_extraction"]
