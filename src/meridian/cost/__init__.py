"""LLM cost preview + summary (CONTEXT.md §13).

Public API:
    estimate_project_cost(conn, source_ids, project_routing) -> CostPreview
    summarise_project_cost(conn) -> CostSummary
    lookup_rate(provider, model) -> tuple[int, int] | None
"""

from meridian.cost.preview import (
    CostLeg,
    CostPreview,
    estimate_project_cost,
)
from meridian.cost.rates import lookup_rate
from meridian.cost.summary import (
    CostSummary,
    JobRow,
    ProviderModelRow,
    PurposeRow,
    summarise_project_cost,
)

__all__ = [
    "CostLeg",
    "CostPreview",
    "CostSummary",
    "JobRow",
    "ProviderModelRow",
    "PurposeRow",
    "estimate_project_cost",
    "lookup_rate",
    "summarise_project_cost",
]
