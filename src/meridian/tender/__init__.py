"""Tender Package Builder — per-trade subcontractor packages.

Takes the accepted-deliverables register for a project and produces a
trade-specific tender package: the document a head contractor would issue
to a subcontractor saying "here is everything we expect you to deliver,
sourced from these spec sections".

This module is read-only on the project SQLite — it never modifies the
deliverables register. It is purely an export surface, parallel to
``meridian.export.excel``.

Public API
----------
- :func:`build_tender_package` — write a per-trade tender file (xlsx or md)
  under ``<projects_dir>/<slug>.tenders/`` (or a caller-specified dir).
- :func:`list_trades_with_deliverables` — enumerate trades that have at
  least one accepted deliverable in the register, with counts.

CLI:  ``meridian.tender.cli.tender_app``  (Typer sub-app)
API:  ``meridian.tender.api.tender_router``  (FastAPI APIRouter)
"""

from __future__ import annotations

from meridian.tender.builder import (
    DEFAULT_TENDERS_SUBDIR,
    SUPPORTED_FORMATS,
    TenderBuildError,
    TenderBuildResult,
    build_tender_package,
    list_trades_with_deliverables,
    tenders_dir_for,
)

__all__ = [
    "DEFAULT_TENDERS_SUBDIR",
    "SUPPORTED_FORMATS",
    "TenderBuildError",
    "TenderBuildResult",
    "build_tender_package",
    "list_trades_with_deliverables",
    "tenders_dir_for",
]
