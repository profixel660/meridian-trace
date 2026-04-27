"""FastAPI router for Legal Evidence Pack endpoints.

Wired into the root app by ``meridian.api.main`` via ``app.include_router(evidence_router)``.

Auth — round-12 §3.2: POST routes here require ``Authorization: Bearer
<token>`` (see ``meridian.auth.fastapi_dep.require_session``). GET routes
are open so dashboards can list previously-generated packs without a session.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from meridian.auth.fastapi_dep import require_session
from meridian.config import settings
from meridian.evidence.builder import build_evidence_pack
from meridian.logging import get_logger
from meridian.projects import _slugify, project_db_path

_log = get_logger("meridian.evidence.api")

evidence_router = APIRouter(tags=["evidence"])


class _BuildEvidenceRequest(BaseModel):
    deliverable_ids: list[str] | None = Field(
        default=None,
        description=(
            "Optional list of deliverable IDs to scope the pack to. "
            "When omitted or null, the entire project is packed."
        ),
    )


class _BuildEvidenceResponse(BaseModel):
    pack_path: str
    deliverable_count: int
    llm_call_count: int
    source_count: int
    incomplete_provenance_ids: list[str]
    schema_notes: list[str]


class _PackListItem(BaseModel):
    filename: str
    path: str
    size_bytes: int
    modified_at: str


def _ensure_project(name: str) -> Path:
    db_path = project_db_path(name)
    if not db_path.exists():
        raise HTTPException(status_code=404, detail=f"Project not found: {name}")
    return db_path


@evidence_router.post(
    "/projects/{name}/evidence/build",
    response_model=_BuildEvidenceResponse,
    dependencies=[Depends(require_session)],
    summary="Build a Legal Evidence Pack zip for the project.",
)
def build_evidence(
    name: str,
    body: _BuildEvidenceRequest | None = None,
) -> _BuildEvidenceResponse:
    """Generate a defensible audit-trail bundle.

    Body is optional. Pass ``{"deliverable_ids": [...]}`` to scope the pack to
    specific rows + their full provenance trail; omit to pack the whole project.
    """
    _ensure_project(name)
    deliverable_ids = body.deliverable_ids if body is not None else None
    try:
        result = build_evidence_pack(
            name, output_path=None, deliverable_ids=deliverable_ids
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _BuildEvidenceResponse(
        pack_path=str(result.pack_path),
        deliverable_count=result.deliverable_count,
        llm_call_count=result.llm_call_count,
        source_count=result.source_count,
        incomplete_provenance_ids=result.incomplete_provenance,
        schema_notes=result.schema_notes,
    )


@evidence_router.get(
    "/projects/{name}/evidence/list",
    response_model=list[_PackListItem],
    summary="List previously-generated evidence packs for the project.",
)
def list_evidence(name: str) -> list[_PackListItem]:
    """List ``*.zip`` files under ``<projects_dir>/<slug>.evidence/``, newest first."""
    _ensure_project(name)
    slug = _slugify(name)
    pack_dir = settings.projects_dir / f"{slug}.evidence"
    if not pack_dir.exists():
        return []

    items: list[_PackListItem] = []
    for p in pack_dir.glob("*.zip"):
        try:
            stat = p.stat()
        except OSError:
            continue
        items.append(
            _PackListItem(
                filename=p.name,
                path=str(p),
                size_bytes=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, UTC).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
            )
        )
    items.sort(key=lambda x: x.modified_at, reverse=True)
    return items


__all__: list[Any] = ["evidence_router"]
