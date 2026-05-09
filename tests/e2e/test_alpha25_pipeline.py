"""End-to-end tests for the alpha-25 pipeline worker + endpoints.

The worker happy path runs bootstrap → extract serially in a daemon
thread and exposes phase / per-source progress via an in-memory
registry. These tests poke the registry directly; HTTP-level tests live
in test_alpha25_pipeline_endpoints.py.
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
