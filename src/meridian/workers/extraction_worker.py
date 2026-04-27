"""Subprocess-isolated extraction worker (CONTEXT.md §6, §19).

Per CONTEXT.md §6: each extraction job runs one subprocess per source for
crash isolation. This module is both:

* a library — :func:`run_extraction_in_subprocess` is called by the parent
  orchestrator (see :mod:`meridian.extract.orchestrator`); and
* a ``__main__`` entry point — invoked as
  ``python -m meridian.workers.extraction_worker --db <path> --job-id <id>
  --source-id <id>``. The child opens its own SQLite connection, records its
  ``worker_pid`` on the ``extraction_job_source`` row, runs
  :func:`meridian.extract.orchestrator.run_extraction_for_source`, and emits
  the resulting :class:`SourceRunResult` back to the parent on stdout as a
  single JSON line.

Result-passing approach
=======================
We use **stdout JSON** (a single line on the last line of stdout, prefixed
with the marker ``__MERIDIAN_RESULT__``) rather than a temporary file. The
parent reads the child's stdout buffer once on completion, scans for the
marker, and decodes the JSON payload. This is simpler than managing a temp
file (no cleanup, no path-passing, no race on truncation) and the result
payload is small and structured. Any other stdout content (e.g. tracebacks
on crash) is preserved in the captured buffer for the parent to log.

Pause / resume
==============
The worker subprocess is **transactional within a single source** — it does
**not** poll a stop-signal file. The parent orchestrator polls the
``<projects_dir>/<slug>.stop_signal`` path between source iterations and
exits the loop after the in-flight source completes. This keeps the worker
simple and avoids partial-source corruption from mid-flight pauses.

Chunk-level resume (schema v5 — round-9 §6 Part A)
===================================================
The per-chunk extraction state lives on ``source_document_chunk``:
``extraction_status`` (``pending``/``in_progress``/``extracted``/``skipped``),
plus ``extraction_started_at``, ``extraction_finished_at``, and
``extraction_job_id``. The triage loop in
:func:`meridian.extract.triage.run_triage_for_source` is the per-chunk
processing site today: it marks each chunk ``in_progress`` before its Haiku
call, then transitions to ``extracted`` (kept) or ``skipped`` (rejected) on
return. On resume, that same loop skips terminal-state chunks and resets
any orphaned ``in_progress`` chunks back to ``pending`` (with a structured
warning ``triage.chunk.orphan_in_progress``). The downstream Sonnet text-spec
/ bod / demarcation calls aggregate the kept chunks into one prompt — they
benefit indirectly because triage no longer re-pays the per-chunk cost on a
resumed source.

EJS transactional consolidation (round-9 §6 Part B)
====================================================
Per-source finalisation in
:func:`meridian.extract.orchestrator.run_extraction_for_source` now wraps
``persist_parsed_output`` + the EJS ``status='completed'`` flip in a single
``transaction()``. If the orchestrator crashes between persist and EJS-flip,
the transaction rolls back and the source stays in ``extracting``, ready for
``resume_job``. The LLM-call row commits separately (it is referenced via FK
from ``deliverable``/``audit_record`` so must exist beforehand) — re-runs
reuse it via the input_hash dedup path in :mod:`meridian.llm.client`.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import traceback
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from meridian.db.connection import connect, transaction
from meridian.extract.orchestrator import SourceRunResult, run_extraction_for_source

# Marker the parent grep's for in the child's stdout to locate the result line.
RESULT_MARKER = "__MERIDIAN_RESULT__"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _record_worker_pid(db_path: Path, *, job_id: str, source_id: str, pid: int) -> None:
    """Stamp the worker's PID on the EJS row at the start of extraction."""
    # Long-running writer: 30 s busy_timeout — give the SQLite engine plenty
    # of slack to ride out concurrent reviewer/CLI writes (Hazard 2).
    conn = connect(db_path, busy_timeout_ms=30000)
    try:
        with transaction(conn):
            # Insert-or-update: the orchestrator's _attach_source_to_job may not
            # have been called yet if a caller invokes the worker directly. Use
            # the same INSERT OR IGNORE pattern, then UPDATE the pid.
            conn.execute(
                """
                INSERT OR IGNORE INTO extraction_job_source
                  (id, job_id, source_id, status, started_at)
                VALUES (lower(hex(randomblob(16))), ?, ?, 'pending', ?)
                """,
                (job_id, source_id, _now()),
            )
            conn.execute(
                "UPDATE extraction_job_source SET worker_pid = ? "
                "WHERE job_id = ? AND source_id = ?",
                (pid, job_id, source_id),
            )
    finally:
        conn.close()


def _record_failure(db_path: Path, *, job_id: str, source_id: str, error: str) -> None:
    """Mark the EJS row as failed with the given error message."""
    try:
        conn = connect(db_path, busy_timeout_ms=30000)
        try:
            with transaction(conn):
                conn.execute(
                    """
                    UPDATE extraction_job_source
                    SET status = 'failed', finished_at = ?, error_message = ?
                    WHERE job_id = ? AND source_id = ?
                    """,
                    (_now(), error[:500], job_id, source_id),
                )
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — best-effort fail-mark; never re-raise
        pass


def run_extraction_in_subprocess(
    *,
    db_path: Path,
    job_id: str,
    source_id: str,
    timeout_seconds: int = 1800,
) -> SourceRunResult:
    """Spawn the extraction worker as a subprocess; return its result.

    On timeout the subprocess is killed and the EJS row is marked ``failed``
    with an error message naming the timeout. On non-zero exit the parent
    raises :class:`RuntimeError` with the captured stderr/stdout for context;
    the caller in the orchestrator catches it and continues to the next
    source.
    """
    cmd = [
        sys.executable,
        "-m",
        "meridian.workers.extraction_worker",
        "--db",
        str(db_path),
        "--job-id",
        job_id,
        "--source-id",
        source_id,
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            stdout, stderr = proc.communicate(timeout=10)
        except Exception:  # noqa: BLE001
            stdout, stderr = "", ""
        msg = (
            f"worker subprocess timeout after {timeout_seconds}s "
            f"(job={job_id} source={source_id})"
        )
        _record_failure(db_path, job_id=job_id, source_id=source_id, error=msg)
        raise RuntimeError(msg) from None

    if proc.returncode != 0:
        # Child crashed before/around the orchestrator call. The child's
        # __main__ block tries to record `failed` itself, but if it died
        # before that we record one here.
        msg = (
            f"worker subprocess exited with code {proc.returncode} "
            f"(job={job_id} source={source_id}). stderr: {stderr[:500]}"
        )
        _record_failure(db_path, job_id=job_id, source_id=source_id, error=msg)
        raise RuntimeError(msg)

    # Locate the result line. Scan from the end so noisy stdout above doesn't
    # confuse us.
    payload: dict | None = None
    for line in reversed(stdout.splitlines()):
        if line.startswith(RESULT_MARKER):
            payload = json.loads(line[len(RESULT_MARKER) :])
            break
    if payload is None:
        msg = (
            f"worker subprocess produced no result marker on stdout "
            f"(job={job_id} source={source_id}). stdout tail: {stdout[-500:]}"
        )
        _record_failure(db_path, job_id=job_id, source_id=source_id, error=msg)
        raise RuntimeError(msg)

    return SourceRunResult(**payload)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="meridian.workers.extraction_worker")
    parser.add_argument("--db", required=True, type=Path, help="Path to the project SQLite file.")
    parser.add_argument("--job-id", required=True, help="extraction_job.id")
    parser.add_argument("--source-id", required=True, help="source_document.id")
    args = parser.parse_args(argv)

    db_path: Path = args.db
    job_id: str = args.job_id
    source_id: str = args.source_id

    # Each subprocess opens its own connection (CONTEXT.md §6).
    try:
        _record_worker_pid(db_path, job_id=job_id, source_id=source_id, pid=os.getpid())
    except Exception as exc:  # noqa: BLE001
        # If we cannot even stamp the PID, surface the failure but keep going
        # — the orchestrator itself will mark this source failed via the
        # non-zero exit path.
        sys.stderr.write(f"failed to record worker_pid: {exc}\n")

    try:
        # Long-running writer: 30 s busy_timeout (Hazard 2 mitigation).
        conn = connect(db_path, busy_timeout_ms=30000)
        try:
            result = run_extraction_for_source(
                conn, job_id=job_id, source_id=source_id
            )
        finally:
            conn.close()
    except BaseException as exc:  # noqa: BLE001 — last-resort guard
        traceback.print_exc(file=sys.stderr)
        err = f"{type(exc).__name__}: {exc}"
        _record_failure(db_path, job_id=job_id, source_id=source_id, error=err)
        return 1

    sys.stdout.write(RESULT_MARKER + json.dumps(asdict(result)) + "\n")
    sys.stdout.flush()
    return 0


__all__ = [
    "RESULT_MARKER",
    "run_extraction_in_subprocess",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
