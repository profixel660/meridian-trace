"""Deliverable review operations: list / get / accept / edit / reject.

CONTEXT.md §3 (gate outcomes), §4 (multi-tag, ⚠ marker convention),
§9 (HITL review queue), §15 (round-trip via stable id + parent_deliverable_id).
DATA_MODEL_V1.md §6 (state machine + parent_deliverable_id chain).

All edits are non-destructive: an "edit" creates a NEW row with
parent_deliverable_id pointing back to the original. The v_master_register
view filter excludes the parent when an edited child exists.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from meridian.db.connection import transaction
from meridian.extract.persist import _flatten_summary_with_marker

# ─── Models ─────────────────────────────────────────────────────────────────


class QuarantinedItem(BaseModel):
    deliverable_id: str
    extraction_group_id: str
    source_id: str
    source_filename: str | None = None
    summary: str
    confidence: str
    flags: list[str] = Field(default_factory=list)
    flag_context: dict[str, Any] = Field(default_factory=dict)
    trade: str | None = None
    service: str | None = None
    category: str | None = None
    gate_outcome: str
    status: str
    created_at: str


class DeliverableDetail(BaseModel):
    deliverable_id: str
    extraction_group_id: str
    source_id: str
    source_filename: str | None = None
    chunk_id: str | None = None
    extraction_job_id: str
    llm_call_id: str
    source_ref: dict[str, Any]
    trade: str | None = None
    service: str | None = None
    category: str | None = None
    applicable_standards: list[str] = Field(default_factory=list)
    confidence: str
    flags: list[str] = Field(default_factory=list)
    flag_context: dict[str, Any] = Field(default_factory=dict)
    summary: str
    gate_outcome: str
    status: str
    auto_route_decision: str
    created_at: str
    last_user_action_at: str | None = None
    parent_deliverable_id: str | None = None
    notes: str | None = None
    # Conflict context — populated when this deliverable participates in any conflict.
    conflict_ids: list[str] = Field(default_factory=list)


class DeliverableMutationResult(BaseModel):
    before_status: str
    after_status: str
    deliverable_id: str
    parent_deliverable_id: str | None = None


# ─── Helpers ────────────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    return str(uuid.uuid4())


def _resolve_taxonomy_value(
    conn: sqlite3.Connection, *, table: str, value: str | None
) -> str | None:
    """Look up a taxonomy value; insert (source='user_added', confirmed_at=now) if missing.

    The user typing a value into the edit form counts as confirmation
    (per spec: 'user typed it = user confirmed it'). Returns the row id
    or None if value is None.
    """
    if value is None:
        return None
    row = conn.execute(
        f"SELECT id FROM {table} WHERE value = ? AND is_active = 1", (value,)
    ).fetchone()
    if row is not None:
        return row["id"]
    new_id = _new_id()
    now = _now()
    if table == "trade_taxonomy":
        conn.execute(
            f"INSERT INTO {table} (id, value, category_hint, source, confirmed_at, created_at) "
            f"VALUES (?, ?, ?, ?, ?, ?)",
            (new_id, value, "specialist", "user_added", now, now),
        )
    else:
        conn.execute(
            f"INSERT INTO {table} (id, value, source, confirmed_at, created_at) "
            f"VALUES (?, ?, ?, ?, ?)",
            (new_id, value, "user_added", now, now),
        )
    return new_id


# ─── Public API ─────────────────────────────────────────────────────────────


def list_quarantined(
    conn: sqlite3.Connection,
    *,
    source_id: str | None = None,
    with_flag: str | None = None,
    confidence: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[QuarantinedItem]:
    """List deliverables currently in the review queue (status='quarantined').

    Filters:
      source_id  — restrict to a single source document
      with_flag  — only items whose flags JSON array contains this value
      confidence — restrict by confidence level
    """
    sql = [
        "SELECT d.*, sd.filename AS source_filename,",
        "       t.value AS trade_value, s.value AS service_value, c.value AS category_value",
        "FROM deliverable d",
        "LEFT JOIN source_document sd ON sd.id = d.source_id",
        "LEFT JOIN trade_taxonomy t ON t.id = d.trade_id",
        "LEFT JOIN service_taxonomy s ON s.id = d.service_id",
        "LEFT JOIN category_taxonomy c ON c.id = d.category_id",
        "WHERE d.status = 'quarantined'",
    ]
    params: list[Any] = []
    if source_id is not None:
        sql.append("AND d.source_id = ?")
        params.append(source_id)
    if confidence is not None:
        sql.append("AND d.confidence = ?")
        params.append(confidence)
    if with_flag is not None:
        # JSON1 contains-style check: scan the JSON array for the value.
        sql.append(
            "AND EXISTS (SELECT 1 FROM json_each(d.flags) WHERE json_each.value = ?)"
        )
        params.append(with_flag)
    sql.append("ORDER BY d.created_at, d.id")
    sql.append("LIMIT ? OFFSET ?")
    params.extend([limit, offset])

    rows = conn.execute("\n".join(sql), params).fetchall()
    out: list[QuarantinedItem] = []
    for r in rows:
        out.append(
            QuarantinedItem(
                deliverable_id=r["id"],
                extraction_group_id=r["extraction_group_id"],
                source_id=r["source_id"],
                source_filename=r["source_filename"],
                summary=r["deliverables_summary"],
                confidence=r["confidence"],
                flags=json.loads(r["flags"] or "[]"),
                flag_context=json.loads(r["flag_context"] or "{}"),
                trade=r["trade_value"],
                service=r["service_value"],
                category=r["category_value"],
                gate_outcome=r["gate_outcome"],
                status=r["status"],
                created_at=r["created_at"],
            )
        )
    return out


def get_deliverable(conn: sqlite3.Connection, deliverable_id: str) -> DeliverableDetail:
    row = conn.execute(
        """
        SELECT d.*, sd.filename AS source_filename,
               t.value AS trade_value, s.value AS service_value, c.value AS category_value
        FROM deliverable d
        LEFT JOIN source_document sd ON sd.id = d.source_id
        LEFT JOIN trade_taxonomy t ON t.id = d.trade_id
        LEFT JOIN service_taxonomy s ON s.id = d.service_id
        LEFT JOIN category_taxonomy c ON c.id = d.category_id
        WHERE d.id = ?
        """,
        (deliverable_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"deliverable not found: {deliverable_id}")

    conflict_rows = conn.execute(
        "SELECT DISTINCT conflict_id FROM conflict_party "
        "WHERE party_kind = 'deliverable' AND party_id = ?",
        (deliverable_id,),
    ).fetchall()

    return DeliverableDetail(
        deliverable_id=row["id"],
        extraction_group_id=row["extraction_group_id"],
        source_id=row["source_id"],
        source_filename=row["source_filename"],
        chunk_id=row["chunk_id"],
        extraction_job_id=row["extraction_job_id"],
        llm_call_id=row["llm_call_id"],
        source_ref=json.loads(row["source_ref"]),
        trade=row["trade_value"],
        service=row["service_value"],
        category=row["category_value"],
        applicable_standards=json.loads(row["applicable_standards"] or "[]"),
        confidence=row["confidence"],
        flags=json.loads(row["flags"] or "[]"),
        flag_context=json.loads(row["flag_context"] or "{}"),
        summary=row["deliverables_summary"],
        gate_outcome=row["gate_outcome"],
        status=row["status"],
        auto_route_decision=row["auto_route_decision"],
        created_at=row["created_at"],
        last_user_action_at=row["last_user_action_at"],
        parent_deliverable_id=row["parent_deliverable_id"],
        notes=row["notes"],
        conflict_ids=[cr["conflict_id"] for cr in conflict_rows],
    )


def accept_deliverable(
    conn: sqlite3.Connection,
    deliverable_id: str,
    *,
    resolved_by_question_id: str | None = None,
) -> DeliverableMutationResult:
    """Mark a quarantined item as user_accepted.

    Idempotent — already-accepted is a no-op (returns matching before/after).
    """
    row = conn.execute(
        "SELECT status, parent_deliverable_id FROM deliverable WHERE id = ?",
        (deliverable_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"deliverable not found: {deliverable_id}")
    before = row["status"]
    if before == "user_accepted":
        return DeliverableMutationResult(
            before_status=before,
            after_status=before,
            deliverable_id=deliverable_id,
            parent_deliverable_id=row["parent_deliverable_id"],
        )
    with transaction(conn):
        conn.execute(
            "UPDATE deliverable SET status = 'user_accepted', "
            "last_user_action_at = ?, last_user_action_by_question_id = ? "
            "WHERE id = ?",
            (_now(), resolved_by_question_id, deliverable_id),
        )
    return DeliverableMutationResult(
        before_status=before,
        after_status="user_accepted",
        deliverable_id=deliverable_id,
        parent_deliverable_id=row["parent_deliverable_id"],
    )


def reject_deliverable(
    conn: sqlite3.Connection,
    deliverable_id: str,
    *,
    reason: str | None = None,
) -> DeliverableMutationResult:
    """Mark a deliverable as user_rejected; optionally record reason in flag_context."""
    row = conn.execute(
        "SELECT status, parent_deliverable_id, flag_context FROM deliverable WHERE id = ?",
        (deliverable_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"deliverable not found: {deliverable_id}")
    before = row["status"]
    flag_context = json.loads(row["flag_context"] or "{}")
    if reason is not None:
        flag_context["user_rejection_reason"] = reason
    with transaction(conn):
        conn.execute(
            "UPDATE deliverable SET status = 'user_rejected', "
            "flag_context = ?, last_user_action_at = ? "
            "WHERE id = ?",
            (json.dumps(flag_context), _now(), deliverable_id),
        )
    return DeliverableMutationResult(
        before_status=before,
        after_status="user_rejected",
        deliverable_id=deliverable_id,
        parent_deliverable_id=row["parent_deliverable_id"],
    )


def edit_deliverable(
    conn: sqlite3.Connection,
    deliverable_id: str,
    *,
    summary: str | None = None,
    trade_value: str | None = None,
    service_value: str | None = None,
    category_value: str | None = None,
    applicable_standards: list[str] | None = None,
    flags: list[str] | None = None,
    flag_context: dict[str, Any] | None = None,
    resolved_by_question_id: str | None = None,
) -> DeliverableMutationResult:
    """Create a NEW deliverable row carrying the user's edits.

    The original row is left intact; the new row's parent_deliverable_id
    points to it. Per CONTEXT.md §15, the v_master_register view excludes
    the parent when an edit child exists, so the master sheet shows the
    edited version while the audit trail keeps the pre-edit row.

    Field-by-field semantics (per spec):
      summary      — None → carry over from original; ⚠ marker re-applied.
      trade/service/category — string lookups; created with source='user_added'
                     and confirmed_at=now() if missing (typing == confirming).
      applicable_standards / flags / flag_context — REPLACE if provided
                     (None = carry over from original).
      auto_route_decision is preserved as audit metadata; an edit doesn't
                     re-derive it. status becomes 'user_edited'.
    """
    original = conn.execute(
        "SELECT * FROM deliverable WHERE id = ?", (deliverable_id,)
    ).fetchone()
    if original is None:
        raise ValueError(f"deliverable not found: {deliverable_id}")
    before = original["status"]

    new_id = _new_id()
    now = _now()

    new_flags = list(flags) if flags is not None else json.loads(original["flags"] or "[]")
    new_flag_context = (
        dict(flag_context)
        if flag_context is not None
        else json.loads(original["flag_context"] or "{}")
    )
    new_standards = (
        list(applicable_standards)
        if applicable_standards is not None
        else json.loads(original["applicable_standards"] or "[]")
    )

    with transaction(conn):
        new_trade_id = (
            _resolve_taxonomy_value(conn, table="trade_taxonomy", value=trade_value)
            if trade_value is not None
            else original["trade_id"]
        )
        new_service_id = (
            _resolve_taxonomy_value(conn, table="service_taxonomy", value=service_value)
            if service_value is not None
            else original["service_id"]
        )
        new_category_id = (
            _resolve_taxonomy_value(conn, table="category_taxonomy", value=category_value)
            if category_value is not None
            else original["category_id"]
        )

        # Summary: if not supplied, start from the original (which may already
        # carry a ⚠ marker). Strip any pre-existing marker so re-applying is idempotent.
        base_summary = summary if summary is not None else original["deliverables_summary"]
        base_summary_clean = base_summary.rstrip()
        if base_summary_clean.endswith("⚠"):
            base_summary_clean = base_summary_clean[:-1].rstrip()
        new_summary = _flatten_summary_with_marker(base_summary_clean, new_flags)

        conn.execute(
            """
            INSERT INTO deliverable (
                id, extraction_group_id, source_id, chunk_id,
                extraction_job_id, llm_call_id, source_ref,
                trade_id, service_id, category_id,
                applicable_standards, confidence, flags, flag_context,
                deliverables_summary, gate_outcome, status,
                auto_route_decision, created_at, last_user_action_at,
                last_user_action_by_question_id, parent_deliverable_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id,
                original["extraction_group_id"],
                original["source_id"],
                original["chunk_id"],
                original["extraction_job_id"],
                original["llm_call_id"],
                original["source_ref"],
                new_trade_id,
                new_service_id,
                new_category_id,
                json.dumps(new_standards),
                original["confidence"],
                json.dumps(new_flags),
                json.dumps(new_flag_context),
                new_summary,
                original["gate_outcome"],
                "user_edited",
                original["auto_route_decision"],
                now,
                now,
                resolved_by_question_id,
                deliverable_id,
            ),
        )

    return DeliverableMutationResult(
        before_status=before,
        after_status="user_edited",
        deliverable_id=new_id,
        parent_deliverable_id=deliverable_id,
    )


__all__ = [
    "DeliverableDetail",
    "DeliverableMutationResult",
    "QuarantinedItem",
    "accept_deliverable",
    "edit_deliverable",
    "get_deliverable",
    "list_quarantined",
    "reject_deliverable",
]
