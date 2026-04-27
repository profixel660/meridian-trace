"""Per-project bootstrap LLM sweep orchestrator.

Builds a stratified sample of project sources, calls the bootstrap prompt,
parses the structured proposal, persists into the existing taxonomy review
flow. CONTEXT.md §23 #4.

Design notes:
  - Sample selection is *stratified by mime_type* so the sample is
    representative across PDF / XLSX / DOCX / EML / DWG. Within each mime
    bucket we prioritise sources that have NOT yet been quality-scanned
    (scan_quality_id IS NULL) so the sweep adds value early in the project
    lifecycle. Quotas are computed proportionally; any leftover slack is
    redistributed to mimes with extra candidates.
  - Per-source text excerpts are capped at 4000 chars (matches the
    quality-scan prompt's ``_QUALITY_SCAN_EXCERPT_CHARS``-style cap, scaled
    down because the bootstrap call sends N excerpts in a single prompt).
  - The LLM purpose recorded on the ``llm_call`` row is ``quality_scan``
    because the locked v1 enum has no ``bootstrap_sweep`` slot; the
    ``prompt_version_ref`` distinguishes it from per-document scans.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from meridian.bootstrap.proposals import (
    AuthorityObservation,
    BootstrapProposal,
    ClassObservation,
    CorpusQualityObservation,
    ServiceMappingProposal,
    TaxonomyExtensionProposal,
    persist_proposal,
)
from meridian.llm.client import call_llm
from meridian.logging import get_logger
from meridian.prompts.loader import (
    extract_prompt_body,
    load_prompt,
    render_prompt,
)

_log = get_logger("meridian.bootstrap")

_BOOTSTRAP_PROMPT_FILENAME = "PROMPT_V1_bootstrap_sweep.md"
_BOOTSTRAP_PROMPT_VERSION = "v1.1"

# Per-source excerpt cap. We send up to 20 sources in a single prompt so we
# keep each excerpt small (4000 chars * 20 = 80k chars worst case).
_PER_SOURCE_EXCERPT_CHARS = 4000

# How many representative filenames to surface per class observation in
# downstream UIs — keep payloads compact.

_SYSTEM_PROMPT = (
    "You are a careful reconnaissance reviewer for construction project "
    "document corpora. Return ONLY valid JSON matching the requested schema, "
    "with no commentary. Be conservative — when uncertain, defer to existing "
    "canonical taxonomy values rather than proposing extensions."
)


# ─── Sample selection ──────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _all_candidate_sources(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """All non-deleted sources joined with their text. We sort newest-import-last
    so within-bucket ordering is stable and deterministic across runs."""
    return list(
        conn.execute(
            """
            SELECT
                sd.id            AS id,
                sd.filename      AS filename,
                sd.mime_type     AS mime_type,
                sd.size_bytes    AS size_bytes,
                sd.relative_path AS relative_path,
                sd.document_class AS document_class,
                sd.quality_scan_id AS quality_scan_id,
                sdt.text         AS text
            FROM source_document sd
            LEFT JOIN source_document_text sdt ON sdt.source_id = sd.id
            ORDER BY sd.imported_at ASC
            """
        ).fetchall()
    )


def _stratify_by_mime(
    rows: list[sqlite3.Row], *, sample_size: int
) -> list[sqlite3.Row]:
    """Stratified sampling by ``mime_type`` with un-scanned-first priority.

    Algorithm:
      1. Group rows by mime_type (preserving insertion order).
      2. Compute proportional quota per mime bucket: floor(N_bucket / N_total
         * sample_size), with a minimum quota of 1 per non-empty bucket
         (capped by sample_size).
      3. Within each bucket, sort: NOT yet quality-scanned first; then by
         original ordering. Take up to quota items.
      4. Redistribute any leftover slots round-robin across buckets that
         still have un-picked candidates.
      5. Trim to sample_size.
    """
    if sample_size <= 0 or not rows:
        return []

    # 1. group
    buckets: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        buckets.setdefault(r["mime_type"] or "unknown", []).append(r)

    # 2. quotas
    total = len(rows)
    quotas: dict[str, int] = {}
    for mime, bucket in buckets.items():
        q = max(1, (len(bucket) * sample_size) // total)
        quotas[mime] = min(q, len(bucket), sample_size)
    # If the sum of mins exceeds sample_size, trim from the largest buckets.
    while sum(quotas.values()) > sample_size:
        biggest_mime = max(quotas, key=lambda m: quotas[m])
        quotas[biggest_mime] -= 1
        if quotas[biggest_mime] <= 0:
            quotas.pop(biggest_mime)

    # 3. order within bucket: un-scanned-first, then original order.
    def _bucket_sort_key(row: sqlite3.Row) -> tuple[int, int]:
        scanned = 0 if row["quality_scan_id"] is None else 1
        return (scanned, 0)

    picked: dict[str, list[sqlite3.Row]] = {}
    for mime, bucket in buckets.items():
        ordered = sorted(bucket, key=_bucket_sort_key)
        picked[mime] = ordered[: quotas.get(mime, 0)]

    # 4. redistribute leftovers
    selected: list[sqlite3.Row] = [r for items in picked.values() for r in items]
    leftover = sample_size - len(selected)
    if leftover > 0:
        # Build per-mime "remaining candidates" lists.
        remaining: dict[str, list[sqlite3.Row]] = {}
        for mime, bucket in buckets.items():
            ordered = sorted(bucket, key=_bucket_sort_key)
            already = {r["id"] for r in picked.get(mime, [])}
            remaining[mime] = [r for r in ordered if r["id"] not in already]
        # Round-robin across buckets that still have candidates.
        active_mimes = [m for m, v in remaining.items() if v]
        idx = 0
        while leftover > 0 and active_mimes:
            mime = active_mimes[idx % len(active_mimes)]
            if remaining[mime]:
                selected.append(remaining[mime].pop(0))
                leftover -= 1
            if not remaining[mime]:
                active_mimes.remove(mime)
            else:
                idx += 1

    # 5. trim & dedupe by id (paranoia)
    seen: set[str] = set()
    out: list[sqlite3.Row] = []
    for r in selected:
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        out.append(r)
        if len(out) >= sample_size:
            break
    return out


def _select_sample_sources(
    conn: sqlite3.Connection, *, sample_size: int = 15
) -> list[sqlite3.Row]:
    """Public-ish helper used by tests + the verification block.

    Returns up to ``sample_size`` source_document rows selected via the
    stratify-by-mime / un-scanned-first heuristic.
    """
    rows = _all_candidate_sources(conn)
    return _stratify_by_mime(rows, sample_size=sample_size)


# ─── Prompt construction ───────────────────────────────────────────────────


def _build_samples_block(samples: list[sqlite3.Row]) -> str:
    """Render the per-source block sent to the model.

    Each entry includes a stable header (id, filename, mime, hint) followed by
    a capped excerpt of the extracted text.
    """
    blocks: list[str] = []
    for i, row in enumerate(samples, 1):
        text = (row["text"] or "")[:_PER_SOURCE_EXCERPT_CHARS]
        truncated = " [... truncated ...]" if (row["text"] or "") and len(row["text"]) > _PER_SOURCE_EXCERPT_CHARS else ""
        hint = row["document_class"] or "unset"
        blocks.append(
            "\n".join(
                [
                    f"## SOURCE {i}",
                    f"source_id:               {row['id']}",
                    f"filename:                {row['filename']}",
                    f"mime_type:               {row['mime_type']}",
                    f"document_class_hint:     {hint}",
                    f"text_excerpt (max {_PER_SOURCE_EXCERPT_CHARS} chars):",
                    "---",
                    f"{text}{truncated}",
                    "---",
                ]
            )
        )
    return "\n\n".join(blocks)


def _seeded_taxonomy_values(
    conn: sqlite3.Connection,
) -> tuple[list[str], list[str], list[str]]:
    """Return (trades, services, categories) currently seeded in the project DB.

    These are passed into the prompt context so the LLM has the legal set of
    `merge_target` values for its taxonomy auto-assessment.
    """
    trades = [r["value"] for r in conn.execute("SELECT value FROM trade_taxonomy ORDER BY value").fetchall()]
    services = [r["value"] for r in conn.execute("SELECT value FROM service_taxonomy ORDER BY value").fetchall()]
    categories = [r["value"] for r in conn.execute("SELECT value FROM category_taxonomy ORDER BY value").fetchall()]
    return trades, services, categories


def _build_prompt(
    *,
    samples: list[sqlite3.Row],
    seeded_trades: list[str],
    seeded_services: list[str],
    seeded_categories: list[str],
) -> str:
    body = extract_prompt_body(load_prompt(_BOOTSTRAP_PROMPT_FILENAME))
    return render_prompt(
        body,
        {
            "sample_size": len(samples),
            "samples_block": _build_samples_block(samples),
            "seeded_trades": ", ".join(seeded_trades) if seeded_trades else "(none)",
            "seeded_services": ", ".join(seeded_services) if seeded_services else "(none)",
            "seeded_categories": ", ".join(seeded_categories) if seeded_categories else "(none)",
        },
    )


# ─── Parsing ───────────────────────────────────────────────────────────────


def _parse_proposal(
    parsed: dict[str, Any],
    *,
    project_name: str,
    samples: list[sqlite3.Row],
) -> BootstrapProposal:
    """Convert raw LLM JSON into the typed ``BootstrapProposal`` model.

    Tolerates missing / extra fields per the Flexibility Principle (CONTEXT.md
    §0); whatever the model returns gets filtered through the Pydantic models'
    defaults.
    """

    _valid_actions = {"confirm", "merge_into", "defer_to_user"}

    def _coerce_assessment(
        item: dict[str, Any], value: str, table: str
    ) -> tuple[str, str | None, float, str]:
        """Coerce the four LLM-assessment fields, defaulting to defer_to_user
        on any malformed input. Returns
        (recommended_action, merge_target, confidence, assessment_reasoning).
        """
        reason_default = (
            f"[downgraded] LLM assessment fields missing or malformed for "
            f"{table} value {value!r}; routed to defer_to_user."
        )

        raw_action = item.get("recommended_action")
        action = raw_action if raw_action in _valid_actions else None
        merge_target_raw = item.get("merge_target")
        merge_target = (
            merge_target_raw.strip()
            if isinstance(merge_target_raw, str) and merge_target_raw.strip()
            else None
        )
        raw_conf = item.get("confidence")
        try:
            confidence = float(raw_conf) if raw_conf is not None else None
        except (TypeError, ValueError):
            confidence = None
        if confidence is not None and not (0.0 <= confidence <= 1.0):
            confidence = None
        raw_reasoning = item.get("assessment_reasoning")
        assessment_reasoning = (
            str(raw_reasoning).strip()
            if isinstance(raw_reasoning, str) and raw_reasoning.strip()
            else ""
        )

        # Downgrade conditions: any of {bad action, bad confidence,
        # merge_into without merge_target}.
        bad = False
        downgrade_reasons: list[str] = []
        if action is None:
            bad = True
            downgrade_reasons.append(f"invalid_or_missing_action={raw_action!r}")
        if confidence is None:
            bad = True
            downgrade_reasons.append(f"invalid_or_missing_confidence={raw_conf!r}")
        if action == "merge_into" and not merge_target:
            bad = True
            downgrade_reasons.append("merge_into_without_target")

        if bad:
            _log.warning(
                "bootstrap.taxonomy.assessment_malformed",
                table=table,
                value=value,
                reasons=downgrade_reasons,
            )
            return (
                "defer_to_user",
                None,
                0.0,
                f"{reason_default} Original reasoning: {assessment_reasoning}",
            )

        # Normalize: strip merge_target on non-merge actions.
        if action != "merge_into":
            merge_target = None

        # Pydantic model still requires non-empty assessment_reasoning; if the
        # LLM left it blank but everything else is valid, give a stub so the
        # row is queryable.
        if not assessment_reasoning:
            assessment_reasoning = "(no reasoning provided)"

        return (action, merge_target, confidence, assessment_reasoning)

    def _ext_list(raw: Any, table: str) -> list[TaxonomyExtensionProposal]:
        out: list[TaxonomyExtensionProposal] = []
        if not isinstance(raw, list):
            return out
        for item in raw:
            if not isinstance(item, dict):
                continue
            value = item.get("value")
            if not isinstance(value, str) or not value.strip():
                continue
            value = value.strip()
            (
                rec_action,
                merge_target,
                confidence,
                assessment_reasoning,
            ) = _coerce_assessment(item, value, table)
            try:
                out.append(
                    TaxonomyExtensionProposal(
                        table=table,  # type: ignore[arg-type]
                        value=value,
                        reasoning=str(item.get("reasoning") or ""),
                        sample_source_filenames=[
                            str(f) for f in (item.get("sample_source_filenames") or [])
                            if isinstance(f, str)
                        ],
                        recommended_action=rec_action,  # type: ignore[arg-type]
                        merge_target=merge_target,
                        confidence=confidence,
                        assessment_reasoning=assessment_reasoning,
                    )
                )
            except Exception as exc:  # noqa: BLE001 — never crash; downgrade silently
                _log.warning(
                    "bootstrap.taxonomy.proposal_construction_failed",
                    table=table,
                    value=value,
                    error=str(exc),
                )
                continue
        return out

    class_obs: list[ClassObservation] = []
    for item in parsed.get("document_class_observations") or []:
        if not isinstance(item, dict):
            continue
        try:
            class_obs.append(
                ClassObservation(
                    document_class=str(item.get("document_class") or "unknown"),
                    count=int(item.get("count") or 0),
                    confidence=item.get("confidence") or "low",  # type: ignore[arg-type]
                    sample_filenames=[
                        str(f) for f in (item.get("sample_filenames") or [])
                        if isinstance(f, str)
                    ],
                )
            )
        except Exception:  # noqa: BLE001 — skip malformed entries
            continue

    mappings: list[ServiceMappingProposal] = []
    for item in parsed.get("proposed_service_mappings") or []:
        if not isinstance(item, dict):
            continue
        text = item.get("disc_section_text")
        if not isinstance(text, str) or not text.strip():
            continue
        ps = item.get("proposed_service")
        mappings.append(
            ServiceMappingProposal(
                disc_section_text=text.strip(),
                proposed_service=ps if isinstance(ps, str) else None,
                reasoning=str(item.get("reasoning") or ""),
            )
        )

    auth_obs: list[AuthorityObservation] = []
    for item in parsed.get("authority_chain_observations") or []:
        if not isinstance(item, dict):
            continue
        try:
            auth_obs.append(
                AuthorityObservation(
                    source_id=str(item.get("source_id") or ""),
                    filename=str(item.get("filename") or ""),
                    role=item.get("role") or "unknown",  # type: ignore[arg-type]
                    confidence=item.get("confidence") or "low",  # type: ignore[arg-type]
                    reasoning=str(item.get("reasoning") or ""),
                )
            )
        except Exception:  # noqa: BLE001 — skip malformed entries
            continue

    qraw = parsed.get("corpus_quality_summary") or {}
    if not isinstance(qraw, dict):
        qraw = {}
    breakdown = qraw.get("scan_quality_breakdown") or {}
    if not isinstance(breakdown, dict):
        breakdown = {}
    quality = CorpusQualityObservation(
        total_sampled=int(qraw.get("total_sampled") or len(samples)),
        scan_quality_breakdown={
            str(k): int(v) for k, v in breakdown.items() if isinstance(v, (int, float))
        },
        ocr_needed_count=int(qraw.get("ocr_needed_count") or 0),
        template_count=int(qraw.get("template_count") or 0),
        unreadable_count=int(qraw.get("unreadable_count") or 0),
    )

    recs_raw = parsed.get("recommendations") or []
    recs = [str(r) for r in recs_raw if isinstance(r, str)]

    return BootstrapProposal(
        project_name=project_name,
        generated_at=_now(),
        sources_sampled=[r["id"] for r in samples],
        sample_size=len(samples),
        document_class_observations=class_obs,
        proposed_trade_extensions=_ext_list(parsed.get("proposed_trade_extensions"), "trade"),
        proposed_service_extensions=_ext_list(parsed.get("proposed_service_extensions"), "service"),
        proposed_category_extensions=_ext_list(parsed.get("proposed_category_extensions"), "category"),
        proposed_service_mappings=mappings,
        authority_chain_observations=auth_obs,
        corpus_quality=quality,
        recommendations=recs,
    )


# ─── Orchestrator ──────────────────────────────────────────────────────────


@dataclass
class SweepResult:
    proposal: BootstrapProposal
    llm_call_id: str
    storage_key: str
    new_taxonomy_proposals_persisted: int


def _project_name_from_db(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT name FROM project LIMIT 1").fetchone()
    return row["name"] if row else ""


def _count_new_proposals(proposal: BootstrapProposal) -> int:
    return (
        len(proposal.proposed_trade_extensions)
        + len(proposal.proposed_service_extensions)
        + len(proposal.proposed_category_extensions)
    )


def run_bootstrap_sweep(
    conn: sqlite3.Connection,
    *,
    sample_size: int = 15,
    provider: str | None = None,
    model: str | None = None,
) -> SweepResult:
    """Sample up to ``sample_size`` sources, call the LLM, persist proposals.

    The LLM call is recorded with ``purpose='quality_scan'`` (the closest
    match in the locked v1 enum) and ``prompt_version_ref='bootstrap_sweep_<ver>'``
    for traceability.
    """
    samples = _select_sample_sources(conn, sample_size=sample_size)
    if not samples:
        raise ValueError(
            "No source_document rows available for bootstrap sweep. "
            "Import at least one source first."
        )

    seeded_trades, seeded_services, seeded_categories = _seeded_taxonomy_values(conn)
    user_prompt = _build_prompt(
        samples=samples,
        seeded_trades=seeded_trades,
        seeded_services=seeded_services,
        seeded_categories=seeded_categories,
    )
    project_name = _project_name_from_db(conn)

    call = call_llm(
        conn,
        purpose="quality_scan",
        provider=provider,
        model=model,
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        prompt_version_ref=f"bootstrap_sweep_{_BOOTSTRAP_PROMPT_VERSION}",
    )
    if not isinstance(call.parsed_json, dict):
        raise RuntimeError(
            f"Bootstrap sweep returned non-object JSON (call {call.id}). "
            "Raw response stored in llm_call.response_text."
        )

    proposal = _parse_proposal(
        call.parsed_json, project_name=project_name, samples=samples
    )
    storage_key = persist_proposal(conn, proposal=proposal, llm_call_id=call.id)

    return SweepResult(
        proposal=proposal,
        llm_call_id=call.id,
        storage_key=storage_key,
        new_taxonomy_proposals_persisted=_count_new_proposals(proposal),
    )


def render_proposal_summary(proposal: BootstrapProposal) -> str:
    """Plain-text summary used by the ``bootstrap-show`` CLI command."""
    lines: list[str] = []
    lines.append(f"Bootstrap proposal — project={proposal.project_name}")
    lines.append(f"  generated_at: {proposal.generated_at}")
    lines.append(f"  sample_size:  {proposal.sample_size}")
    lines.append("")

    lines.append("Document classes observed:")
    if not proposal.document_class_observations:
        lines.append("  (none)")
    for obs in proposal.document_class_observations:
        lines.append(
            f"  - {obs.document_class}: count={obs.count} "
            f"confidence={obs.confidence}"
        )
    lines.append("")

    def _ext_block(label: str, items: list[TaxonomyExtensionProposal]) -> None:
        lines.append(f"Proposed {label} extensions:")
        if not items:
            lines.append("  (none)")
        for it in items:
            # Surface the LLM's auto-assessment in the summary line so the
            # SME walk (and the bootstrap-show CLI) can show "LLM recommends
            # confirm (95% confidence): <reasoning>" at a glance.
            if it.recommended_action == "merge_into" and it.merge_target:
                rec = f"merge_into:{it.merge_target}"
            else:
                rec = it.recommended_action
            conf_pct = int(round(it.confidence * 100))
            lines.append(
                f"  - {it.value!r} — {it.reasoning}"
            )
            lines.append(
                f"      LLM recommends {rec} ({conf_pct}% confidence): "
                f"{it.assessment_reasoning}"
            )
        lines.append("")

    _ext_block("trade", proposal.proposed_trade_extensions)
    _ext_block("service", proposal.proposed_service_extensions)
    _ext_block("category", proposal.proposed_category_extensions)

    lines.append("Proposed service mappings:")
    if not proposal.proposed_service_mappings:
        lines.append("  (none)")
    for m in proposal.proposed_service_mappings:
        ps = m.proposed_service or "(informational — do not auto-map)"
        lines.append(f"  - {m.disc_section_text!r} -> {ps}")
    lines.append("")

    lines.append("Authority chain:")
    if not proposal.authority_chain_observations:
        lines.append("  (none)")
    for a in proposal.authority_chain_observations:
        lines.append(
            f"  - {a.filename}: {a.role} (confidence={a.confidence})"
        )
    lines.append("")

    q = proposal.corpus_quality
    lines.append("Corpus quality:")
    lines.append(f"  total sampled:   {q.total_sampled}")
    lines.append(f"  ocr needed:      {q.ocr_needed_count}")
    lines.append(f"  templates:       {q.template_count}")
    lines.append(f"  unreadable:      {q.unreadable_count}")
    if q.scan_quality_breakdown:
        lines.append(f"  scan breakdown:  {json.dumps(q.scan_quality_breakdown)}")
    lines.append("")

    lines.append("Recommendations:")
    if not proposal.recommendations:
        lines.append("  (none)")
    for r in proposal.recommendations:
        lines.append(f"  - {r}")
    return "\n".join(lines)


__all__ = [
    "SweepResult",
    "render_proposal_summary",
    "run_bootstrap_sweep",
]
