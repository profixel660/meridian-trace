"""FastAPI app — minimal endpoints to drive Meridian end-to-end.

The Next.js shell will replace this UI; in the meantime, FastAPI's auto-
generated `/docs` is the operator surface.

Auth — round-12 §3.2 enforcement:
    POST/PUT/DELETE routes (mutating "write" endpoints) require a valid
    bearer token via ``Authorization: Bearer <token>`` (see
    ``meridian.auth.fastapi_dep.require_session``). GET routes are
    deliberately left open so local-dev tooling, the CLI, and observability
    probes (``/health``, ``/projects/.../status``, ``/coverage``, etc.)
    keep working without auth. Mint a token via ``POST /auth/login`` after
    enrolling TOTP through ``meridian auth enroll``.

    Two POST endpoints are *intentionally* unprotected:
      * ``POST /auth/login``  — chicken-and-egg, defined in ``auth_router``.
      * ``POST /auth/logout`` — idempotent, may need to clear a stale token.
      * ``POST /projects``    — first-run bootstrap, see comment on the
        handler. Tightened in v2 once a "first user" flow exists.
"""

from __future__ import annotations

# ============================================================================
# Alpha-12 stdout/stderr redirect — keep this at the VERY TOP of imports so
# every subsequent module import sees the redirected streams.
# ============================================================================
#
# The alpha-12 installer launches the backend via ``pythonw.exe`` (Windows
# GUI subsystem, no console) so closing the parent terminal can't signal
# uvicorn to shut down (that was the alpha-11 "documents uploaded then web
# interface broke" failure mode). pythonw has no inherited stderr, so
# uvicorn's INFO lines + the alpha-12 ``meridian.wizard.import`` per-file
# lines would silently disappear — unless we redirect.
#
# Redirect rule: when ``MERIDIAN_BACKEND_LOG`` is set, open it append-mode
# line-buffered and replace sys.stdout / sys.stderr. The installer sets the
# var; the gauntlet's cwd-System32-spawn step does too. In dev-tree /
# pytest / un-detached runs the var is unset and behaviour is unchanged.
import os as _os
import sys as _sys

_BACKEND_LOG_PATH = _os.environ.get("MERIDIAN_BACKEND_LOG")
_backend_log_file = None  # held in module globals for process lifetime
if _BACKEND_LOG_PATH:
    try:
        _log_dir = _os.path.dirname(_BACKEND_LOG_PATH)
        if _log_dir:
            _os.makedirs(_log_dir, exist_ok=True)
        # buffering=1 → line-buffered; lets `Get-Content -Tail -Wait` see
        # each uvicorn log line in real time without process flush.
        _backend_log_file = open(  # noqa: SIM115 — file kept open for process lifetime
            _BACKEND_LOG_PATH, "a", encoding="utf-8", buffering=1,
        )
        _sys.stdout = _backend_log_file
        _sys.stderr = _backend_log_file
        # Alpha-12 reviewer fix: register an atexit cleanup so the file
        # handle releases on graceful shutdown. Process death + OS reclaim
        # would handle this anyway, but the explicit close keeps test
        # re-imports honest (each pytest collection that imports this
        # module would otherwise leak one fd into the next run).
        import atexit as _atexit
        _atexit.register(lambda: _backend_log_file.close() if _backend_log_file else None)
    except OSError:
        # Best-effort: if the log path is unwriteable, fall through to
        # whatever stdout/stderr inherits (dev-tree run, or the user
        # will see the OSError on first uvicorn write — better than
        # crashing on import).
        pass

import contextlib
import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from meridian import __version__
from meridian.config import settings
from meridian.db.connection import connect
from meridian.export.excel import export_to_xlsx
from meridian.extract.orchestrator import run_job_over_sources
from meridian.ingest import ingest_file
from meridian.logging import configure_logging, get_logger
from meridian.projects import ProjectBusy, create_project, project_db_path

# Configure structured logging once at import. The FastAPI process is long-
# lived; the global `_global.logs/` location is the right default. Per-project
# context is bound on a per-request basis by the middleware below when the
# path includes a project name.
configure_logging(console=False)

# Alpha-15: TOTP enforcement on mutating endpoints removed. The TOTP
# infrastructure (login, totp, recovery, session) is preserved in the
# meridian.auth package for re-enabling later, but no route currently
# uses Depends(require_session). Rationale: shipping TOTP enforcement
# before the product itself was validated end-to-end created two alpha
# cycles of debug-bypass churn (alpha-13 / alpha-14) that delivered no
# product progress. Validate the product flow first; re-add auth gates
# at the v0.3 readiness checkpoint.

_log = get_logger("meridian.api")

app = FastAPI(
    title="Meridian API",
    version=__version__,
    description="Per-trade deliverables register from heterogeneous construction project documents.",
)

# Alpha-20: every /projects/{name}/<verb> JSON endpoint is registered on
# this router and mounted under the /api prefix. The bare /projects/<slug>
# URL space is reserved for the SPA — Next.js static-export HTML served
# directly by FastAPI's StaticFiles + the SPA-fallback routes for runtime
# slugs. Prior alphas registered API routes at /projects/{name}/... AND
# the SPA fallback at /projects/{slug}/{page}/..., which collided: a
# browser navigation to /projects/bod-2/quarantine matched the JSON API
# route (registered first) and the user got raw JSON instead of the SPA
# shell. Splitting at the URL level is the only collision-free fix.
_projects_api = APIRouter()


@app.middleware("http")
async def _structlog_middleware(request: Request, call_next):
    """Emit `api.request` + `api.response` events for every HTTP call."""
    started = time.perf_counter()
    method = request.method
    path = request.url.path
    _log.info("api.request", method=method, path=path)
    try:
        response = await call_next(request)
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        _log.exception(
            "api.response",
            method=method,
            path=path,
            status_code=500,
            duration_ms=duration_ms,
            error_type=type(exc).__name__,
        )
        raise
    duration_ms = int((time.perf_counter() - started) * 1000)
    _log.info(
        "api.response",
        method=method,
        path=path,
        status_code=response.status_code,
        duration_ms=duration_ms,
    )
    return response


# --------------------------------------------------------------------------
# Pydantic request/response models
# --------------------------------------------------------------------------


class CreateProjectRequest(BaseModel):
    name: str
    notes: str | None = None
    default_provider: str | None = None
    default_model: str | None = None


class CreateProjectResponse(BaseModel):
    project_id: str
    db_path: str


class ImportResponse(BaseModel):
    source_id: str
    filename: str
    extraction_method: str
    text_length: int
    chunk_count: int
    deduped: bool


class ExtractRequest(BaseModel):
    source_ids: list[str] | None = Field(default=None)
    provider: str | None = None
    model: str | None = None


class SourceResultPayload(BaseModel):
    source_id: str
    filename: str
    extraction_path: str
    counts: dict[str, int]
    skipped_reason: str | None = None
    error: str | None = None


class ExtractResponse(BaseModel):
    job_id: str
    sources: list[SourceResultPayload]


class ProjectListItem(BaseModel):
    name: str
    db_path: str
    created_at: str
    deliverables_master: int
    sources: int
    questions_pending: int
    conflicts_pending: int


class SourceItem(BaseModel):
    id: str
    filename: str
    mime_type: str
    size_bytes: int
    imported_at: str
    document_class: str | None = None
    document_state: str | None = None
    revision: str | None = None
    is_template: bool
    is_demarcation_schedule: bool
    extraction_path: str
    deliverables_count: int
    audit_count: int
    quality_scan_summary: str | None = None


class QuestionItem(BaseModel):
    id: str
    kind: str
    context: str
    question_text: str
    candidate_source_refs: list[Any]
    proposed_resolution: str | None = None
    created_at: str
    extraction_job_id: str


class ConflictParty(BaseModel):
    party_kind: Literal["deliverable", "audit"]
    party_id: str
    party_position: str | None = None
    summary_or_text: str


class ConflictItem(BaseModel):
    id: str
    kind: str
    status: str
    most_onerous_party_id: str | None = None
    most_onerous_reasoning: str
    created_at: str
    resolved_at: str | None = None
    parties: list[ConflictParty]


class AuditItem(BaseModel):
    id: str
    source_id: str
    source_document: str
    source_ref: dict[str, Any]
    candidate_text: str
    rejection_reason: str
    landlord_response: str | None = None
    user_promoted_to_deliverable_id: str | None = None
    created_at: str


class JobSourceItem(BaseModel):
    source_id: str
    filename: str
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    error_message: str | None = None
    last_completed_chunk_id: str | None = None
    worker_pid: int | None = None


class JobStatusResponse(BaseModel):
    id: str
    started_at: str
    finished_at: str | None = None
    status: str
    provider: str
    model: str
    prompt_text_spec_version: str
    prompt_bod_version: str
    cost_estimate_cents: int | None = None
    cost_actual_cents: int | None = None
    sources: list[JobSourceItem]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _ensure_project(name: str) -> Path:
    db_path = project_db_path(name)
    if not db_path.exists():
        raise HTTPException(status_code=404, detail=f"Project not found: {name}")
    return db_path


def _parse_json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, ValueError):
        return default


# --------------------------------------------------------------------------
# Existing endpoints
# --------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


# NOTE: ``POST /projects`` is intentionally NOT protected by ``require_session``.
# v1 is single-user desktop; first-time setup needs to create the very first
# project before the operator has any way to log in. Tighten this in v2 once
# we have a "first user" flow that can mint a token without an existing project.
@_projects_api.post("/projects", response_model=CreateProjectResponse)
def projects_create(req: CreateProjectRequest) -> CreateProjectResponse:
    try:
        project_id, db_path = create_project(
            name=req.name,
            notes=req.notes,
            default_provider=req.default_provider,
            default_model=req.default_model,
        )
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return CreateProjectResponse(project_id=project_id, db_path=str(db_path))


@_projects_api.get("/projects", response_model=list[ProjectListItem])
def projects_list() -> list[ProjectListItem]:
    """List every project SQLite file under `settings.projects_dir`.

    Skips files that fail to open or are missing the `project` table
    (corrupted / in-progress).
    """
    projects_dir = settings.projects_dir
    if not projects_dir.exists():
        return []

    items: list[ProjectListItem] = []
    for db_path in sorted(projects_dir.glob("*.sqlite")):
        try:
            conn = connect(db_path)
        except sqlite3.Error:
            continue
        try:
            row = conn.execute("SELECT created_at FROM project LIMIT 1").fetchone()
            if row is None:
                continue
            created_at = row["created_at"]
            deliverables_master = conn.execute(
                "SELECT COUNT(*) FROM v_master_register"
            ).fetchone()[0]
            sources = conn.execute(
                "SELECT COUNT(*) FROM source_document"
            ).fetchone()[0]
            questions_pending = conn.execute(
                "SELECT COUNT(*) FROM question WHERE status = 'pending'"
            ).fetchone()[0]
            conflicts_pending = conn.execute(
                "SELECT COUNT(*) FROM conflict WHERE status = 'pending'"
            ).fetchone()[0]
        except sqlite3.Error:
            conn.close()
            continue
        else:
            items.append(
                ProjectListItem(
                    name=db_path.stem,
                    db_path=str(db_path),
                    created_at=created_at,
                    deliverables_master=deliverables_master,
                    sources=sources,
                    questions_pending=questions_pending,
                    conflicts_pending=conflicts_pending,
                )
            )
        finally:
            conn.close()
    return items


@_projects_api.get("/projects/{name}/status")
def projects_status(name: str) -> dict[str, Any]:
    db_path = _ensure_project(name)
    conn = connect(db_path)
    try:
        result: dict[str, Any] = {}
        for label, sql in {
            "sources": "SELECT COUNT(*) FROM source_document",
            "deliverables_total": "SELECT COUNT(*) FROM deliverable",
            "deliverables_master": "SELECT COUNT(*) FROM v_master_register",
            "audit_outside": "SELECT COUNT(*) FROM audit_record",
            "questions_pending": "SELECT COUNT(*) FROM question WHERE status = 'pending'",
            "extraction_jobs": "SELECT COUNT(*) FROM extraction_job",
            "llm_calls": "SELECT COUNT(*) FROM llm_call",
        }.items():
            result[label] = conn.execute(sql).fetchone()[0]
        return result
    finally:
        conn.close()


@_projects_api.post(
    "/projects/{name}/sources",
    response_model=ImportResponse,
)
async def projects_import_source(name: str, file: UploadFile) -> ImportResponse:
    db_path = _ensure_project(name)
    suffix = Path(file.filename or "upload").suffix or ""
    incoming_dir = settings.projects_dir / name / "incoming"
    incoming_dir.mkdir(parents=True, exist_ok=True)
    target = incoming_dir / (file.filename or f"upload{suffix}")
    target.write_bytes(await file.read())

    # Write path: ingest writes rows to source_document and chunks. Use the
    # long busy_timeout — ingest can race a concurrent extraction job.
    conn = connect(db_path, busy_timeout_ms=30000)
    try:
        result = ingest_file(conn, file_path=target, project_root=settings.project_root)
        return ImportResponse(
            source_id=result.source_id,
            filename=result.filename,
            extraction_method=result.extraction_method,
            text_length=result.text_length,
            chunk_count=result.chunk_count,
            deduped=result.deduped,
        )
    finally:
        conn.close()


@_projects_api.get("/projects/{name}/sources", response_model=list[SourceItem])
def projects_list_sources(name: str) -> list[SourceItem]:
    """List source documents imported into the project."""
    db_path = _ensure_project(name)
    conn = connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT
                sd.id, sd.filename, sd.mime_type, sd.size_bytes, sd.imported_at,
                sd.document_class, sd.document_state, sd.revision,
                sd.is_template, sd.is_demarcation_schedule, sd.extraction_path,
                (SELECT COUNT(*) FROM deliverable   WHERE source_id = sd.id) AS deliverables_count,
                (SELECT COUNT(*) FROM audit_record  WHERE source_id = sd.id) AS audit_count,
                (SELECT summary FROM document_quality_scan WHERE source_id = sd.id)
                    AS quality_scan_summary
            FROM source_document sd
            ORDER BY sd.imported_at ASC
            """
        ).fetchall()
        return [
            SourceItem(
                id=r["id"],
                filename=r["filename"],
                mime_type=r["mime_type"],
                size_bytes=r["size_bytes"],
                imported_at=r["imported_at"],
                document_class=r["document_class"],
                document_state=r["document_state"],
                revision=r["revision"],
                is_template=bool(r["is_template"]),
                is_demarcation_schedule=bool(r["is_demarcation_schedule"]),
                extraction_path=r["extraction_path"],
                deliverables_count=r["deliverables_count"],
                audit_count=r["audit_count"],
                quality_scan_summary=r["quality_scan_summary"],
            )
            for r in rows
        ]
    finally:
        conn.close()


@_projects_api.post(
    "/projects/{name}/extract",
    response_model=ExtractResponse,
)
def projects_extract(name: str, req: ExtractRequest) -> ExtractResponse:
    db_path = _ensure_project(name)
    # Long-running writer: 30 s busy_timeout (Hazard 2 mitigation).
    conn = connect(db_path, busy_timeout_ms=30000)
    try:
        ids = req.source_ids
        if not ids:
            ids = [
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM source_document ORDER BY imported_at"
                ).fetchall()
            ]
        try:
            result = run_job_over_sources(
                conn, source_ids=ids, provider=req.provider, model=req.model
            )
        except ProjectBusy as exc:
            # Another process holds the project's extract lock — surface
            # 409 with the holder details so the client can retry.
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "project_busy",
                    "message": str(exc),
                    "holder_pid": exc.pid,
                    "holder_hostname": exc.hostname,
                    "holder_purpose": exc.purpose,
                    "holder_acquired_at": exc.acquired_at,
                },
            ) from exc
        return ExtractResponse(
            job_id=result.job_id,
            sources=[
                SourceResultPayload(
                    source_id=s.source_id,
                    filename=s.filename,
                    extraction_path=s.extraction_path,
                    counts=s.counts or {},
                    skipped_reason=s.skipped_reason,
                    error=s.error,
                )
                for s in result.sources
            ],
        )
    finally:
        conn.close()


@_projects_api.get("/projects/{name}/quarantine")
def projects_quarantine(name: str, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
    """List quarantined deliverables awaiting human review (CONTEXT.md §9)."""
    from meridian.review.deliverables import list_quarantined

    db_path = _ensure_project(name)
    conn = connect(db_path)
    try:
        return [item.model_dump() for item in list_quarantined(conn, limit=limit, offset=offset)]
    finally:
        conn.close()


@_projects_api.get("/projects/{name}/master")
def projects_master(name: str) -> list[dict[str, Any]]:
    db_path = _ensure_project(name)
    conn = connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT
                d.id, d.deliverables_summary, d.confidence, d.flags,
                d.applicable_standards, d.source_ref,
                sd.filename AS source_document,
                sd.document_class, sd.document_state,
                tt.value AS trade, st.value AS service, ct.value AS category
            FROM v_master_register d
            LEFT JOIN source_document   sd ON sd.id = d.source_id
            LEFT JOIN trade_taxonomy    tt ON tt.id = d.trade_id
            LEFT JOIN service_taxonomy  st ON st.id = d.service_id
            LEFT JOIN category_taxonomy ct ON ct.id = d.category_id
            ORDER BY sd.filename, d.created_at
            """
        ).fetchall()
        return [
            {
                "id": r["id"],
                "summary": r["deliverables_summary"],
                "trade": r["trade"],
                "service": r["service"],
                "category": r["category"],
                "confidence": r["confidence"],
                "flags": json.loads(r["flags"] or "[]"),
                "applicable_standards": json.loads(r["applicable_standards"] or "[]"),
                "source_ref": json.loads(r["source_ref"] or "{}"),
                "source_document": r["source_document"],
                "document_class": r["document_class"],
                "document_state": r["document_state"],
            }
            for r in rows
        ]
    finally:
        conn.close()


# --------------------------------------------------------------------------
# New endpoints: HITL queues, audit log, job status
# --------------------------------------------------------------------------


@_projects_api.get("/projects/{name}/questions", response_model=list[QuestionItem])
def projects_list_questions(
    name: str,
    status: Literal["pending", "resolved", "dismissed", "all"] = Query("pending"),
) -> list[QuestionItem]:
    """Return HITL questions, default filter status='pending'."""
    db_path = _ensure_project(name)
    conn = connect(db_path)
    try:
        if status == "all":
            rows = conn.execute(
                """
                SELECT id, kind, context, question_text, candidate_source_refs,
                       proposed_resolution, created_at, extraction_job_id
                FROM question
                ORDER BY created_at ASC
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, kind, context, question_text, candidate_source_refs,
                       proposed_resolution, created_at, extraction_job_id
                FROM question
                WHERE status = ?
                ORDER BY created_at ASC
                """,
                (status,),
            ).fetchall()
        return [
            QuestionItem(
                id=r["id"],
                kind=r["kind"],
                context=r["context"],
                question_text=r["question_text"],
                candidate_source_refs=_parse_json(r["candidate_source_refs"], []),
                proposed_resolution=r["proposed_resolution"],
                created_at=r["created_at"],
                extraction_job_id=r["extraction_job_id"],
            )
            for r in rows
        ]
    finally:
        conn.close()


@_projects_api.get("/projects/{name}/conflicts", response_model=list[ConflictItem])
def projects_list_conflicts(
    name: str,
    status: str = Query("pending"),
) -> list[ConflictItem]:
    """Return conflicts with their parties resolved.

    `status` accepts any of the values stored in conflict.status, plus 'all'
    to disable filtering. Default 'pending'.
    """
    db_path = _ensure_project(name)
    conn = connect(db_path)
    try:
        if status == "all":
            conflict_rows = conn.execute(
                """
                SELECT id, kind, status, most_onerous_party_id,
                       most_onerous_reasoning, created_at, resolved_at
                FROM conflict
                ORDER BY created_at ASC
                """
            ).fetchall()
        else:
            conflict_rows = conn.execute(
                """
                SELECT id, kind, status, most_onerous_party_id,
                       most_onerous_reasoning, created_at, resolved_at
                FROM conflict
                WHERE status = ?
                ORDER BY created_at ASC
                """,
                (status,),
            ).fetchall()

        items: list[ConflictItem] = []
        for c in conflict_rows:
            party_rows = conn.execute(
                """
                SELECT party_kind, party_id, party_position
                FROM conflict_party
                WHERE conflict_id = ?
                """,
                (c["id"],),
            ).fetchall()

            parties: list[ConflictParty] = []
            for p in party_rows:
                kind = p["party_kind"]
                pid = p["party_id"]
                summary_or_text = ""
                if kind == "deliverable":
                    drow = conn.execute(
                        "SELECT deliverables_summary FROM deliverable WHERE id = ?",
                        (pid,),
                    ).fetchone()
                    if drow is not None:
                        summary_or_text = drow["deliverables_summary"] or ""
                elif kind == "audit":
                    arow = conn.execute(
                        "SELECT candidate_text FROM audit_record WHERE id = ?",
                        (pid,),
                    ).fetchone()
                    if arow is not None:
                        summary_or_text = arow["candidate_text"] or ""
                parties.append(
                    ConflictParty(
                        party_kind=kind,
                        party_id=pid,
                        party_position=p["party_position"],
                        summary_or_text=summary_or_text,
                    )
                )

            items.append(
                ConflictItem(
                    id=c["id"],
                    kind=c["kind"],
                    status=c["status"],
                    most_onerous_party_id=c["most_onerous_party_id"],
                    most_onerous_reasoning=c["most_onerous_reasoning"],
                    created_at=c["created_at"],
                    resolved_at=c["resolved_at"],
                    parties=parties,
                )
            )
        return items
    finally:
        conn.close()


@_projects_api.get("/projects/{name}/audit", response_model=list[AuditItem])
def projects_list_audit(
    name: str,
    source_id: str | None = Query(None),
) -> list[AuditItem]:
    """Return OUTSIDE rows (rejected candidates), newest first."""
    db_path = _ensure_project(name)
    conn = connect(db_path)
    try:
        if source_id is None:
            rows = conn.execute(
                """
                SELECT
                    ar.id, ar.source_id, ar.source_ref, ar.candidate_text,
                    ar.rejection_reason, ar.landlord_response,
                    ar.user_promoted_to_deliverable_id, ar.created_at,
                    sd.filename AS source_document
                FROM audit_record ar
                LEFT JOIN source_document sd ON sd.id = ar.source_id
                ORDER BY ar.created_at DESC
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT
                    ar.id, ar.source_id, ar.source_ref, ar.candidate_text,
                    ar.rejection_reason, ar.landlord_response,
                    ar.user_promoted_to_deliverable_id, ar.created_at,
                    sd.filename AS source_document
                FROM audit_record ar
                LEFT JOIN source_document sd ON sd.id = ar.source_id
                WHERE ar.source_id = ?
                ORDER BY ar.created_at DESC
                """,
                (source_id,),
            ).fetchall()
        return [
            AuditItem(
                id=r["id"],
                source_id=r["source_id"],
                source_document=r["source_document"] or "",
                source_ref=_parse_json(r["source_ref"], {}),
                candidate_text=r["candidate_text"],
                rejection_reason=r["rejection_reason"],
                landlord_response=r["landlord_response"],
                user_promoted_to_deliverable_id=r["user_promoted_to_deliverable_id"],
                created_at=r["created_at"],
            )
            for r in rows
        ]
    finally:
        conn.close()


@_projects_api.get("/projects/{name}/jobs/{job_id}", response_model=JobStatusResponse)
def projects_job_status(name: str, job_id: str) -> JobStatusResponse:
    """Return job state plus per-source progress for an extraction job.

    `cost_actual_cents` is computed at call time from
    `SUM(llm_call.cost_cents) WHERE job_id = ?`.
    """
    db_path = _ensure_project(name)
    conn = connect(db_path)
    try:
        job = conn.execute(
            """
            SELECT id, started_at, finished_at, status, provider, model,
                   prompt_text_spec_version, prompt_bod_version,
                   cost_estimate_cents
            FROM extraction_job
            WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
        if job is None:
            raise HTTPException(
                status_code=404, detail=f"Job not found: {job_id}"
            )

        cost_row = conn.execute(
            "SELECT SUM(cost_cents) AS total FROM llm_call WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        cost_actual = cost_row["total"] if cost_row is not None else None

        source_rows = conn.execute(
            """
            SELECT
                ejs.source_id, sd.filename, ejs.status, ejs.started_at,
                ejs.finished_at, ejs.error_message,
                ejs.last_completed_chunk_id, ejs.worker_pid
            FROM extraction_job_source ejs
            LEFT JOIN source_document sd ON sd.id = ejs.source_id
            WHERE ejs.job_id = ?
            ORDER BY COALESCE(ejs.started_at, sd.imported_at) ASC
            """,
            (job_id,),
        ).fetchall()

        return JobStatusResponse(
            id=job["id"],
            started_at=job["started_at"],
            finished_at=job["finished_at"],
            status=job["status"],
            provider=job["provider"],
            model=job["model"],
            prompt_text_spec_version=job["prompt_text_spec_version"],
            prompt_bod_version=job["prompt_bod_version"],
            cost_estimate_cents=job["cost_estimate_cents"],
            cost_actual_cents=cost_actual,
            sources=[
                JobSourceItem(
                    source_id=s["source_id"],
                    filename=s["filename"] or "",
                    status=s["status"],
                    started_at=s["started_at"],
                    finished_at=s["finished_at"],
                    error_message=s["error_message"],
                    last_completed_chunk_id=s["last_completed_chunk_id"],
                    worker_pid=s["worker_pid"],
                )
                for s in source_rows
            ],
        )
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Export — legacy POST kept for backwards compat; new GET streams the file
# --------------------------------------------------------------------------


@_projects_api.post(
    "/projects/{name}/export",
)
def projects_export(name: str, output_path: str) -> dict[str, str]:
    """Legacy: write the workbook server-side and return the path.

    Kept for backwards compatibility. New clients should prefer
    GET /projects/{name}/export.xlsx (streams the workbook).
    """
    db_path = _ensure_project(name)
    out = Path(output_path)
    conn = connect(db_path)
    try:
        result = export_to_xlsx(conn, output_path=out)
        return {"path": str(result)}
    finally:
        conn.close()


@_projects_api.get("/projects/{name}/export.xlsx")
def projects_export_stream(name: str) -> FileResponse:
    """Stream the project's master workbook to the client.

    The workbook is materialised to a temporary file (openpyxl writes via a
    path), then served as an attachment. The temp file is removed by the OS
    after the response is sent — FileResponse handles cleanup via the
    background task wired through Starlette when `filename=` is set.
    """
    db_path = _ensure_project(name)
    slug = db_path.stem

    # mkstemp returns a low-level fd we close immediately; openpyxl writes via
    # path (Windows can't reopen a still-open NamedTemporaryFile). Cleanup is
    # handled below via FileResponse's background callback.
    fd, tmp_name = tempfile.mkstemp(suffix=".xlsx", prefix=f"{slug}-master-")
    os.close(fd)
    tmp_path = Path(tmp_name)

    conn = connect(db_path)
    try:
        export_to_xlsx(conn, output_path=tmp_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        conn.close()
        raise
    finally:
        conn.close()

    def _cleanup() -> None:
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)

    return FileResponse(
        path=str(tmp_path),
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        filename=f"{slug}-master.xlsx",
        background=BackgroundTask(_cleanup),
    )


# ────────────────────────────────────────────────────────────────────────────
# Review endpoints — accept/reject/edit deliverables, promote audit, resolve
# questions/conflicts, manage taxonomy proposals + coverage dashboard.
# Consumes the meridian.review.* and meridian.coverage libraries.
# ────────────────────────────────────────────────────────────────────────────


class _AcceptRequest(BaseModel):
    resolved_by_question_id: str | None = None


class _RejectRequest(BaseModel):
    reason: str | None = None


class _EditRequest(BaseModel):
    summary: str | None = None
    trade_value: str | None = None
    service_value: str | None = None
    category_value: str | None = None
    applicable_standards: list[str] | None = None
    flags: list[str] | None = None
    flag_context: dict[str, Any] | None = None


class _PromoteAuditRequest(BaseModel):
    trade_value: str | None = None
    service_value: str | None = None
    category_value: str | None = None
    summary: str | None = None
    flags: list[str] | None = None
    applicable_standards: list[str] | None = None


class _QuestionResolveRequest(BaseModel):
    resolution_payload: dict[str, Any]
    mark_status: Literal["resolved", "dismissed"] = "resolved"


class _QuestionDismissRequest(BaseModel):
    reason: str | None = None


class _ConflictResolveRequest(BaseModel):
    action: Literal["accept_a", "accept_b", "reject_both", "hybrid"]
    accept_party_id: str | None = None
    hybrid_summary: str | None = None
    hybrid_trade_value: str | None = None
    hybrid_service_value: str | None = None
    hybrid_category_value: str | None = None
    hybrid_flags: list[str] | None = None


class _TaxonomyPendingResponse(BaseModel):
    """Pending taxonomy proposal as surfaced to SME review surfaces.

    Mirrors ``meridian.review.taxonomy.TaxonomyProposal`` but pinned here so
    OpenAPI docs describe the shape consumers (CLI, Next.js queue) can rely
    on. The four ``llm_*`` fields are populated by the bootstrap LLM sweep's
    auto-assessment (round 15 / DECISIONS.md §3.10) and stay ``null`` for
    legacy rows that pre-date that pass.
    """

    table: Literal["trade", "service", "category"] = Field(
        description="Which taxonomy axis this proposal belongs to."
    )
    id: str = Field(description="Stable taxonomy row UUID.")
    value: str = Field(description="The proposed value (e.g. 'Chiller System').")
    source: str = Field(
        description=(
            "How the value got proposed: 'user_added', 'llm_proposed', "
            "'llm_proposed_high_confidence' (already auto-applied by the bootstrap "
            "sweep but still surfaced for visibility), etc."
        )
    )
    created_at: str = Field(description="ISO-8601 UTC timestamp.")
    in_use_count: int = Field(
        description="How many deliverables currently reference this row."
    )
    llm_recommended_action: Literal["confirm", "merge_into", "defer_to_user"] | None = Field(
        default=None,
        description=(
            "Bootstrap LLM auto-assessment recommendation. NULL for legacy "
            "rows. The SME still presses a key — this is advisory only."
        ),
    )
    llm_merge_target: str | None = Field(
        default=None,
        description=(
            "If llm_recommended_action='merge_into', the suggested existing "
            "value to fold into. NULL otherwise."
        ),
    )
    llm_confidence: float | None = Field(
        default=None,
        description="Bootstrap LLM confidence in its recommendation, 0.0–1.0. NULL on legacy rows.",
    )
    llm_reasoning: str | None = Field(
        default=None,
        description="Short LLM-generated rationale for the recommendation. NULL on legacy rows.",
    )


class _TaxonomyConfirmRequest(BaseModel):
    table: Literal["trade", "service", "category"]
    value: str


class _TaxonomyMergeRequest(BaseModel):
    table: Literal["trade", "service", "category"]
    source_value: str
    target_value: str


class _TaxonomyRejectRequest(BaseModel):
    table: Literal["trade", "service", "category"]
    value: str


@_projects_api.get("/projects/{name}/coverage")
def projects_coverage(name: str) -> dict[str, Any]:
    """Baseline-trustworthiness dashboard (CONTEXT.md §9 + §14)."""
    from meridian.coverage import project_coverage

    db_path = _ensure_project(name)
    conn = connect(db_path)
    try:
        return project_coverage(conn).model_dump()
    finally:
        conn.close()


@_projects_api.post(
    "/projects/{name}/deliverables/{deliverable_id}/accept",
)
def deliverable_accept(name: str, deliverable_id: str, req: _AcceptRequest) -> dict[str, Any]:
    from meridian.review.deliverables import accept_deliverable

    db_path = _ensure_project(name)
    # Reviewer write — 30 s busy_timeout in case a concurrent extraction holds
    # the SQLite write lock momentarily (Hazard 2 mitigation).
    conn = connect(db_path, busy_timeout_ms=30000)
    try:
        return accept_deliverable(
            conn, deliverable_id, resolved_by_question_id=req.resolved_by_question_id
        ).model_dump()
    finally:
        conn.close()


@_projects_api.post(
    "/projects/{name}/deliverables/{deliverable_id}/reject",
)
def deliverable_reject(name: str, deliverable_id: str, req: _RejectRequest) -> dict[str, Any]:
    from meridian.review.deliverables import reject_deliverable

    db_path = _ensure_project(name)
    conn = connect(db_path, busy_timeout_ms=30000)  # reviewer write (Hazard 2)
    try:
        return reject_deliverable(conn, deliverable_id, reason=req.reason).model_dump()
    finally:
        conn.close()


@_projects_api.post(
    "/projects/{name}/deliverables/{deliverable_id}/edit",
)
def deliverable_edit(name: str, deliverable_id: str, req: _EditRequest) -> dict[str, Any]:
    from meridian.review.deliverables import edit_deliverable

    db_path = _ensure_project(name)
    conn = connect(db_path, busy_timeout_ms=30000)  # reviewer write (Hazard 2)
    try:
        return edit_deliverable(
            conn,
            deliverable_id,
            summary=req.summary,
            trade_value=req.trade_value,
            service_value=req.service_value,
            category_value=req.category_value,
            applicable_standards=req.applicable_standards,
            flags=req.flags,
            flag_context=req.flag_context,
        ).model_dump()
    finally:
        conn.close()


@_projects_api.post(
    "/projects/{name}/audit/{audit_id}/promote",
)
def audit_promote(name: str, audit_id: str, req: _PromoteAuditRequest) -> dict[str, Any]:
    from meridian.review.audit import promote_audit_to_deliverable

    db_path = _ensure_project(name)
    conn = connect(db_path, busy_timeout_ms=30000)  # reviewer write (Hazard 2)
    try:
        return promote_audit_to_deliverable(
            conn,
            audit_id,
            trade_value=req.trade_value,
            service_value=req.service_value,
            category_value=req.category_value,
            summary=req.summary,
            flags=req.flags,
            applicable_standards=req.applicable_standards,
        ).model_dump()
    finally:
        conn.close()


@_projects_api.post(
    "/projects/{name}/questions/{question_id}/resolve",
)
def question_resolve(name: str, question_id: str, req: _QuestionResolveRequest) -> dict[str, Any]:
    from meridian.review.questions import resolve_question

    db_path = _ensure_project(name)
    conn = connect(db_path, busy_timeout_ms=30000)  # reviewer write (Hazard 2)
    try:
        return resolve_question(
            conn,
            question_id,
            resolution_payload=req.resolution_payload,
            mark_status=req.mark_status,
        ).model_dump()
    finally:
        conn.close()


@_projects_api.post(
    "/projects/{name}/questions/{question_id}/dismiss",
)
def question_dismiss(name: str, question_id: str, req: _QuestionDismissRequest) -> dict[str, Any]:
    from meridian.review.questions import dismiss_question

    db_path = _ensure_project(name)
    conn = connect(db_path, busy_timeout_ms=30000)  # reviewer write (Hazard 2)
    try:
        return dismiss_question(conn, question_id, reason=req.reason).model_dump()
    finally:
        conn.close()


@_projects_api.post(
    "/projects/{name}/conflicts/{conflict_id}/resolve",
)
def conflict_resolve(name: str, conflict_id: str, req: _ConflictResolveRequest) -> dict[str, Any]:
    from meridian.review.conflicts import resolve_conflict

    db_path = _ensure_project(name)
    conn = connect(db_path, busy_timeout_ms=30000)  # reviewer write (Hazard 2)
    try:
        return resolve_conflict(
            conn,
            conflict_id,
            action=req.action,
            accept_party_id=req.accept_party_id,
            hybrid_summary=req.hybrid_summary,
            hybrid_trade_value=req.hybrid_trade_value,
            hybrid_service_value=req.hybrid_service_value,
            hybrid_category_value=req.hybrid_category_value,
            hybrid_flags=req.hybrid_flags,
        ).model_dump()
    finally:
        conn.close()


@_projects_api.get(
    "/projects/{name}/taxonomy/pending",
    response_model=list[_TaxonomyPendingResponse],
)
def taxonomy_list_pending(name: str) -> list[dict[str, Any]]:
    """List unresolved taxonomy proposals (CONTEXT.md §5; DECISIONS.md §3.10).

    Each row carries the bootstrap LLM's auto-assessment recommendation so
    SMEs can quick-accept high-signal cases. Legacy rows return ``null`` for
    the four ``llm_*`` fields — never fabricated.
    """
    from meridian.review.taxonomy import list_pending_taxonomy

    db_path = _ensure_project(name)
    conn = connect(db_path)
    try:
        return [t.model_dump() for t in list_pending_taxonomy(conn)]
    finally:
        conn.close()


@_projects_api.post(
    "/projects/{name}/taxonomy/confirm",
)
def taxonomy_confirm(name: str, req: _TaxonomyConfirmRequest) -> dict[str, Any]:
    from meridian.review.taxonomy import confirm_taxonomy

    db_path = _ensure_project(name)
    conn = connect(db_path, busy_timeout_ms=30000)  # reviewer write (Hazard 2)
    try:
        return confirm_taxonomy(conn, table=req.table, value=req.value).model_dump()
    finally:
        conn.close()


@_projects_api.post(
    "/projects/{name}/taxonomy/merge",
)
def taxonomy_merge(name: str, req: _TaxonomyMergeRequest) -> dict[str, Any]:
    from meridian.review.taxonomy import merge_taxonomy

    db_path = _ensure_project(name)
    conn = connect(db_path, busy_timeout_ms=30000)  # reviewer write (Hazard 2)
    try:
        return merge_taxonomy(
            conn, table=req.table, source_value=req.source_value, target_value=req.target_value
        ).model_dump()
    finally:
        conn.close()


@_projects_api.post(
    "/projects/{name}/taxonomy/reject",
)
def taxonomy_reject(name: str, req: _TaxonomyRejectRequest) -> dict[str, Any]:
    from meridian.review.taxonomy import reject_taxonomy

    db_path = _ensure_project(name)
    conn = connect(db_path, busy_timeout_ms=30000)  # reviewer write (Hazard 2)
    try:
        return reject_taxonomy(conn, table=req.table, value=req.value).model_dump()
    finally:
        conn.close()


# ────────────────────────────────────────────────────────────────────────────
# Bootstrap sweep endpoints (CONTEXT.md §23 #4) — propose project-specific
# classes / taxonomies / authority chain into the existing review queues.
# ────────────────────────────────────────────────────────────────────────────


class _BootstrapRequest(BaseModel):
    sample_size: int = 15
    provider: str | None = None
    model: str | None = None


@_projects_api.post(
    "/projects/{name}/bootstrap",
)
def projects_bootstrap_run(name: str, req: _BootstrapRequest) -> dict[str, Any]:
    """Run the per-project bootstrap LLM sweep."""
    from meridian.bootstrap import run_bootstrap_sweep

    db_path = _ensure_project(name)
    # Bootstrap sweep persists taxonomy proposals — long-ish writer.
    conn = connect(db_path, busy_timeout_ms=30000)
    try:
        try:
            result = run_bootstrap_sweep(
                conn,
                sample_size=req.sample_size,
                provider=req.provider,
                model=req.model,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "llm_call_id": result.llm_call_id,
            "storage_key": result.storage_key,
            "new_taxonomy_proposals_persisted": result.new_taxonomy_proposals_persisted,
            "proposal": result.proposal.model_dump(),
        }
    finally:
        conn.close()


@_projects_api.get("/projects/{name}/bootstrap")
def projects_bootstrap_latest(name: str) -> dict[str, Any]:
    """Return the latest bootstrap proposal for the project."""
    from meridian.bootstrap import load_latest_proposal

    db_path = _ensure_project(name)
    conn = connect(db_path)
    try:
        proposal = load_latest_proposal(conn)
        if proposal is None:
            raise HTTPException(
                status_code=404,
                detail="No bootstrap proposal recorded for this project.",
            )
        return proposal.model_dump()
    finally:
        conn.close()


@_projects_api.get("/projects/{name}/bootstrap/list")
def projects_bootstrap_list(name: str) -> list[dict[str, str]]:
    """List past bootstrap proposals (newest first)."""
    from meridian.bootstrap import list_proposals

    db_path = _ensure_project(name)
    conn = connect(db_path)
    try:
        return [
            {"storage_key": k, "generated_at": ts}
            for k, ts in list_proposals(conn)
        ]
    finally:
        conn.close()


# ────────────────────────────────────────────────────────────────────────────
# Round-10 routers — registered as APIRouter sub-apps.
# ────────────────────────────────────────────────────────────────────────────

from meridian.tender.api import tender_router  # noqa: E402

app.include_router(tender_router, prefix="/api")

from meridian.evidence.api import evidence_router  # noqa: E402

app.include_router(evidence_router, prefix="/api")

from meridian.extract.cross_references_api import xref_router  # noqa: E402

app.include_router(xref_router, prefix="/api")

# ── Round 12: API-side auth (POST /auth/login, /logout, /whoami, /status) ──
# §3.2 enforcement (POST-only) is now wired — see the file-top docstring and
# the per-route ``dependencies=[Depends(require_session)]`` annotations above.
from meridian.auth.login_api import auth_router  # noqa: E402

app.include_router(auth_router)

# ── Round 17: GUI setup-wizard endpoints (/setup/*) ──────────────────────
# All endpoints in this router are PUBLIC — the wizard runs pre-auth. See
# the file-top docstring of meridian.wizard.api for the §3.2 deferral note.
from meridian.wizard import wizard_router  # noqa: E402

app.include_router(wizard_router)

# ── Alpha-20: project JSON endpoints under /api ──────────────────────────
# Every /projects/{name}/<verb> route registered above on _projects_api
# now mounts at /api/projects/{name}/<verb>. The bare /projects/<slug>
# URL space is owned exclusively by the SPA (StaticFiles + the SPA
# fallback below), eliminating the alpha-19 collision where a browser
# navigation to /projects/bod-2/quarantine matched the JSON list endpoint
# and the user got raw JSON instead of the React shell.
app.include_router(_projects_api, prefix="/api")


# ── Round 18: StaticFiles mount serving the Next.js GUI export ────────────
#
# Mounted LAST so every API router above takes precedence — Starlette's
# routing is order-sensitive (first-match-wins) and a top-level
# StaticFiles(html=True) at "/" would otherwise swallow API paths if it
# were registered before include_router calls.
#
# When the bundled web directory cannot be found (no env override, no dev
# tree, no in-wheel `_web/`), we log a warning and skip the mount. The
# headless / API-only deployment is still fully functional — only the
# in-browser wizard goes dark, which is the correct behaviour for an
# operator running ``uvicorn meridian.api.main:app`` without ever building
# the GUI.
from starlette.staticfiles import StaticFiles  # noqa: E402

_web_dir = settings.web_dir
if _web_dir is not None:
    # Alpha-18: SPA fallback for /projects/<slug> URLs.
    #
    # The Next.js shell is built with `output: "export"` and
    # `generateStaticParams` returns a single placeholder
    # ``[{ name: "_" }]`` (see apps/web/src/app/projects/[name]/layout.tsx),
    # so only ``/projects/_/index.html`` exists on disk. Inside Tauri
    # this is fine -- the SPA loads at "/" and React's client-side
    # router handles all subsequent navigation, never reloading the
    # page. In a plain browser, hitting ``/projects/bod-2`` directly
    # 404s because there is no ``/projects/bod-2/index.html``.
    #
    # Fix: serve the placeholder ``/projects/_/index.html`` for ANY
    # ``/projects/<slug>`` URL. The SPA's client-side router reads the
    # actual slug from ``window.location`` and renders the right
    # project. The same trick covers the nested review subroutes
    # (``/projects/<slug>/sources``, ``.../master``, ``.../quarantine``)
    # whose static files only exist under ``/projects/_/...``.
    _projects_placeholder_dir = Path(_web_dir) / "projects" / "_"
    _projects_placeholder_html = _projects_placeholder_dir / "index.html"

    if _projects_placeholder_html.exists():
        # SPA-fallback routes register on `app` directly (NOT on
        # `_projects_api`) — they live in the bare `/projects/<slug>/...`
        # URL space, NOT under `/api`. Alpha-20 reviewer caught: the
        # global `@app.get → @_projects_api.get` replace incorrectly
        # caught these too, sending them to `/api/projects/<slug>` AND
        # registering them on the router AFTER it had already been
        # included into the app — so they never reached `app.routes`
        # at all. Both bugs fixed by going back to `@app.get(...)`.
        @app.get("/projects/{slug}", include_in_schema=False)
        @app.get("/projects/{slug}/", include_in_schema=False)
        def _spa_project_root(slug: str):  # noqa: ARG001 — slug is read by the SPA from window.location
            return FileResponse(str(_projects_placeholder_html))

        # Nested routes: /projects/<slug>/<page> e.g. /sources, /master,
        # /quarantine. Each has a corresponding placeholder under
        # /projects/_/<page>/index.html in the static export. Serve the
        # placeholder; SPA router takes over.
        @app.get("/projects/{slug}/{page}", include_in_schema=False)
        @app.get("/projects/{slug}/{page}/", include_in_schema=False)
        def _spa_project_page(slug: str, page: str):  # noqa: ARG001
            nested = _projects_placeholder_dir / page / "index.html"
            if nested.exists():
                return FileResponse(str(nested))
            # Fall back to the project root placeholder if the nested
            # page wasn't pre-rendered (e.g. someone added a new
            # /projects/<name>/<newpage> client-side route without
            # updating generateStaticParams).
            return FileResponse(str(_projects_placeholder_html))

    app.mount(
        "/",
        StaticFiles(directory=str(_web_dir), html=True),
        name="web",
    )
    _log.info(
        "api.staticfiles_mounted",
        path="/",
        directory=str(_web_dir),
    )
else:
    _log.warning(
        "api.staticfiles_skipped",
        message=(
            "No GUI bundle found (no MERIDIAN_WEB_DIR override, no "
            "apps/web/out/ in dev tree, no _web/ in wheel). API-only mode."
        ),
    )


# Allow ``python -m meridian.api.main`` and the alpha-2 PowerShell installer's
# Start-Process launch to bring the backend up without depending on the
# meridian.exe shim being on PATH for the elevated installer process. The
# CLI's ``meridian start`` command takes the same in-process uvicorn path.
if __name__ == "__main__":
    import os as _os

    import uvicorn as _uvicorn

    _port = int(_os.environ.get("MERIDIAN_PORT", "8000"))
    _host = _os.environ.get("MERIDIAN_HOST", "127.0.0.1")
    _uvicorn.run(app, host=_host, port=_port, log_level="info")

