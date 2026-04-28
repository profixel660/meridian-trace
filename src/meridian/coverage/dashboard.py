"""Coverage / provenance metrics for a Meridian project.

Pure SELECT aggregations against the per-project SQLite file. No mutations,
no LLM calls. The output is a Pydantic v2 dataclass tree that the CLI, the
HTTP API, and the Next.js review dashboard all render.

Trustworthiness rule (see CONTEXT.md §3, §9, §14):

    is_baseline_trustworthy
      ⇔  pending_decisions == 0
         AND provenance.fully_provenanced_pct == 100.0
         AND cost.cost_pct_known >= 95.0

The 95% cost-coverage threshold is a soft floor: a handful of pre-pricing
LLM calls (e.g. quality scans run before the cost catalogue had the model)
should not block trustworthiness, but a wholesale gap in cost data should.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from pydantic import BaseModel

# ─── Pydantic models ───────────────────────────────────────────────────────

class StatusBreakdown(BaseModel):
    auto_approved: int
    user_accepted: int
    user_edited: int
    user_promoted: int
    user_rejected: int
    quarantined: int
    total: int


class AuditCoverage(BaseModel):
    total: int
    promoted: int
    pending: int


class QuestionCoverage(BaseModel):
    pending: int
    resolved: int
    dismissed: int
    total: int


class ConflictCoverage(BaseModel):
    pending: int
    resolved_accept_a: int
    resolved_accept_b: int
    resolved_hybrid: int
    resolved_reject_both: int
    total: int


class TaxonomyCoverage(BaseModel):
    pending_proposals: int
    confirmed_user_added: int
    default_count: int
    by_table: dict[str, dict[str, int]]


class ProvenanceCoverage(BaseModel):
    total_deliverables: int
    with_source_id: int
    with_chunk_id: int
    with_extraction_job_id: int
    with_llm_call_id: int
    with_source_ref: int
    fully_provenanced: int
    fully_provenanced_pct: float


class CostCoverage(BaseModel):
    total_calls: int
    total_cost_cents: int
    calls_with_null_cost: int
    cost_pct_known: float


class ProjectCoverage(BaseModel):
    project_name: str
    schema_version: int

    sources_imported: int
    sources_scanned: int
    sources_extracted: int
    sources_pending_extraction: int

    deliverable_status: StatusBreakdown
    audit: AuditCoverage
    questions: QuestionCoverage
    conflicts: ConflictCoverage
    taxonomy: TaxonomyCoverage
    provenance: ProvenanceCoverage
    cost: CostCoverage

    review_coverage_pct: float
    auto_route_rate_pct: float
    pending_decisions: int

    last_extraction_at: str | None
    last_llm_call_at: str | None
    last_review_action_at: str | None

    is_data_present: bool
    is_baseline_trustworthy: bool | None
    baseline_trust_blockers: list[str]


# ─── Internal helpers ──────────────────────────────────────────────────────

def _scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return None
    # sqlite3.Row supports index access; plain tuple too.
    return row[0]


def _pct(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100.0, 1)


def _max_str(values: list[str | None]) -> str | None:
    """Max over ISO-8601 strings, ignoring None. ISO-8601 sorts lexicographically."""
    real = [v for v in values if v]
    return max(real) if real else None


# ─── Per-section computations ─────────────────────────────────────────────

def _status_breakdown(conn: sqlite3.Connection) -> StatusBreakdown:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS c FROM deliverable GROUP BY status"
    ).fetchall()
    counts: dict[str, int] = {r["status"]: r["c"] for r in rows}
    total = sum(counts.values())
    return StatusBreakdown(
        auto_approved=counts.get("auto_approved", 0),
        user_accepted=counts.get("user_accepted", 0),
        user_edited=counts.get("user_edited", 0),
        user_promoted=counts.get("user_promoted", 0),
        user_rejected=counts.get("user_rejected", 0),
        quarantined=counts.get("quarantined", 0),
        total=total,
    )


def _audit_coverage(conn: sqlite3.Connection) -> AuditCoverage:
    total = int(_scalar(conn, "SELECT COUNT(*) FROM audit_record") or 0)
    promoted = int(
        _scalar(
            conn,
            "SELECT COUNT(*) FROM audit_record "
            "WHERE user_promoted_to_deliverable_id IS NOT NULL",
        )
        or 0
    )
    return AuditCoverage(total=total, promoted=promoted, pending=total - promoted)


def _question_coverage(conn: sqlite3.Connection) -> QuestionCoverage:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS c FROM question GROUP BY status"
    ).fetchall()
    counts = {r["status"]: r["c"] for r in rows}
    return QuestionCoverage(
        pending=counts.get("pending", 0),
        resolved=counts.get("resolved", 0),
        dismissed=counts.get("dismissed", 0),
        total=sum(counts.values()),
    )


def _conflict_coverage(conn: sqlite3.Connection) -> ConflictCoverage:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS c FROM conflict GROUP BY status"
    ).fetchall()
    counts = {r["status"]: r["c"] for r in rows}
    return ConflictCoverage(
        pending=counts.get("pending", 0),
        resolved_accept_a=counts.get("resolved_accept_a", 0),
        resolved_accept_b=counts.get("resolved_accept_b", 0),
        resolved_hybrid=counts.get("resolved_hybrid", 0),
        resolved_reject_both=counts.get("resolved_reject_both", 0),
        total=sum(counts.values()),
    )


_TAXONOMY_TABLES = ("trade", "service", "category")


def _taxonomy_coverage(conn: sqlite3.Connection) -> TaxonomyCoverage:
    by_table: dict[str, dict[str, int]] = {}
    pending_total = 0
    confirmed_user_added_total = 0
    default_total = 0

    for short in _TAXONOMY_TABLES:
        table = f"{short}_taxonomy"
        pending = int(
            _scalar(
                conn,
                f"SELECT COUNT(*) FROM {table} "
                "WHERE source != 'default' AND confirmed_at IS NULL",
            )
            or 0
        )
        confirmed = int(
            _scalar(
                conn,
                f"SELECT COUNT(*) FROM {table} "
                "WHERE source != 'default' AND confirmed_at IS NOT NULL",
            )
            or 0
        )
        default_count = int(
            _scalar(conn, f"SELECT COUNT(*) FROM {table} WHERE source = 'default'") or 0
        )
        by_table[short] = {
            "pending": pending,
            "confirmed": confirmed,
            "default": default_count,
        }
        pending_total += pending
        confirmed_user_added_total += confirmed
        default_total += default_count

    return TaxonomyCoverage(
        pending_proposals=pending_total,
        confirmed_user_added=confirmed_user_added_total,
        default_count=default_total,
        by_table=by_table,
    )


def _provenance_coverage(conn: sqlite3.Connection) -> ProvenanceCoverage:
    row = conn.execute(
        """
        SELECT
            COUNT(*)                                                AS total,
            SUM(CASE WHEN source_id         IS NOT NULL THEN 1 ELSE 0 END) AS with_source_id,
            SUM(CASE WHEN chunk_id          IS NOT NULL THEN 1 ELSE 0 END) AS with_chunk_id,
            SUM(CASE WHEN extraction_job_id IS NOT NULL THEN 1 ELSE 0 END) AS with_job,
            SUM(CASE WHEN llm_call_id       IS NOT NULL THEN 1 ELSE 0 END) AS with_call,
            SUM(CASE
                WHEN json_extract(source_ref, '$.rendered') IS NOT NULL
                 AND json_extract(source_ref, '$.rendered') != ''
                THEN 1 ELSE 0 END) AS with_ref,
            SUM(CASE
                WHEN source_id         IS NOT NULL
                 AND extraction_job_id IS NOT NULL
                 AND llm_call_id       IS NOT NULL
                 AND json_extract(source_ref, '$.rendered') IS NOT NULL
                 AND json_extract(source_ref, '$.rendered') != ''
                THEN 1 ELSE 0 END) AS fully_provenanced
        FROM deliverable
        """
    ).fetchone()

    total = int(row["total"] or 0)
    fully = int(row["fully_provenanced"] or 0)
    return ProvenanceCoverage(
        total_deliverables=total,
        with_source_id=int(row["with_source_id"] or 0),
        with_chunk_id=int(row["with_chunk_id"] or 0),
        with_extraction_job_id=int(row["with_job"] or 0),
        with_llm_call_id=int(row["with_call"] or 0),
        with_source_ref=int(row["with_ref"] or 0),
        fully_provenanced=fully,
        fully_provenanced_pct=_pct(fully, total),
    )


def _cost_coverage(conn: sqlite3.Connection) -> CostCoverage:
    row = conn.execute(
        """
        SELECT
            COUNT(*)                                            AS total_calls,
            COALESCE(SUM(cost_cents), 0)                        AS total_cost,
            SUM(CASE WHEN cost_cents IS NULL THEN 1 ELSE 0 END) AS null_cost
        FROM llm_call
        """
    ).fetchone()
    total = int(row["total_calls"] or 0)
    null_cost = int(row["null_cost"] or 0)
    known = total - null_cost
    return CostCoverage(
        total_calls=total,
        total_cost_cents=int(row["total_cost"] or 0),
        calls_with_null_cost=null_cost,
        cost_pct_known=_pct(known, total),
    )


# ─── Public API ───────────────────────────────────────────────────────────

def project_coverage(conn: sqlite3.Connection) -> ProjectCoverage:
    """Compute coverage / provenance metrics for the project on `conn`.

    Pure read-only. Safe to call concurrently with extraction. <100ms even
    on large projects (all queries are indexed COUNTs / GROUP BYs).
    """
    # Ensure dict-style row access regardless of caller's row_factory choice.
    if conn.row_factory is not sqlite3.Row:
        conn.row_factory = sqlite3.Row

    project_row = conn.execute(
        "SELECT name, schema_version FROM project LIMIT 1"
    ).fetchone()
    project_name = project_row["name"] if project_row else "(unknown)"
    schema_version = int(project_row["schema_version"]) if project_row else 0

    sources_imported = int(_scalar(conn, "SELECT COUNT(*) FROM source_document") or 0)
    sources_scanned = int(
        _scalar(conn, "SELECT COUNT(*) FROM document_quality_scan") or 0
    )
    sources_extracted = int(
        _scalar(
            conn,
            "SELECT COUNT(DISTINCT source_id) FROM extraction_job_source "
            "WHERE status = 'completed'",
        )
        or 0
    )
    sources_pending_extraction = max(0, sources_imported - sources_extracted)

    status = _status_breakdown(conn)
    audit = _audit_coverage(conn)
    questions = _question_coverage(conn)
    conflicts = _conflict_coverage(conn)
    taxonomy = _taxonomy_coverage(conn)
    provenance = _provenance_coverage(conn)
    cost = _cost_coverage(conn)

    # auto_route_rate: how often the gate decided "trust this without review"
    auto_route_rate_pct = _pct(status.auto_approved, status.total)

    # review_coverage: of items that *needed* review (i.e. quarantined),
    # what fraction has been touched by a human (accepted / edited / promoted
    # / rejected)? Items that auto-approved aren't part of the denominator.
    reviewed = (
        status.user_accepted
        + status.user_edited
        + status.user_promoted
        + status.user_rejected
    )
    needing_review = status.total - status.auto_approved
    review_coverage_pct = min(100.0, _pct(reviewed, needing_review))

    pending_decisions = (
        status.quarantined
        + audit.pending
        + questions.pending
        + conflicts.pending
        + taxonomy.pending_proposals
    )

    last_extraction_at = _scalar(
        conn, "SELECT MAX(finished_at) FROM extraction_job"
    )
    last_llm_call_at = _scalar(conn, "SELECT MAX(finished_at) FROM llm_call")

    # No promoted_at column on audit_record — fall back to created_at for
    # the "any review activity happened here" signal. (Flagged in report.)
    last_review_candidates: list[str | None] = [
        _scalar(conn, "SELECT MAX(last_user_action_at) FROM deliverable"),
        _scalar(conn, "SELECT MAX(resolved_at) FROM question"),
        _scalar(conn, "SELECT MAX(resolved_at) FROM conflict"),
        _scalar(
            conn,
            "SELECT MAX(created_at) FROM audit_record "
            "WHERE user_promoted_to_deliverable_id IS NOT NULL",
        ),
    ]
    last_review_action_at = _max_str(last_review_candidates)

    # Aggregate "any data exists" signal across sources, deliverables, and LLM
    # calls. Empty projects (no opinion yet) get is_data_present=False,
    # is_baseline_trustworthy=None, and an empty blocker list — instead of the
    # nonsense "0% complete" messaging that previously fired the dashboard
    # NEEDS REVIEW banner on a project that just needs documents added.
    total_data_signals = sources_imported + status.total + cost.total_calls
    is_data_present = total_data_signals > 0

    if not is_data_present:
        is_baseline_trustworthy: bool | None = None
        blockers: list[str] = []
    else:
        blockers = []
        if status.quarantined:
            blockers.append(
                f"{status.quarantined} quarantined deliverables awaiting review"
            )
        if audit.pending:
            blockers.append(f"{audit.pending} audit rows awaiting promote/reject")
        if questions.pending:
            blockers.append(f"{questions.pending} pending HITL questions")
        if conflicts.pending:
            blockers.append(f"{conflicts.pending} pending conflicts")
        if taxonomy.pending_proposals:
            blockers.append(
                f"{taxonomy.pending_proposals} pending taxonomy proposals"
            )
        if provenance.fully_provenanced_pct < 100.0:
            missing = provenance.total_deliverables - provenance.fully_provenanced
            blockers.append(
                f"{missing} deliverables missing full provenance "
                f"({provenance.fully_provenanced_pct}% complete)"
            )
        if cost.cost_pct_known < 95.0:
            blockers.append(
                f"only {cost.cost_pct_known}% of LLM calls have known cost "
                f"({cost.calls_with_null_cost} of {cost.total_calls} unpriced)"
            )

        is_baseline_trustworthy = (
            pending_decisions == 0
            and provenance.fully_provenanced_pct == 100.0
            and cost.cost_pct_known >= 95.0
        )

    return ProjectCoverage(
        project_name=project_name,
        schema_version=schema_version,
        sources_imported=sources_imported,
        sources_scanned=sources_scanned,
        sources_extracted=sources_extracted,
        sources_pending_extraction=sources_pending_extraction,
        deliverable_status=status,
        audit=audit,
        questions=questions,
        conflicts=conflicts,
        taxonomy=taxonomy,
        provenance=provenance,
        cost=cost,
        review_coverage_pct=review_coverage_pct,
        auto_route_rate_pct=auto_route_rate_pct,
        pending_decisions=pending_decisions,
        last_extraction_at=last_extraction_at,
        last_llm_call_at=last_llm_call_at,
        last_review_action_at=last_review_action_at,
        is_data_present=is_data_present,
        is_baseline_trustworthy=is_baseline_trustworthy,
        baseline_trust_blockers=blockers,
    )


# ─── Plain-text renderer (CLI) ────────────────────────────────────────────

def _pct_of(n: int, total: int) -> str:
    if total <= 0:
        return ""
    return f" ({n / total * 100:.1f}%)"


def _dollars(cents: int) -> str:
    return f"${cents / 100:.2f}"


def render_coverage_text(c: ProjectCoverage) -> str:
    s = c.deliverable_status
    lines: list[str] = []
    lines.append(f"Project: {c.project_name} (schema v{c.schema_version})")
    lines.append("")
    lines.append("Sources:")
    lines.append(f"  imported       {c.sources_imported}")
    lines.append(f"  scanned        {c.sources_scanned}")
    lines.append(
        f"  extracted      {c.sources_extracted} / {c.sources_imported}"
    )
    if c.sources_pending_extraction:
        lines.append(
            f"  pending        {c.sources_pending_extraction}"
        )
    lines.append("")
    lines.append(f"Deliverables:    {s.total}")
    total = s.total
    lines.append(
        f"  auto_approved   {s.auto_approved}{_pct_of(s.auto_approved, total)}"
        "  <- baseline of unreviewed-but-trusted rows"
    )
    lines.append(f"  user_accepted   {s.user_accepted}")
    lines.append(f"  user_edited     {s.user_edited}")
    lines.append(f"  user_promoted   {s.user_promoted}")
    lines.append(f"  user_rejected   {s.user_rejected}")
    lines.append(
        f"  quarantined     {s.quarantined}{_pct_of(s.quarantined, total)}"
        "  <- awaiting review"
    )
    lines.append("")
    lines.append(f"Auto-route rate: {c.auto_route_rate_pct}%")
    lines.append(
        f"Review coverage: {c.review_coverage_pct}% of items needing review"
    )
    lines.append("")
    lines.append(f"Audit (OUTSIDE):  {c.audit.total}")
    lines.append(f"  promoted        {c.audit.promoted}")
    lines.append(f"  pending         {c.audit.pending}")
    lines.append("")
    lines.append("Pending decisions:")
    lines.append(f"  quarantined deliverables  {s.quarantined}")
    lines.append(f"  audit pending             {c.audit.pending}")
    lines.append(f"  HITL questions pending    {c.questions.pending}")
    lines.append(f"  conflicts pending         {c.conflicts.pending}")
    lines.append(
        f"  taxonomy proposals        {c.taxonomy.pending_proposals}"
    )
    lines.append(f"  TOTAL                     {c.pending_decisions}")
    lines.append("")
    lines.append(
        f"Provenance: {c.provenance.fully_provenanced_pct}% fully_provenanced "
        f"({c.provenance.fully_provenanced}/{c.provenance.total_deliverables})"
    )
    priced = c.cost.total_calls - c.cost.calls_with_null_cost
    lines.append(
        f"Cost coverage: {c.cost.cost_pct_known}% "
        f"({priced}/{c.cost.total_calls} calls priced; "
        f"{_dollars(c.cost.total_cost_cents)} total)"
    )
    lines.append("")
    lines.append(f"Last extraction: {c.last_extraction_at or 'never'}")
    lines.append(f"Last LLM call:   {c.last_llm_call_at or 'never'}")
    lines.append(
        f"Last review action: {c.last_review_action_at or 'never'}"
    )
    lines.append("")
    if c.is_baseline_trustworthy is None:
        lines.append("[--] NO DATA YET: add documents to populate coverage.")
    elif c.is_baseline_trustworthy:
        lines.append(
            "[OK] BASELINE TRUSTWORTHY: 0 pending decisions, full provenance."
        )
    else:
        lines.append("[BLOCKED] BASELINE NOT YET TRUSTWORTHY:")
        for b in c.baseline_trust_blockers:
            lines.append(f"   - {b}")
    return "\n".join(lines)
