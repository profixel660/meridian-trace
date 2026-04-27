"""Smoke tests for the FastAPI surface via Starlette TestClient.

Exercises the discoverability endpoints that the upcoming Next.js shell will
hit on first paint, plus the round-12 auth/login endpoint (xfail until the
auth_router gets wired into ``meridian.api.main``)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from meridian.extract.orchestrator import run_job_over_sources
from meridian.projects import create_project


def test_get_projects_empty(fastapi_client: TestClient) -> None:
    """No project files in the projects dir → GET /projects returns []."""
    response = fastapi_client.get("/projects")
    assert response.status_code == 200
    assert response.json() == []


def test_get_project_after_create(fastapi_client: TestClient) -> None:
    """Create a project via projects.py and confirm GET /projects shows it."""
    create_project(name="api-listed-proj", notes=None)

    response = fastapi_client.get("/projects")
    assert response.status_code == 200
    rows = response.json()
    names = {r["name"] for r in rows}
    assert "api-listed-proj" in names

    item = next(r for r in rows if r["name"] == "api-listed-proj")
    # Brand-new project — every count should be zero.
    for k in ("deliverables_master", "sources", "questions_pending", "conflicts_pending"):
        assert item[k] == 0, f"expected {k}=0, got {item[k]}"


def test_post_auth_login_unauthenticated(fastapi_client: TestClient) -> None:
    """POST /auth/login with bad credentials must return 401.

    The endpoint is defined in ``meridian.auth.login_api`` but as of writing
    its router is NOT yet ``include_router``-ed in ``meridian.api.main``;
    pytest will report this as XFAIL until that wiring lands. We assert the
    desired contract here so the day someone hooks the router up the test
    flips to XPASS and prompts the strict-fail removal.
    """
    response = fastapi_client.post(
        "/auth/login", json={"totp_code": "000000"}
    )
    if response.status_code == 404:
        pytest.xfail(
            "auth_router not yet wired into meridian.api.main "
            "(round-12 in progress; flip this to a hard assert once "
            "`app.include_router(auth_router)` lands)."
        )
    assert response.status_code == 401, response.text


def test_get_tender_trades(
    fastapi_client: TestClient,
    fresh_project_with_sample_doc: tuple[str, Path, sqlite3.Connection, str],
    mock_llm_client,
) -> None:
    """End-to-end: ingest → mock-extract → GET /tender/trades returns valid response."""
    name, _db, conn, src_id = fresh_project_with_sample_doc

    # Run the full extraction in-process so deliverable rows land before we
    # query the tender endpoint.
    run_job_over_sources(conn, source_ids=[src_id])
    conn.close()

    response = fastapi_client.get(f"/projects/{name}/tender/trades")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project"] == name
    assert isinstance(body["trades"], list)
    # The default stub deliverable was auto-routed to 'auto_approved' (high
    # confidence, no flags), so it lands in the master register and the
    # 'mechanical' trade should show up with at least one count.
    trade_names = {t["trade"] for t in body["trades"]}
    assert "Mechanical" in trade_names, (
        f"expected 'Mechanical' trade in list, got {trade_names!r}"
    )


def test_get_health(fastapi_client: TestClient) -> None:
    """Liveness probe: /health returns ok + version. Cheap canary for the
    middleware / app-init wiring that every other test relies on."""
    response = fastapi_client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body


# ── Round-15 auto-assessment surfacing (DECISIONS.md §3.10) ───────────────
#
# These two tests pin the GET /projects/{name}/taxonomy/pending JSON shape
# so the SME-facing CLI + Next.js queue can rely on the four llm_* fields.
# They write taxonomy rows directly via SQL — no live LLM call — to keep
# the harness offline-safe and to isolate the API surface from the
# bootstrap pipeline.
#
# DEPENDENCY ON STREAM A: these tests assume the schema carries the four
# new columns (llm_recommended_action / llm_merge_target / llm_confidence /
# llm_reasoning) on service_taxonomy. If Stream A's migration hasn't
# committed yet the INSERT will raise OperationalError; that's the
# expected sequencing failure mode flagged in the report.


def _insert_service_taxonomy_proposal(
    conn: sqlite3.Connection,
    *,
    row_id: str,
    value: str,
    llm_recommended_action: str | None,
    llm_merge_target: str | None,
    llm_confidence: float | None,
    llm_reasoning: str | None,
) -> None:
    """Insert one service_taxonomy proposal row with the round-15 fields."""
    from datetime import UTC, datetime

    conn.execute(
        """
        INSERT INTO service_taxonomy (
            id, value, source, is_active, created_at,
            llm_recommended_action, llm_merge_target,
            llm_confidence, llm_reasoning
        ) VALUES (?, ?, 'user_added', 1, ?, ?, ?, ?, ?)
        """,
        (
            row_id,
            value,
            datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            llm_recommended_action,
            llm_merge_target,
            llm_confidence,
            llm_reasoning,
        ),
    )
    conn.commit()


def test_taxonomy_pending_includes_llm_fields(
    fastapi_client: TestClient,
    fresh_project: tuple[str, Path, sqlite3.Connection],
) -> None:
    """A proposal with auto-assessment populated must surface all four llm_* fields.

    Round 15 contract: SME surfaces (CLI walk-taxonomy + Next.js queue)
    render the LLM recommendation alongside each pending proposal so the
    reviewer can quick-accept high-signal cases. The API is the join point.
    """
    name, _db, conn = fresh_project
    _insert_service_taxonomy_proposal(
        conn,
        row_id="svc-chiller-test",
        value="Chiller System",
        llm_recommended_action="confirm",
        llm_merge_target=None,
        llm_confidence=0.92,
        llm_reasoning="47 chiller-specific paragraphs across 3 docs",
    )

    response = fastapi_client.get(f"/projects/{name}/taxonomy/pending")
    assert response.status_code == 200, response.text
    rows = response.json()
    match = next((r for r in rows if r["value"] == "Chiller System"), None)
    assert match is not None, f"proposal not returned; got: {rows!r}"
    assert match["llm_recommended_action"] == "confirm"
    assert match["llm_merge_target"] is None
    assert match["llm_confidence"] == pytest.approx(0.92)
    assert match["llm_reasoning"] == "47 chiller-specific paragraphs across 3 docs"


def test_taxonomy_pending_handles_legacy_null_fields(
    fastapi_client: TestClient,
    fresh_project: tuple[str, Path, sqlite3.Connection],
) -> None:
    """Legacy rows (pre-round-15) leave the llm_* columns NULL — JSON must mirror that.

    Guards against a regression where the API fabricates defaults
    (e.g. confidence=0.0) and the SME mistakes "no recommendation" for
    "low confidence".
    """
    name, _db, conn = fresh_project
    _insert_service_taxonomy_proposal(
        conn,
        row_id="svc-legacy-test",
        value="Legacy Value",
        llm_recommended_action=None,
        llm_merge_target=None,
        llm_confidence=None,
        llm_reasoning=None,
    )

    response = fastapi_client.get(f"/projects/{name}/taxonomy/pending")
    assert response.status_code == 200, response.text
    rows = response.json()
    match = next((r for r in rows if r["value"] == "Legacy Value"), None)
    assert match is not None
    assert match["llm_recommended_action"] is None
    assert match["llm_merge_target"] is None
    assert match["llm_confidence"] is None
    assert match["llm_reasoning"] is None
