"""Conflict-detection second pass (CONTEXT.md §9 + §10).

Runs after single-source extraction completes. Pulls master register + audit,
calls the conflict-pass prompt, persists conflicts + parties, and stamps each
affected deliverable with a `conflicts_with_source_<conflict_id>` flag whose
context payload references the conflict id and the most-onerous reasoning.

The conflict pass NEVER auto-resolves. Every conflict is surfaced for HITL.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from meridian.config import settings
from meridian.db.connection import transaction
from meridian.llm.client import call_llm
from meridian.prompts.loader import (
    extract_prompt_body,
    load_prompt,
    render_prompt,
)

_CONFLICT_PROMPT_FILENAME = "PROMPT_V1_conflict_pass.md"

_SYSTEM_PROMPT = (
    "You are a careful cross-source reviewer for construction project "
    "deliverables. Your ENTIRE response must be a single valid JSON object "
    "matching the schema in the user prompt. Begin with `{` and end with `}`. "
    "No preamble. No markdown fencing. JSON only."
)

# Bound how many deliverable rows we send in a single call. Beyond this we'd
# need to batch. For v1 baseline a single call covers projects up to ~1000 rows.
_MAX_DELIVERABLES_PER_CALL = 800
_MAX_AUDIT_PER_CALL = 200


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    return str(uuid.uuid4())


@dataclass
class ConflictPassResult:
    job_id: str
    llm_call_id: str
    conflicts_persisted: int
    deliverables_in_input: int
    audit_in_input: int


def _select_deliverables_for_pass(conn: sqlite3.Connection) -> list[dict[str, object]]:
    rows = conn.execute(
        """
        SELECT
            d.id, d.confidence, d.flags, d.applicable_standards,
            d.source_ref, d.deliverables_summary,
            sd.filename     AS source_document,
            sd.document_class,
            sd.document_state,
            sd.is_demarcation_schedule,
            tt.value        AS trade,
            st.value        AS service,
            ct.value        AS category
        FROM deliverable d
        LEFT JOIN source_document   sd ON sd.id = d.source_id
        LEFT JOIN trade_taxonomy    tt ON tt.id = d.trade_id
        LEFT JOIN service_taxonomy  st ON st.id = d.service_id
        LEFT JOIN category_taxonomy ct ON ct.id = d.category_id
        WHERE d.status IN ('auto_approved', 'quarantined', 'user_accepted',
                           'user_edited', 'user_promoted')
        ORDER BY sd.is_demarcation_schedule DESC, sd.filename, d.created_at
        LIMIT ?
        """,
        (_MAX_DELIVERABLES_PER_CALL,),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "source_document": r["source_document"],
            "source_ref": json.loads(r["source_ref"] or "{}").get("rendered")
            or r["source_ref"],
            "trade": r["trade"],
            "service": r["service"],
            "category": r["category"],
            "confidence": r["confidence"],
            "applicable_standards": json.loads(r["applicable_standards"] or "[]"),
            "flags": json.loads(r["flags"] or "[]"),
            "document_class": r["document_class"],
            "document_state": r["document_state"],
            "is_demarcation_schedule": bool(r["is_demarcation_schedule"]),
            "deliverables_summary": r["deliverables_summary"],
        }
        for r in rows
    ]


def _select_audit_for_pass(conn: sqlite3.Connection) -> list[dict[str, object]]:
    rows = conn.execute(
        """
        SELECT
            a.id, a.candidate_text, a.rejection_reason, a.landlord_response,
            a.source_ref,
            sd.filename AS source_document,
            sd.document_class,
            sd.is_demarcation_schedule
        FROM audit_record a
        LEFT JOIN source_document sd ON sd.id = a.source_id
        ORDER BY sd.is_demarcation_schedule DESC, sd.filename, a.created_at
        LIMIT ?
        """,
        (_MAX_AUDIT_PER_CALL,),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "source_document": r["source_document"],
            "source_ref": json.loads(r["source_ref"] or "{}").get("rendered")
            or r["source_ref"],
            "candidate_text": r["candidate_text"],
            "rejection_reason": r["rejection_reason"],
            "landlord_response": r["landlord_response"],
            "document_class": r["document_class"],
            "is_demarcation_schedule": bool(r["is_demarcation_schedule"]),
        }
        for r in rows
    ]


def _create_pass_job(
    conn: sqlite3.Connection,
    *,
    provider: str,
    model: str,
) -> str:
    job_id = _new_id()
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO extraction_job (
                id, started_at, status, provider, model,
                prompt_text_spec_version, prompt_bod_version, triggered_by
            ) VALUES (?, ?, 'running', ?, ?, ?, ?, 'user_initiated')
            """,
            (
                job_id,
                _now(),
                provider,
                model,
                settings.prompt_text_spec_version,
                settings.prompt_bod_version,
            ),
        )
    return job_id


def _finish_pass_job(conn: sqlite3.Connection, *, job_id: str) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE extraction_job SET status = 'completed', finished_at = ? WHERE id = ?",
            (_now(), job_id),
        )


def _annotate_deliverable_with_conflict(
    conn: sqlite3.Connection,
    *,
    deliverable_id: str,
    conflict_id: str,
    most_onerous_reasoning: str,
) -> None:
    """Append `conflicts_with_source_<conflict_id>` to flags + populate flag_context."""
    row = conn.execute(
        "SELECT flags, flag_context FROM deliverable WHERE id = ?", (deliverable_id,)
    ).fetchone()
    if row is None:
        return
    flags = json.loads(row["flags"] or "[]")
    flag_context = json.loads(row["flag_context"] or "{}")
    flag_label = f"conflicts_with_source_{conflict_id}"
    if flag_label not in flags:
        flags.append(flag_label)
    flag_context[flag_label] = {
        "conflict_id": conflict_id,
        "most_onerous_reasoning": most_onerous_reasoning,
    }
    with transaction(conn):
        conn.execute(
            "UPDATE deliverable SET flags = ?, flag_context = ? WHERE id = ?",
            (json.dumps(flags), json.dumps(flag_context), deliverable_id),
        )


def _persist_conflicts(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    llm_call_id: str,
    parsed: dict[str, object],
    valid_deliverable_ids: set[str],
    valid_audit_ids: set[str],
) -> int:
    conflicts = parsed.get("conflicts") or []
    if not isinstance(conflicts, list):
        return 0

    persisted = 0
    now = _now()
    for raw in conflicts:
        if not isinstance(raw, dict):
            continue
        kind = raw.get("kind")
        if kind not in {
            "cross_source_content",
            "responsibility",
            "revision",
            "document_class",
            "scope_demarcation",
        }:
            continue
        parties = raw.get("parties") or []
        if not isinstance(parties, list) or len(parties) < 2:
            continue

        # Validate parties reference real ids.
        validated_parties: list[tuple[str, str, str]] = []  # (kind, id, position)
        for party in parties:
            if not isinstance(party, dict):
                continue
            d_id = party.get("deliverable_id")
            a_id = party.get("audit_id")
            position = str(party.get("position") or "")
            if d_id and d_id in valid_deliverable_ids:
                validated_parties.append(("deliverable", d_id, position))
            elif a_id and a_id in valid_audit_ids:
                validated_parties.append(("audit", a_id, position))
        if len(validated_parties) < 2:
            continue

        most_onerous = raw.get("most_onerous_party")
        most_onerous_reasoning = str(raw.get("most_onerous_reasoning") or "").strip()
        if not most_onerous_reasoning:
            most_onerous_reasoning = "(no reasoning provided)"
        # The most_onerous_party id must be one of the validated parties.
        if most_onerous and not any(p[1] == most_onerous for p in validated_parties):
            most_onerous = None

        conflict_id = _new_id()
        with transaction(conn):
            conn.execute(
                """
                INSERT INTO conflict (
                    id, extraction_job_id, llm_call_id, kind,
                    most_onerous_party_id, most_onerous_reasoning,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    conflict_id,
                    job_id,
                    llm_call_id,
                    kind,
                    most_onerous,
                    most_onerous_reasoning,
                    now,
                ),
            )
            for party_kind, party_id, position in validated_parties:
                conn.execute(
                    """
                    INSERT INTO conflict_party (conflict_id, party_kind, party_id, party_position)
                    VALUES (?, ?, ?, ?)
                    """,
                    (conflict_id, party_kind, party_id, position),
                )

        # Stamp `conflicts_with_source_<conflict_id>` on each deliverable party.
        for party_kind, party_id, _ in validated_parties:
            if party_kind == "deliverable":
                _annotate_deliverable_with_conflict(
                    conn,
                    deliverable_id=party_id,
                    conflict_id=conflict_id,
                    most_onerous_reasoning=most_onerous_reasoning,
                )

        persisted += 1

    return persisted


def run_conflict_pass(
    conn: sqlite3.Connection,
    *,
    provider: str | None = None,
    model: str | None = None,
) -> ConflictPassResult:
    """End-to-end: collect rows, call LLM, persist conflicts + party links + flags.

    `provider`/`model` are optional — when omitted, call_llm resolves via the
    per-purpose routing chain. The `extraction_job` snapshot row still records
    a (provider, model) pair for reproducibility headers; we resolve it here
    via settings so it reflects the *default* at job start.
    """
    if not provider or not model:
        from meridian.projects import get_project_routing
        try:
            project_routing = get_project_routing(conn)
        except Exception:  # noqa: BLE001 — robust to missing app_setting
            project_routing = None
        resolved_provider, resolved_model = settings.resolve_route(
            "conflict_pass", project_routing=project_routing
        )
        provider = provider or resolved_provider
        model = model or resolved_model

    deliverables = _select_deliverables_for_pass(conn)
    audit = _select_audit_for_pass(conn)
    if not deliverables and not audit:
        raise RuntimeError(
            "No deliverables or audit rows present — run extraction first before conflict pass."
        )

    body = extract_prompt_body(load_prompt(_CONFLICT_PROMPT_FILENAME))
    user_prompt = render_prompt(
        body,
        {
            "deliverables_json": json.dumps(deliverables, ensure_ascii=False, indent=2),
            "audit_json": json.dumps(audit, ensure_ascii=False, indent=2),
        },
    )

    job_id = _create_pass_job(conn, provider=provider, model=model)
    try:
        call = call_llm(
            conn,
            purpose="conflict_pass",
            provider=provider,
            model=model,
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            prompt_version_ref="conflict_pass_v1.0",
            job_id=job_id,
            max_tokens=settings.extraction_max_tokens,
        )
        if not isinstance(call.parsed_json, dict):
            raise RuntimeError(
                f"Conflict pass returned non-object JSON (call {call.id}). "
                "Raw response in llm_call.response_text."
            )
        valid_d = {row["id"] for row in deliverables}
        valid_a = {row["id"] for row in audit}
        persisted = _persist_conflicts(
            conn,
            job_id=job_id,
            llm_call_id=call.id,
            parsed=call.parsed_json,
            valid_deliverable_ids=valid_d,
            valid_audit_ids=valid_a,
        )
    finally:
        _finish_pass_job(conn, job_id=job_id)

    return ConflictPassResult(
        job_id=job_id,
        llm_call_id=call.id,
        conflicts_persisted=persisted,
        deliverables_in_input=len(deliverables),
        audit_in_input=len(audit),
    )


__all__ = ["ConflictPassResult", "run_conflict_pass"]
