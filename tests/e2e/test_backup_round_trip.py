"""Backup round-trip tests for ``meridian.backup``.

Exercises ``create_backup`` -> ``verify_backup`` -> ``restore_backup``
end-to-end on a fresh project (with ingested content) to guarantee:

  * a freshly-minted backup verifies clean,
  * restoring to a new slug clones the project DB faithfully,
  * a single mutated byte inside the zip is detected as ``invalid``,
  * sibling-dir absence does not break ``create_backup``.

All offline; uses the synthetic DOCX fixture so no real LLM calls happen.
"""

from __future__ import annotations

import io
import sqlite3
import zipfile
from pathlib import Path

from meridian.backup import (
    SIBLING_DIR_SUFFIXES,
    create_backup,
    restore_backup,
    verify_backup,
)


def _row_count(db_path: Path, table: str) -> int:
    """Count rows in ``table`` for the SQLite file at ``db_path``."""
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def test_backup_create_then_verify(
    fresh_project_with_sample_doc: tuple[str, Path, sqlite3.Connection, str],
) -> None:
    """A freshly-created backup must verify as ``valid`` end-to-end."""
    name, _db_path, conn, _src_id = fresh_project_with_sample_doc
    # Close the fixture's connection so the online-backup snapshot doesn't
    # contend with our own write transaction.
    conn.close()

    result = create_backup(name)
    assert result.output_path.exists(), "backup zip must be written to disk"
    assert result.file_count >= 1, "the SQLite file at minimum must be captured"

    verify = verify_backup(result.output_path)
    assert verify.status == "valid", (
        f"freshly-minted backup should verify clean, got {verify.status!r} "
        f"(missing={verify.missing_files} extra={verify.extra_files})"
    )
    assert verify.ok is True


def test_backup_restore_to_new_slug(
    fresh_project_with_sample_doc: tuple[str, Path, sqlite3.Connection, str],
) -> None:
    """Restoring backup of project A into slug B must clone the DB content."""
    name_a, db_path_a, conn, _src_id = fresh_project_with_sample_doc

    # Capture row counts BEFORE closing the connection (these stay stable).
    pre_sources = _row_count(db_path_a, "source_document")
    pre_chunks = _row_count(db_path_a, "source_document_chunk")
    conn.close()

    backup = create_backup(name_a)
    target_slug = "clone-target"

    restored = restore_backup(backup.output_path, target_project_slug=target_slug)
    assert restored.project == target_slug
    assert restored.restored_to.exists(), "restored DB file must exist on disk"
    assert restored.restored_to.name == f"{target_slug}.sqlite"

    # Both DBs should have identical content for the round-tripped tables.
    assert _row_count(restored.restored_to, "source_document") == pre_sources
    assert _row_count(restored.restored_to, "source_document_chunk") == pre_chunks


def test_backup_corrupt_zip_detected(
    fresh_project_with_sample_doc: tuple[str, Path, sqlite3.Connection, str],
) -> None:
    """Mutating a single byte inside a zip member must surface as ``invalid``."""
    name, _db_path, conn, _src_id = fresh_project_with_sample_doc
    conn.close()

    backup = create_backup(name)

    # Rewrite the zip with one member's bytes flipped. We can't just twiddle
    # raw zip bytes (that breaks the CRC at the zip layer); instead we
    # rewrite the archive with a corrupted SQLite payload so the inner
    # SHA-256 mismatch — which is what verify_backup actually checks —
    # fires cleanly.
    with zipfile.ZipFile(backup.output_path, "r") as src_zf:
        members = {info.filename: src_zf.read(info.filename) for info in src_zf.infolist()}

    # Pick the SQLite member as the corruption target.
    db_member = next(n for n in members if n.endswith(".sqlite"))
    mutated = bytearray(members[db_member])
    # Flip one byte well past the SQLite header so the file is still a
    # plausible zip entry but its hash changes.
    flip_index = min(len(mutated) - 1, 4096)
    mutated[flip_index] ^= 0xFF
    members[db_member] = bytes(mutated)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as out_zf:
        for fname, data in members.items():
            out_zf.writestr(fname, data)
    backup.output_path.write_bytes(buf.getvalue())

    verify = verify_backup(backup.output_path)
    assert verify.status == "invalid", (
        f"byte-flip should have produced invalid, got {verify.status!r}"
    )
    failed_names = [r["name"] for r in verify.file_results if not r["ok"]]
    assert db_member in failed_names, (
        f"the mutated SQLite member should appear in the failed list; "
        f"got {failed_names!r}"
    )


def test_backup_skips_missing_sibling_dirs(
    fresh_project: tuple[str, Path, sqlite3.Connection],
    tmp_projects_dir: Path,
) -> None:
    """A project with no .tenders/.evidence/.reports siblings must still back up."""
    name, _db_path, conn = fresh_project
    conn.close()

    # Sanity: none of the sibling dirs exist for this freshly-created project.
    for suffix in SIBLING_DIR_SUFFIXES:
        sibling = tmp_projects_dir / f"{name}{suffix}"
        assert not sibling.exists(), (
            f"fixture invariant violated: {sibling} should not exist"
        )

    result = create_backup(name)
    assert result.output_path.exists()
    # Only the SQLite file should have been captured; siblings were absent.
    assert result.file_count == 1, (
        f"expected only the SQLite file in the manifest, got {result.file_count}"
    )

    verify = verify_backup(result.output_path)
    assert verify.status == "valid"
    assert verify.manifest["sibling_dirs_present"] == [], (
        "manifest must record an empty sibling_dirs_present list when none exist"
    )
