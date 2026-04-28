"""Alpha-22: coverage endpoint must distinguish 'project has no data
yet' from 'project's baseline is untrustworthy'.

Empty projects (zero sources, zero deliverables, zero LLM calls) are
not 'untrustworthy' — they have no opinion to be trustworthy or not.
The frontend uses is_data_present=false to suppress the BaselineBanner
on empty projects (Task 7).
"""

from __future__ import annotations

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
    """Sanity: when a project HAS data, the trust flag should still be
    bool (true or false), NOT None. is_data_present should be true."""
    from meridian.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from meridian.projects import create_project, project_db_path

    create_project(name="populated-test")
    db = project_db_path("populated-test")

    # Insert a single source row directly so the project has data.
    # Column names match src/meridian/db/schema.sql source_document table.
    import sqlite3
    with sqlite3.connect(db) as conn:
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
        conn.commit()

    r = fastapi_client.get("/api/projects/populated-test/coverage")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["sources_imported"] == 1
    assert body["is_data_present"] is True, body
    # is_baseline_trustworthy is now a bool (not None) — could be true OR false
    # depending on the existing trust-computation logic, but it must NOT be None.
    assert isinstance(body["is_baseline_trustworthy"], bool), body
