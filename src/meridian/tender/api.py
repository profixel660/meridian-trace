"""Tender Package Builder — FastAPI router.

Two endpoints, mirroring the conventions used by ``meridian.api.main``:

* ``GET /projects/{name}/tender/trades`` — discoverability for the UI.
* ``POST /projects/{name}/tender/build`` — write a per-trade package and
  either return its server-side path (default) or stream the file.

The router is exported as ``tender_router`` and is wired into the main
FastAPI app by the parent module.

Auth — round-12 §3.2: POST routes here require ``Authorization: Bearer
<token>`` (see ``meridian.auth.fastapi_dep.require_session``). GET routes
are open so local-dev / observability can list trades without a session.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from meridian.auth.fastapi_dep import require_session
from meridian.logging import get_logger
from meridian.tender.builder import (
    SUPPORTED_FORMATS,
    TenderBuildError,
    build_tender_package,
    list_trades_with_deliverables,
    tenders_dir_for,
)

_log = get_logger("meridian.tender.api")

tender_router = APIRouter(
    prefix="/projects/{name}/tender",
    tags=["tender"],
)


# --------------------------------------------------------------------------
# Response / request models
# --------------------------------------------------------------------------


class TradeCount(BaseModel):
    """One row of the trade-discoverability list."""

    trade: str = Field(
        ...,
        description=(
            "Trade name as stored in trade_taxonomy. "
            "May be '(unassigned)' if accepted deliverables exist that "
            "have not yet been mapped to a trade — those should be "
            "reviewed before issuing any tender package."
        ),
    )
    deliverable_count: int = Field(
        ...,
        ge=0,
        description="Number of accepted deliverables tagged to this trade.",
    )


class TradeListResponse(BaseModel):
    """Response for ``GET /projects/{name}/tender/trades``."""

    project: str = Field(..., description="Project name (echoed from path).")
    trades: list[TradeCount] = Field(
        default_factory=list,
        description=(
            "Trades with at least one accepted deliverable. "
            "Empty list = nothing to package yet (run extraction + review)."
        ),
    )


class TenderBuildRequest(BaseModel):
    """Body for ``POST /projects/{name}/tender/build``."""

    trade: str = Field(
        ...,
        min_length=1,
        description=(
            "Trade name (case-insensitive, matched against trade_taxonomy.value)."
        ),
    )
    format: Literal["xlsx", "md"] = Field(
        "xlsx",
        description=f"Output format. One of: {', '.join(SUPPORTED_FORMATS)}.",
    )
    output_dir: str | None = Field(
        None,
        description=(
            "Optional server-side directory to write into. "
            "Defaults to <projects_dir>/<slug>.tenders/."
        ),
    )
    stream: bool = Field(
        False,
        description=(
            "When true, the file is written to a temp path and streamed "
            "back as the response body (useful for browser downloads). "
            "When false (default), only the JSON metadata is returned and "
            "the file is left on the server under output_dir."
        ),
    )


class TenderBuildResponse(BaseModel):
    """Response for the non-streaming build path."""

    project: str = Field(..., description="Project name (echoed from path).")
    trade: str = Field(..., description="Trade the package was built for.")
    format: str = Field(..., description="Output format used.")
    output_path: str = Field(
        ..., description="Absolute path to the written file on the server."
    )
    output_dir: str = Field(
        ..., description="Directory the file was written into."
    )


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------


@tender_router.get(
    "/trades",
    response_model=TradeListResponse,
    summary="List trades available for tender packaging",
    description=(
        "Returns every trade that has at least one accepted deliverable "
        "in this project's master register. Use the counts to pick a "
        "trade for `POST /tender/build`. An entry called '(unassigned)' "
        "indicates accepted deliverables that have not been mapped to a "
        "trade — review them in the master register first."
    ),
)
def get_tender_trades(name: str) -> TradeListResponse:
    try:
        trades = list_trades_with_deliverables(name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return TradeListResponse(
        project=name,
        trades=[TradeCount(trade=t, deliverable_count=c) for t, c in trades],
    )


@tender_router.post(
    "/build",
    response_model=TenderBuildResponse,
    dependencies=[Depends(require_session)],
    responses={
        200: {
            "description": (
                "Either the JSON metadata (default) or a streamed file body "
                "(when `stream=true` in the request). Browser clients should "
                "set `stream=true` to trigger a download."
            )
        },
        400: {"description": "Unsupported format or unknown trade."},
        404: {"description": "Project not found."},
    },
    summary="Build a per-trade tender package",
    description=(
        "Writes a tender file (xlsx or md) containing a cover sheet "
        "(project, trade, timestamp, source-doc list, standards summary, "
        "flag summary, items needing review) plus deliverables grouped by "
        "service then category. Only accepted deliverables are included — "
        "quarantined and rejected rows are excluded."
    ),
)
def post_tender_build(name: str, payload: TenderBuildRequest):
    """Build the package and return JSON metadata, or stream the file."""
    output_dir = Path(payload.output_dir) if payload.output_dir else None

    try:
        path = build_tender_package(
            name,
            payload.trade,
            output_dir=output_dir,
            fmt=payload.format,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TenderBuildError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not payload.stream:
        return TenderBuildResponse(
            project=name,
            trade=payload.trade,
            format=payload.format,
            output_path=str(path),
            output_dir=str(path.parent),
        )

    # Streaming path: re-materialise into a temp file so we can hand it
    # to FileResponse with cleanup. (We could stream the on-disk artefact
    # we just wrote, but doing so would either delete the operator's
    # persistent copy or leak it; the temp-copy strategy is what the
    # existing /export.xlsx endpoint uses and we mirror it here.)
    media_type = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if payload.format == "xlsx"
        else "text/markdown; charset=utf-8"
    )
    suffix = f".{payload.format}"
    fd, tmp_name = tempfile.mkstemp(suffix=suffix, prefix=f"tender-{name}-")
    os.close(fd)
    tmp_path = Path(tmp_name)
    tmp_path.write_bytes(path.read_bytes())

    def _cleanup() -> None:
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)

    return FileResponse(
        path=str(tmp_path),
        media_type=media_type,
        filename=path.name,
        background=BackgroundTask(_cleanup),
    )


@tender_router.get(
    "/output-dir",
    response_model=dict,
    summary="Show the default output directory for tender packages",
    description=(
        "Discoverability helper: returns the default "
        "<projects_dir>/<slug>.tenders/ path so a UI can show users where "
        "their tender files will land before they kick off a build."
    ),
)
def get_tender_output_dir(name: str) -> dict[str, str]:
    return {"project": name, "default_output_dir": str(tenders_dir_for(name))}


__all__ = ["tender_router"]
