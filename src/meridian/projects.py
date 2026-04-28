"""Project lifecycle: create / open per-project SQLite files."""

from __future__ import annotations

import contextlib
import errno
import json
import os
import re
import shutil
import socket
import sqlite3
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from meridian.config import DEFAULT_PURPOSE_ROUTING, settings
from meridian.db.connection import SCHEMA_VERSION, connect, initialise, transaction
from meridian.logging import get_logger
from meridian.taxonomy.defaults import seed

_log = get_logger("meridian.projects")

# app_setting keys for routing config (no schema change required)
_PURPOSE_ROUTING_KEY = "purpose_routing"
_AIR_GAPPED_KEY = "air_gapped"


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower())
    return slug.strip("-") or "project"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def project_db_path(name: str) -> Path:
    return settings.projects_dir / f"{_slugify(name)}.sqlite"


def create_project(
    *,
    name: str,
    notes: str | None = None,
    default_provider: str | None = None,
    default_model: str | None = None,
) -> tuple[str, Path]:
    """Create a new SQLite file, run schema, seed taxonomies, insert project row.

    Returns (project_id, db_path).
    """
    db_path = project_db_path(name)
    if db_path.exists():
        raise FileExistsError(f"Project file already exists: {db_path}")

    # Source defaults from the per-purpose routing table when not explicitly
    # supplied. The project row's default_provider/default_model columns are
    # retained in the schema (locked); we use the extract_text_spec route as
    # the canonical "default" since it's the primary extraction purpose.
    routing_default = DEFAULT_PURPOSE_ROUTING["extract_text_spec"]
    resolved_provider = default_provider or routing_default[0]
    resolved_model = default_model or routing_default[1]

    conn = initialise(db_path)
    try:
        seed(conn)
        project_id = str(uuid.uuid4())
        with transaction(conn):
            conn.execute(
                """
                INSERT INTO project (
                    id, name, created_at, schema_version, app_version,
                    tool_disclaimer_version, default_provider, default_model,
                    notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    name,
                    _now(),
                    SCHEMA_VERSION,
                    settings.app_version,
                    settings.tool_disclaimer_version,
                    resolved_provider,
                    resolved_model,
                    notes,
                ),
            )
        return project_id, db_path
    finally:
        conn.close()


def adopt_project(
    *,
    old_db_path: Path,
    new_db_path: Path,
    new_name: str,
) -> None:
    """Adopt a SQLite project file into a new path under a new name.

    Moves ``old_db_path`` to ``new_db_path`` and rewrites the
    ``project.name`` row inside the moved DB to ``new_name``.

    Pre-conditions:
        - ``new_db_path`` must not exist (refuses to overwrite).
        - ``old_db_path`` must be a closed SQLite file (no live
          connections from other processes / threads). The helper
          checkpoints WAL and switches to rollback-journal mode
          before moving so no ``-wal`` / ``-shm`` sidecars are
          orphaned.

    Post-conditions:
        - ``new_db_path`` exists with the moved data and the new
          ``project.name``.
        - ``old_db_path`` no longer exists.
        - On UPDATE failure, a best-effort rollback moves the file
          back to ``old_db_path`` and the original exception is
          re-raised. If the rollback itself fails, an error is
          logged but the original exception still surfaces.

    Raises:
        FileExistsError: if ``new_db_path`` already exists.
        OSError: if the move fails (e.g. source file in use on
            Windows).

    Used by the wizard's ``setup_create_project`` to adopt the
    import-staging DB into the user's chosen projects_dir + name.
    """
    if new_db_path.exists():
        raise FileExistsError(f"adopt target already exists: {new_db_path}")
    new_db_path.parent.mkdir(parents=True, exist_ok=True)

    # I2: Checkpoint WAL and switch to rollback-journal mode BEFORE moving so
    # no -wal / -shm sidecars are orphaned at the staging path and no unflushed
    # WAL pages are lost. Use contextlib.closing (M3) so the connection is
    # actually closed, not merely committed, before the file move.
    with contextlib.closing(sqlite3.connect(old_db_path)) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode=DELETE")

    shutil.move(str(old_db_path), str(new_db_path))

    # I3: Wrap the post-move UPDATE in try/except so a failure (disk full,
    # permission flip, etc.) triggers a best-effort rollback of the file move.
    # M3: contextlib.closing ensures the connection is actually closed so no
    # WAL/SHM sidecars linger at new_db_path.
    try:
        with contextlib.closing(sqlite3.connect(new_db_path)) as conn:
            conn.execute("UPDATE project SET name = ?", (new_name,))
            conn.commit()
    except Exception:
        # Best-effort rollback so the user isn't stranded with a moved file
        # that has the wrong name. If THIS fails, log loudly so the
        # operator can recover manually.
        try:
            shutil.move(str(new_db_path), str(old_db_path))
        except OSError as rollback_exc:
            _log.error(
                "projects.adopt.rollback_failed",
                old_db_path=str(old_db_path),
                new_db_path=str(new_db_path),
                error=f"{type(rollback_exc).__name__}: {rollback_exc}",
            )
        raise


def open_project(name: str) -> sqlite3.Connection:
    """Open an existing project's SQLite file. Returns a connection."""
    db_path = project_db_path(name)
    if not db_path.exists():
        raise FileNotFoundError(f"Project not found: {db_path}")
    return connect(db_path)


# --------------------------------------------------------------------------
# Project-level routing config (design/PROVIDER_ROUTING_V1.md §3.2)
# --------------------------------------------------------------------------


def get_project_routing(
    conn: sqlite3.Connection,
) -> dict[str, tuple[str, str]] | None:
    """Read per-project purpose routing override (None if unset).

    Returns dict[purpose] -> (provider, model). JSON on disk stores routes as
    [provider, model] arrays; we coerce to tuples for type stability.
    """
    row = conn.execute(
        "SELECT value FROM app_setting WHERE key = ?", (_PURPOSE_ROUTING_KEY,)
    ).fetchone()
    if row is None or not row["value"]:
        return None
    try:
        raw = json.loads(row["value"])
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    out: dict[str, tuple[str, str]] = {}
    for purpose, entry in raw.items():
        if (
            isinstance(entry, (list, tuple))
            and len(entry) == 2
            and all(isinstance(x, str) for x in entry)
        ):
            out[str(purpose)] = (entry[0], entry[1])
    return out or None


def set_project_routing(
    conn: sqlite3.Connection,
    routing: dict[str, tuple[str, str] | list[str]] | None,
) -> None:
    """Write per-project routing. Pass None to clear all overrides."""
    if routing is None:
        with transaction(conn):
            conn.execute(
                "DELETE FROM app_setting WHERE key = ?", (_PURPOSE_ROUTING_KEY,)
            )
        return
    serialisable = {
        str(purpose): [entry[0], entry[1]] for purpose, entry in routing.items()
    }
    with transaction(conn):
        conn.execute(
            """
            INSERT OR REPLACE INTO app_setting (key, value, updated_at)
            VALUES (?, ?, ?)
            """,
            (_PURPOSE_ROUTING_KEY, json.dumps(serialisable), _now()),
        )


def get_air_gapped(conn: sqlite3.Connection) -> bool:
    """Read project-level air-gap flag (False if unset)."""
    row = conn.execute(
        "SELECT value FROM app_setting WHERE key = ?", (_AIR_GAPPED_KEY,)
    ).fetchone()
    if row is None or not row["value"]:
        return False
    val = row["value"].strip().lower()
    return val in {"1", "true", "yes", "on"}


def set_air_gapped(conn: sqlite3.Connection, value: bool) -> None:
    """Write project-level air-gap flag."""
    with transaction(conn):
        conn.execute(
            """
            INSERT OR REPLACE INTO app_setting (key, value, updated_at)
            VALUES (?, ?, ?)
            """,
            (_AIR_GAPPED_KEY, "true" if value else "false", _now()),
        )


# --------------------------------------------------------------------------
# Process-level project lock (round-14 concurrency safe-defaults)
# --------------------------------------------------------------------------
#
# Prevents two Meridian processes from concurrently performing a destructive
# operation on the same project (most notably: two `meridian extract` calls
# clobbering chunk-level state). See docs/concurrency-analysis.md for the
# full audit and rationale.
#
# Lock file lives at `<projects_dir>/<slug>.lock` and contains JSON describing
# the holding process. On acquire we check the holder PID's liveness; if the
# process is gone the lock is taken over (orphan recovery for crashed
# processes). Three-outcome liveness: alive / dead / unknown — see below.

_LOCK_GRACE_SECONDS = 60  # how long to honour an "unknown" liveness lock
_DEFAULT_LOCK_POLL_INTERVAL = 0.5  # seconds between retry attempts when waiting


class ProjectBusy(RuntimeError):  # noqa: N818 — public API name (round-14 spec)
    """Raised when a project lock is already held by another live process.

    The exception message names the holding PID, hostname, purpose, and
    acquisition time so the operator can decide whether to wait or take
    manual action.
    """

    def __init__(
        self,
        slug: str,
        *,
        pid: int,
        hostname: str,
        purpose: str,
        acquired_at: str,
    ) -> None:
        self.slug = slug
        self.pid = pid
        self.hostname = hostname
        self.purpose = purpose
        self.acquired_at = acquired_at
        super().__init__(
            f"Project '{slug}' is locked by PID {pid} on {hostname} "
            f"for {purpose!r} since {acquired_at}."
        )


def _project_lock_path(slug: str) -> Path:
    return settings.projects_dir / f"{_slugify(slug)}.lock"


def _pid_liveness(pid: int) -> str:
    """Return one of 'alive', 'dead', 'unknown' for the given PID.

    Cross-platform notes:

    * POSIX: ``os.kill(pid, 0)`` raises ``ProcessLookupError`` if the PID
      has no process; ``PermissionError`` if a process exists but we lack
      permission to signal it (treat as 'unknown' — could be a different
      user's process). Anything else → 'alive'.
    * Windows: Python's ``os.kill(pid, 0)`` is implemented but its
      semantics are surprising — it can succeed for a recently-exited PID
      whose handle has not yet been recycled. We attempt the call and
      treat success as 'unknown' rather than 'alive' so the grace-window
      logic governs. ``OSError`` with ``errno.EINVAL`` / ``ESRCH`` is
      treated as 'dead'. We deliberately do NOT depend on ``psutil``
      (not in pyproject); operators who want strict Windows liveness can
      install it later and we can add an opt-in.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "dead"
    except PermissionError:
        return "unknown"
    except OSError as exc:
        if exc.errno in (errno.ESRCH,):
            return "dead"
        if exc.errno in (errno.EINVAL, errno.EPERM):
            return "unknown"
        return "unknown"
    # The call succeeded. On POSIX this is reliably 'alive'. On Windows
    # the result is ambiguous (the handle table can survive briefly past
    # process exit); fall back to 'unknown' there so the grace window
    # decides.
    if os.name == "nt":
        return "unknown"
    return "alive"


def _read_lock_payload(lock_path: Path) -> dict | None:
    try:
        raw = lock_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        # Corrupt lock file (e.g. half-written from a crash mid-write).
        # Treat as orphan: callers will overwrite it.
        _log.warning(
            "projects.lock.corrupt_payload",
            path=str(lock_path),
        )
        return None
    if not isinstance(data, dict):
        return None
    return data


def _seconds_since(iso_ts: str) -> float | None:
    """Return seconds elapsed since `iso_ts` (UTC ISO 8601 'Z' format), or None."""
    try:
        if iso_ts.endswith("Z"):
            iso_ts = iso_ts[:-1] + "+00:00"
        dt = datetime.fromisoformat(iso_ts)
    except (ValueError, TypeError):
        return None
    return (datetime.now(UTC) - dt).total_seconds()


class ProjectLock:
    """Context manager wrapping the on-disk project lock.

    Use via :func:`acquire_project_lock`. Exiting the context (normally or
    via exception) deletes the lock file. If the holding process is killed
    hard (SIGKILL, power loss) the file remains on disk; the next
    :func:`acquire_project_lock` call detects the dead PID and takes over.
    """

    def __init__(self, slug: str, *, purpose: str, lock_path: Path) -> None:
        self.slug = slug
        self.purpose = purpose
        self._lock_path = lock_path
        self._held = False

    @property
    def path(self) -> Path:
        return self._lock_path

    def __enter__(self) -> ProjectLock:
        # acquire_project_lock created the file before constructing us;
        # __enter__ is a marker so callers can still use `with` syntax.
        self._held = True
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    def release(self) -> None:
        if not self._held:
            return
        self._held = False
        try:
            self._lock_path.unlink()
        except FileNotFoundError:
            # Already gone — possibly stolen by an orphan-takeover. Log
            # but don't crash the cleanup path.
            _log.warning(
                "projects.lock.release_missing",
                slug=self.slug,
                path=str(self._lock_path),
            )
        except OSError as exc:
            _log.warning(
                "projects.lock.release_failed",
                slug=self.slug,
                path=str(self._lock_path),
                error=f"{type(exc).__name__}: {exc}",
            )


def acquire_project_lock(
    slug: str,
    *,
    purpose: str,
    wait_seconds: int = 0,
) -> ProjectLock:
    """Acquire the process-level project lock for `slug`.

    Args:
        slug: project slug (will be normalised via the same `_slugify`
            used by `project_db_path`).
        purpose: short human-readable label recorded in the lock file
            (e.g. ``"extract"``, ``"backup"``). Surfaced in
            :class:`ProjectBusy` so the operator knows what is holding it.
        wait_seconds: if > 0, retry every 0.5s for up to this many
            seconds before raising :class:`ProjectBusy`. Default 0 =
            fail immediately.

    Returns: :class:`ProjectLock` (already acquired). Use as a context
    manager OR call ``.release()`` explicitly.

    Raises: :class:`ProjectBusy` if the lock is held by a live process
    (or by an unknown-liveness process within the 60-second grace
    window) after the wait period elapses.

    Orphan recovery: if the recorded PID is dead (or unknown but past
    the grace window), a structured warning ``projects.lock.orphan_taken_over``
    is logged and the lock is taken over.
    """
    normalised = _slugify(slug)
    lock_path = _project_lock_path(normalised)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    deadline = time.monotonic() + max(0, wait_seconds)
    while True:
        existing = _read_lock_payload(lock_path)
        if existing is None:
            # File absent OR unreadable/corrupt; safe to (re)create.
            if _try_write_lock(lock_path, purpose=purpose):
                _log.info(
                    "projects.lock.acquired",
                    slug=normalised,
                    purpose=purpose,
                    path=str(lock_path),
                )
                return ProjectLock(
                    normalised, purpose=purpose, lock_path=lock_path
                ).__enter__()
            # Lost the O_EXCL race against another writer that just
            # created the file. Fall through to the wait/retry path.
            if time.monotonic() >= deadline:
                # Re-read so we surface the actual holder, not "?".
                latest = _read_lock_payload(lock_path) or {}
                raise ProjectBusy(
                    normalised,
                    pid=int(latest.get("pid", -1)),
                    hostname=str(latest.get("hostname", "?")),
                    purpose=str(latest.get("purpose", "?")),
                    acquired_at=str(latest.get("acquired_at", "")),
                )
            time.sleep(_DEFAULT_LOCK_POLL_INTERVAL)
            continue

        holder_pid = int(existing.get("pid", -1))
        holder_host = str(existing.get("hostname", "?"))
        holder_purpose = str(existing.get("purpose", "?"))
        holder_ts = str(existing.get("acquired_at", ""))

        liveness = _pid_liveness(holder_pid) if holder_pid > 0 else "dead"
        if liveness == "dead":
            _log.warning(
                "projects.lock.orphan_taken_over",
                slug=normalised,
                holder_pid=holder_pid,
                holder_host=holder_host,
                holder_purpose=holder_purpose,
                holder_acquired_at=holder_ts,
                reason="pid_not_running",
            )
            with contextlib.suppress(FileNotFoundError):
                lock_path.unlink()
            # Immediately retry acquire — the orphan recovery path must
            # not be gated on the wait deadline (we've already determined
            # nobody live owns the lock).
            continue

        if liveness == "unknown":
            age = _seconds_since(holder_ts)
            if age is not None and age > _LOCK_GRACE_SECONDS:
                _log.warning(
                    "projects.lock.orphan_taken_over",
                    slug=normalised,
                    holder_pid=holder_pid,
                    holder_host=holder_host,
                    holder_purpose=holder_purpose,
                    holder_acquired_at=holder_ts,
                    reason="unknown_liveness_past_grace_window",
                    grace_seconds=_LOCK_GRACE_SECONDS,
                    age_seconds=int(age),
                )
                with contextlib.suppress(FileNotFoundError):
                    lock_path.unlink()
                continue
            # Within grace window — respect the lock.
            if time.monotonic() >= deadline:
                raise ProjectBusy(
                    normalised,
                    pid=holder_pid,
                    hostname=holder_host,
                    purpose=holder_purpose,
                    acquired_at=holder_ts,
                )
            time.sleep(_DEFAULT_LOCK_POLL_INTERVAL)
            continue

        # liveness == "alive"
        if time.monotonic() >= deadline:
            raise ProjectBusy(
                normalised,
                pid=holder_pid,
                hostname=holder_host,
                purpose=holder_purpose,
                acquired_at=holder_ts,
            )
        time.sleep(_DEFAULT_LOCK_POLL_INTERVAL)


def _try_write_lock(lock_path: Path, *, purpose: str) -> bool:
    """Attempt to create the lock file exclusively. Returns True on success."""
    payload = json.dumps(
        {
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "purpose": purpose,
            "acquired_at": _now(),
        }
    )
    try:
        # O_EXCL prevents clobbering a concurrent writer.
        fd = os.open(str(lock_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        return False
    except OSError:
        return False
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)
    return True


__all__ = [
    "ProjectBusy",
    "ProjectLock",
    "acquire_project_lock",
    "adopt_project",
    "create_project",
    "get_air_gapped",
    "get_project_routing",
    "open_project",
    "project_db_path",
    "set_air_gapped",
    "set_project_routing",
]
