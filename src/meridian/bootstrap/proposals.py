"""Pydantic dataclasses + persistence helpers for the per-project bootstrap LLM sweep.

CONTEXT.md §23 #4: a first-pass LLM sweep over a representative sample of a new
project's corpus that PROPOSES the project-specific document classes,
taxonomies (trade / service / category extensions), and authority chain — then
surfaces the proposals to the user for confirmation in the existing review
queues.

This module owns the proposal data shapes and the persistence story:

  - Each proposed *trade / service / category* extension lands in the
    corresponding `*_taxonomy` table. Where it lands depends on the LLM's
    auto-assessment (added in v6, docs/DECISIONS.md §3.10):

      * ``recommended_action == "merge_into"`` AND
        ``confidence >= _PERSIST_AUTO_APPLY_CONFIDENCE``
        → auto-applied as a synonym of the merge target with
          ``source='llm_auto_merged'``. NOT added as a fresh user-review row.
      * ``recommended_action == "confirm"`` AND
        ``confidence >= _PERSIST_AUTO_APPLY_CONFIDENCE``
        → added as a fresh proposal with ``source='llm_proposed_high_confidence'``;
          the SME's review walk shows the LLM's recommendation up front.
      * Anything else (lower confidence, ``defer_to_user``, malformed
        assessment) → standard pending-review row (``source='user_added'``)
        enriched with the LLM's recommendation columns so Stream B can
        render the SME walk.

  - Service-mapping proposals are NOT auto-written to ``service_mapping`` in v1
    (CONTEXT.md §5: mapping rows always require explicit confirmation). They
    live inside the audit blob for the reviewer to act on.
  - The full proposal — including service-mapping suggestions, authority-chain
    observations, corpus quality, recommendations — is stored as a JSON blob
    in ``app_setting`` under ``bootstrap_proposal_<timestamp>`` for audit /
    review UI consumption.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from meridian.db.connection import transaction
from meridian.logging import get_logger

# Storage-key prefix for the audit blob in `app_setting`.
_PROPOSAL_KEY_PREFIX = "bootstrap_proposal_"

# Auto-apply confidence threshold for the LLM's bootstrap-sweep recommendation.
#
# Conservative default — favour user review over silent auto-application. A
# proposal must clear this bar (>=) before we either auto-merge it into an
# existing taxonomy value or pre-flag it as a high-confidence confirm in the
# review queue. Anything below routes to the standard pending-review flow.
#
# 0.85 was picked because the bootstrap LLM has only seen a sample of the
# corpus (per CONTEXT.md §23 #4 the default sample_size is 15) and the cost
# of a wrong auto-merge is the SME having to un-merge later. The cost of a
# false negative (a clean merge sent for review) is one extra click — which
# is correctly cheaper. Subclass / monkeypatch this constant if a project's
# review burden warrants a different trade-off.
_PERSIST_AUTO_APPLY_CONFIDENCE = 0.85

_log = get_logger("meridian.bootstrap")


# ─── Models ─────────────────────────────────────────────────────────────────


class ClassObservation(BaseModel):
    """One observed document_class within the sample."""

    document_class: str
    count: int
    confidence: Literal["high", "medium", "low"]
    sample_filenames: list[str] = Field(default_factory=list)


class TaxonomyExtensionProposal(BaseModel):
    """A proposed new taxonomy value the v1 locked taxonomy does not cover.

    The ``recommended_action`` / ``merge_target`` / ``confidence`` /
    ``assessment_reasoning`` fields carry the LLM's bootstrap-sweep
    auto-assessment (docs/DECISIONS.md §3.10). They are required at
    construction time; ``run_bootstrap_sweep`` downgrades malformed LLM
    output to ``defer_to_user`` rather than crashing.
    """

    table: Literal["trade", "service", "category"]
    value: str
    reasoning: str
    sample_source_filenames: list[str] = Field(default_factory=list)

    recommended_action: Literal["confirm", "merge_into", "defer_to_user"] = Field(
        ...,
        description=(
            "LLM's verdict on whether this proposed taxonomy value deserves a "
            "standalone entry ('confirm'), should fold into an existing seeded "
            "value ('merge_into'), or is genuinely ambiguous ('defer_to_user')."
        ),
    )
    merge_target: str | None = Field(
        default=None,
        description=(
            "When recommended_action == 'merge_into', the existing seeded "
            "taxonomy value this proposal should be merged into (added as a "
            "synonym). Validated against the seeded vocabulary by the runtime; "
            "the model itself only enforces the structural pairing."
        ),
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="0.0–1.0 — the LLM's self-reported certainty in its recommended_action.",
    )
    assessment_reasoning: str = Field(
        ...,
        description=(
            "Short LLM-generated explanation surfaced in the SME review walk "
            "(e.g. 'Corpus contains 50+ paragraphs of chiller-specific spec; "
            "merging into HVAC would lose detail.')."
        ),
    )

    @model_validator(mode="after")
    def _validate_merge_target_pairing(self) -> TaxonomyExtensionProposal:
        if self.recommended_action == "merge_into":
            if not self.merge_target or not self.merge_target.strip():
                raise ValueError(
                    "merge_target is required when recommended_action == 'merge_into'"
                )
        elif self.merge_target is not None:
            # Don't accept stray merge_target values for non-merge actions —
            # silently drop rather than crash so the model is forgiving on
            # the client side, but the runtime parses raw LLM JSON via
            # ``run_bootstrap_sweep`` which already coerces.
            self.merge_target = None
        return self


class ServiceMappingProposal(BaseModel):
    """A BOD discipline-section text suggested for service mapping."""

    disc_section_text: str
    proposed_service: str | None = None  # None = informational; do not auto-map
    reasoning: str = ""


class AuthorityObservation(BaseModel):
    """Authority-chain role inferred for one sampled source."""

    source_id: str
    filename: str
    role: Literal[
        "customer_requirements",
        "global_tr",
        "global_ose_spec",
        "demarcation_schedule",
        "project_amendment",
        "project_clarification",
        "drawing",
        "methodology",
        "template",
        "unknown",
    ]
    confidence: Literal["high", "medium", "low"]
    reasoning: str = ""


class CorpusQualityObservation(BaseModel):
    """Aggregate scan-quality summary across the sample."""

    total_sampled: int
    scan_quality_breakdown: dict[str, int] = Field(default_factory=dict)
    ocr_needed_count: int = 0
    template_count: int = 0
    unreadable_count: int = 0


class BootstrapProposal(BaseModel):
    """Full LLM-emitted proposal for one bootstrap sweep run."""

    project_name: str
    generated_at: str
    sources_sampled: list[str] = Field(default_factory=list)
    sample_size: int
    document_class_observations: list[ClassObservation] = Field(default_factory=list)
    proposed_trade_extensions: list[TaxonomyExtensionProposal] = Field(default_factory=list)
    proposed_service_extensions: list[TaxonomyExtensionProposal] = Field(default_factory=list)
    proposed_category_extensions: list[TaxonomyExtensionProposal] = Field(default_factory=list)
    proposed_service_mappings: list[ServiceMappingProposal] = Field(default_factory=list)
    authority_chain_observations: list[AuthorityObservation] = Field(default_factory=list)
    corpus_quality: CorpusQualityObservation
    recommendations: list[str] = Field(default_factory=list)


# ─── Helpers ────────────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    return str(uuid.uuid4())


def _table_name(table: str) -> str:
    if table == "trade":
        return "trade_taxonomy"
    if table == "service":
        return "service_taxonomy"
    if table == "category":
        return "category_taxonomy"
    raise ValueError(f"unknown taxonomy table: {table!r}")


def _value_exists(conn: sqlite3.Connection, *, table: str, value: str) -> bool:
    """Case-sensitive existence check — ``trade_taxonomy.value`` is UNIQUE per schema."""
    row = conn.execute(
        f"SELECT 1 FROM {_table_name(table)} WHERE value = ?", (value,)
    ).fetchone()
    return row is not None


def _seeded_values(conn: sqlite3.Connection, *, table: str) -> set[str]:
    """All currently-known taxonomy values for ``table``.

    Includes both ``source='default'`` rows and any user-added rows that
    have already been confirmed — anything the LLM is allowed to point a
    ``merge_into`` target at.
    """
    rows = conn.execute(f"SELECT value FROM {_table_name(table)}").fetchall()
    return {r["value"] for r in rows}


def _add_synonym(
    conn: sqlite3.Connection, *, table: str, target_value: str, synonym: str
) -> bool:
    """Append ``synonym`` to ``target_value``'s ``synonyms`` JSON list.

    Idempotent — does not append if the synonym is already present. Returns
    True if a row was modified.
    """
    row = conn.execute(
        f"SELECT id, synonyms FROM {_table_name(table)} WHERE value = ?",
        (target_value,),
    ).fetchone()
    if row is None:
        return False
    raw = row["synonyms"]
    syns: list[str] = []
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                syns = [str(s) for s in parsed if isinstance(s, str)]
        except (json.JSONDecodeError, ValueError):
            syns = []
    if synonym in syns:
        return False
    syns.append(synonym)
    conn.execute(
        f"UPDATE {_table_name(table)} SET synonyms = ? WHERE id = ?",
        (json.dumps(syns), row["id"]),
    )
    return True


def _insert_taxonomy_proposal(
    conn: sqlite3.Connection,
    *,
    table: str,
    ext: TaxonomyExtensionProposal,
    llm_call_id: str,
    source: str,
) -> bool:
    """Insert one taxonomy proposal row with the LLM-assessment columns populated.

    Returns True if a new row was created. Idempotent: if ``ext.value`` is
    already present (default seed or prior bootstrap run), returns False.

    ``source`` is one of:
      * ``'user_added'`` — standard pending review.
      * ``'llm_proposed_high_confidence'`` — LLM said confirm with conf >= threshold;
        the review walk uses this to render an "LLM recommends confirm" pre-fill.
    """
    if _value_exists(conn, table=table, value=ext.value):
        return False

    now = _now()
    note_bits = [
        f"bootstrap_sweep llm_call_id={llm_call_id}",
        f"action={ext.recommended_action}",
        f"confidence={ext.confidence:.2f}",
    ]
    if ext.merge_target:
        note_bits.append(f"merge_target={ext.merge_target}")
    note_bits.append(ext.reasoning)
    note = ": ".join([" ".join(note_bits[:1]), " ".join(note_bits[1:])]).strip()

    if table == "trade":
        # trade_taxonomy requires a category_hint. Bootstrap-proposed trades
        # default to 'specialist'; the reviewer can edit before confirming.
        conn.execute(
            """
            INSERT INTO trade_taxonomy
              (id, value, category_hint, source, created_at, notes,
               llm_recommended_action, llm_merge_target, llm_confidence, llm_reasoning)
            VALUES (?, ?, 'specialist', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _new_id(),
                ext.value,
                source,
                now,
                note,
                ext.recommended_action,
                ext.merge_target,
                ext.confidence,
                ext.assessment_reasoning,
            ),
        )
    else:
        conn.execute(
            f"""
            INSERT INTO {_table_name(table)}
              (id, value, source, created_at, notes,
               llm_recommended_action, llm_merge_target, llm_confidence, llm_reasoning)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _new_id(),
                ext.value,
                source,
                now,
                note,
                ext.recommended_action,
                ext.merge_target,
                ext.confidence,
                ext.assessment_reasoning,
            ),
        )
    return True


def _apply_extension(
    conn: sqlite3.Connection,
    *,
    table: str,
    ext: TaxonomyExtensionProposal,
    llm_call_id: str,
) -> str:
    """Route one proposal per the persist policy. Returns a short routing tag.

    Tags:
      * 'auto_merged'        — high-confidence merge, applied as synonym.
      * 'high_conf_proposed' — high-confidence confirm, pre-flagged proposal.
      * 'pending_review'     — standard pending review (incl. defer_to_user
                               and any sub-threshold action).
      * 'duplicate'          — value already existed; nothing to do.
      * 'invalid_target'     — merge_into pointed at an unseeded value;
                               downgraded to defer_to_user and routed to
                               pending review.
    """
    seeded = _seeded_values(conn, table=table)

    # If the LLM proposed merging into a value that doesn't exist in the
    # seeded taxonomy, that's a structural error — downgrade defensively.
    if (
        ext.recommended_action == "merge_into"
        and ext.merge_target is not None
        and ext.merge_target not in seeded
    ):
        _log.warning(
            "bootstrap.taxonomy.invalid_merge_target",
            table=table,
            value=ext.value,
            attempted_target=ext.merge_target,
            llm_call_id=llm_call_id,
        )
        downgraded = ext.model_copy(
            update={
                "recommended_action": "defer_to_user",
                "merge_target": None,
                "assessment_reasoning": (
                    f"[downgraded] LLM proposed merge_into "
                    f"{ext.merge_target!r} but target not in seeded taxonomy. "
                    f"Original reasoning: {ext.assessment_reasoning}"
                ),
            }
        )
        created = _insert_taxonomy_proposal(
            conn,
            table=table,
            ext=downgraded,
            llm_call_id=llm_call_id,
            source="user_added",
        )
        if created:
            _log.info(
                "bootstrap.taxonomy.pending_review",
                table=table,
                value=ext.value,
                action="defer_to_user",
                confidence=ext.confidence,
                downgraded_from="merge_into:invalid_target",
                llm_call_id=llm_call_id,
            )
        return "invalid_target"

    high_conf = ext.confidence >= _PERSIST_AUTO_APPLY_CONFIDENCE

    if ext.recommended_action == "merge_into" and high_conf:
        # Auto-apply as a synonym of the merge target. We do NOT insert a
        # fresh row for the proposed value — by definition we're saying
        # "this is the same thing as <target>".
        assert ext.merge_target is not None  # validator + early-return guarantee
        modified = _add_synonym(
            conn, table=table, target_value=ext.merge_target, synonym=ext.value
        )
        if modified:
            _log.info(
                "bootstrap.taxonomy.auto_merged",
                table=table,
                value=ext.value,
                target=ext.merge_target,
                confidence=ext.confidence,
                llm_call_id=llm_call_id,
            )
        return "auto_merged"

    if ext.recommended_action == "confirm" and high_conf:
        created = _insert_taxonomy_proposal(
            conn,
            table=table,
            ext=ext,
            llm_call_id=llm_call_id,
            source="llm_proposed_high_confidence",
        )
        if created:
            _log.info(
                "bootstrap.taxonomy.high_confidence_confirm",
                table=table,
                value=ext.value,
                confidence=ext.confidence,
                llm_call_id=llm_call_id,
            )
            return "high_conf_proposed"
        return "duplicate"

    # Standard pending-review path. Covers:
    #   - defer_to_user (any confidence)
    #   - confirm at sub-threshold confidence
    #   - merge_into at sub-threshold confidence (we still record the
    #     suggested target via llm_merge_target so the SME walk can show it)
    created = _insert_taxonomy_proposal(
        conn,
        table=table,
        ext=ext,
        llm_call_id=llm_call_id,
        source="user_added",
    )
    if created:
        _log.info(
            "bootstrap.taxonomy.pending_review",
            table=table,
            value=ext.value,
            action=ext.recommended_action,
            confidence=ext.confidence,
            llm_call_id=llm_call_id,
        )
        return "pending_review"
    return "duplicate"


# ─── Public API ─────────────────────────────────────────────────────────────


def persist_proposal(
    conn: sqlite3.Connection,
    *,
    proposal: BootstrapProposal,
    llm_call_id: str,
) -> str:
    """Persist a bootstrap proposal.

    Side effects:
      - Each proposed trade / service / category extension is routed per the
        persist policy described in the module docstring (auto-merge,
        high-confidence proposal, or standard pending review). Idempotent —
        values already present are skipped.
      - Records the full proposal as JSON in ``app_setting`` under
        ``bootstrap_proposal_<generated_at>`` for audit / review UI.

    Returns the storage key used in ``app_setting`` for retrieval.
    """
    storage_key = f"{_PROPOSAL_KEY_PREFIX}{proposal.generated_at}"

    blob = {
        "llm_call_id": llm_call_id,
        "proposal": proposal.model_dump(),
    }

    with transaction(conn):
        for ext in proposal.proposed_trade_extensions:
            _apply_extension(conn, table="trade", ext=ext, llm_call_id=llm_call_id)
        for ext in proposal.proposed_service_extensions:
            _apply_extension(conn, table="service", ext=ext, llm_call_id=llm_call_id)
        for ext in proposal.proposed_category_extensions:
            _apply_extension(conn, table="category", ext=ext, llm_call_id=llm_call_id)

        conn.execute(
            """
            INSERT OR REPLACE INTO app_setting (key, value, updated_at)
            VALUES (?, ?, ?)
            """,
            (storage_key, json.dumps(blob), _now()),
        )

    return storage_key


def load_latest_proposal(conn: sqlite3.Connection) -> BootstrapProposal | None:
    """Return the most-recent bootstrap proposal, or None if none persisted."""
    row = conn.execute(
        """
        SELECT value FROM app_setting
        WHERE key LIKE ?
        ORDER BY key DESC
        LIMIT 1
        """,
        (f"{_PROPOSAL_KEY_PREFIX}%",),
    ).fetchone()
    if row is None or not row["value"]:
        return None
    try:
        blob = json.loads(row["value"])
    except (json.JSONDecodeError, ValueError):
        return None
    payload = blob.get("proposal") if isinstance(blob, dict) else None
    if not isinstance(payload, dict):
        return None
    try:
        return BootstrapProposal.model_validate(payload)
    except Exception:  # noqa: BLE001 — malformed audit blob shouldn't crash callers
        return None


def list_proposals(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """List persisted proposals as ``[(storage_key, generated_at), ...]``, newest first."""
    rows = conn.execute(
        """
        SELECT key, value FROM app_setting
        WHERE key LIKE ?
        ORDER BY key DESC
        """,
        (f"{_PROPOSAL_KEY_PREFIX}%",),
    ).fetchall()
    out: list[tuple[str, str]] = []
    for r in rows:
        key = r["key"]
        generated_at = key[len(_PROPOSAL_KEY_PREFIX):] if key.startswith(_PROPOSAL_KEY_PREFIX) else ""
        # Prefer the in-blob generated_at when present; fall back to the
        # key-suffix value.
        try:
            blob = json.loads(r["value"]) if r["value"] else {}
            payload = blob.get("proposal") if isinstance(blob, dict) else None
            if isinstance(payload, dict) and isinstance(payload.get("generated_at"), str):
                generated_at = payload["generated_at"]
        except (json.JSONDecodeError, ValueError):
            pass
        out.append((key, generated_at))
    return out


__all__ = [
    "AuthorityObservation",
    "BootstrapProposal",
    "ClassObservation",
    "CorpusQualityObservation",
    "ServiceMappingProposal",
    "TaxonomyExtensionProposal",
    "_PERSIST_AUTO_APPLY_CONFIDENCE",
    "list_proposals",
    "load_latest_proposal",
    "persist_proposal",
]
