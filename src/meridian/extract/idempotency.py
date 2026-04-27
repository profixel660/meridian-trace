"""Re-run idempotency for the extraction orchestrator.

CONTEXT.md §13 ("Skip re-processing of unchanged sources on re-run") locks
the requirement that, by default, a re-run of ``meridian extract`` must NOT
re-process sources whose content_hash has not changed and whose prior
extraction completed successfully. This protects reviewer state on the
``deliverable`` and ``audit_record`` tables — once a PM has touched a
deliverable, a silent re-extract that produces fresh rows would orphan that
work.

The decision logic lives here so it can be invoked from both the in-process
path (``run_job_over_sources``) and the subprocess-isolated path
(``run_job_over_sources_isolated``) BEFORE a worker is spawned. Skipped
sources never spawn a worker.

Force-mode is an explicit override (``force=True``). When used and the prior
extraction has reviewer-touched rows, the orchestrator emits a warning so
the user understands that historical reviewer state is being orphaned (the
underlying rows are preserved in the DB; the new extraction simply runs
alongside).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from meridian.logging import get_logger

_log = get_logger("meridian.extract.idempotency")


@dataclass
class SkipDecision:
    should_skip: bool
    reason: str | None  # e.g. 'already_extracted_unchanged', 'never_extracted', ...


# EJS statuses that count as a "real" prior run for idempotency purposes.
_TERMINAL_OK_STATUSES = ("completed", "skipped_unchanged")

# Deliverable.status values that indicate a reviewer has touched the row.
_REVIEWER_TOUCHED_STATUSES = (
    "user_accepted",
    "user_edited",
    "user_rejected",
    "user_promoted",
)


def should_skip_extraction(
    conn: sqlite3.Connection, source_id: str, *, force: bool = False
) -> SkipDecision:
    """Decide whether the orchestrator should skip extraction for ``source_id``.

    Decision tree:

    1. ``force=True`` → re-extract (reason ``'forced'``).
    2. Source row missing → ``ValueError``.
    3. No prior ``extraction_job_source`` row → re-extract
       (reason ``'never_extracted'``).
    4. Most-recent prior EJS status is not in
       ``{'completed','skipped_unchanged'}`` → re-extract
       (reason ``'prior_extraction_incomplete'``); the prior run failed
       mid-way and is safe to retry.
    5. The prior completed job produced ZERO ``deliverable`` rows AND zero
       ``audit_record`` rows for this source → SKIP with
       reason ``'prior_extraction_empty'``. This is the v1 proxy for
       "extraction_path_changed" — the schema does not record the
       extraction_path used at extraction time, so an empty-result prior is
       treated as "a real run that produced nothing; don't silently retry by
       default" (the user can still pass ``force=True`` to re-extract).
    6. Otherwise → SKIP with reason ``'already_extracted_unchanged'``.

    The function is read-only; it does not mutate the DB.
    """
    if force:
        return SkipDecision(False, "forced")

    src = conn.execute(
        "SELECT id FROM source_document WHERE id = ?", (source_id,)
    ).fetchone()
    if src is None:
        raise ValueError(f"source_document not found: {source_id}")

    prior = conn.execute(
        """
        SELECT job_id, status
        FROM extraction_job_source
        WHERE source_id = ?
        ORDER BY started_at DESC, id DESC
        LIMIT 1
        """,
        (source_id,),
    ).fetchone()

    if prior is None:
        return SkipDecision(False, "never_extracted")

    if prior["status"] not in _TERMINAL_OK_STATUSES:
        return SkipDecision(False, "prior_extraction_incomplete")

    # Proxy for "extraction_path_changed": did the prior completed job
    # actually persist anything for this source? If neither a deliverable nor
    # an audit_record exists from that job, treat the prior run as
    # ``prior_extraction_empty`` — still a skip, but reported with a distinct
    # reason so the caller can surface it.
    prior_job_id = prior["job_id"]
    deliv = conn.execute(
        "SELECT 1 FROM deliverable WHERE source_id = ? AND extraction_job_id = ? LIMIT 1",
        (source_id, prior_job_id),
    ).fetchone()
    audit = conn.execute(
        "SELECT 1 FROM audit_record WHERE source_id = ? AND extraction_job_id = ? LIMIT 1",
        (source_id, prior_job_id),
    ).fetchone()
    if deliv is None and audit is None:
        return SkipDecision(True, "prior_extraction_empty")

    return SkipDecision(True, "already_extracted_unchanged")


def report_skipped_source(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    source_id: str,
    reason: str,
) -> None:
    """Insert an EJS row recording the skip in the new job's audit trail.

    Uses ``status='skipped_unchanged'`` and stuffs the human-readable reason
    into ``error_message`` so the audit trail explicitly shows WHY this
    source was skipped on this run. ``INSERT OR IGNORE`` so a pre-existing
    row (e.g. if the orchestrator already ``_attach_source_to_job``-ed)
    doesn't trip the UNIQUE(job_id, source_id) constraint; we then update
    that row to the skip state.
    """
    import uuid
    from datetime import UTC, datetime

    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    ejs_id = str(uuid.uuid4())

    # Use a single transaction so the row exists in skipped state atomically.
    conn.execute("BEGIN")
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO extraction_job_source
              (id, job_id, source_id, status, started_at, finished_at, error_message)
            VALUES (?, ?, ?, 'skipped_unchanged', ?, ?, ?)
            """,
            (ejs_id, job_id, source_id, now, now, f"skipped: {reason}"),
        )
        conn.execute(
            """
            UPDATE extraction_job_source
            SET status = 'skipped_unchanged',
                started_at = COALESCE(started_at, ?),
                finished_at = ?,
                error_message = ?
            WHERE job_id = ? AND source_id = ?
            """,
            (now, now, f"skipped: {reason}", job_id, source_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    _log.info(
        "extraction.idempotency.skip_recorded",
        job_id=job_id,
        source_id=source_id,
        reason=reason,
    )


def reviewer_touched_count(
    conn: sqlite3.Connection, source_id: str
) -> tuple[int, int]:
    """Return ``(touched_deliverables, promoted_audit)`` for ``source_id``.

    * ``touched_deliverables``: count of ``deliverable`` rows for this source
      whose ``status`` indicates a reviewer interaction (``user_accepted``,
      ``user_edited``, ``user_rejected``, ``user_promoted``).
    * ``promoted_audit``: count of ``audit_record`` rows for this source
      where ``user_promoted_to_deliverable_id IS NOT NULL`` — a reviewer
      promoted an OUTSIDE row back into the master register.

    Used by the orchestrator to format the force-mode warning. Read-only.
    """
    placeholders = ",".join("?" * len(_REVIEWER_TOUCHED_STATUSES))
    deliv_row = conn.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM deliverable
        WHERE source_id = ? AND status IN ({placeholders})
        """,  # noqa: S608 — placeholders only
        (source_id, *_REVIEWER_TOUCHED_STATUSES),
    ).fetchone()
    audit_row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM audit_record
        WHERE source_id = ? AND user_promoted_to_deliverable_id IS NOT NULL
        """,
        (source_id,),
    ).fetchone()
    return int(deliv_row["n"]), int(audit_row["n"])


__all__ = [
    "SkipDecision",
    "report_skipped_source",
    "reviewer_touched_count",
    "should_skip_extraction",
]
