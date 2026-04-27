"""End-to-end tests for the bootstrap-sweep taxonomy auto-assessment (round 15).

Authority: docs/DECISIONS.md §3.10. Each proposed taxonomy value carries a
``recommended_action`` (``confirm`` / ``merge_into`` / ``defer_to_user``),
``merge_target``, ``confidence``, and ``assessment_reasoning``. The persist
policy:

  * ``merge_into`` + confidence >= 0.85 → auto-applied as a synonym of the
    target, ``source='llm_auto_merged'``. NOT a fresh review row.
  * ``confirm``    + confidence >= 0.85 → fresh proposal with
    ``source='llm_proposed_high_confidence'`` and the reasoning persisted in
    the new ``llm_*`` columns.
  * Any sub-threshold or ``defer_to_user`` → standard pending review row
    enriched with the ``llm_*`` columns so Stream B can render the SME walk.
  * Malformed assessment fields or invalid ``merge_target`` → defensively
    downgraded to ``defer_to_user`` with a structured warning.

Every test stubs the bootstrap LLM through the existing ``mock_llm_client``
fixture — no live LLM. The ``run_bootstrap_sweep`` orchestrator records its
LLM call under ``purpose='quality_scan'`` (the closest slot in the locked
v1 enum), so the per-call hook keys off that purpose.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from meridian.bootstrap.sweep import run_bootstrap_sweep


def _bootstrap_response(extensions: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a minimal bootstrap-sweep JSON payload with ``extensions`` as
    the proposed_service_extensions list."""
    return {
        "document_class_observations": [],
        "proposed_trade_extensions": [],
        "proposed_service_extensions": extensions,
        "proposed_category_extensions": [],
        "proposed_service_mappings": [],
        "authority_chain_observations": [],
        "corpus_quality_summary": {
            "total_sampled": 1,
            "scan_quality_breakdown": {"clean": 1},
            "ocr_needed_count": 0,
            "template_count": 0,
            "unreadable_count": 0,
        },
        "recommendations": [],
    }


def _service_row(
    conn: sqlite3.Connection, value: str
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM service_taxonomy WHERE value = ?", (value,)
    ).fetchone()


# ───────────────────────────────────────────────────────────────────────────


def test_high_confidence_merge_auto_applies(
    fresh_project_with_sample_doc: tuple[str, Path, sqlite3.Connection, str],
    mock_llm_client,
) -> None:
    """LLM proposes 'Chiller System' as merge_into HVAC at 0.95 confidence —
    must land as a synonym of HVAC, NOT as a pending review row."""
    _name, _db, conn, _source_id = fresh_project_with_sample_doc

    mock_llm_client.responses["quality_scan"] = _bootstrap_response(
        [
            {
                "value": "Chiller System",
                "reasoning": "Generic mechanical scope mentions chillers in passing.",
                "sample_source_filenames": ["sample.docx"],
                "recommended_action": "merge_into",
                "merge_target": "HVAC",
                "confidence": 0.95,
                "assessment_reasoning": (
                    "Only 2 passing mentions inside generic HVAC scope; "
                    "no dedicated chiller content."
                ),
            }
        ]
    )

    result = run_bootstrap_sweep(conn, sample_size=5)
    assert result.new_taxonomy_proposals_persisted == 1, (
        "the proposal still counts as 'seen' even though it auto-merged"
    )

    # No fresh row for 'Chiller System' should exist.
    assert _service_row(conn, "Chiller System") is None

    # HVAC row must now carry 'Chiller System' as a synonym.
    hvac = _service_row(conn, "HVAC")
    assert hvac is not None
    assert hvac["synonyms"], "HVAC.synonyms should be populated after auto-merge"
    syns = json.loads(hvac["synonyms"])
    assert "Chiller System" in syns


def test_high_confidence_confirm_persisted_with_reasoning(
    fresh_project_with_sample_doc: tuple[str, Path, sqlite3.Connection, str],
    mock_llm_client,
) -> None:
    """LLM proposes 'Chiller System' as confirm at 0.92 — proposal must land
    with source='llm_proposed_high_confidence' and the reasoning queryable."""
    _name, _db, conn, _source_id = fresh_project_with_sample_doc

    mock_llm_client.responses["quality_scan"] = _bootstrap_response(
        [
            {
                "value": "Chiller System",
                "reasoning": "Hyperscale data centre with 60-page chiller spec.",
                "sample_source_filenames": ["sample.docx"],
                "recommended_action": "confirm",
                "merge_target": None,
                "confidence": 0.92,
                "assessment_reasoning": (
                    "60-page spec dedicates 50+ paragraphs to chiller-specific "
                    "design; HVAC alone would lose detail."
                ),
            }
        ]
    )

    result = run_bootstrap_sweep(conn, sample_size=5)
    assert result.new_taxonomy_proposals_persisted == 1

    row = _service_row(conn, "Chiller System")
    assert row is not None, "high-confidence confirm should land as a fresh row"
    assert row["source"] == "llm_proposed_high_confidence"
    assert row["llm_recommended_action"] == "confirm"
    assert row["llm_merge_target"] is None
    assert row["llm_confidence"] == pytest.approx(0.92)
    assert "60-page spec" in (row["llm_reasoning"] or "")


def test_lower_confidence_routes_to_review_with_recommendation(
    fresh_project_with_sample_doc: tuple[str, Path, sqlite3.Connection, str],
    mock_llm_client,
) -> None:
    """LLM proposes 'Chiller System' as merge_into HVAC at 0.6 — sub-threshold,
    so it must land as a STANDARD pending row (source='user_added') with the
    LLM recommendation columns populated for Stream B's review surface."""
    _name, _db, conn, _source_id = fresh_project_with_sample_doc

    mock_llm_client.responses["quality_scan"] = _bootstrap_response(
        [
            {
                "value": "Chiller System",
                "reasoning": "Mixed corpus signal.",
                "sample_source_filenames": ["sample.docx"],
                "recommended_action": "merge_into",
                "merge_target": "HVAC",
                "confidence": 0.6,
                "assessment_reasoning": (
                    "Some chiller-specific paragraphs but also general HVAC "
                    "scope; signal is ambiguous."
                ),
            }
        ]
    )

    run_bootstrap_sweep(conn, sample_size=5)

    row = _service_row(conn, "Chiller System")
    assert row is not None
    assert row["source"] == "user_added"  # standard pending review
    assert row["llm_recommended_action"] == "merge_into"
    assert row["llm_merge_target"] == "HVAC"
    assert row["llm_confidence"] == pytest.approx(0.6)
    assert "ambiguous" in (row["llm_reasoning"] or "")
    # HVAC must NOT have been touched — confidence is below the auto-apply bar.
    hvac = _service_row(conn, "HVAC")
    assert hvac is not None
    assert not hvac["synonyms"], (
        "HVAC.synonyms should be untouched at sub-threshold confidence"
    )


def test_defer_to_user_routes_to_review(
    fresh_project_with_sample_doc: tuple[str, Path, sqlite3.Connection, str],
    mock_llm_client,
) -> None:
    """LLM emits ``defer_to_user`` — even at high confidence the row must
    land as standard pending review (the action itself is the SME punt)."""
    _name, _db, conn, _source_id = fresh_project_with_sample_doc

    mock_llm_client.responses["quality_scan"] = _bootstrap_response(
        [
            {
                "value": "Chiller System",
                "reasoning": "Cannot tell from the sample.",
                "sample_source_filenames": ["sample.docx"],
                "recommended_action": "defer_to_user",
                "merge_target": None,
                "confidence": 0.9,
                "assessment_reasoning": "Sample is too thin to call.",
            }
        ]
    )

    run_bootstrap_sweep(conn, sample_size=5)

    row = _service_row(conn, "Chiller System")
    assert row is not None
    assert row["source"] == "user_added"
    assert row["llm_recommended_action"] == "defer_to_user"
    assert row["llm_merge_target"] is None
    assert row["llm_confidence"] == pytest.approx(0.9)


def test_invalid_merge_target_rejected(
    fresh_project_with_sample_doc: tuple[str, Path, sqlite3.Connection, str],
    mock_llm_client,
    capfd: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """LLM proposes ``merge_into: <not-in-seeded-taxonomy>`` — must be
    downgraded to ``defer_to_user`` and routed to standard pending review,
    with a structured warning logged.

    The structured logger may route through structlog's console renderer
    (stdout) or the stdlib logger (caplog), depending on test ordering /
    prior ``configure_logging`` calls — assert against both, mirroring
    ``tests/e2e/test_concurrency.py``."""
    _name, _db, conn, _source_id = fresh_project_with_sample_doc

    mock_llm_client.responses["quality_scan"] = _bootstrap_response(
        [
            {
                "value": "Chiller System",
                "reasoning": "—",
                "sample_source_filenames": ["sample.docx"],
                "recommended_action": "merge_into",
                "merge_target": "NotARealService",
                "confidence": 0.95,
                "assessment_reasoning": "Original LLM reasoning here.",
            }
        ]
    )

    import logging

    caplog.set_level(logging.WARNING, logger="meridian.bootstrap")
    run_bootstrap_sweep(conn, sample_size=5)

    # The downgraded row must exist as standard pending review.
    row = _service_row(conn, "Chiller System")
    assert row is not None
    assert row["source"] == "user_added"
    assert row["llm_recommended_action"] == "defer_to_user"
    assert row["llm_merge_target"] is None
    # Reasoning must record the downgrade so the SME walk can show it.
    reasoning = row["llm_reasoning"] or ""
    assert "[downgraded]" in reasoning
    assert "NotARealService" in reasoning
    assert "Original LLM reasoning here." in reasoning

    # The structured warning must have fired — check both routes.
    # capfd (file-descriptor capture) catches structlog output regardless of
    # whether structlog grabbed sys.stderr at import time before pytest's
    # capsys could swap it. caplog catches the stdlib propagation path.
    captured = capfd.readouterr()
    combined = (
        captured.out
        + "\n"
        + captured.err
        + "\n"
        + "\n".join(rec.getMessage() for rec in caplog.records)
    )
    assert "bootstrap.taxonomy.invalid_merge_target" in combined, (
        f"expected bootstrap.taxonomy.invalid_merge_target log; got: {combined!r}"
    )
