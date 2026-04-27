"""Standard text-spec extractor — calls PROMPT_V1_text_spec, persists results."""

from __future__ import annotations

import sqlite3
from typing import Any

from meridian.config import settings
from meridian.extract.cross_references import build_supplementary_context
from meridian.extract.persist import persist_parsed_output
from meridian.extract.triage import assemble_triaged_text
from meridian.llm.client import call_llm
from meridian.prompts.loader import (
    TEXT_SPEC_PROMPT_FILENAME,
    extract_prompt_body,
    load_prompt,
    render_prompt,
)

_SYSTEM_PROMPT = (
    "You are an extractor for construction project deliverables. Your ENTIRE "
    "response must be a single valid JSON object matching the schema in the "
    "user prompt. Begin your response with the opening brace `{` and end with "
    "the closing brace `}`. Do NOT include any preamble, plan, narration, "
    "markdown fencing, or commentary. JSON only."
)

# Cap how much source text we send per call. Sonnet 4.x handles ~200k tokens
# but we don't want to waste cost; if we need more, the orchestrator should
# chunk per-section.
_MAX_SOURCE_CHARS = 100_000


def extract_text_spec_payload(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    extraction_job_id: str,
    provider: str | None = None,
    model: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Run the text-spec LLM call only.

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

    # Pull triaged text (only chunks the Haiku triage marked keep=true).
    # If no triage has run, this falls back to full source text.
    source_text = assemble_triaged_text(conn, source_id=source_id)
    if len(source_text) > _MAX_SOURCE_CHARS:
        source_text = source_text[:_MAX_SOURCE_CHARS] + "\n\n[... truncated ...]"

    supplementary_context, resolved_refs = build_supplementary_context(
        conn, source_id=source_id, source_text=source_text
    )

    body = extract_prompt_body(load_prompt(TEXT_SPEC_PROMPT_FILENAME))
    user_prompt = render_prompt(
        body,
        {
            "filename": source_row["filename"],
            "document_class": source_row["document_class"] or "unknown",
            "document_state": source_row["document_state"] or "null",
            "revision": source_row["revision"] or "",
            "source_text": source_text,
            "supplementary_context": supplementary_context,
        },
    )

    call = call_llm(
        conn,
        purpose="extract_text_spec",
        provider=provider,
        model=model,
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        prompt_version_ref=f"text_spec_{settings.prompt_text_spec_version}",
        job_id=extraction_job_id,
        max_tokens=settings.extraction_max_tokens,
    )
    if not isinstance(call.parsed_json, dict):
        raise RuntimeError(
            f"Text-spec extraction returned non-object JSON for source {source_id} "
            f"(call {call.id})."
        )

    return call.parsed_json, call.id


def run_text_spec_extraction(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    extraction_job_id: str,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, int]:
    """Run text-spec extraction for a single source. Returns persistence counts.

    Backwards-compatible wrapper that calls the LLM and immediately persists
    in a separate transaction. The orchestrator now prefers
    :func:`extract_text_spec_payload` so it can fold persist + EJS state
    transition into a single atomic commit.
    """
    parsed, call_id = extract_text_spec_payload(
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


__all__ = ["extract_text_spec_payload", "run_text_spec_extraction"]
