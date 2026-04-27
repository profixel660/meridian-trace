"""Audit-record review: list and promote OUTSIDE candidates to deliverables.

Per CONTEXT.md §3, "outside" rows are NOT silently dropped — they live in
audit_record and are visible in a "Below Threshold" review queue. A reviewer
who judges an audit row to be a real deliverable promotes it; the audit row
keeps `user_promoted_to_deliverable_id` for the audit trail.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from meridian.db.connection import transaction
from meridian.review.deliverables import _resolve_taxonomy_value

# ─── Models ─────────────────────────────────────────────────────────────────


class AuditItem(BaseModel):
    audit_id: str
    source_id: str
    source_filename: str | None = None
    candidate_text: str
    rejection_reason: str
    landlord_response: str | None = None
    source_ref: dict[str, Any]
    created_at: str
    user_promoted_to_deliverable_id: str | None = None


class AuditPromotionResult(BaseModel):
    audit_id: str
    new_deliverable_id: str


# ─── Helpers ────────────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    return str(uuid.uuid4())


# ─── Public API ─────────────────────────────────────────────────────────────


def list_audit(
    conn: sqlite3.Connection,
    *,
    source_id: str | None = None,
    promoted_only: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> list[AuditItem]:
    """List audit (OUTSIDE) records.

    promoted_only=True restricts to rows already rescued into deliverables
    (useful for "what did the reviewer promote out of the audit queue?").
    """
    sql = [
        "SELECT a.*, sd.filename AS source_filename",
        "FROM audit_record a",
        "LEFT JOIN source_document sd ON sd.id = a.source_id",
        "WHERE 1 = 1",
    ]
    params: list[Any] = []
    if source_id is not None:
        sql.append("AND a.source_id = ?")
        params.append(source_id)
    if promoted_only:
        sql.append("AND a.user_promoted_to_deliverable_id IS NOT NULL")
    sql.append("ORDER BY a.created_at, a.id")
    sql.append("LIMIT ? OFFSET ?")
    params.extend([limit, offset])

    rows = conn.execute("\n".join(sql), params).fetchall()
    return [
        AuditItem(
            audit_id=r["id"],
            source_id=r["source_id"],
            source_filename=r["source_filename"],
            candidate_text=r["candidate_text"],
            rejection_reason=r["rejection_reason"],
            landlord_response=r["landlord_response"],
            source_ref=json.loads(r["source_ref"]),
            created_at=r["created_at"],
            user_promoted_to_deliverable_id=r["user_promoted_to_deliverable_id"],
        )
        for r in rows
    ]


def promote_audit_to_deliverable(
    conn: sqlite3.Connection,
    audit_id: str,
    *,
    trade_value: str | None = None,
    service_value: str | None = None,
    category_value: str | None = None,
    summary: str | None = None,
    flags: list[str] | None = None,
    applicable_standards: list[str] | None = None,
) -> AuditPromotionResult:
    """Promote an audit row to a new deliverable (status='user_promoted').

    The audit row stays put; its user_promoted_to_deliverable_id is set so
    the audit trail is preserved (CONTEXT.md §3 — "logged not lost").

    The new deliverable:
      - gate_outcome = 'inside' (the user's promotion is the gate decision)
      - auto_route_decision = 'quarantined' (it never went through auto-route)
      - extraction_group_id = a fresh uuid (not part of any prior multi-tag fan-out)
      - source_ref / source_id / chunk_id / extraction_job_id / llm_call_id
        are inherited from the audit row.
      - summary defaults to candidate_text if not supplied.
      - flags defaults to ['user_promoted'].
    """
    audit = conn.execute(
        "SELECT * FROM audit_record WHERE id = ?", (audit_id,)
    ).fetchone()
    if audit is None:
        raise ValueError(f"audit record not found: {audit_id}")

    new_deliverable_id = _new_id()
    new_group_id = _new_id()
    now = _now()

    final_summary = summary if summary is not None else audit["candidate_text"]
    final_flags = list(flags) if flags is not None else ["user_promoted"]
    final_standards = list(applicable_standards) if applicable_standards is not None else []

    with transaction(conn):
        trade_id = _resolve_taxonomy_value(
            conn, table="trade_taxonomy", value=trade_value
        )
        service_id = _resolve_taxonomy_value(
            conn, table="service_taxonomy", value=service_value
        )
        category_id = _resolve_taxonomy_value(
            conn, table="category_taxonomy", value=category_value
        )

        conn.execute(
            """
            INSERT INTO deliverable (
                id, extraction_group_id, source_id, chunk_id,
                extraction_job_id, llm_call_id, source_ref,
                trade_id, service_id, category_id,
                applicable_standards, confidence, flags, flag_context,
                deliverables_summary, gate_outcome, status,
                auto_route_decision, created_at, last_user_action_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_deliverable_id,
                new_group_id,
                audit["source_id"],
                audit["chunk_id"],
                audit["extraction_job_id"],
                audit["llm_call_id"],
                audit["source_ref"],
                trade_id,
                service_id,
                category_id,
                json.dumps(final_standards),
                "medium",  # promoted-from-audit defaults to medium confidence; user can edit later
                json.dumps(final_flags),
                json.dumps({}),
                final_summary,
                "inside",
                "user_promoted",
                "quarantined",
                now,
                now,
            ),
        )
        conn.execute(
            "UPDATE audit_record SET user_promoted_to_deliverable_id = ? WHERE id = ?",
            (new_deliverable_id, audit_id),
        )

    return AuditPromotionResult(audit_id=audit_id, new_deliverable_id=new_deliverable_id)


__all__ = [
    "AuditItem",
    "AuditPromotionResult",
    "list_audit",
    "promote_audit_to_deliverable",
]
