"""Alpha-25 pipeline worker: bootstrap → extract, serial, threaded.

A _PipelineJob represents one async run on a project. The HTTP layer
spawns a daemon thread per job; the GET endpoint reads from the in-memory
registry. Mirrors the alpha-24 wizard `_ImportJob` shape.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from meridian.bootstrap import run_bootstrap_sweep
from meridian.db.connection import connect
from meridian.extract.orchestrator import run_job_over_sources
from meridian.logging import get_logger
from meridian.projects import ProjectBusy

_log = get_logger("meridian.pipeline")


def _iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class _PipelineJob:
    id: str
    db_path: Path
    phase: str = "pending"  # pending | bootstrap | extract | done | failed
    bootstrap_status: str = "pending"  # pending | running | succeeded | failed
    extract_total: int = 0
    extract_completed: int = 0
    current_source_filename: str | None = None
    started_at: str = field(default_factory=_iso_now)
    finished_at: str | None = None
    error_message: str | None = None
    holder_pid: int | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)


_pipeline_jobs: dict[str, _PipelineJob] = {}
_pipeline_jobs_lock = threading.Lock()


def _bump_completed(job: _PipelineJob, source_id: str, filename: str) -> None:
    """Callback wired into run_job_over_sources."""
    with job._lock:
        job.extract_completed += 1
        job.current_source_filename = filename or None


def _run_pipeline(
    job: _PipelineJob,
    *,
    sample_size: int,
    provider: str | None,
    model: str | None,
) -> None:
    """Worker thread body. Sequential bootstrap → extract; soft-fail bootstrap."""
    conn = connect(job.db_path, busy_timeout_ms=30000)
    try:
        # Phase 1 — bootstrap (advisory)
        with job._lock:
            job.phase = "bootstrap"
            job.bootstrap_status = "running"
        try:
            run_bootstrap_sweep(
                conn, sample_size=sample_size, provider=provider, model=model,
            )
            with job._lock:
                job.bootstrap_status = "succeeded"
        except Exception as exc:  # noqa: BLE001 — soft-fail by design
            _log.warning(
                "pipeline.bootstrap_soft_failed",
                job_id=job.id, error=f"{type(exc).__name__}: {exc}",
            )
            with job._lock:
                job.bootstrap_status = "failed"

        # Phase 2 — extract
        with job._lock:
            job.phase = "extract"
        source_ids = [
            r["id"] for r in conn.execute(
                "SELECT id FROM source_document ORDER BY imported_at"
            ).fetchall()
        ]
        with job._lock:
            job.extract_total = len(source_ids)
        run_job_over_sources(
            conn,
            source_ids=source_ids,
            provider=provider,
            model=model,
            on_source_complete=lambda sid, fn: _bump_completed(job, sid, fn),
        )
        with job._lock:
            job.phase = "done"
            job.finished_at = _iso_now()
            job.current_source_filename = None
    except ProjectBusy as exc:
        with job._lock:
            job.phase = "failed"
            job.finished_at = _iso_now()
            job.error_message = (
                f"Project is busy — held by PID {exc.pid} for {exc.purpose}."
            )
            job.holder_pid = exc.pid
        _log.warning("pipeline.busy", job_id=job.id, holder_pid=exc.pid)
    except BaseException as exc:  # noqa: BLE001 — keep the thread observable
        with job._lock:
            job.phase = "failed"
            job.finished_at = _iso_now()
            job.error_message = f"{type(exc).__name__}: {exc}"
        _log.exception("pipeline.failed", job_id=job.id)
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass


def start_pipeline_thread(job: _PipelineJob, **kwargs) -> threading.Thread:
    """Spawn the daemon thread for a registered job. Caller registers the job first."""
    thread = threading.Thread(
        target=_run_pipeline,
        kwargs={"job": job, **kwargs},
        daemon=True,
        name=f"pipeline-{job.id[:8]}",
    )
    thread.start()
    return thread


# Test seam — DO NOT call from production code.
def _reset_for_tests() -> None:
    with _pipeline_jobs_lock:
        _pipeline_jobs.clear()


__all__ = [
    "_PipelineJob",
    "_pipeline_jobs",
    "_pipeline_jobs_lock",
    "_run_pipeline",
    "start_pipeline_thread",
]
