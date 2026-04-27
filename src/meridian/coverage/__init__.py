"""Coverage / provenance metrics for a Meridian project.

Pure read-only computation of "is the analytics baseline trustworthy?"
metrics used by the CLI, the API, and the future review dashboard.

See `dashboard.py` for the public API.
"""

from __future__ import annotations

from meridian.coverage.dashboard import (
    AuditCoverage,
    ConflictCoverage,
    CostCoverage,
    ProjectCoverage,
    ProvenanceCoverage,
    QuestionCoverage,
    StatusBreakdown,
    TaxonomyCoverage,
    project_coverage,
    render_coverage_text,
)

__all__ = [
    "AuditCoverage",
    "ConflictCoverage",
    "CostCoverage",
    "ProjectCoverage",
    "ProvenanceCoverage",
    "QuestionCoverage",
    "StatusBreakdown",
    "TaxonomyCoverage",
    "project_coverage",
    "render_coverage_text",
]
