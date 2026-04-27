"""Cross-source conflict resolution.

CONTEXT.md §9 — "most onerous" principle, no silent auto-resolution.
DATA_MODEL_V1.md §9 — conflict + conflict_party tables, status enum.

Resolution actions:
  accept_a / accept_b — accept one party; cascade-reject any losing
                        deliverable parties.
  reject_both         — cascade-reject every deliverable party.
  hybrid              — create a NEW deliverable representing the user's
                        merged answer; cascade-reject the originals.

For all actions, this module also strips the
`conflicts_with_source_<conflict_id>` flag from each affected deliverable's
flags array AND removes the matching key from flag_context — keeping the
schema clean once the conflict is settled.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from meridian.db.connection import transaction
from meridian.review.deliverables import _resolve_taxonomy_value, reject_deliverable

# ─── Models ─────────────────────────────────────────────────────────────────


class ConflictParty(BaseModel):
    party_kind: Literal["deliverable", "audit"]
    party_id: str
    party_position: str | None = None
    # Resolved label fields — populated for deliverable parties so the
    # caller can render the conflict without N extra queries.
    summary: str | None = None
    trade: str | None = None
    service: str | None = None
    source_filename: str | None = None


class ConflictItem(BaseModel):
    conflict_id: str
    extraction_job_id: str
    llm_call_id: str
    kind: str
    most_onerous_party_id: str | None = None
    most_onerous_reasoning: str
    status: str
    resolved_at: str | None = None
    resolved_into_deliverable_id: str | None = None
    created_at: str
    parties: list[ConflictParty] = Field(default_factory=list)


class ConflictResolutionResult(BaseModel):
    conflict_id: str
    action: str
    after_status: str
    new_deliverable_id: str | None = None
    rejected_deliverable_ids: list[str] = Field(default_factory=list)


# ─── Helpers ────────────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    return str(uuid.uuid4())


def _load_parties(
    conn: sqlite3.Connection, conflict_id: str
) -> list[ConflictParty]:
    """Load a conflict's parties, with deliverable details denormalised in.

    Order is by (rowid implicit creation order) — used by accept_a/accept_b
    to determine which party is "A" (first) vs "B" (later).
    """
    party_rows = conn.execute(
        "SELECT party_kind, party_id, party_position FROM conflict_party "
        "WHERE conflict_id = ? ORDER BY rowid",
        (conflict_id,),
    ).fetchall()
    parties: list[ConflictParty] = []
    for pr in party_rows:
        p = ConflictParty(
            party_kind=pr["party_kind"],
            party_id=pr["party_id"],
            party_position=pr["party_position"],
        )
        if pr["party_kind"] == "deliverable":
            d = conn.execute(
                """
                SELECT d.deliverables_summary, sd.filename AS source_filename,
                       t.value AS trade_value, s.value AS service_value
                FROM deliverable d
                LEFT JOIN source_document sd ON sd.id = d.source_id
                LEFT JOIN trade_taxonomy t ON t.id = d.trade_id
                LEFT JOIN service_taxonomy s ON s.id = d.service_id
                WHERE d.id = ?
                """,
                (pr["party_id"],),
            ).fetchone()
            if d is not None:
                p.summary = d["deliverables_summary"]
                p.source_filename = d["source_filename"]
                p.trade = d["trade_value"]
                p.service = d["service_value"]
        parties.append(p)
    return parties


def _strip_conflict_flag(
    conn: sqlite3.Connection, *, deliverable_id: str, conflict_id: str
) -> None:
    """Remove `conflicts_with_source_<conflict_id>` from the row's flags + flag_context."""
    flag_token = f"conflicts_with_source_{conflict_id}"
    row = conn.execute(
        "SELECT flags, flag_context FROM deliverable WHERE id = ?",
        (deliverable_id,),
    ).fetchone()
    if row is None:
        return
    flags = json.loads(row["flags"] or "[]")
    flag_context = json.loads(row["flag_context"] or "{}")
    new_flags = [f for f in flags if f != flag_token]
    had_ctx = flag_token in flag_context
    flag_context.pop(flag_token, None)
    if new_flags == flags and not had_ctx:
        # No-op: this row didn't carry the conflict token — nothing to strip.
        return
    conn.execute(
        "UPDATE deliverable SET flags = ?, flag_context = ? WHERE id = ?",
        (json.dumps(new_flags), json.dumps(flag_context), deliverable_id),
    )


# ─── Public API ─────────────────────────────────────────────────────────────


def list_conflicts(
    conn: sqlite3.Connection,
    *,
    status: str | None = "pending",
    kind: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[ConflictItem]:
    """List conflicts (default: pending) with parties resolved.

    Pass status=None to list every conflict.
    """
    sql = ["SELECT * FROM conflict WHERE 1 = 1"]
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
    out: list[ConflictItem] = []
    for r in rows:
        out.append(
            ConflictItem(
                conflict_id=r["id"],
                extraction_job_id=r["extraction_job_id"],
                llm_call_id=r["llm_call_id"],
                kind=r["kind"],
                most_onerous_party_id=r["most_onerous_party_id"],
                most_onerous_reasoning=r["most_onerous_reasoning"],
                status=r["status"],
                resolved_at=r["resolved_at"],
                resolved_into_deliverable_id=r["resolved_into_deliverable_id"],
                created_at=r["created_at"],
                parties=_load_parties(conn, r["id"]),
            )
        )
    return out


def resolve_conflict(
    conn: sqlite3.Connection,
    conflict_id: str,
    *,
    action: Literal["accept_a", "accept_b", "reject_both", "hybrid"],
    accept_party_id: str | None = None,
    hybrid_summary: str | None = None,
    hybrid_trade_value: str | None = None,
    hybrid_service_value: str | None = None,
    hybrid_category_value: str | None = None,
    hybrid_flags: list[str] | None = None,
) -> ConflictResolutionResult:
    """Resolve a conflict with one of four actions.

    accept_a / accept_b:
      * `accept_party_id` is required and must match a party in conflict_party.
      * Party order is implicit creation order (rowid). The "first" party is A;
        any later party is B. If accept_party_id matches the first listed party
        the conflict status becomes 'resolved_accept_a', otherwise
        'resolved_accept_b'. (Documented because the spec notes this is a
        convention choice, not a stored axis.)
      * Each OTHER deliverable party is rejected via reject_deliverable.

    reject_both:
      * Every deliverable party is rejected. Status → 'resolved_reject_both'.

    hybrid:
      * Requires hybrid_summary AND at least one of trade/service.
      * Creates a NEW deliverable (status='user_promoted', gate_outcome='inside',
        auto_route_decision='quarantined') carrying the user's merged answer.
      * extraction_group_id matches the FIRST deliverable party's group, so the
        hybrid sits with the related fan-out rows.
      * Each existing deliverable party is rejected (the hybrid replaces them).
      * conflict.resolved_into_deliverable_id is set to the new id.

    For every action: any remaining `conflicts_with_source_<conflict_id>` flag
    on each affected deliverable is stripped, and the matching flag_context
    entry is removed.
    """
    conflict_row = conn.execute(
        "SELECT * FROM conflict WHERE id = ?", (conflict_id,)
    ).fetchone()
    if conflict_row is None:
        raise ValueError(f"conflict not found: {conflict_id}")

    parties = _load_parties(conn, conflict_id)
    if not parties:
        raise ValueError(f"conflict has no parties: {conflict_id}")

    deliverable_party_ids = [p.party_id for p in parties if p.party_kind == "deliverable"]
    rejected_ids: list[str] = []
    new_deliverable_id: str | None = None
    after_status: str

    if action in ("accept_a", "accept_b"):
        if accept_party_id is None:
            raise ValueError("accept_party_id is required for accept_a / accept_b")
        if accept_party_id not in {p.party_id for p in parties}:
            raise ValueError(
                f"accept_party_id {accept_party_id} is not a party of conflict {conflict_id}"
            )
        # Convention: party at index 0 → A; any later party → B. Action label
        # must match the position of the accepted party.
        is_first = parties[0].party_id == accept_party_id
        derived_status = "resolved_accept_a" if is_first else "resolved_accept_b"
        if action != derived_status[len("resolved_") :]:
            # Caller said accept_a but accept_party_id is at position >0 (or vice
            # versa). Honour the position-derived label — that's the source of
            # truth for which side won.
            pass
        after_status = derived_status
        # Reject the OTHER deliverable parties (the losers).
        for did in deliverable_party_ids:
            if did == accept_party_id:
                continue
            reject_deliverable(
                conn, did, reason=f"conflict_{conflict_id}_lost_to_{accept_party_id}"
            )
            rejected_ids.append(did)

    elif action == "reject_both":
        after_status = "resolved_reject_both"
        for did in deliverable_party_ids:
            reject_deliverable(conn, did, reason=f"conflict_{conflict_id}_reject_both")
            rejected_ids.append(did)

    elif action == "hybrid":
        if not hybrid_summary:
            raise ValueError("hybrid_summary is required for hybrid resolution")
        if hybrid_trade_value is None and hybrid_service_value is None:
            raise ValueError(
                "hybrid resolution requires at least one of "
                "hybrid_trade_value / hybrid_service_value"
            )
        after_status = "resolved_hybrid"
        # Pull source / job / llm context from the first deliverable party so
        # the hybrid is anchored to a concrete provenance record. Its
        # extraction_group_id matches that first party's group so related
        # multi-tag rows stay together.
        first_deliverable_id = (
            deliverable_party_ids[0] if deliverable_party_ids else None
        )
        if first_deliverable_id is None:
            raise ValueError(
                f"conflict {conflict_id} has no deliverable parties — "
                "cannot synthesise a hybrid"
            )
        anchor = conn.execute(
            "SELECT * FROM deliverable WHERE id = ?", (first_deliverable_id,)
        ).fetchone()
        if anchor is None:
            raise ValueError(
                f"anchor deliverable {first_deliverable_id} not found"
            )

        new_deliverable_id = _new_id()
        now = _now()
        with transaction(conn):
            trade_id = _resolve_taxonomy_value(
                conn, table="trade_taxonomy", value=hybrid_trade_value
            )
            service_id = _resolve_taxonomy_value(
                conn, table="service_taxonomy", value=hybrid_service_value
            )
            category_id = _resolve_taxonomy_value(
                conn, table="category_taxonomy", value=hybrid_category_value
            )
            final_flags = list(hybrid_flags) if hybrid_flags is not None else []
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
                    anchor["extraction_group_id"],
                    anchor["source_id"],
                    anchor["chunk_id"],
                    anchor["extraction_job_id"],
                    anchor["llm_call_id"],
                    anchor["source_ref"],
                    trade_id,
                    service_id,
                    category_id,
                    json.dumps([]),
                    "medium",
                    json.dumps(final_flags),
                    json.dumps({}),
                    hybrid_summary,
                    "inside",
                    "user_promoted",
                    "quarantined",
                    now,
                    now,
                ),
            )
        # Reject the original deliverable parties — the hybrid supersedes them.
        for did in deliverable_party_ids:
            reject_deliverable(
                conn, did, reason=f"conflict_{conflict_id}_replaced_by_hybrid"
            )
            rejected_ids.append(did)
    else:
        raise ValueError(f"unknown action: {action}")

    # Strip the conflicts_with_source_<id> flag from every deliverable party
    # (loser AND accepted party — once the conflict is resolved nobody should
    # still carry the flag).
    with transaction(conn):
        for did in deliverable_party_ids:
            _strip_conflict_flag(conn, deliverable_id=did, conflict_id=conflict_id)
        conn.execute(
            "UPDATE conflict SET status = ?, resolved_at = ?, "
            "resolved_into_deliverable_id = ? WHERE id = ?",
            (after_status, _now(), new_deliverable_id, conflict_id),
        )

    return ConflictResolutionResult(
        conflict_id=conflict_id,
        action=action,
        after_status=after_status,
        new_deliverable_id=new_deliverable_id,
        rejected_deliverable_ids=rejected_ids,
    )


__all__ = [
    "ConflictItem",
    "ConflictParty",
    "ConflictResolutionResult",
    "list_conflicts",
    "resolve_conflict",
]
