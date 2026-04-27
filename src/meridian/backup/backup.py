"""Backup/restore for a Meridian project's full on-disk footprint.

A project's footprint is::

    <projects_dir>/<slug>.sqlite          (the per-project DB)
    <projects_dir>/<slug>.logs/           (structured log JSONLs)
    <projects_dir>/<slug>.tenders/        (generated tender packages)
    <projects_dir>/<slug>.evidence/       (legal evidence packs)
    <projects_dir>/<slug>.reports/        (analytics / management reports)

A backup zips all of the above into a single archive with a top-level
``BACKUP_MANIFEST.json`` that carries a SHA-256 + size for every file. A
restore verifies every hash *before* writing anything, then either restores
to the same slug or to a new slug (clone-style).

SQLite safety
-------------
The SQLite file is captured via :meth:`sqlite3.Connection.backup` — the
*online backup API*. This is the standard, lock-safe pattern: it streams a
consistent snapshot even while the user is mid-extraction. We deliberately
avoid raw file copy because the WAL/-wal/-shm sidecar files would not be
in a consistent state.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import sqlite3
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from meridian import __version__ as _meridian_version
from meridian.config import settings
from meridian.db.connection import SCHEMA_VERSION, initialise
from meridian.logging import get_logger
from meridian.projects import _slugify, project_db_path  # internal-but-stable

_log = get_logger("meridian.backup")

# Bumped when the on-disk backup layout/manifest schema changes. Independent
# of the project DB schema_version.
BACKUP_SCHEMA_VERSION = 1

# Sibling directories captured alongside the SQLite file.
SIBLING_DIR_SUFFIXES: tuple[str, ...] = (
    ".logs",
    ".tenders",
    ".evidence",
    ".reports",
)

# Conflict policies for restore.
_CONFLICT_REFUSE = "refuse"
_CONFLICT_OVERWRITE = "overwrite"
_CONFLICT_MERGE = "merge"
_CONFLICT_POLICIES = frozenset({_CONFLICT_REFUSE, _CONFLICT_OVERWRITE, _CONFLICT_MERGE})


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class BackupCorrupt(Exception):  # noqa: N818  (public name kept for spec compatibility)
    """Raised when SHA-256 verification of a backup zip fails.

    The ``failed_files`` attribute lists the entries (manifest name → reason)
    so callers can present a precise diagnosis.
    """

    def __init__(self, message: str, failed_files: list[str] | None = None) -> None:
        super().__init__(message)
        self.failed_files: list[str] = list(failed_files or [])


class BackupConflict(Exception):  # noqa: N818  (public name kept for spec compatibility)
    """Raised on restore when the target slug already exists and policy is 'refuse'."""


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class BackupResult:
    """Outcome of a single :func:`create_backup` call."""

    project: str
    output_path: Path
    file_count: int
    total_bytes: int
    generated_at: str
    schema_version: int


@dataclass
class RestoreResult:
    """Outcome of a single :func:`restore_backup` call."""

    project: str
    restored_to: Path
    file_count: int
    schema_version_at_backup: int
    schema_version_after_restore: int
    conflicts_resolved: list[str] = field(default_factory=list)


@dataclass
class VerifyResult:
    """Outcome of a single :func:`verify_backup` call.

    Three-outcome discipline:
        * ``status == "valid"``    — every manifest file matches and no extras.
        * ``status == "invalid"``  — one or more SHA-256 mismatches.
        * ``status == "incomplete"`` — manifest references files not in the
          zip, or the zip carries files not in the manifest.
    """

    backup_path: Path
    status: str  # "valid" | "invalid" | "incomplete"
    manifest: dict[str, Any]
    file_results: list[dict[str, Any]] = field(default_factory=list)
    missing_files: list[str] = field(default_factory=list)
    extra_files: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "valid"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_stamp_for_filename() -> str:
    # `:` is illegal in Windows filenames; collapse separators for a
    # filesystem-safe stamp (mirrors evidence/builder.py behaviour).
    return _now_utc().replace(":", "").replace("-", "")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Stream a file through sha256 — keeps memory bounded for large packs."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def _zip_member_sha256(zf: zipfile.ZipFile, name: str, *, chunk_size: int = 1024 * 1024) -> str:
    """Stream a zip member through sha256 without loading it all into RAM."""
    h = hashlib.sha256()
    with zf.open(name, "r") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _online_sqlite_backup(src_db: Path, dest_db: Path) -> None:
    """Copy ``src_db`` to ``dest_db`` via the online-backup API.

    This is safe even if another process is mid-write (WAL mode + the
    backup API guarantees a consistent snapshot). We deliberately do NOT
    use shutil.copy — that would race against the WAL/-shm sidecars.
    """
    src_conn = sqlite3.connect(src_db)
    try:
        # `connect(dest_db)` will create the file if absent.
        dest_conn = sqlite3.connect(dest_db)
        try:
            # `pages=-1` → copy all pages in one batch; safe for typical
            # project DBs (tens of MB). For very large DBs the SQLite C
            # backend chunks internally.
            src_conn.backup(dest_conn, pages=-1, progress=None)
        finally:
            dest_conn.close()
    finally:
        src_conn.close()


def _iter_files(root: Path) -> list[Path]:
    """Recursively list every regular file under ``root`` (sorted, stable)."""
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file())


def _arcname_for_sibling(project_root: Path, file_path: Path) -> str:
    """Compute the in-zip path for a sibling-dir file.

    The arcname is relative to ``project_root``, using forward slashes
    (zip convention) so cross-platform restore is deterministic.
    """
    rel = file_path.relative_to(project_root)
    return rel.as_posix()


def _read_manifest(zf: zipfile.ZipFile) -> dict[str, Any]:
    try:
        raw = zf.read("BACKUP_MANIFEST.json")
    except KeyError as exc:
        raise BackupCorrupt(
            "Backup is missing BACKUP_MANIFEST.json — not a Meridian project backup."
        ) from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BackupCorrupt(
            f"BACKUP_MANIFEST.json is not valid JSON: {exc}"
        ) from exc


def _validate_slug(slug: str) -> str:
    """Reject anything that wouldn't round-trip through ``_slugify``.

    Defensive: prevents path traversal via a malicious manifest
    (``"../etc/passwd"``) or a typo'd ``--as`` argument.
    """
    cleaned = _slugify(slug)
    if cleaned != slug:
        raise ValueError(
            f"Slug {slug!r} is not in canonical form. Use {cleaned!r} instead."
        )
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", cleaned):
        raise ValueError(f"Slug {slug!r} contains illegal characters.")
    return cleaned


def _project_footprint(slug: str) -> tuple[Path, list[Path]]:
    """Return ``(db_path, [existing sibling dirs])`` for ``slug``."""
    db_path = settings.projects_dir / f"{slug}.sqlite"
    siblings = [
        settings.projects_dir / f"{slug}{suffix}"
        for suffix in SIBLING_DIR_SUFFIXES
    ]
    existing_siblings = [d for d in siblings if d.exists() and d.is_dir()]
    return db_path, existing_siblings


# ---------------------------------------------------------------------------
# create_backup
# ---------------------------------------------------------------------------


def create_backup(
    project_slug: str,
    *,
    output_dir: Path | None = None,
) -> BackupResult:
    """Build a backup zip for ``project_slug``.

    Captures the per-project SQLite file (via the online-backup API) plus
    every sibling artefact directory that exists. Missing sibling dirs are
    skipped silently — having no tender packages, for instance, is a valid
    project state.

    Parameters
    ----------
    project_slug
        Project name (slugified to find the SQLite file).
    output_dir
        Destination directory for the zip. Defaults to
        ``<projects_dir>/<slug>.backups/``.

    Returns
    -------
    BackupResult
        Includes the zip path, file count, total uncompressed bytes,
        UTC generation timestamp, and the project's schema version at
        the time of backup.
    """
    slug = _slugify(project_slug)
    db_path = project_db_path(project_slug)
    if not db_path.exists():
        raise FileNotFoundError(f"Project not found: {db_path}")

    generated_at = _now_utc()
    stamp = _utc_stamp_for_filename()

    if output_dir is None:
        output_dir = settings.projects_dir / f"{slug}.backups"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{stamp}.zip"

    _log.info(
        "backup.create.start",
        project=slug,
        output=str(output_path),
        source_db=str(db_path),
    )

    # ── Step 1: take an online-backup snapshot of the SQLite file ───────
    # We snapshot to a temp file inside the destination zip's parent so
    # we can hash it before writing into the zip. The snapshot is deleted
    # at the end (its bytes live in the zip).
    snapshot_path = output_dir / f".{stamp}.sqlite.snapshot"
    try:
        _online_sqlite_backup(db_path, snapshot_path)
        _log.info(
            "backup.create.sqlite_snapshot",
            project=slug,
            snapshot_bytes=snapshot_path.stat().st_size,
        )

        # ── Step 2: enumerate the footprint, hash everything ───────────
        _, sibling_dirs = _project_footprint(slug)
        file_index: dict[str, dict[str, Any]] = {}

        # The DB itself sits at the zip root as `<slug>.sqlite`.
        db_arcname = f"{slug}.sqlite"
        file_index[db_arcname] = {
            "sha256": _sha256_file(snapshot_path),
            "size_bytes": snapshot_path.stat().st_size,
        }

        # Sibling-dir files keep their relative-to-projects_dir layout
        # (e.g. `syd2-shell-cd.logs/2026-04-27.jsonl`).
        sibling_files: list[tuple[Path, str]] = []
        for sib_dir in sibling_dirs:
            for f in _iter_files(sib_dir):
                arc = _arcname_for_sibling(settings.projects_dir, f)
                sibling_files.append((f, arc))
                file_index[arc] = {
                    "sha256": _sha256_file(f),
                    "size_bytes": f.stat().st_size,
                }

        total_bytes = sum(entry["size_bytes"] for entry in file_index.values())
        _log.info(
            "backup.create.indexed",
            project=slug,
            file_count=len(file_index),
            total_bytes=total_bytes,
            sibling_dirs=[d.name for d in sibling_dirs],
        )

        # Read the schema_version *from the snapshot* so we record exactly
        # what's in the zip (not what's currently in the live DB, which
        # could in theory have raced ahead between snapshot and read).
        schema_version_at_backup = _read_schema_version(snapshot_path)

        manifest = {
            "backup_schema_version": BACKUP_SCHEMA_VERSION,
            "tool_version": _meridian_version,
            "project_slug": slug,
            "source_db_path": str(db_path),
            "schema_version": schema_version_at_backup,
            "generated_at": generated_at,
            "file_index": file_index,
            "sibling_dirs_present": sorted(d.name for d in sibling_dirs),
            "sibling_dir_suffixes": list(SIBLING_DIR_SUFFIXES),
        }
        manifest_bytes = json.dumps(
            manifest, indent=2, sort_keys=True, default=str
        ).encode("utf-8")

        # ── Step 3: write the zip ──────────────────────────────────────
        with zipfile.ZipFile(
            output_path, "w", compression=zipfile.ZIP_DEFLATED
        ) as zf:
            zf.writestr("BACKUP_MANIFEST.json", manifest_bytes)
            # SQLite snapshot
            zf.write(snapshot_path, arcname=db_arcname)
            # Sibling artefacts
            for src, arc in sibling_files:
                zf.write(src, arcname=arc)

        _log.info(
            "backup.create.done",
            project=slug,
            output=str(output_path),
            file_count=len(file_index),
            total_bytes=total_bytes,
            schema_version=schema_version_at_backup,
        )

        return BackupResult(
            project=slug,
            output_path=output_path,
            file_count=len(file_index),
            total_bytes=total_bytes,
            generated_at=generated_at,
            schema_version=schema_version_at_backup,
        )
    finally:
        # Always remove the temporary snapshot — its contents are in the zip.
        if snapshot_path.exists():
            try:
                snapshot_path.unlink()
            except OSError as exc:
                _log.warning(
                    "backup.create.snapshot_cleanup_failed",
                    snapshot=str(snapshot_path),
                    error=str(exc),
                )


def _read_schema_version(db_path: Path) -> int:
    """Return MAX(schema_migrations.version) for ``db_path``, or 0 if absent."""
    conn = sqlite3.connect(db_path)
    try:
        try:
            row = conn.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()
        except sqlite3.Error:
            return 0
        return int(row[0]) if row and row[0] is not None else 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# verify_backup
# ---------------------------------------------------------------------------


def verify_backup(zip_path: Path) -> VerifyResult:
    """Re-hash every file inside ``zip_path`` and compare to BACKUP_MANIFEST.json.

    Returns a :class:`VerifyResult` whose ``status`` is one of:
        * ``"valid"`` — every manifest file matches; no extras.
        * ``"invalid"`` — at least one SHA-256 mismatch.
        * ``"incomplete"`` — manifest references files not in the zip, or
          the zip carries files not in the manifest. (No mismatches.)
    """
    if not zip_path.exists():
        raise FileNotFoundError(f"Backup not found: {zip_path}")

    _log.info("backup.verify.start", path=str(zip_path))

    with zipfile.ZipFile(zip_path, "r") as zf:
        manifest = _read_manifest(zf)
        file_index = manifest.get("file_index", {})
        if not isinstance(file_index, dict):
            raise BackupCorrupt(
                "BACKUP_MANIFEST.json file_index is not an object."
            )

        present = set(zf.namelist()) - {"BACKUP_MANIFEST.json"}
        expected = set(file_index)

        missing = sorted(expected - present)
        extra = sorted(present - expected)

        results: list[dict[str, Any]] = []
        any_mismatch = False
        for name in sorted(expected & present):
            expected_hash = file_index[name].get("sha256")
            actual_hash = _zip_member_sha256(zf, name)
            ok = actual_hash == expected_hash
            if not ok:
                any_mismatch = True
            results.append({
                "name": name,
                "expected": expected_hash,
                "actual": actual_hash,
                "ok": ok,
            })

    if any_mismatch:
        status = "invalid"
    elif missing or extra:
        status = "incomplete"
    else:
        status = "valid"

    _log.info(
        "backup.verify.done",
        path=str(zip_path),
        status=status,
        file_count=len(results),
        missing=len(missing),
        extra=len(extra),
    )

    return VerifyResult(
        backup_path=zip_path,
        status=status,
        manifest=manifest,
        file_results=results,
        missing_files=missing,
        extra_files=extra,
    )


# ---------------------------------------------------------------------------
# restore_backup
# ---------------------------------------------------------------------------


def restore_backup(
    zip_path: Path,
    *,
    target_project_slug: str | None = None,
    on_conflict: str = _CONFLICT_REFUSE,
) -> RestoreResult:
    """Restore a backup zip into ``settings.projects_dir``.

    Parameters
    ----------
    zip_path
        Path to a backup produced by :func:`create_backup`.
    target_project_slug
        If ``None``, restore to the slug recorded in the manifest. If
        provided, restore as the new slug — useful for cloning a
        production project into a test slug.
    on_conflict
        One of:
            * ``"refuse"`` (default) — raise :class:`BackupConflict` if the
              target slug already exists.
            * ``"overwrite"`` — delete the existing footprint first. Caller
              is responsible for confirming with the user before passing
              this; the CLI layer enforces interactive Y/N.
            * ``"merge"`` — **not supported in v1.** Raises
              :class:`NotImplementedError` with a clear message.

    Behaviour
    ---------
    1. Verify every SHA-256 in the manifest against the zip BEFORE
       extracting anything. If any hash fails, raise :class:`BackupCorrupt`.
    2. Extract files atomically via a temporary staging dir, then
       move/rename in place.
    3. Run :func:`initialise` on the restored DB — auto-applies any pending
       schema migrations (e.g. backup at v3 → current schema at v5).
    4. Return the conflict list (sibling-dir filename collisions when
       restoring to an existing slug under ``"overwrite"``; empty
       otherwise).
    """
    if on_conflict not in _CONFLICT_POLICIES:
        raise ValueError(
            f"on_conflict must be one of {sorted(_CONFLICT_POLICIES)}, "
            f"got {on_conflict!r}."
        )
    if on_conflict == _CONFLICT_MERGE:
        raise NotImplementedError(
            "on_conflict='merge' is not supported in v1. The merge policy "
            "would need a per-table reconciliation strategy (which row wins "
            "on PK collision? how to handle deleted rows? etc.). For v1, "
            "use 'refuse' to keep the existing project, or 'overwrite' to "
            "replace it entirely. To preserve the existing project AND "
            "restore the backup, pass target_project_slug=<new-slug> to "
            "restore as a clone."
        )

    if not zip_path.exists():
        raise FileNotFoundError(f"Backup not found: {zip_path}")

    _log.info(
        "backup.restore.start",
        zip_path=str(zip_path),
        target_slug=target_project_slug,
        on_conflict=on_conflict,
    )

    # ── Step 1: verify hashes BEFORE touching any user files ───────────
    verify = verify_backup(zip_path)
    if verify.status == "invalid":
        failed = [r["name"] for r in verify.file_results if not r["ok"]]
        raise BackupCorrupt(
            f"Backup {zip_path} failed SHA-256 verification on "
            f"{len(failed)} file(s). Restore aborted to avoid writing "
            f"corrupt data into the projects directory.",
            failed_files=failed,
        )
    if verify.status == "incomplete":
        # Be strict: an incomplete backup is *not* round-trippable. Mismatched
        # file lists usually indicate the zip was tampered with or a partial
        # write occurred during backup creation.
        problems: list[str] = []
        problems.extend(f"missing:{n}" for n in verify.missing_files)
        problems.extend(f"extra:{n}" for n in verify.extra_files)
        raise BackupCorrupt(
            f"Backup {zip_path} is incomplete (missing="
            f"{len(verify.missing_files)}, extra={len(verify.extra_files)}). "
            f"Restore aborted.",
            failed_files=problems,
        )

    manifest = verify.manifest
    source_slug = _validate_slug(str(manifest.get("project_slug", "")))
    schema_version_at_backup = int(manifest.get("schema_version", 0))

    if target_project_slug is None:
        target_slug = source_slug
    else:
        target_slug = _validate_slug(target_project_slug)

    target_db = settings.projects_dir / f"{target_slug}.sqlite"
    target_siblings = [
        settings.projects_dir / f"{target_slug}{suffix}"
        for suffix in SIBLING_DIR_SUFFIXES
    ]

    # ── Step 2: conflict policy ────────────────────────────────────────
    existing_paths: list[Path] = [
        p for p in [target_db, *target_siblings] if p.exists()
    ]
    conflicts_resolved: list[str] = []

    if existing_paths and on_conflict == _CONFLICT_REFUSE:
        names = ", ".join(p.name for p in existing_paths)
        raise BackupConflict(
            f"Target slug '{target_slug}' already exists "
            f"({names}). Pass on_conflict='overwrite' to replace, or "
            f"target_project_slug=<new-slug> to clone."
        )

    if existing_paths and on_conflict == _CONFLICT_OVERWRITE:
        for p in existing_paths:
            conflicts_resolved.append(f"removed:{p.name}")
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
        _log.info(
            "backup.restore.overwrote",
            target_slug=target_slug,
            removed=[p.name for p in existing_paths],
        )

    # ── Step 3: extract via a staging dir, then move in place ─────────
    settings.projects_dir.mkdir(parents=True, exist_ok=True)
    staging = settings.projects_dir / f".{target_slug}.restore-staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    file_index = manifest.get("file_index", {})
    extracted_count = 0
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for name in sorted(file_index):
                # Compute the staged + final destination paths.
                # `name` is either `<source_slug>.sqlite` or
                # `<source_slug>.<suffix>/...`. Rewrite the slug prefix when
                # restoring to a new slug.
                rewritten = _rewrite_arcname_for_target(
                    name, source_slug=source_slug, target_slug=target_slug
                )
                staged_dest = staging / rewritten
                staged_dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name, "r") as src, staged_dest.open("wb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
                extracted_count += 1

        # Move staged tree into place. Each top-level entry in `staging`
        # corresponds to one final path (the .sqlite file or one sibling dir).
        for entry in sorted(staging.iterdir()):
            final = settings.projects_dir / entry.name
            if final.exists():
                # `overwrite` already cleared the headline paths, but a sibling
                # dir might have leftover files we didn't touch. Be conservative:
                # only remove what's actually a known artefact path.
                if entry.is_dir() and final.is_dir():
                    # Move file-by-file, recording any collisions.
                    for src_file in _iter_files(entry):
                        rel = src_file.relative_to(entry)
                        dst_file = final / rel
                        dst_file.parent.mkdir(parents=True, exist_ok=True)
                        if dst_file.exists():
                            conflicts_resolved.append(
                                f"replaced:{entry.name}/{rel.as_posix()}"
                            )
                            dst_file.unlink()
                        shutil.move(str(src_file), str(dst_file))
                else:
                    if final.is_dir():
                        shutil.rmtree(final)
                    else:
                        final.unlink()
                    shutil.move(str(entry), str(final))
            else:
                shutil.move(str(entry), str(final))
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

    # ── Step 4: run initialise() to apply any pending migrations ──────
    if not target_db.exists():
        raise BackupCorrupt(
            f"Restore extracted but the target SQLite file is missing: "
            f"{target_db}. Manifest may be malformed."
        )

    conn = initialise(target_db)
    try:
        row = conn.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()
        schema_version_after_restore = (
            int(row[0]) if row and row[0] is not None else SCHEMA_VERSION
        )
    finally:
        conn.close()

    _log.info(
        "backup.restore.done",
        zip_path=str(zip_path),
        target_slug=target_slug,
        file_count=extracted_count,
        schema_at_backup=schema_version_at_backup,
        schema_after_restore=schema_version_after_restore,
        conflicts=len(conflicts_resolved),
    )

    return RestoreResult(
        project=target_slug,
        restored_to=target_db,
        file_count=extracted_count,
        schema_version_at_backup=schema_version_at_backup,
        schema_version_after_restore=schema_version_after_restore,
        conflicts_resolved=conflicts_resolved,
    )


def _rewrite_arcname_for_target(
    arcname: str, *, source_slug: str, target_slug: str
) -> str:
    """Rewrite the slug prefix of a manifest arcname for a clone restore.

    Manifest entries look like::

        <source_slug>.sqlite
        <source_slug>.logs/2026-04-27.jsonl
        <source_slug>.tenders/pkg-001/manifest.json

    When ``target_slug == source_slug`` this is a no-op. When cloning, we
    rewrite the leading slug component so the restored layout sits under
    the new slug.
    """
    if target_slug == source_slug:
        return arcname

    # `<source_slug>.sqlite` is exact-match.
    if arcname == f"{source_slug}.sqlite":
        return f"{target_slug}.sqlite"

    # `<source_slug>.<suffix>/...` — replace just the leading dir name.
    head, sep, tail = arcname.partition("/")
    if sep and head.startswith(f"{source_slug}."):
        suffix = head[len(source_slug):]  # includes the leading dot
        return f"{target_slug}{suffix}/{tail}"

    # Fallback: leave as-is. (Should not occur for well-formed backups.)
    return arcname


# ---------------------------------------------------------------------------
# list_backups
# ---------------------------------------------------------------------------


def list_backups(project_slug: str) -> list[dict[str, Any]]:
    """Return metadata for every backup zip under ``<slug>.backups/``.

    Each entry contains: ``path``, ``size_bytes``, ``modified_at`` (UTC
    ISO), and — best-effort — ``generated_at`` and ``schema_version``
    pulled from the zip's manifest. A zip whose manifest can't be read
    is still listed (with the manifest fields set to ``None``) so the
    user sees it.
    """
    slug = _slugify(project_slug)
    backups_dir = settings.projects_dir / f"{slug}.backups"
    if not backups_dir.exists():
        return []

    entries: list[dict[str, Any]] = []
    for zip_path in sorted(backups_dir.glob("*.zip")):
        stat = zip_path.stat()
        entry: dict[str, Any] = {
            "path": zip_path,
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(
                stat.st_mtime, tz=UTC
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "generated_at": None,
            "schema_version": None,
            "file_count": None,
        }
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                manifest = _read_manifest(zf)
                entry["generated_at"] = manifest.get("generated_at")
                entry["schema_version"] = manifest.get("schema_version")
                fi = manifest.get("file_index", {})
                if isinstance(fi, dict):
                    entry["file_count"] = len(fi)
        except (BackupCorrupt, zipfile.BadZipFile, OSError):
            # Best-effort listing — corrupted zips still surface.
            pass
        entries.append(entry)
    return entries


# Make linters happy: io is imported for symmetry with evidence.builder
# (and is available for any future stream-based helpers).
_ = io
