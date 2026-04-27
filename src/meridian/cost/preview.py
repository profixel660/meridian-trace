"""Pre-run cost preview (CONTEXT.md §13).

Estimates the total LLM spend for an upcoming extraction across a project +
list of source ids (or "all sources"). Per-purpose routing is resolved via the
same chain `call_llm` uses, so the preview reflects the actual route the run
will take (env > project > settings > defaults).

The preview is intentionally an *upper-bound estimate*:
- Output token estimates use the configured `extraction_max_tokens` for
  extraction calls — real outputs are usually much smaller. The CLI surfaces a
  disclaimer ("real costs are typically 30-70% lower").
- Input token estimates use the per-extractor `_MAX_SOURCE_CHARS` cap.
- Token counts use `litellm.token_counter` when the model is recognised by
  LiteLLM, otherwise fall back to `len(text) // 4` (a coarse but stable
  English-text approximation).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from meridian.config import settings
from meridian.cost.rates import lookup_rate

# ── Per-purpose estimation constants — mirror the values used at call sites.
# Update these if the corresponding constants in extract/* change.

# quality_scan.py: _QUALITY_SCAN_EXCERPT_CHARS
_QUALITY_SCAN_INPUT_CHARS = 18_000
_QUALITY_SCAN_OUTPUT_TOKENS = 500  # small JSON

# triage.py: _MIN_CHUNK_CHARS_TO_TRIAGE / _MAX_CHUNK_CHARS_PER_TRIAGE
_TRIAGE_MIN_CHUNK_CHARS = 40
_TRIAGE_MAX_CHARS_PER_CHUNK = 4_000
_TRIAGE_OUTPUT_TOKENS = 100  # tiny JSON {keep, reason}

# text_spec.py / demarcation.py / bod_import.py
_TEXT_SPEC_MAX_INPUT_CHARS = 100_000
_BOD_MAX_INPUT_CHARS = 120_000
_DEMARCATION_MAX_INPUT_CHARS = 120_000

# conflict_pass.py: _MAX_DELIVERABLES_PER_CALL / _MAX_AUDIT_PER_CALL
_CONFLICT_CHARS_PER_DELIVERABLE = 200  # JSON-serialised row, rough
_CONFLICT_CHARS_PER_AUDIT_ROW = 200
_CONFLICT_OUTPUT_TOKENS = 8_000

# Coarse char-to-token ratio for the fallback path (English prose ≈ 4 chars/tok).
_FALLBACK_CHARS_PER_TOKEN = 4


def _count_input_tokens(*, full_model: str, char_budget: int) -> int:
    """Estimate input tokens for a char budget.

    Tries `litellm.token_counter` for accuracy; falls back to chars/4 if the
    model is unknown to LiteLLM, the call raises, or returns 0. We synthesise
    a placeholder text of the right length rather than passing real prompts —
    the preview must not require any real source content to estimate.
    """
    if char_budget <= 0:
        return 0
    try:
        # Lazy import — keep cost preview cheap to import.
        from litellm import token_counter  # type: ignore[import-not-found]

        # A repeating 4-char unit gives a stable placeholder; the tokenizer
        # cares about character composition, not semantic content.
        placeholder = ("word " * (char_budget // 5 + 1))[:char_budget]
        n = int(token_counter(model=full_model, text=placeholder))
        if n > 0:
            return n
    except Exception:  # noqa: BLE001 — token_counter is best-effort
        pass
    return char_budget // _FALLBACK_CHARS_PER_TOKEN


def _provider_model_str(provider: str, model: str) -> str:
    """Build LiteLLM-style 'provider/model' (matches llm.client._provider_model)."""
    if model.startswith(f"{provider}/"):
        return model
    return f"{provider}/{model}"


# ── Public dataclasses ─────────────────────────────────────────────────────


@dataclass
class CostLeg:
    purpose: str
    provider: str
    model: str
    source_ids: list[str]
    n_calls_estimated: int
    input_tokens_estimated: int
    output_tokens_estimated: int
    input_cents: int
    output_cents: int
    total_cents: int
    rate_unknown: bool
    n_calls_already_run: int = 0


@dataclass
class CostPreview:
    total_cents: int
    total_unknown: bool
    breakdown: list[CostLeg] = field(default_factory=list)


# ── Internal helpers ───────────────────────────────────────────────────────


def _resolve_route(purpose: str, project_routing: dict | None) -> tuple[str, str]:
    return settings.resolve_route(purpose, project_routing=project_routing)


def _leg_cost(
    *,
    rate: tuple[int, int] | None,
    input_tokens: int,
    output_tokens: int,
) -> tuple[int, int, int, bool]:
    """Return (input_cents, output_cents, total_cents, rate_unknown)."""
    if rate is None:
        return (0, 0, 0, True)
    in_rate, out_rate = rate
    in_cents = (input_tokens * in_rate) // 1_000_000
    out_cents = (output_tokens * out_rate) // 1_000_000
    return (in_cents, out_cents, in_cents + out_cents, False)


def _completed_source_ids_for_purpose(
    conn: sqlite3.Connection, *, purpose: str, source_ids: list[str]
) -> set[str]:
    """Sources for which an `llm_call` of this purpose already exists.

    Used to populate `n_calls_already_run` per leg so the UI can show how many
    sources will be skipped on the next run (once unchanged-source-skip lands).
    Counted via deliverable.llm_call.purpose join — the purpose enum is the
    most reliable signal that a particular extraction path ran for a source.
    """
    if not source_ids:
        return set()
    placeholders = ",".join("?" for _ in source_ids)
    rows = conn.execute(
        f"""
        SELECT DISTINCT d.source_id
        FROM deliverable d
        JOIN llm_call lc ON lc.id = d.llm_call_id
        WHERE lc.purpose = ?
          AND d.source_id IN ({placeholders})
        """,
        (purpose, *source_ids),
    ).fetchall()
    return {r["source_id"] for r in rows}


def _completed_quality_scan_source_ids(
    conn: sqlite3.Connection, *, source_ids: list[str]
) -> set[str]:
    if not source_ids:
        return set()
    placeholders = ",".join("?" for _ in source_ids)
    rows = conn.execute(
        f"""
        SELECT id FROM source_document
        WHERE id IN ({placeholders}) AND quality_scan_id IS NOT NULL
        """,
        tuple(source_ids),
    ).fetchall()
    return {r["id"] for r in rows}


def _resolve_source_rows(
    conn: sqlite3.Connection, source_ids: list[str] | None
) -> list[sqlite3.Row]:
    if source_ids is None:
        return list(
            conn.execute(
                "SELECT id, filename, extraction_path FROM source_document "
                "ORDER BY imported_at"
            ).fetchall()
        )
    placeholders = ",".join("?" for _ in source_ids)
    if not source_ids:
        return []
    return list(
        conn.execute(
            f"SELECT id, filename, extraction_path FROM source_document "
            f"WHERE id IN ({placeholders})",
            tuple(source_ids),
        ).fetchall()
    )


def _get_source_text_len(conn: sqlite3.Connection, source_id: str) -> int:
    row = conn.execute(
        "SELECT length(text) AS n FROM source_document_text WHERE source_id = ?",
        (source_id,),
    ).fetchone()
    return int(row["n"]) if row and row["n"] is not None else 0


def _count_triage_eligible_chunks(conn: sqlite3.Connection, source_id: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM source_document_chunk
        WHERE source_id = ?
          AND length(COALESCE(text, '')) >= ?
        """,
        (source_id, _TRIAGE_MIN_CHUNK_CHARS),
    ).fetchone()
    return int(row["n"]) if row and row["n"] is not None else 0


def _count_total_chunks(conn: sqlite3.Connection, source_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM source_document_chunk WHERE source_id = ?",
        (source_id,),
    ).fetchone()
    return int(row["n"]) if row and row["n"] is not None else 0


def _project_deliverable_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM deliverable").fetchone()
    return int(row["n"]) if row and row["n"] is not None else 0


def _project_audit_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM audit_record").fetchone()
    return int(row["n"]) if row and row["n"] is not None else 0


# ── Public API ─────────────────────────────────────────────────────────────


def estimate_project_cost(
    conn: sqlite3.Connection,
    source_ids: list[str] | None,
    project_routing: dict | None = None,
) -> CostPreview:
    """Estimate worst-case LLM spend for an upcoming extraction.

    Args:
        conn: open project SQLite connection.
        source_ids: explicit list of source ids to estimate, or None for all
            sources currently in the project.
        project_routing: optional override; if omitted, routing is read from
            the project's app_setting via the same path call_llm uses.

    Returns:
        CostPreview with one CostLeg per (purpose, provider, model) bucket
        plus a project-wide conflict_pass leg.
    """
    sources = _resolve_source_rows(conn, source_ids)
    source_id_list = [r["id"] for r in sources]

    # Group by extraction_path to know which sources hit which extractor.
    bod_sources: list[sqlite3.Row] = []
    text_spec_sources: list[sqlite3.Row] = []
    demarcation_sources: list[sqlite3.Row] = []
    for r in sources:
        path = (r["extraction_path"] or "text_spec").lower()
        if path == "bod_import":
            bod_sources.append(r)
        elif path == "demarcation":
            demarcation_sources.append(r)
        else:
            text_spec_sources.append(r)

    legs: list[CostLeg] = []

    # ── 1. Quality scan — 1 call per source ─────────────────────────────
    if sources:
        provider, model = _resolve_route("quality_scan", project_routing)
        rate = lookup_rate(provider, model)
        full_model = _provider_model_str(provider, model)
        already = _completed_quality_scan_source_ids(
            conn, source_ids=source_id_list
        )
        per_call_out = _QUALITY_SCAN_OUTPUT_TOKENS
        # Per-source input chars (capped at the scan's excerpt cap).
        total_input_tokens = 0
        for r in sources:
            text_len = _get_source_text_len(conn, r["id"])
            char_budget = min(text_len, _QUALITY_SCAN_INPUT_CHARS)
            total_input_tokens += _count_input_tokens(
                full_model=full_model, char_budget=char_budget
            )
        n_calls = len(sources)
        out_tokens = per_call_out * n_calls
        in_c, out_c, tot_c, unk = _leg_cost(
            rate=rate, input_tokens=total_input_tokens, output_tokens=out_tokens
        )
        legs.append(
            CostLeg(
                purpose="quality_scan",
                provider=provider,
                model=model,
                source_ids=source_id_list,
                n_calls_estimated=n_calls,
                input_tokens_estimated=total_input_tokens,
                output_tokens_estimated=out_tokens,
                input_cents=in_c,
                output_cents=out_c,
                total_cents=tot_c,
                rate_unknown=unk,
                n_calls_already_run=len(already),
            )
        )
    # ── 2. Triage — 1 call per chunk above _MIN_CHUNK_CHARS_TO_TRIAGE ───
    triage_sources = text_spec_sources + demarcation_sources  # BOD skips triage
    if triage_sources:
        provider, model = _resolve_route("triage", project_routing)
        rate = lookup_rate(provider, model)
        full_model = _provider_model_str(provider, model)
        triage_source_ids: list[str] = []
        total_chunks = 0
        per_chunk_input_tokens = _count_input_tokens(
            full_model=full_model, char_budget=_TRIAGE_MAX_CHARS_PER_CHUNK
        )
        for r in triage_sources:
            n = _count_triage_eligible_chunks(conn, r["id"])
            if n > 0:
                triage_source_ids.append(r["id"])
                total_chunks += n
        if total_chunks > 0:
            in_tokens = per_chunk_input_tokens * total_chunks
            out_tokens = _TRIAGE_OUTPUT_TOKENS * total_chunks
            in_c, out_c, tot_c, unk = _leg_cost(
                rate=rate, input_tokens=in_tokens, output_tokens=out_tokens
            )
            legs.append(
                CostLeg(
                    purpose="triage",
                    provider=provider,
                    model=model,
                    source_ids=triage_source_ids,
                    n_calls_estimated=total_chunks,
                    input_tokens_estimated=in_tokens,
                    output_tokens_estimated=out_tokens,
                    input_cents=in_c,
                    output_cents=out_c,
                    total_cents=tot_c,
                    rate_unknown=unk,
                    n_calls_already_run=0,  # triage doesn't write deliverables
                )
            )

    # ── 3. Extraction legs (text_spec / demarcation / bod) — 1 call/source
    for purpose, group, max_chars in (
        ("extract_text_spec", text_spec_sources, _TEXT_SPEC_MAX_INPUT_CHARS),
        ("extract_demarcation", demarcation_sources, _DEMARCATION_MAX_INPUT_CHARS),
        ("extract_bod", bod_sources, _BOD_MAX_INPUT_CHARS),
    ):
        if not group:
            continue
        provider, model = _resolve_route(purpose, project_routing)
        rate = lookup_rate(provider, model)
        full_model = _provider_model_str(provider, model)
        ids = [r["id"] for r in group]
        already = _completed_source_ids_for_purpose(
            conn, purpose=purpose, source_ids=ids
        )
        total_input_tokens = 0
        for r in group:
            text_len = _get_source_text_len(conn, r["id"])
            char_budget = min(text_len, max_chars)
            total_input_tokens += _count_input_tokens(
                full_model=full_model, char_budget=char_budget
            )
        n_calls = len(group)
        # Worst-case output: extraction_max_tokens per call. This is the safe
        # upper bound; real outputs are typically much smaller.
        out_tokens = settings.extraction_max_tokens * n_calls
        in_c, out_c, tot_c, unk = _leg_cost(
            rate=rate, input_tokens=total_input_tokens, output_tokens=out_tokens
        )
        legs.append(
            CostLeg(
                purpose=purpose,
                provider=provider,
                model=model,
                source_ids=ids,
                n_calls_estimated=n_calls,
                input_tokens_estimated=total_input_tokens,
                output_tokens_estimated=out_tokens,
                input_cents=in_c,
                output_cents=out_c,
                total_cents=tot_c,
                rate_unknown=unk,
                n_calls_already_run=len(already),
            )
        )

    # ── 4. Conflict pass — 1 call per project ───────────────────────────
    n_deliv = _project_deliverable_count(conn)
    n_audit = _project_audit_count(conn)
    if sources and (n_deliv + n_audit) > 0:
        provider, model = _resolve_route("conflict_pass", project_routing)
        rate = lookup_rate(provider, model)
        full_model = _provider_model_str(provider, model)
        char_budget = (
            n_deliv * _CONFLICT_CHARS_PER_DELIVERABLE
            + n_audit * _CONFLICT_CHARS_PER_AUDIT_ROW
        )
        in_tokens = _count_input_tokens(
            full_model=full_model, char_budget=char_budget
        )
        out_tokens = _CONFLICT_OUTPUT_TOKENS
        in_c, out_c, tot_c, unk = _leg_cost(
            rate=rate, input_tokens=in_tokens, output_tokens=out_tokens
        )
        legs.append(
            CostLeg(
                purpose="conflict_pass",
                provider=provider,
                model=model,
                source_ids=[],  # project-wide
                n_calls_estimated=1,
                input_tokens_estimated=in_tokens,
                output_tokens_estimated=out_tokens,
                input_cents=in_c,
                output_cents=out_c,
                total_cents=tot_c,
                rate_unknown=unk,
                n_calls_already_run=0,
            )
        )

    total = sum(leg.total_cents for leg in legs)
    any_unknown = any(leg.rate_unknown for leg in legs)
    return CostPreview(total_cents=total, total_unknown=any_unknown, breakdown=legs)


__all__ = ["CostLeg", "CostPreview", "estimate_project_cost"]
