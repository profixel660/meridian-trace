"""Local structured logging for Meridian (CONTEXT.md §19).

Always-on, JSON-lines per project, rotating at 10 MB / keep last 5. Rich
console renderer for interactive use. Wired at every entry point: CLI,
FastAPI, ingest, quality scan, triage, extraction, conflict pass, export,
review actions.
"""

from __future__ import annotations

from meridian.logging.setup import (
    bind_project_context,
    configure_logging,
    get_logger,
)

__all__ = [
    "bind_project_context",
    "configure_logging",
    "get_logger",
]
