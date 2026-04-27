"""Review / categorisation library for Meridian.

Pure library functions for the HITL review queue (CONTEXT.md §9):
deliverables (accept / edit / reject), audit promotion, batched questions,
conflict resolution, and taxonomy governance (CONTEXT.md §5).

No CLI, no API surface, no LLM calls. The CLI / API layer is wired
separately on top of these functions.
"""

from __future__ import annotations

from meridian.review.audit import (
    AuditItem,
    AuditPromotionResult,
    list_audit,
    promote_audit_to_deliverable,
)
from meridian.review.conflicts import (
    ConflictItem,
    ConflictParty,
    ConflictResolutionResult,
    list_conflicts,
    resolve_conflict,
)
from meridian.review.deliverables import (
    DeliverableDetail,
    DeliverableMutationResult,
    QuarantinedItem,
    accept_deliverable,
    edit_deliverable,
    get_deliverable,
    list_quarantined,
    reject_deliverable,
)
from meridian.review.questions import (
    QuestionItem,
    QuestionResolutionResult,
    dismiss_question,
    list_questions,
    resolve_question,
)
from meridian.review.taxonomy import (
    TaxonomyMutationResult,
    TaxonomyProposal,
    confirm_taxonomy,
    list_pending_taxonomy,
    merge_taxonomy,
    reject_taxonomy,
)

__all__ = [
    # deliverables
    "DeliverableDetail",
    "DeliverableMutationResult",
    "QuarantinedItem",
    "accept_deliverable",
    "edit_deliverable",
    "get_deliverable",
    "list_quarantined",
    "reject_deliverable",
    # audit
    "AuditItem",
    "AuditPromotionResult",
    "list_audit",
    "promote_audit_to_deliverable",
    # questions
    "QuestionItem",
    "QuestionResolutionResult",
    "dismiss_question",
    "list_questions",
    "resolve_question",
    # conflicts
    "ConflictItem",
    "ConflictParty",
    "ConflictResolutionResult",
    "list_conflicts",
    "resolve_conflict",
    # taxonomy
    "TaxonomyMutationResult",
    "TaxonomyProposal",
    "confirm_taxonomy",
    "list_pending_taxonomy",
    "merge_taxonomy",
    "reject_taxonomy",
]
