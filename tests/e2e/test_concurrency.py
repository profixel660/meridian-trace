"""Concurrency safe-default tests for the project lock primitive.

Covers the round-14 ``meridian.projects.acquire_project_lock`` primitive
introduced as the minimal-safe-default for hazard 1 in
``docs/concurrency-analysis.md`` (two extractions on the same project
clobber each other's chunk-level state).

These tests exercise the lock directly — no LLM calls are made and no
extraction is run. Concurrency is simulated by manipulating the on-disk
lock file (the JSON payload includes a PID; we write a stale one to
simulate an orphaned lock from a crashed process).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest

from meridian.projects import (
    ProjectBusy,
    ProjectLock,
    acquire_project_lock,
)


def _expected_lock_path(projects_dir: Path, slug: str) -> Path:
    return projects_dir / f"{slug}.lock"


def test_two_locks_on_same_project_block(tmp_projects_dir: Path) -> None:
    """Acquiring the lock twice for the same slug must raise ProjectBusy.

    The first acquire writes the lock file with this process's PID; the
    second acquire sees the live PID and refuses (no wait).
    """
    slug = "concurrency-block"
    first = acquire_project_lock(slug, purpose="extract")
    try:
        assert isinstance(first, ProjectLock)
        lock_path = _expected_lock_path(tmp_projects_dir, slug)
        assert lock_path.exists(), "first acquire must materialise the lock file"

        # Verify the JSON payload looks right.
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        assert payload["pid"] == os.getpid()
        assert payload["purpose"] == "extract"
        assert "hostname" in payload
        assert "acquired_at" in payload

        # Second acquire MUST raise — same PID is alive (this very process).
        with pytest.raises(ProjectBusy) as excinfo:
            acquire_project_lock(slug, purpose="extract")
        assert excinfo.value.slug == slug
        assert excinfo.value.pid == os.getpid()
        assert excinfo.value.purpose == "extract"
    finally:
        first.release()
        # File must be cleaned up on release.
        assert not _expected_lock_path(tmp_projects_dir, slug).exists()


def test_lock_released_on_context_exit(tmp_projects_dir: Path) -> None:
    """Using the lock as a context manager must release on normal exit
    AND on exceptional exit, allowing immediate re-acquisition."""
    slug = "concurrency-context"
    lock_path = _expected_lock_path(tmp_projects_dir, slug)

    # Normal exit path.
    with acquire_project_lock(slug, purpose="extract"):
        assert lock_path.exists()
    assert not lock_path.exists(), "context manager must delete lock on exit"

    # Re-acquire immediately — should succeed.
    with acquire_project_lock(slug, purpose="extract"):
        assert lock_path.exists()
    assert not lock_path.exists()

    # Exceptional-exit path: lock must still be released.
    class _BoomError(RuntimeError):
        pass

    with pytest.raises(_BoomError), acquire_project_lock(slug, purpose="extract"):
        assert lock_path.exists()
        raise _BoomError("simulated failure mid-work")
    assert not lock_path.exists(), (
        "context manager must release lock even when work raises"
    )

    # Re-acquire after the exception — should succeed.
    third = acquire_project_lock(slug, purpose="extract")
    try:
        assert lock_path.exists()
    finally:
        third.release()


def _find_unused_pid() -> int:
    """Return a PID that is highly unlikely to belong to a live process.

    Uses a value above the typical Linux /proc/sys/kernel/pid_max ceiling
    (4194304) and well past the Windows 32-bit PID range that Python's
    `os.kill` actually checks. ``os.kill(pid, 0)`` against this value
    raises ProcessLookupError on POSIX, which the lock module reads as
    'dead'.
    """
    return 9_999_999


def test_orphan_lock_taken_over(
    tmp_projects_dir: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An on-disk lock file naming a non-existent PID must be treated as
    orphaned; the next acquire takes it over and emits a structured warning.

    Simulates the crash-during-extraction scenario: a previous process
    held the lock, was SIGKILL'd, and the file was never deleted.

    On Windows the synthetic high PID returns 'unknown' liveness rather
    than 'dead' (Python's ``os.kill(pid, 0)`` semantics differ); the
    grace-window degrade-to-orphan path then fires because the
    ``acquired_at`` timestamp is well in the past. Either path emits
    the same ``projects.lock.orphan_taken_over`` event.
    """
    slug = "concurrency-orphan"
    lock_path = _expected_lock_path(tmp_projects_dir, slug)

    # Forge a stale lock file with a clearly-dead PID.
    stale_pid = _find_unused_pid()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(
        json.dumps(
            {
                "pid": stale_pid,
                "hostname": "dead-host",
                "purpose": "extract",
                "acquired_at": "2024-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    # Capture stdlib log output too — depending on test ordering,
    # configure_logging may have wired structlog through the stdlib root
    # logger (file mode used by other e2e tests) instead of stdout
    # (interactive mode used when this test runs first).
    caplog.set_level(logging.WARNING, logger="meridian.projects")

    # Acquire MUST succeed (orphan takeover) and the log MUST record it.
    new_lock = acquire_project_lock(slug, purpose="extract")
    try:
        assert lock_path.exists(), "successful acquire must (re)write the lock file"
        # Payload should now reflect THIS process.
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        assert payload["pid"] == os.getpid()
        assert payload["pid"] != stale_pid

        # The structured-logging pipeline routes through either structlog's
        # console renderer (stdout) or the stdlib logger (caplog),
        # depending on which configure_logging() call ran first in the
        # test session. Accept either source.
        captured = capsys.readouterr()
        combined_streams = captured.out + captured.err
        combined_caplog = "\n".join(rec.getMessage() for rec in caplog.records)
        all_log_output = combined_streams + "\n" + combined_caplog
        assert "projects.lock.orphan_taken_over" in all_log_output, (
            "expected projects.lock.orphan_taken_over warning; "
            f"stdout={captured.out!r} stderr={captured.err!r} "
            f"caplog={combined_caplog!r}"
        )
    finally:
        new_lock.release()
    assert not lock_path.exists()


def test_different_slugs_do_not_block_each_other(tmp_projects_dir: Path) -> None:
    """The lock is per-slug — locking project A must not affect project B."""
    a = acquire_project_lock("project-alpha", purpose="extract")
    try:
        b = acquire_project_lock("project-bravo", purpose="extract")
        try:
            assert _expected_lock_path(tmp_projects_dir, "project-alpha").exists()
            assert _expected_lock_path(tmp_projects_dir, "project-bravo").exists()
        finally:
            b.release()
    finally:
        a.release()
