"""Demarcation Schedule extractor — primary-reference scope+allocation extraction.

Authority: CONTEXT.md §5 (demarcation = primary reference for trade allocation),
PROMPT_V1_demarcation.md.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from meridian.config import settings
from meridian.extract.persist import persist_parsed_output
from meridian.extract.triage import assemble_triaged_text
from meridian.llm.client import call_llm
from meridian.prompts.loader import (
    extract_prompt_body,
    load_prompt,
    render_prompt,
)

_DEMARCATION_PROMPT_FILENAME = "PROMPT_V1_demarcation.md"

_SYSTEM_PROMPT = (
    "You are extracting an authoritative scope+allocation register from a "
    "Demarcation Schedule. Your ENTIRE response must be a single valid JSON "
    "object matching the schema in the user prompt. Begin with `{` and end "
    "with `}`. No preamble. No markdown fencing. JSON only."
)

_MAX_SOURCE_CHARS = 120_000


def extract_demarcation_payload(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    extraction_job_id: str,
    provider: str | None = None,
    model: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Run the demarcation LLM call only.

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

    source_text = assemble_triaged_text(conn, source_id=source_id)
    if len(source_text) > _MAX_SOURCE_CHARS:
        source_text = source_text[:_MAX_SOURCE_CHARS] + "\n\n[... truncated ...]"

    body = extract_prompt_body(load_prompt(_DEMARCATION_PROMPT_FILENAME))
    user_prompt = render_prompt(
        body,
        {
            "filename": source_row["filename"],
            "document_state": source_row["document_state"] or "null",
            "revision": source_row["revision"] or "",
            "source_text": source_text,
        },
    )

    call = call_llm(
        conn,
        purpose="extract_demarcation",  # dedicated purpose enum value (schema v2)
        provider=provider,
        model=model,
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        prompt_version_ref="demarcation_v1.0",
        job_id=extraction_job_id,
        max_tokens=settings.extraction_max_tokens,
    )
    if not isinstance(call.parsed_json, dict):
        raise RuntimeError(
            f"Demarcation extraction returned non-object JSON for source {source_id} "
            f"(call {call.id})."
        )

    return call.parsed_json, call.id


def run_demarcation_extraction(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    extraction_job_id: str,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, int]:
    """Run demarcation-schedule extraction for a single source. Returns persistence counts.

    Backwards-compatible wrapper that calls the LLM and immediately persists
    in a separate transaction. The orchestrator now prefers
    :func:`extract_demarcation_payload` so it can fold persist + EJS state
    transition into a single atomic commit.
    """
    parsed, call_id = extract_demarcation_payload(
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


__all__ = ["extract_demarcation_payload", "run_demarcation_extraction"]
