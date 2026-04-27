"""Per-project bootstrap LLM sweep — proposes classes / taxonomies / authority chain.

CONTEXT.md §23 #4. Public API:

  run_bootstrap_sweep(conn, *, sample_size=15, provider=None, model=None)
      → SweepResult (proposal + llm_call_id + storage_key + persisted count)

  BootstrapProposal, ClassObservation, TaxonomyExtensionProposal,
  ServiceMappingProposal, AuthorityObservation, CorpusQualityObservation
      Pydantic models for the proposal data.

  persist_proposal(conn, *, proposal, llm_call_id) -> str
  load_latest_proposal(conn) -> BootstrapProposal | None
  list_proposals(conn) -> list[tuple[str, str]]
      Persistence helpers; proposals land in the existing taxonomy review flow
      (source='user_added', confirmed_at=NULL) plus a JSON audit blob in
      app_setting under 'bootstrap_proposal_<timestamp>'.
"""

from __future__ import annotations

from meridian.bootstrap.proposals import (
    AuthorityObservation,
    BootstrapProposal,
    ClassObservation,
    CorpusQualityObservation,
    ServiceMappingProposal,
    TaxonomyExtensionProposal,
    list_proposals,
    load_latest_proposal,
    persist_proposal,
)
from meridian.bootstrap.sweep import (
    SweepResult,
    render_proposal_summary,
    run_bootstrap_sweep,
)

__all__ = [
    "AuthorityObservation",
    "BootstrapProposal",
    "ClassObservation",
    "CorpusQualityObservation",
    "ServiceMappingProposal",
    "SweepResult",
    "TaxonomyExtensionProposal",
    "list_proposals",
    "load_latest_proposal",
    "persist_proposal",
    "render_proposal_summary",
    "run_bootstrap_sweep",
]
