"""Alpha-22: coverage endpoint must distinguish 'project has no data
yet' from 'project's baseline is untrustworthy'.

Empty projects (zero sources, zero deliverables, zero LLM calls) are
not 'untrustworthy' — they have no opinion to be trustworthy or not.
The frontend uses is_data_present=false to suppress the BaselineBanner
on empty projects (Task 7).
"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient


def test_coverage_empty_project_reports_no_data(
    fastapi_client: TestClient,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from meridian.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from meridian.projects import create_project
    create_project(name="empty-test")

    r = fastapi_client.get("/api/projects/empty-test/coverage")
    assert r.status_code == 200, r.text
    body = r.json()

    # Sanity check: this project really is empty.
    assert body["sources_imported"] == 0
    assert body["deliverable_status"]["total"] == 0
    assert body["cost"]["total_calls"] == 0

    # The new contract: empty projects report is_data_present=False.
    assert body["is_data_present"] is False, (
        f"empty project should have is_data_present=False, got: {body}"
    )

    # And is_baseline_trustworthy should be None (no opinion), NOT False.
    assert body["is_baseline_trustworthy"] is None, (
        f"empty project should have is_baseline_trustworthy=None "
        f"(no opinion), got: {body['is_baseline_trustworthy']}"
    )

    # Blocker list should be EMPTY on empty projects (no nonsense
    # 'X% missing provenance' messages on a zero-data project).
    assert body["baseline_trust_blockers"] == [], (
        f"empty project should have empty blocker list, got: "
        f"{body['baseline_trust_blockers']}"
    )


def test_coverage_populated_project_still_reports_trustworthiness(
    fastapi_client: TestClient,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Sanity: when a project HAS data (deliverables or LLM calls), the trust flag
    should still be bool (true or false), NOT None. is_data_present should be true."""
    from meridian.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from meridian.projects import create_project, project_db_path
    from datetime import UTC, datetime

    create_project(name="populated-test")
    db = project_db_path("populated-test")

    # Insert a single source row + an LLM call so the project has data.
    # Just having an LLM call is enough to trigger is_data_present=True.
    # Column names match src/meridian/db/schema.sql source_document table.
    import sqlite3
    import json
    import uuid

    with sqlite3.connect(db) as conn:
        # Add a source
        conn.execute(
            "INSERT INTO source_document "
            "(id, filename, relative_path, content_hash, mime_type, size_bytes, "
            "imported_at, extraction_path) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "src1",
                "x.docx",
                "x.docx",
                "h1",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                100,
                "2026-04-28T00:00:00Z",
                "pending",
            ),
        )
        # Add an LLM call so is_data_present will be True
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            """
            INSERT INTO llm_call (
                id, job_id, purpose, provider, model, provider_api_version,
                system_prompt, user_prompt, prompt_version_ref,
                temperature, top_p, max_tokens, input_hash,
                response_text, parsed_response,
                prompt_tokens, completion_tokens,
                cache_read_tokens, cache_write_tokens, cost_cents,
                started_at, finished_at, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                None,
                "quality_scan",
                "test",
                "test-model",
                None,
                "test system",
                "test user",
                "quality_scan_v_test",
                0.0,
                None,
                16000,
                "deadbeef",
                json.dumps({"scan_quality": "clean"}),
                None,
                10,
                10,
                None,
                None,
                0,
                now,
                now,
                None,
            ),
        )
        conn.commit()

    r = fastapi_client.get("/api/projects/populated-test/coverage")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["sources_imported"] == 1
    assert body["is_data_present"] is True, body
    # is_baseline_trustworthy is now a bool (not None) — could be true OR false
    # depending on the existing trust-computation logic, but it must NOT be None.
    assert isinstance(body["is_baseline_trustworthy"], bool), body


def test_render_coverage_text_empty_state(tmp_path: Path, monkeypatch) -> None:
    """The CLI renderer must surface the new is_data_present=False
    branch as 'EMPTY: NO DATA YET' (or similar), NOT fall through to
    the BLOCKED branch.
    """
    from meridian.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from meridian.projects import create_project, project_db_path
    from meridian.coverage.dashboard import (
        project_coverage,
        render_coverage_text,
    )
    create_project(name="empty-render-test")
    db = project_db_path("empty-render-test")
    import sqlite3
    with sqlite3.connect(db) as conn:
        coverage = project_coverage(conn)

    text = render_coverage_text(coverage)
    assert "NO DATA YET" in text, text
    # Crucial: must NOT render the BLOCKED branch on an empty project.
    assert "BASELINE NOT YET TRUSTWORTHY" not in text, text
    assert "BLOCKED" not in text, text


def test_is_data_present_false_when_only_sources_imported(
    project_with_two_sources_via_api, api_client,
):
    """Sources-only project (no deliverables, no LLM calls) → is_data_present=False."""
    slug = project_with_two_sources_via_api
    cov = api_client.get(f"/api/projects/{slug}/coverage").json()
    # Sources are present; deliverables and LLM calls are not (no extract yet).
    assert cov["sources_imported"] >= 1
    assert cov["deliverable_status"]["total"] == 0
    assert cov["is_data_present"] is False, (
        "Sources-only projects should suppress the BaselineBanner amber "
        "(alpha-25 §7)."
    )


def test_is_data_present_true_when_a_deliverable_exists(
    project_with_two_sources_via_api, api_client, mock_llm_client,
):
    """One source + one deliverable → is_data_present=True (regression guard)."""
    slug = project_with_two_sources_via_api
    res = api_client.post(f"/api/projects/{slug}/pipeline", json={})
    assert res.status_code == 200
    job_id = res.json()["job_id"]
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        s = api_client.get(f"/api/projects/{slug}/pipeline/{job_id}").json()
        if s["phase"] in {"done", "failed"}:
            break
        time.sleep(0.1)
    cov = api_client.get(f"/api/projects/{slug}/coverage").json()
    assert cov["is_data_present"] is True
    assert cov["deliverable_status"]["total"] > 0


def test_coverage_conflicts_includes_resolved_and_superseded_counts(
    project_with_two_sources_via_api, api_client, mock_llm_client,
):
    """Alpha-26: coverage.conflicts.{resolved_count,superseded_count} populated."""
    slug = project_with_two_sources_via_api
    res = api_client.post(f"/api/projects/{slug}/pipeline", json={})
    assert res.status_code == 200
    job_id = res.json()["job_id"]
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        s = api_client.get(f"/api/projects/{slug}/pipeline/{job_id}").json()
        if s["phase"] in {"done", "failed"}:
            break
        time.sleep(0.1)
    cov = api_client.get(f"/api/projects/{slug}/coverage").json()
    conflicts = cov["conflicts"]
    assert "pending" in conflicts
    assert "resolved_count" in conflicts
    assert "superseded_count" in conflicts
    assert isinstance(conflicts["resolved_count"], int)
    assert isinstance(conflicts["superseded_count"], int)
