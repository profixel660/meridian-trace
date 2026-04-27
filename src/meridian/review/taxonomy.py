"""Taxonomy governance — confirm / merge / reject pending taxonomy values.

CONTEXT.md §5: trade / service / category taxonomies are extensible per
project but extension is controlled. New values appear when an extraction
encounters a term not in the canonical list — they enter as
`source='user_added', confirmed_at=NULL`. The reviewer then:

  * confirm → sets confirmed_at; the value becomes canonical for the project.
  * merge   → folds the value into an existing canonical (target) value;
              all referencing deliverable rows are repointed; source value
              is soft-deleted with `source='user_merged'`; source's value
              is appended to target's `synonyms` JSON.
  * reject  → soft-delete (is_active=0). Refuses if any deliverable still
              references the row — the reviewer must re-edit those first.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel

from meridian.db.connection import transaction

# ─── Models ─────────────────────────────────────────────────────────────────


class TaxonomyProposal(BaseModel):
    table: Literal["trade", "service", "category"]
    id: str
    value: str
    source: str
    created_at: str
    in_use_count: int
    # ── Round-15 auto-assessment fields (DECISIONS.md §3.10). Populated by
    # the bootstrap LLM sweep when it proposes a new value; NULL on legacy
    # rows that pre-date the auto-assessment feature. The SME-facing
    # surfaces (CLI walk-taxonomy + web TaxonomyQueue) render these as a
    # recommendation alongside the proposal — the SME still presses a key.
    llm_recommended_action: Literal["confirm", "merge_into", "defer_to_user"] | None = None
    llm_merge_target: str | None = None
    llm_confidence: float | None = None
    llm_reasoning: str | None = None


class TaxonomyMutationResult(BaseModel):
    table: Literal["trade", "service", "category"]
    id: str
    value: str
    action: Literal["confirmed", "merged", "rejected"]
    affected_deliverable_count: int = 0
    target_id: str | None = None
    target_value: str | None = None


# ─── Helpers ────────────────────────────────────────────────────────────────


_TABLE_MAP: dict[str, tuple[str, str]] = {
    # logical name → (table_name, deliverable FK column)
    "trade": ("trade_taxonomy", "trade_id"),
    "service": ("service_taxonomy", "service_id"),
    "category": ("category_taxonomy", "category_id"),
}


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_table(table: str) -> tuple[str, str]:
    if table not in _TABLE_MAP:
        raise ValueError(
            f"table must be one of {sorted(_TABLE_MAP)}, got {table!r}"
        )
    return _TABLE_MAP[table]


def _in_use_count(conn: sqlite3.Connection, *, fk_col: str, taxonomy_id: str) -> int:
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM deliverable WHERE {fk_col} = ?",
        (taxonomy_id,),
    ).fetchone()
    return int(row["n"]) if row else 0


# ─── Public API ─────────────────────────────────────────────────────────────


def list_pending_taxonomy(conn: sqlite3.Connection) -> list[TaxonomyProposal]:
    """List unresolved taxonomy proposals across all three axes.

    "Pending" = source != 'default' AND confirmed_at IS NULL AND is_active = 1.
    Sorted by in_use_count DESC then created_at — most-referenced first
    drives review priority.
    """
    proposals: list[TaxonomyProposal] = []
    for logical, (table, fk_col) in _TABLE_MAP.items():
        rows = conn.execute(
            f"SELECT id, value, source, created_at, "
            f"  llm_recommended_action, llm_merge_target, "
            f"  llm_confidence, llm_reasoning "
            f"FROM {table} "
            f"WHERE source != 'default' AND confirmed_at IS NULL AND is_active = 1 "
            f"ORDER BY created_at"
        ).fetchall()
        for r in rows:
            proposals.append(
                TaxonomyProposal(
                    table=logical,  # type: ignore[arg-type]
                    id=r["id"],
                    value=r["value"],
                    source=r["source"],
                    created_at=r["created_at"],
                    in_use_count=_in_use_count(
                        conn, fk_col=fk_col, taxonomy_id=r["id"]
                    ),
                    llm_recommended_action=r["llm_recommended_action"],
                    llm_merge_target=r["llm_merge_target"],
                    llm_confidence=r["llm_confidence"],
                    llm_reasoning=r["llm_reasoning"],
                )
            )
    proposals.sort(key=lambda p: (-p.in_use_count, p.created_at))
    return proposals


def confirm_taxonomy(
    conn: sqlite3.Connection,
    *,
    table: Literal["trade", "service", "category"],
    value: str,
) -> TaxonomyMutationResult:
    """Mark a taxonomy value as canonical (confirmed_at=now). Source is left as-is."""
    table_name, fk_col = _resolve_table(table)
    row = conn.execute(
        f"SELECT id FROM {table_name} WHERE value = ?", (value,)
    ).fetchone()
    if row is None:
        raise ValueError(f"{table} value not found: {value!r}")
    with transaction(conn):
        conn.execute(
            f"UPDATE {table_name} SET confirmed_at = ? WHERE id = ?",
            (_now(), row["id"]),
        )
    return TaxonomyMutationResult(
        table=table,
        id=row["id"],
        value=value,
        action="confirmed",
        affected_deliverable_count=_in_use_count(
            conn, fk_col=fk_col, taxonomy_id=row["id"]
        ),
    )


def merge_taxonomy(
    conn: sqlite3.Connection,
    *,
    table: Literal["trade", "service", "category"],
    source_value: str,
    target_value: str,
) -> TaxonomyMutationResult:
    """Merge source_value into target_value. Source must exist; target must be active.

    - All deliverable rows referencing source.id are repointed to target.id.
    - source_value is added to target's synonyms JSON array.
    - source row is soft-deleted (is_active=0) with source='user_merged'.

    This is the §5 governance flow for "user merges a near-match into the
    existing canonical value".
    """
    table_name, fk_col = _resolve_table(table)
    src = conn.execute(
        f"SELECT id, value FROM {table_name} WHERE value = ?", (source_value,)
    ).fetchone()
    if src is None:
        raise ValueError(f"source {table} value not found: {source_value!r}")
    tgt = conn.execute(
        f"SELECT id, value, synonyms FROM {table_name} WHERE value = ? AND is_active = 1",
        (target_value,),
    ).fetchone()
    if tgt is None:
        raise ValueError(
            f"target {table} value not found or inactive: {target_value!r}"
        )
    if src["id"] == tgt["id"]:
        raise ValueError(f"cannot merge {table} value into itself: {source_value!r}")

    src_id = src["id"]
    tgt_id = tgt["id"]
    syns = json.loads(tgt["synonyms"]) if tgt["synonyms"] else []
    if not isinstance(syns, list):
        syns = []
    if source_value not in syns:
        syns.append(source_value)

    with transaction(conn):
        affected = conn.execute(
            f"UPDATE deliverable SET {fk_col} = ? WHERE {fk_col} = ?",
            (tgt_id, src_id),
        ).rowcount or 0
        conn.execute(
            f"UPDATE {table_name} SET synonyms = ? WHERE id = ?",
            (json.dumps(syns), tgt_id),
        )
        conn.execute(
            f"UPDATE {table_name} SET is_active = 0, source = 'user_merged' WHERE id = ?",
            (src_id,),
        )

    return TaxonomyMutationResult(
        table=table,
        id=src_id,
        value=source_value,
        action="merged",
        affected_deliverable_count=int(affected),
        target_id=tgt_id,
        target_value=target_value,
    )


def reject_taxonomy(
    conn: sqlite3.Connection,
    *,
    table: Literal["trade", "service", "category"],
    value: str,
) -> TaxonomyMutationResult:
    """Soft-delete a taxonomy value (is_active=0).

    Refuses if any deliverable still references the row — the reviewer
    must merge or re-edit those rows first. This guards against orphaning
    FKs in the master register.
    """
    table_name, fk_col = _resolve_table(table)
    row = conn.execute(
        f"SELECT id FROM {table_name} WHERE value = ?", (value,)
    ).fetchone()
    if row is None:
        raise ValueError(f"{table} value not found: {value!r}")
    in_use = _in_use_count(conn, fk_col=fk_col, taxonomy_id=row["id"])
    if in_use > 0:
        raise ValueError(
            f"cannot reject {table} value {value!r} — {in_use} deliverable(s) "
            "still reference it; merge or edit them first"
        )
    with transaction(conn):
        conn.execute(
            f"UPDATE {table_name} SET is_active = 0 WHERE id = ?",
            (row["id"],),
        )
    return TaxonomyMutationResult(
        table=table,
        id=row["id"],
        value=value,
        action="rejected",
        affected_deliverable_count=0,
    )


__all__ = [
    "TaxonomyMutationResult",
    "TaxonomyProposal",
    "confirm_taxonomy",
    "list_pending_taxonomy",
    "merge_taxonomy",
    "reject_taxonomy",
]
