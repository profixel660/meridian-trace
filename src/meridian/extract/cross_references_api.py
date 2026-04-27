"""FastAPI router for the cross-reference exhaustive sweep.

Wired into the main app by `src/meridian/api/main.py` (main agent will add a
single line: `app.include_router(xref_router)`). Until then this router is
self-contained and importable.

Endpoints:
  - POST /projects/{name}/xref/sweep    body: {llm_assist: bool, dry_run: bool}
  - GET  /projects/{name}/xref/report   query: ?format=csv|md (default md)

Auth — round-12 §3.2: POST routes here require ``Authorization: Bearer
<token>`` (see ``meridian.auth.fastapi_dep.require_session``). GET routes
are open so report download / observability work without a session.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from meridian.auth.fastapi_dep import require_session
from meridian.db.connection import connect
from meridian.extract.cross_references import (
    SweepReport,
    latest_sweep_findings,
    run_exhaustive_sweep,
)
from meridian.logging import get_logger
from meridian.projects import project_db_path

_log = get_logger("meridian.api.xref")

xref_router = APIRouter(tags=["cross-references"])


class SweepRequest(BaseModel):
    """Body for POST /projects/{name}/xref/sweep."""

    llm_assist: bool = Field(
        default=False,
        description=(
            "Engage the optional LLM pass for ambiguous references the "
            "deterministic regex pass could not resolve. Default false."
        ),
    )
    dry_run: bool = Field(
        default=False,
        description=(
            "Generate the CSV+Markdown report WITHOUT writing to the DB. "
            "Useful for previewing what would be written."
        ),
    )


class SweepResponse(BaseModel):
    sweep_run_id: str
    started_at: str
    finished_at: str
    status: str
    sources_scanned: int
    deliverables_scanned: int
    findings_total: int
    confirmed: int
    borderline: int
    rejected: int
    report_csv_path: str | None
    report_md_path: str | None
    dry_run: bool
    llm_assist: bool


class XrefFinding(BaseModel):
    from_source_id: str
    to_source_id: str | None
    to_deliverable_id: str | None
    anchor_kind: str
    raw_match: str
    normalised_match: str
    detection_method: str
    outcome: str
    confidence: str
    reason: str | None
    review_question_id: str | None


class XrefReportResponse(BaseModel):
    sweep_run_id: str
    started_at: str
    finished_at: str | None
    status: str
    sources_scanned: int
    deliverables_scanned: int
    references_confirmed: int
    references_borderline: int
    references_rejected: int
    report_csv_path: str | None
    report_md_path: str | None
    findings: list[XrefFinding]


def _to_response(r: SweepReport) -> SweepResponse:
    return SweepResponse(**r.__dict__)


@xref_router.post(
    "/projects/{name}/xref/sweep",
    response_model=SweepResponse,
    dependencies=[Depends(require_session)],
    summary="Run the exhaustive cross-reference sweep",
    description=(
        "Walks every authoritative source for the project, extracts cross-"
        "reference anchors deterministically (sections, drawings, specs, "
        "standards, equipment tags, vendor names), resolves each anchor to "
        "another project source where possible, persists findings to "
        "`cross_reference_sweep_result`, and queues borderline findings into "
        "the SME review queue. Returns counts + paths to CSV/Markdown reports."
    ),
)
def post_sweep(name: str, body: SweepRequest) -> SweepResponse:
    db_path = project_db_path(name)
    if not db_path.exists():
        raise HTTPException(status_code=404, detail=f"Project not found: {name}")
    try:
        report = run_exhaustive_sweep(
            name, llm_assist=body.llm_assist, dry_run=body.dry_run
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        _log.exception("xref.sweep.api_failed", project=name)
        raise HTTPException(status_code=500, detail=f"sweep failed: {exc!r}") from exc
    return _to_response(report)


@xref_router.get(
    "/projects/{name}/xref/report",
    summary="Re-render the most recent cross-reference sweep results",
    description=(
        "Returns the most recent sweep run for the project, including all "
        "findings. Pass ?format=csv to download the CSV file directly; "
        "default returns JSON. ?format=md downloads the Markdown report."
    ),
)
def get_report(
    name: str,
    fmt: str = Query("json", alias="format", description="json | csv | md"),
):
    db_path = project_db_path(name)
    if not db_path.exists():
        raise HTTPException(status_code=404, detail=f"Project not found: {name}")
    fmt_norm = fmt.lower().strip()
    if fmt_norm not in {"json", "csv", "md"}:
        raise HTTPException(
            status_code=400, detail=f"Unknown format: {fmt} (expected json|csv|md)"
        )
    conn = connect(db_path)
    try:
        run_row, result_rows = latest_sweep_findings(conn)
    finally:
        conn.close()
    if run_row is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No cross-reference sweep has been run for this project yet. "
                "POST /projects/{name}/xref/sweep first."
            ),
        )

    if fmt_norm in {"csv", "md"}:
        path = run_row["report_csv_path"] if fmt_norm == "csv" else run_row["report_md_path"]
        if not path or not Path(path).exists():
            raise HTTPException(
                status_code=404,
                detail=f"Report file missing on disk: {path}",
            )
        media_type = "text/csv" if fmt_norm == "csv" else "text/markdown"
        return FileResponse(path, media_type=media_type, filename=Path(path).name)

    findings = [
        XrefFinding(
            from_source_id=r["from_source_id"],
            to_source_id=r["to_source_id"],
            to_deliverable_id=r["to_deliverable_id"],
            anchor_kind=r["anchor_kind"],
            raw_match=r["raw_match"],
            normalised_match=r["normalised_match"],
            detection_method=r["detection_method"],
            outcome=r["outcome"],
            confidence=r["confidence"],
            reason=r["reason"],
            review_question_id=r["review_question_id"],
        )
        for r in result_rows
    ]
    return XrefReportResponse(
        sweep_run_id=run_row["id"],
        started_at=run_row["started_at"],
        finished_at=run_row["finished_at"],
        status=run_row["status"],
        sources_scanned=run_row["sources_scanned"],
        deliverables_scanned=run_row["deliverables_scanned"],
        references_confirmed=run_row["references_confirmed"],
        references_borderline=run_row["references_borderline"],
        references_rejected=run_row["references_rejected"],
        report_csv_path=run_row["report_csv_path"],
        report_md_path=run_row["report_md_path"],
        findings=findings,
    )


__all__ = ["xref_router"]
