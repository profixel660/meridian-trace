"""HITL question queue: list / resolve / dismiss.

Per CONTEXT.md §9, the LLM logs ambiguity questions during a run; the user
resolves them in batch. This module ONLY records the user's answer — it
does not act on the payload (e.g. it doesn't add a service mapping when a
service_mapping question is resolved). The caller is responsible for any
side effects; this layer is the audit trail of the answer.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from meridian.db.connection import transaction

# ─── Models ─────────────────────────────────────────────────────────────────


class QuestionItem(BaseModel):
    question_id: str
    extraction_job_id: str
    llm_call_id: str
    kind: str
    context: str
    question_text: str
    # Existing data may carry refs as either structured dicts or raw strings;
    # accept both so list_questions doesn't choke on legacy rows.
    candidate_source_refs: list[Any] = Field(default_factory=list)
    proposed_resolution: str | None = None
    status: str
    resolved_at: str | None = None
    resolution_payload: dict[str, Any] | None = None
    created_at: str


class QuestionResolutionResult(BaseModel):
    question_id: str
    before_status: str
    after_status: str


# ─── Helpers ────────────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── Public API ─────────────────────────────────────────────────────────────


def list_questions(
    conn: sqlite3.Connection,
    *,
    status: str | None = "pending",
    kind: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[QuestionItem]:
    """List HITL questions (default: pending).

    Pass status=None to list all questions regardless of status.
    """
    sql = ["SELECT * FROM question WHERE 1 = 1"]
    params: list[Any] = []
    if status is not None:
        sql.append("AND status = ?")
        params.append(status)
    if kind is not None:
        sql.append("AND kind = ?")
        params.append(kind)
    sql.append("ORDER BY created_at, id")
    sql.append("LIMIT ? OFFSET ?")
    params.extend([limit, offset])

    rows = conn.execute("\n".join(sql), params).fetchall()
    out: list[QuestionItem] = []
    for r in rows:
        out.append(
            QuestionItem(
                question_id=r["id"],
                extraction_job_id=r["extraction_job_id"],
                llm_call_id=r["llm_call_id"],
                kind=r["kind"],
                context=r["context"],
                question_text=r["question_text"],
                candidate_source_refs=json.loads(r["candidate_source_refs"] or "[]"),
                proposed_resolution=r["proposed_resolution"],
                status=r["status"],
                resolved_at=r["resolved_at"],
                resolution_payload=(
                    json.loads(r["resolution_payload"]) if r["resolution_payload"] else None
                ),
                created_at=r["created_at"],
            )
        )
    return out


def resolve_question(
    conn: sqlite3.Connection,
    question_id: str,
    *,
    resolution_payload: dict[str, Any],
    mark_status: str = "resolved",
) -> QuestionResolutionResult:
    """Record the user's answer to a HITL question.

    Does NOT itself act on the payload — e.g. resolving a service_mapping
    question does not insert a service_mapping row. The caller is
    responsible for any subsequent side effects; this function is the
    audit trail of the answer.
    """
    if mark_status not in {"resolved", "dismissed"}:
        raise ValueError(
            f"mark_status must be 'resolved' or 'dismissed', got {mark_status!r}"
        )
    row = conn.execute(
        "SELECT status FROM question WHERE id = ?", (question_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"question not found: {question_id}")
    before = row["status"]
    with transaction(conn):
        conn.execute(
            "UPDATE question SET status = ?, resolved_at = ?, resolution_payload = ? "
            "WHERE id = ?",
            (mark_status, _now(), json.dumps(resolution_payload), question_id),
        )
    return QuestionResolutionResult(
        question_id=question_id, before_status=before, after_status=mark_status
    )


def dismiss_question(
    conn: sqlite3.Connection,
    question_id: str,
    *,
    reason: str | None = None,
) -> QuestionResolutionResult:
    """Mark a question dismissed (status='dismissed').

    Stores `{"dismissed_reason": reason}` in resolution_payload when reason
    is supplied, otherwise stores `{}`.
    """
    payload: dict[str, Any] = {}
    if reason is not None:
        payload["dismissed_reason"] = reason
    return resolve_question(
        conn, question_id, resolution_payload=payload, mark_status="dismissed"
    )


__all__ = [
    "QuestionItem",
    "QuestionResolutionResult",
    "dismiss_question",
    "list_questions",
    "resolve_question",
]
