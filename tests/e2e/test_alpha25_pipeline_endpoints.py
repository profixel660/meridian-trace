"""HTTP-level tests for /api/projects/{name}/pipeline.

Idempotency, 404s, 409 on busy. The worker itself runs in a thread —
these tests monkeypatch run_bootstrap_sweep + run_job_over_sources to
no-ops so the test doesn't need real LLM calls or PDFs.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures — api_client and project_slug bridge the conftest's fastapi_client
# and fresh_project into the names used by the task-4 spec.
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client(fastapi_client: TestClient) -> TestClient:
    """Alias so tests written against the spec use the canonical name."""
    return fastapi_client


@pytest.fixture
def project_slug(tmp_projects_dir, api_client: TestClient) -> str:
    """Create a project via the API and return its slug."""
    res = api_client.post(
        "/api/projects",
        json={"name": "alpha25-pipeline-test", "notes": "endpoint test fixture"},
    )
    assert res.status_code == 200, res.text
    # The slug is derived from the name the same way _slugify does it.
    return "alpha25-pipeline-test"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_pipeline_post_404_unknown_project(api_client: TestClient):
    res = api_client.post("/api/projects/does-not-exist/pipeline", json={})
    assert res.status_code == 404


def test_pipeline_post_400_malformed_idempotency_key(
    api_client: TestClient, project_slug: str,
):
    res = api_client.post(
        f"/api/projects/{project_slug}/pipeline",
        json={},
        headers={"Idempotency-Key": "not-a-uuid"},
    )
    assert res.status_code == 400
    assert res.json()["detail"]["error"] == "invalid_idempotency_key"


def test_pipeline_post_idempotent_replay(
    api_client: TestClient, project_slug: str, monkeypatch,
):
    """Two POSTs with the same Idempotency-Key return the same job_id."""
    from meridian.api import idempotency
    from meridian.workers import pipeline_worker

    # Hold the worker so the job stays alive long enough to look up.
    monkeypatch.setattr(
        "meridian.workers.pipeline_worker.run_bootstrap_sweep",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "meridian.workers.pipeline_worker.run_job_over_sources",
        lambda *a, **kw: time.sleep(0.5),
    )
    idempotency._reset_for_tests()
    pipeline_worker._reset_for_tests()

    key = "33333333-3333-4333-8333-333333333333"
    first = api_client.post(
        f"/api/projects/{project_slug}/pipeline", json={},
        headers={"Idempotency-Key": key},
    )
    second = api_client.post(
        f"/api/projects/{project_slug}/pipeline", json={},
        headers={"Idempotency-Key": key},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["job_id"] == second.json()["job_id"]


def test_pipeline_get_by_id_404(api_client: TestClient, project_slug: str):
    res = api_client.get(
        f"/api/projects/{project_slug}/pipeline/00000000-0000-4000-8000-000000000000"
    )
    assert res.status_code == 404


def test_pipeline_get_index_404_when_no_jobs(
    api_client: TestClient, project_slug: str, monkeypatch,
):
    from meridian.workers import pipeline_worker
    pipeline_worker._reset_for_tests()
    res = api_client.get(f"/api/projects/{project_slug}/pipeline")
    assert res.status_code == 404


def test_pipeline_get_index_returns_latest(
    api_client: TestClient, project_slug: str, monkeypatch,
):
    from meridian.workers import pipeline_worker

    monkeypatch.setattr(
        "meridian.workers.pipeline_worker.run_bootstrap_sweep",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "meridian.workers.pipeline_worker.run_job_over_sources",
        lambda *a, **kw: time.sleep(0.3),
    )
    pipeline_worker._reset_for_tests()

    post_res = api_client.post(
        f"/api/projects/{project_slug}/pipeline", json={},
    )
    job_id = post_res.json()["job_id"]
    idx = api_client.get(f"/api/projects/{project_slug}/pipeline")
    assert idx.status_code == 200
    assert idx.json()["job_id"] == job_id
