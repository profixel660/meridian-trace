"""Top-level extraction orchestration: create job, run quality scan + extractor per source."""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from meridian.config import DEFAULT_PURPOSE_ROUTING, settings
from meridian.db.connection import transaction
from meridian.extract.bod_import import extract_bod_payload
from meridian.extract.demarcation import extract_demarcation_payload
from meridian.extract.idempotency import (
    report_skipped_source,
    reviewer_touched_count,
    should_skip_extraction,
)
from meridian.extract.persist import persist_parsed_output
from meridian.extract.quality_scan import run_quality_scan
from meridian.extract.text_spec import extract_text_spec_payload
from meridian.extract.triage import run_triage_for_source
from meridian.logging import get_logger
from meridian.projects import acquire_project_lock

_log = get_logger("meridian.extract.orchestrator")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    return str(uuid.uuid4())


def _slug_from_conn(conn: sqlite3.Connection) -> str | None:
    """Recover the project slug from an open SQLite connection's main file path.

    Used to acquire the project-level lock from entry points that take a
    bare connection (no db_path argument). Returns None if the connection
    is in-memory or otherwise has no on-disk file (in which case the
    caller skips locking — there's no shared resource to protect).
    """
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
    except sqlite3.Error:
        return None
    for row in rows:
        # row schema: seq, name, file
        if row["name"] == "main":
            file_path = row["file"]
            if not file_path:
                return None
            return Path(file_path).stem
    return None


@dataclass
class SourceRunResult:
    source_id: str
    filename: str
    extraction_path: str
    is_template: bool
    counts: dict[str, int] = field(default_factory=dict)
    skipped_reason: str | None = None
    error: str | None = None


@dataclass
class JobRunResult:
    job_id: str
    sources: list[SourceRunResult] = field(default_factory=list)


def create_job(
    conn: sqlite3.Connection,
    *,
    provider: str | None = None,
    model: str | None = None,
    triggered_by: str = "user_initiated",
) -> str:
    """Insert an extraction_job row; return its id.

    The (provider, model) snapshot recorded on the job row is the resolved
    default at job start (per design/PROVIDER_ROUTING_V1.md §8). Per-call
    routing can differ — `llm_call` rows carry the per-call truth.
    """
    if provider is None or model is None:
        default_provider, default_model = DEFAULT_PURPOSE_ROUTING["extract_text_spec"]
        provider = provider or default_provider
        model = model or default_model
    job_id = _new_id()
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO extraction_job (
                id, started_at, status, provider, model,
                prompt_text_spec_version, prompt_bod_version, triggered_by
            ) VALUES (?, ?, 'running', ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                _now(),
                provider,
                model,
                settings.prompt_text_spec_version,
                settings.prompt_bod_version,
                triggered_by,
            ),
        )
    _log.info(
        "extraction.job.start",
        job_id=job_id,
        provider=provider,
        model=model,
        triggered_by=triggered_by,
    )
    return job_id


def finish_job(
    conn: sqlite3.Connection, *, job_id: str, status: str = "completed"
) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE extraction_job SET status = ?, finished_at = ? WHERE id = ?",
            (status, _now(), job_id),
        )
    _log.info("extraction.job.finish", job_id=job_id, status=status)


def _attach_source_to_job(conn: sqlite3.Connection, *, job_id: str, source_id: str) -> str:
    ejs_id = _new_id()
    with transaction(conn):
        conn.execute(
            """
            INSERT OR IGNORE INTO extraction_job_source
              (id, job_id, source_id, status, started_at)
            VALUES (?, ?, ?, 'pending', ?)
            """,
            (ejs_id, job_id, source_id, _now()),
        )
    return ejs_id


def _set_ejs_status(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    source_id: str,
    status: str,
    error: str | None = None,
) -> None:
    with transaction(conn):
        conn.execute(
            """
            UPDATE extraction_job_source
            SET status = ?, finished_at = ?, error_message = ?
            WHERE job_id = ? AND source_id = ?
            """,
            (status, _now() if status in {"completed", "failed", "skipped_unchanged"} else None,
             error, job_id, source_id),
        )


def run_extraction_for_source(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    source_id: str,
    rerun_quality_scan: bool = False,
    force: bool = False,
) -> SourceRunResult:
    """End-to-end: ensure quality scan, dispatch to extractor, persist.

    ``force=False`` (default) consults
    :func:`meridian.extract.idempotency.should_skip_extraction` BEFORE any
    work and returns a skipped :class:`SourceRunResult` if the source has a
    completed prior extraction (CONTEXT.md §13). Pass ``force=True`` to
    bypass the skip; if the prior extraction has reviewer-touched rows, the
    orchestrator emits a stderr warning naming the orphaned counts (the
    underlying rows remain in the DB).
    """
    source_row = conn.execute(
        "SELECT * FROM source_document WHERE id = ?", (source_id,)
    ).fetchone()
    if source_row is None:
        raise ValueError(f"source_document not found: {source_id}")

    _log.info(
        "extraction.source.start",
        job_id=job_id,
        source_id=source_id,
        filename=source_row["filename"],
        extraction_path=source_row["extraction_path"],
        force=force,
    )

    decision = should_skip_extraction(conn, source_id, force=force)
    if decision.should_skip:
        report_skipped_source(
            conn, job_id=job_id, source_id=source_id, reason=decision.reason or ""
        )
        _log.info(
            "extraction.source.skip",
            job_id=job_id,
            source_id=source_id,
            filename=source_row["filename"],
            reason=decision.reason,
        )
        return SourceRunResult(
            source_id=source_id,
            filename=source_row["filename"],
            extraction_path=source_row["extraction_path"],
            is_template=bool(source_row["is_template"]),
            skipped_reason=decision.reason,
        )

    # Force-mode warning: if we are re-extracting a source that has reviewer
    # state, emit a structured warning so the CLI / log readers can surface it.
    if force:
        touched, promoted = reviewer_touched_count(conn, source_id)
        if touched > 0 or promoted > 0:
            _log.warning(
                "extraction.force_warning",
                source_id=source_id,
                filename=source_row["filename"],
                touched_deliverables=touched,
                promoted_audit=promoted,
                job_id=job_id,
            )

    _attach_source_to_job(conn, job_id=job_id, source_id=source_id)

    needs_scan = rerun_quality_scan or source_row["quality_scan_id"] is None
    if needs_scan:
        _set_ejs_status(conn, job_id=job_id, source_id=source_id, status="triaging")
        run_quality_scan(conn, source_id=source_id, job_id=job_id)
        # Re-read after scan to pick up updated extraction_path / template flag.
        source_row = conn.execute(
            "SELECT * FROM source_document WHERE id = ?", (source_id,)
        ).fetchone()

    extraction_path = source_row["extraction_path"]
    is_template = bool(source_row["is_template"])
    is_demarcation = bool(source_row["is_demarcation_schedule"])
    filename = source_row["filename"]
    effective_path = "demarcation" if is_demarcation else extraction_path

    # Templates are always skipped (CONTEXT.md §8). Excluded sources are skipped
    # UNLESS they are demarcation schedules — the quality-scan prompt's
    # extraction_path enum does not include 'demarcation', so the LLM may
    # default a demarcation source to 'excluded'. The is_demarcation_schedule
    # flag is the authoritative dispatch signal in that case.
    if is_template or (extraction_path == "excluded" and not is_demarcation):
        _set_ejs_status(conn, job_id=job_id, source_id=source_id, status="skipped_unchanged")
        _log.info(
            "extraction.source.skip",
            job_id=job_id,
            source_id=source_id,
            filename=filename,
            reason="template_or_excluded",
        )
        return SourceRunResult(
            source_id=source_id,
            filename=filename,
            extraction_path=effective_path,
            is_template=is_template,
            skipped_reason="template_or_excluded",
        )

    # Run Haiku triage (CONTEXT.md §13) on chunked text paths. BOD path skips
    # internally — every BOD row is already a candidate per the disposition rule.
    if extraction_path != "bod_import":
        try:
            run_triage_for_source(conn, source_id=source_id, job_id=job_id)
        except RuntimeError as exc:
            # Triage is best-effort; if it fails, continue with full text.
            # The chunks remain un-triaged → assemble_triaged_text returns full content.
            _set_ejs_status(
                conn,
                job_id=job_id,
                source_id=source_id,
                status="extracting",
                error=f"triage failed; continuing with full text: {exc}"[:500],
            )

    _set_ejs_status(conn, job_id=job_id, source_id=source_id, status="extracting")
    # Round-9 §6 Part B — EJS transactional consolidation.
    # The LLM call commits inside call_llm (its row is referenced by FK from
    # deliverable / audit_record, so it must exist before persist runs).
    # We then wrap (persist + EJS state-flip) in a SINGLE transaction so a
    # crash between them cannot leave the source in a torn-write state where
    # deliverable rows exist but EJS still says 'extracting'.
    try:
        if is_demarcation:
            parsed, llm_call_id = extract_demarcation_payload(
                conn, source_id=source_id, extraction_job_id=job_id
            )
        elif extraction_path == "bod_import":
            parsed, llm_call_id = extract_bod_payload(
                conn, source_id=source_id, extraction_job_id=job_id
            )
        else:
            # Drawing path falls through to text_spec for v1; flagged in CONTEXT.
            parsed, llm_call_id = extract_text_spec_payload(
                conn, source_id=source_id, extraction_job_id=job_id
            )
    except Exception as exc:  # noqa: BLE001 — record error per source, continue job
        err = f"{type(exc).__name__}: {exc}"
        _set_ejs_status(conn, job_id=job_id, source_id=source_id, status="failed", error=err)
        _log.error(
            "extraction.source.fail",
            job_id=job_id,
            source_id=source_id,
            filename=filename,
            extraction_path=effective_path,
            error=err,
        )
        return SourceRunResult(
            source_id=source_id,
            filename=filename,
            extraction_path=effective_path,
            is_template=is_template,
            error=err,
        )

    # ATOMIC SOURCE-COMPLETION BOUNDARY: persist + EJS=completed in one txn.
    try:
        with transaction(conn):
            counts = persist_parsed_output(
                conn,
                parsed=parsed,
                source_id=source_id,
                extraction_job_id=job_id,
                llm_call_id=llm_call_id,
                in_transaction=True,
            )
            now = _now()
            conn.execute(
                """
                UPDATE extraction_job_source
                SET status = 'completed',
                    finished_at = ?,
                    error_message = NULL
                WHERE job_id = ? AND source_id = ?
                """,
                (now, job_id, source_id),
            )
    except Exception as exc:  # noqa: BLE001 — torn persist; mark failed and surface
        err = f"persist_or_finalise_failed: {type(exc).__name__}: {exc}"
        _set_ejs_status(conn, job_id=job_id, source_id=source_id, status="failed", error=err)
        _log.error(
            "extraction.source.persist_fail",
            job_id=job_id,
            source_id=source_id,
            filename=filename,
            extraction_path=effective_path,
            error=err,
        )
        return SourceRunResult(
            source_id=source_id,
            filename=filename,
            extraction_path=effective_path,
            is_template=is_template,
            error=err,
        )

    _log.info(
        "extraction.source.committed",
        job_id=job_id,
        source_id=source_id,
        filename=filename,
        extraction_path=effective_path,
        llm_call_id=llm_call_id,
        counts=counts,
    )
    # The previous _set_ejs_status(status='completed') call has been folded
    # into the atomic persist+finalise transaction above. Keep the existing
    # finish log line for downstream tooling that grep's for it.
    _log.info(
        "extraction.source.finish",
        job_id=job_id,
        source_id=source_id,
        filename=filename,
        extraction_path=effective_path,
        counts=counts,
    )
    return SourceRunResult(
        source_id=source_id,
        filename=filename,
        extraction_path=effective_path,
        is_template=is_template,
        counts=counts,
    )


def run_job_over_sources(
    conn: sqlite3.Connection,
    *,
    source_ids: list[str],
    provider: str | None = None,
    model: str | None = None,
    force: bool = False,
) -> JobRunResult:
    """Convenience: create a job, iterate sources, finish.

    ``force=False`` (default) honours per-source idempotency
    (CONTEXT.md §13): each source consults
    :func:`meridian.extract.idempotency.should_skip_extraction`.

    Concurrency: holds the project-level lock for the entire run (see
    ``meridian.projects.acquire_project_lock`` and
    ``docs/concurrency-analysis.md`` hazard 1). Raises
    :class:`meridian.projects.ProjectBusy` if another extract/backup is
    already in flight on the same project. In-memory connections (no
    on-disk slug) skip locking — they have no shared resource.
    """
    slug = _slug_from_conn(conn)
    lock_cm = (
        acquire_project_lock(slug, purpose="extract") if slug else None
    )
    try:
        job_id = create_job(conn, provider=provider, model=model)
        result = JobRunResult(job_id=job_id)
        final_status = "completed"
        try:
            for source_id in source_ids:
                result.sources.append(
                    run_extraction_for_source(
                        conn, job_id=job_id, source_id=source_id, force=force
                    )
                )
            if any(s.error for s in result.sources):
                final_status = "completed"  # job ran; per-source errors recorded on EJS
        finally:
            finish_job(conn, job_id=job_id, status=final_status)
        return result
    finally:
        if lock_cm is not None:
            lock_cm.release()


# --------------------------------------------------------------------------
# Subprocess-isolated path (CONTEXT.md §6 — worker model lock)
# --------------------------------------------------------------------------
#
# `run_job_over_sources_isolated` is the production extraction entry point:
# it spawns a child subprocess per source via the extraction_worker module.
# `run_job_over_sources` (above) remains the in-process path used by tests
# and by the conflict pass, which doesn't need the crash-isolation guarantee.


def _set_job_status(conn: sqlite3.Connection, *, job_id: str, status: str) -> None:
    finished = _now() if status in {"completed", "failed", "cancelled"} else None
    with transaction(conn):
        if finished is not None:
            conn.execute(
                "UPDATE extraction_job SET status = ?, finished_at = ? WHERE id = ?",
                (status, finished, job_id),
            )
        else:
            conn.execute(
                "UPDATE extraction_job SET status = ? WHERE id = ?",
                (status, job_id),
            )


def run_job_over_sources_isolated(
    conn: sqlite3.Connection,
    *,
    db_path: Path,
    source_ids: list[str],
    provider: str | None = None,
    model: str | None = None,
    force: bool = False,
    stop_signal_path: Path | None = None,
    timeout_seconds: int = 1800,
    job_id: str | None = None,
) -> JobRunResult:
    """Run an extraction job with one subprocess per source.

    For each source the orchestrator:

    1. Consults :func:`should_skip_extraction` (CONTEXT.md §13). If the
       source has a completed prior extraction and ``force=False``, the
       skip is recorded against this job's audit trail and the worker is
       NEVER spawned.
    2. Calls :func:`_attach_source_to_job` so the EJS row exists before the
       child boots (the child also tolerates a missing row).
    3. Spawns the worker via :func:`run_extraction_in_subprocess`. The
       worker itself does not re-run the idempotency check; the
       orchestrator already gated dispatch.
    4. If the worker raises (subprocess crash beyond its own error handling),
       records the EJS row as ``failed`` and continues to the next source.

    Between sources, if ``stop_signal_path`` exists, the orchestrator marks
    the job as ``paused`` and returns early. The child worker itself does
    NOT poll the signal — pause is granular at the source boundary so an
    in-flight extraction is never interrupted mid-LLM-call.
    """
    # Local import to avoid a circular dependency: extraction_worker imports
    # from this module.
    from meridian.workers.extraction_worker import run_extraction_in_subprocess

    # Project-level lock — prevents two extractions on the same project.
    # Derive slug from the on-disk db_path. See docs/concurrency-analysis.md.
    lock_cm = acquire_project_lock(db_path.stem, purpose="extract")

    if job_id is None:
        job_id = create_job(conn, provider=provider, model=model)
    result = JobRunResult(job_id=job_id)
    paused = False
    try:
        for source_id in source_ids:
            if stop_signal_path is not None and stop_signal_path.exists():
                paused = True
                break

            # Idempotency check runs in the orchestrator BEFORE dispatch so
            # skipped sources never spawn a worker subprocess.
            decision = should_skip_extraction(conn, source_id, force=force)
            if decision.should_skip:
                report_skipped_source(
                    conn,
                    job_id=job_id,
                    source_id=source_id,
                    reason=decision.reason or "",
                )
                src_row = conn.execute(
                    "SELECT filename, extraction_path, is_template "
                    "FROM source_document WHERE id = ?",
                    (source_id,),
                ).fetchone()
                result.sources.append(
                    SourceRunResult(
                        source_id=source_id,
                        filename=src_row["filename"] if src_row else source_id,
                        extraction_path=(src_row["extraction_path"] if src_row else "") or "",
                        is_template=bool(src_row["is_template"]) if src_row else False,
                        skipped_reason=decision.reason,
                    )
                )
                continue

            # Force-mode warning (mirrors the in-process path).
            if force:
                touched, promoted = reviewer_touched_count(conn, source_id)
                if touched > 0 or promoted > 0:
                    src_row = conn.execute(
                        "SELECT filename FROM source_document WHERE id = ?",
                        (source_id,),
                    ).fetchone()
                    fname = src_row["filename"] if src_row else source_id
                    _log.warning(
                        "extraction.force_warning",
                        source_id=source_id,
                        filename=fname,
                        touched_deliverables=touched,
                        promoted_audit=promoted,
                        job_id=job_id,
                    )

            _attach_source_to_job(conn, job_id=job_id, source_id=source_id)
            try:
                src_result = run_extraction_in_subprocess(
                    db_path=db_path,
                    job_id=job_id,
                    source_id=source_id,
                    timeout_seconds=timeout_seconds,
                )
            except Exception as exc:  # noqa: BLE001 — record + continue
                err = f"{type(exc).__name__}: {exc}"
                _set_ejs_status(
                    conn, job_id=job_id, source_id=source_id, status="failed", error=err
                )
                # Look up the source filename for the result row.
                row = conn.execute(
                    "SELECT filename, extraction_path FROM source_document WHERE id = ?",
                    (source_id,),
                ).fetchone()
                result.sources.append(
                    SourceRunResult(
                        source_id=source_id,
                        filename=row["filename"] if row else source_id,
                        extraction_path=(row["extraction_path"] if row else "") or "",
                        is_template=False,
                        error=err,
                    )
                )
                continue
            result.sources.append(src_result)
    finally:
        if paused:
            _set_job_status(conn, job_id=job_id, status="paused")
        else:
            finish_job(conn, job_id=job_id, status="completed")
        lock_cm.release()
    return result


# Statuses that indicate "not yet finished" — eligible for resume.
_RESUMABLE_EJS_STATUSES = ("pending", "failed", "triaging", "extracting")


def resume_job(
    conn: sqlite3.Connection,
    *,
    db_path: Path,
    job_id: str,
    stop_signal_path: Path | None = None,
    timeout_seconds: int = 1800,
) -> JobRunResult:
    """Resume a paused or partially-completed extraction job.

    Selects every ``extraction_job_source`` row not in
    (``completed``, ``skipped_unchanged``) and re-attaches each via the
    isolated worker loop. Flips the parent job row from ``paused`` back to
    ``running`` for the duration.

    Idempotency note (CONTEXT.md §13): ``resume_job`` deliberately bypasses
    the normal "skip already-extracted sources" check by passing
    ``force=True`` into the isolated loop. The user is explicitly asking to
    rerun what was interrupted; the skip check would otherwise treat any
    source that had been marked ``completed`` mid-resume as already done
    and silently drop it.

    Chunk-level resume (schema v5):
        Within a partially-completed source, the triage loop in
        :func:`meridian.extract.triage.run_triage_for_source` consults
        ``source_document_chunk.extraction_status`` and skips chunks already
        in {``extracted``, ``skipped``}. Chunks left in ``in_progress`` from a
        prior crash are reset to ``pending`` and logged as
        ``triage.chunk.orphan_in_progress``. This means a rerun resumes at the
        next un-extracted chunk rather than restarting at chunk 0.
    """
    rows = conn.execute(
        f"""
        SELECT source_id FROM extraction_job_source
        WHERE job_id = ? AND status IN ({",".join("?" * len(_RESUMABLE_EJS_STATUSES))})
        ORDER BY started_at, source_id
        """,  # noqa: S608 — placeholders only
        (job_id, *_RESUMABLE_EJS_STATUSES),
    ).fetchall()
    source_ids = [r["source_id"] for r in rows]

    # Flip the job back to running; preserve job_id so the caller sees the
    # same id throughout.
    _set_job_status(conn, job_id=job_id, status="running")

    if not source_ids:
        # Nothing to do — mark completed and return an empty result.
        finish_job(conn, job_id=job_id, status="completed")
        return JobRunResult(job_id=job_id)

    return run_job_over_sources_isolated(
        conn,
        db_path=db_path,
        source_ids=source_ids,
        force=True,  # resume_job is an explicit rerun; bypass idempotency
        stop_signal_path=stop_signal_path,
        timeout_seconds=timeout_seconds,
        job_id=job_id,
    )


__all__ = [
    "JobRunResult",
    "SourceRunResult",
    "create_job",
    "finish_job",
    "resume_job",
    "run_extraction_for_source",
    "run_job_over_sources",
    "run_job_over_sources_isolated",
]
