"""Pydantic models that mirror the SQLite schema for in-memory work.

These are not full ORMs — just typed records moved between layers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# ─── Source-ref structured object (per CONTEXT.md §7) ────────────────────────


class SourceRefPdfText(BaseModel):
    kind: Literal["pdf_text"] = "pdf_text"
    filename: str
    page: int
    section: str | None = None
    rendered: str


class SourceRefXlsxRow(BaseModel):
    kind: Literal["xlsx_row"] = "xlsx_row"
    filename: str
    sheet: str
    row: int
    req_id: str | None = None
    rendered: str


class SourceRefPdfDrawing(BaseModel):
    kind: Literal["pdf_drawing"] = "pdf_drawing"
    filename: str
    page: int
    sheet_label: str | None = None
    bbox: list[float] | None = None
    annotation_id: str | None = None
    rendered: str


class SourceRefDwg(BaseModel):
    kind: Literal["dwg"] = "dwg"
    filename: str
    layer: str | None = None
    view: str | None = None
    extents: list[float] | None = None
    rendered: str


class SourceRefEmail(BaseModel):
    kind: Literal["email"] = "email"
    filename: str
    message_index: int
    paragraph: int | None = None
    rendered: str


SourceRef = (
    SourceRefPdfText | SourceRefXlsxRow | SourceRefPdfDrawing | SourceRefDwg | SourceRefEmail
)


# ─── Records ────────────────────────────────────────────────────────────────


class Project(BaseModel):
    id: str
    name: str
    created_at: datetime
    schema_version: int
    app_version: str
    tool_disclaimer_version: str
    default_provider: Literal["anthropic", "openai"]
    default_model: str
    auto_approval_threshold: str = "high_no_flags"
    notes: str | None = None


class TaxonomyEntry(BaseModel):
    id: str
    value: str
    source: Literal["default", "user_added", "user_merged"]
    confirmed_at: datetime | None = None
    is_active: bool = True
    created_at: datetime
    notes: str | None = None
    synonyms: list[str] = Field(default_factory=list)


class TradeEntry(TaxonomyEntry):
    category_hint: Literal["specialist", "cross_cutting", "vendor"]


DocumentClass = Literal[
    "customer_requirements",
    "global_tr",
    "global_ose_spec",
    "project_amendment",
    "project_clarification",
    "drawing",
    "demarcation_schedule",
    "methodology",
    "template",
    "unknown",
]

DocumentState = Literal["concept", "30%", "50%", "90%", "100%", "IFC", "as-built"]

ExtractionPath = Literal[
    "text_spec", "bod_import", "drawing", "demarcation", "excluded", "pending"
]


class SourceDocument(BaseModel):
    id: str
    filename: str
    relative_path: str
    content_hash: str
    mime_type: str
    size_bytes: int
    imported_at: datetime
    document_class: DocumentClass | None = None
    document_state: DocumentState | None = None
    revision: str | None = None
    revision_detected_via: (
        Literal["filename_pattern", "embedded_metadata", "content_scan", "user_pinned"] | None
    ) = None
    is_authoritative_revision: bool = True
    superseded_by_id: str | None = None
    is_template: bool = False
    is_demarcation_schedule: bool = False
    quality_scan_id: str | None = None
    extraction_path: ExtractionPath = "pending"
    notes: str | None = None


class DeliverableRow(BaseModel):
    """The shape an extraction prompt returns per kept row, plus persistence metadata."""

    id: str
    extraction_group_id: str
    source_id: str
    chunk_id: str | None = None
    extraction_job_id: str
    llm_call_id: str
    source_ref: dict[str, Any]
    trade_id: str | None = None
    service_id: str | None = None
    category_id: str | None = None
    applicable_standards: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"]
    flags: list[str] = Field(default_factory=list)
    flag_context: dict[str, Any] = Field(default_factory=dict)
    deliverables_summary: str
    gate_outcome: Literal["inside", "borderline"]
    status: Literal[
        "auto_approved",
        "quarantined",
        "user_accepted",
        "user_edited",
        "user_rejected",
        "user_promoted",
    ]
    auto_route_decision: Literal["auto_approved", "quarantined"]
    created_at: datetime
    parent_deliverable_id: str | None = None


class AuditRecord(BaseModel):
    id: str
    source_id: str
    chunk_id: str | None = None
    extraction_job_id: str
    llm_call_id: str
    source_ref: dict[str, Any]
    candidate_text: str
    rejection_reason: str
    landlord_response: str | None = None
    created_at: datetime


class HitlQuestion(BaseModel):
    id: str
    extraction_job_id: str
    llm_call_id: str
    kind: Literal[
        "service_mapping",
        "taxonomy_new_value",
        "disposition_unclear",
        "borderline_decision",
        "conflict_resolution",
        "other",
    ]
    context: str
    question_text: str
    candidate_source_refs: list[dict[str, Any]] = Field(default_factory=list)
    proposed_resolution: str | None = None
    status: Literal["pending", "resolved", "dismissed"] = "pending"
    created_at: datetime


__all__ = [
    "AuditRecord",
    "DeliverableRow",
    "DocumentClass",
    "DocumentState",
    "ExtractionPath",
    "HitlQuestion",
    "Project",
    "SourceDocument",
    "SourceRef",
    "SourceRefDwg",
    "SourceRefEmail",
    "SourceRefPdfDrawing",
    "SourceRefPdfText",
    "SourceRefXlsxRow",
    "TaxonomyEntry",
    "TradeEntry",
]
