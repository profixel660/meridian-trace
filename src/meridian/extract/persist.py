"""Persist parsed prompt output: deliverables, audit, questions.

Includes taxonomy resolution (canonical name → id), with `taxonomy_new_value_proposed`
flagging when a value isn't in the canonical list.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable
from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Any

from meridian.db.connection import transaction


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    return str(uuid.uuid4())


def _taxonomy_lookup(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    rows = conn.execute(f"SELECT id, value FROM {table} WHERE is_active = 1").fetchall()
    return {r["value"]: r["id"] for r in rows}


def _resolve_or_propose(
    conn: sqlite3.Connection,
    *,
    table: str,
    value: str | None,
    cache: dict[str, str],
    taxonomy_extra_cols: dict[str, Any] | None = None,
) -> tuple[str | None, bool]:
    """Return (id, is_new). For new values: insert with source='user_added' (unconfirmed)."""
    if value is None:
        return None, False
    existing = cache.get(value)
    if existing:
        return existing, False
    new_id = _new_id()
    cols = ["id", "value", "source", "created_at"]
    vals: list[Any] = [new_id, value, "user_added", _now()]
    if taxonomy_extra_cols:
        for col, val in taxonomy_extra_cols.items():
            cols.append(col)
            vals.append(val)
    conn.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
        vals,
    )
    cache[value] = new_id
    return new_id, True


def _decide_auto_route(
    *, gate_outcome: str, confidence: str, flags: Iterable[str]
) -> str:
    """Per CONTEXT.md §9: high+no-flags → auto_approved; else quarantined."""
    flag_list = list(flags)
    if gate_outcome == "inside" and confidence == "high" and not flag_list:
        return "auto_approved"
    return "quarantined"


def _flatten_summary_with_marker(summary: str, flags: list[str]) -> str:
    """Hybrid Option C: append ' ⚠' when negotiated_response is in flags (CONTEXT.md §4)."""
    if "negotiated_response" in flags and "⚠" not in summary:
        return summary.rstrip() + " ⚠"
    return summary


def persist_parsed_output(
    conn: sqlite3.Connection,
    *,
    parsed: dict[str, Any],
    source_id: str,
    extraction_job_id: str,
    llm_call_id: str,
    in_transaction: bool = False,
) -> dict[str, int]:
    """Insert deliverables / audit / questions rows. Returns counts dict.

    By default this opens its own transaction. Pass ``in_transaction=True``
    when the caller has already opened a transaction (the orchestrator does
    this so the EJS state-flip + persist commit atomically per source —
    Part B of round-9 §6).
    """
    deliverables = parsed.get("deliverables") or []
    audit = parsed.get("audit") or []
    questions = parsed.get("questions") or []

    counts = {
        "inside": 0,
        "borderline": 0,
        "outside": 0,
        "questions": 0,
        "new_taxonomy_values": 0,
    }

    trade_cache = _taxonomy_lookup(conn, "trade_taxonomy")
    service_cache = _taxonomy_lookup(conn, "service_taxonomy")
    category_cache = _taxonomy_lookup(conn, "category_taxonomy")

    now = _now()
    txn_cm = nullcontext(conn) if in_transaction else transaction(conn)
    with txn_cm:
        for row in deliverables:
            if not isinstance(row, dict):
                continue

            trade_id, trade_new = _resolve_or_propose(
                conn,
                table="trade_taxonomy",
                value=row.get("trade"),
                cache=trade_cache,
                taxonomy_extra_cols={"category_hint": "specialist"},
            )
            service_id, service_new = _resolve_or_propose(
                conn,
                table="service_taxonomy",
                value=row.get("service"),
                cache=service_cache,
            )
            category_id, category_new = _resolve_or_propose(
                conn,
                table="category_taxonomy",
                value=row.get("category"),
                cache=category_cache,
            )

            flags = list(row.get("flags") or [])
            if any([trade_new, service_new, category_new]) and "taxonomy_new_value_proposed" not in flags:
                flags.append("taxonomy_new_value_proposed")
                counts["new_taxonomy_values"] += int(trade_new) + int(service_new) + int(category_new)

            gate_outcome = "borderline" if "definition_borderline" in flags else "inside"
            confidence = row.get("confidence") or "medium"
            auto_route = _decide_auto_route(
                gate_outcome=gate_outcome, confidence=confidence, flags=flags
            )
            status = auto_route  # initial status mirrors auto-route decision

            counts["borderline" if gate_outcome == "borderline" else "inside"] += 1

            source_ref = row.get("source_ref")
            if isinstance(source_ref, str):
                source_ref_obj: dict[str, Any] = {"kind": "raw", "rendered": source_ref}
            elif isinstance(source_ref, dict):
                source_ref_obj = source_ref
            else:
                source_ref_obj = {"kind": "raw", "rendered": ""}

            applicable_standards = row.get("applicable_standards") or []
            if not isinstance(applicable_standards, list):
                applicable_standards = [str(applicable_standards)]

            flag_context = row.get("flag_context") or {}
            if not isinstance(flag_context, dict):
                flag_context = {"raw": str(flag_context)}

            summary = _flatten_summary_with_marker(
                str(row.get("deliverables_summary", "")), flags
            )

            extraction_group_id = row.get("extraction_group_id") or _new_id()

            conn.execute(
                """
                INSERT INTO deliverable (
                    id, extraction_group_id, source_id, chunk_id,
                    extraction_job_id, llm_call_id, source_ref,
                    trade_id, service_id, category_id,
                    applicable_standards, confidence, flags, flag_context,
                    deliverables_summary, gate_outcome, status,
                    auto_route_decision, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _new_id(),
                    extraction_group_id,
                    source_id,
                    None,  # chunk_id wiring is per-chunk extractor's job
                    extraction_job_id,
                    llm_call_id,
                    json.dumps(source_ref_obj),
                    trade_id,
                    service_id,
                    category_id,
                    json.dumps(applicable_standards),
                    confidence,
                    json.dumps(flags),
                    json.dumps(flag_context),
                    summary,
                    gate_outcome,
                    status,
                    auto_route,
                    now,
                ),
            )

        for row in audit:
            if not isinstance(row, dict):
                continue
            counts["outside"] += 1
            source_ref = row.get("source_ref")
            if isinstance(source_ref, str):
                source_ref_obj = {"kind": "raw", "rendered": source_ref}
            elif isinstance(source_ref, dict):
                source_ref_obj = source_ref
            else:
                source_ref_obj = {"kind": "raw", "rendered": ""}
            conn.execute(
                """
                INSERT INTO audit_record (
                    id, source_id, chunk_id, extraction_job_id, llm_call_id,
                    source_ref, candidate_text, rejection_reason,
                    landlord_response, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _new_id(),
                    source_id,
                    None,
                    extraction_job_id,
                    llm_call_id,
                    json.dumps(source_ref_obj),
                    str(row.get("candidate_text", "")),
                    str(row.get("rejection_reason", "")),
                    row.get("landlord_response"),
                    now,
                ),
            )

        for q in questions:
            if not isinstance(q, dict):
                continue
            counts["questions"] += 1
            kind = q.get("kind") or "other"
            if kind not in {
                "service_mapping",
                "taxonomy_new_value",
                "disposition_unclear",
                "borderline_decision",
                "conflict_resolution",
                "other",
            }:
                kind = "other"
            refs = q.get("candidate_source_refs") or []
            if not isinstance(refs, list):
                refs = [refs]
            conn.execute(
                """
                INSERT INTO question (
                    id, extraction_job_id, llm_call_id, kind, context,
                    question_text, candidate_source_refs, proposed_resolution,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _new_id(),
                    extraction_job_id,
                    llm_call_id,
                    kind,
                    str(q.get("context", "")),
                    str(q.get("question") or q.get("question_text") or ""),
                    json.dumps(refs),
                    q.get("proposed_resolution"),
                    now,
                ),
            )

    return counts


__all__ = ["persist_parsed_output"]
