"""End-to-end tests for the alpha-25 pipeline worker + endpoints.

The worker happy path runs bootstrap → extract serially in a daemon
thread and exposes phase / per-source progress via an in-memory
registry. The first two tests poke the registry directly; the last two
drive the full HTTP loop from POST through polling.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest


def _wait_for_phase(job, *, target: str, timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if job.phase == target:
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for phase={target}; saw phase={job.phase}")


def test_pipeline_worker_happy_path(project_with_two_sources, mock_llm_client):
    from meridian.workers.pipeline_worker import (
        _PipelineJob,
        _run_pipeline,
        _pipeline_jobs,
        _pipeline_jobs_lock,
    )
    conn, source_ids, _ = project_with_two_sources
    db_path = Path(conn.execute("PRAGMA database_list").fetchone()["file"])
    job = _PipelineJob(id="job-happy-1", db_path=db_path)
    with _pipeline_jobs_lock:
        _pipeline_jobs[job.id] = job

    # Run the worker body inline (no thread) — the test owns the schedule.
    _run_pipeline(job, sample_size=2, provider=None, model=None)

    assert job.phase == "done"
    assert job.bootstrap_status == "succeeded"
    assert job.extract_total == len(source_ids)
    assert job.extract_completed == len(source_ids)
    assert job.error_message is None


def test_pipeline_worker_bootstrap_soft_failed(
    project_with_two_sources, mock_llm_client, monkeypatch,
):
    """A bootstrap exception is logged + flagged but does NOT abort extract."""
    from meridian.workers.pipeline_worker import _PipelineJob, _run_pipeline

    def boom(*a, **kw):
        raise RuntimeError("synthetic bootstrap failure")
    monkeypatch.setattr(
        "meridian.workers.pipeline_worker.run_bootstrap_sweep", boom,
    )

    conn, source_ids, _ = project_with_two_sources
    db_path = Path(conn.execute("PRAGMA database_list").fetchone()["file"])
    job = _PipelineJob(id="job-soft-fail-1", db_path=db_path)
    _run_pipeline(job, sample_size=2, provider=None, model=None)

    assert job.phase == "done"
    assert job.bootstrap_status == "failed"
    assert job.extract_completed == len(source_ids)


# ---------------------------------------------------------------------------
# E2E HTTP tests — full POST → poll → deliverables loop
# ---------------------------------------------------------------------------


def test_pipeline_e2e_happy_path(
    api_client, project_with_two_sources_via_api, mock_llm_client,
):
    """Full HTTP loop: POST /pipeline, poll GET /pipeline/{id}, deliverables exist."""
    slug = project_with_two_sources_via_api  # fixture returns the slug
    post = api_client.post(f"/api/projects/{slug}/pipeline", json={})
    assert post.status_code == 200
    job_id = post.json()["job_id"]

    deadline = time.monotonic() + 30.0
    last = None
    while time.monotonic() < deadline:
        res = api_client.get(f"/api/projects/{slug}/pipeline/{job_id}")
        assert res.status_code == 200
        last = res.json()
        if last["phase"] in {"done", "failed"}:
            break
        time.sleep(0.1)

    assert last is not None and last["phase"] == "done", f"final state: {last}"
    cov = api_client.get(f"/api/projects/{slug}/coverage").json()
    assert cov["deliverable_status"]["total"] > 0
    assert cov["is_data_present"] is True


def test_pipeline_e2e_busy_409(
    api_client, project_with_two_sources_via_api, mock_llm_client, monkeypatch,
):
    """A second pipeline POST while the first runs returns 409 with holder_pid.

    Strategy: monkeypatch ``is_project_lock_held`` (the check in the POST
    handler) to return True after the first POST fires, simulating the
    project-lock-held condition that the real worker would establish.  The
    timing-based race (0.5 s sleep) proved unreliable because the
    monkeypatched slow lambda delays *before* ``acquire_project_lock`` is
    called, so the lock file never exists during the sleep window.
    """
    slug = project_with_two_sources_via_api

    # Fire the first POST to get a real job in flight.
    first = api_client.post(f"/api/projects/{slug}/pipeline", json={})
    assert first.status_code == 200

    # Now simulate the project lock being held (as it would be mid-extraction).
    # Patch at the source module because the handler does a local import
    # (``from meridian.projects import is_project_lock_held``) at call time,
    # resolving through the meridian.projects namespace.
    monkeypatch.setattr(
        "meridian.projects.is_project_lock_held",
        lambda _slug: True,
    )

    second = api_client.post(f"/api/projects/{slug}/pipeline", json={})
    assert second.status_code == 409
    assert second.json()["detail"]["error"] == "project_busy"


def test_pipeline_runs_conflict_pass_after_extract(
    project_with_two_sources, mock_llm_client,
):
    """Alpha-25.1 regression pin: the pipeline worker MUST run conflict-pass
    after extract, so the master register's flags + conflict_summary columns
    are populated. The original alpha-25 keystone shipped without this step;
    the SME's first review caught both columns empty across all rows."""
    from meridian.workers.pipeline_worker import _PipelineJob, _run_pipeline

    conn, _source_ids, _ = project_with_two_sources
    db_path = Path(conn.execute("PRAGMA database_list").fetchone()["file"])
    job = _PipelineJob(id="job-conflict-pass-1", db_path=db_path)
    _run_pipeline(job, sample_size=2, provider=None, model=None)

    assert job.phase == "done", f"final phase: {job.phase}, error: {job.error_message}"
    assert job.conflict_pass_status == "succeeded", (
        f"conflict-pass should have run; status={job.conflict_pass_status}"
    )

    # The conflict-pass writes an llm_call row with purpose='conflict_pass'.
    # That row's presence is the canonical proof the pass actually executed —
    # the empty-flags-column regression mode would leave this query at zero.
    count = conn.execute(
        "SELECT COUNT(*) FROM llm_call WHERE purpose = 'conflict_pass'"
    ).fetchone()[0]
    assert count >= 1, (
        f"expected at least one llm_call with purpose='conflict_pass'; got {count}"
    )


def test_pipeline_conflict_pass_soft_fails_when_extract_produced_nothing(
    project_with_two_sources, mock_llm_client, monkeypatch,
):
    """If extract produces zero deliverables, conflict-pass would raise
    'No deliverables or audit rows present'. The pipeline must mark
    conflict_pass_status='skipped' and still reach phase=done."""
    from meridian.workers.pipeline_worker import _PipelineJob, _run_pipeline

    # No-op extract: skips deliverable creation entirely.
    monkeypatch.setattr(
        "meridian.workers.pipeline_worker.run_job_over_sources",
        lambda *a, **kw: None,
    )

    conn, _source_ids, _ = project_with_two_sources
    db_path = Path(conn.execute("PRAGMA database_list").fetchone()["file"])
    job = _PipelineJob(id="job-conflict-pass-empty-1", db_path=db_path)
    _run_pipeline(job, sample_size=2, provider=None, model=None)

    assert job.phase == "done"
    assert job.conflict_pass_status == "skipped"
