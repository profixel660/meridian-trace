"""Project backup/restore facility.

Captures a project's SQLite file plus its sibling artefact directories
(``<slug>.logs/``, ``<slug>.tenders/``, ``<slug>.evidence/``,
``<slug>.reports/``) into a single SHA-256-manifested zip and round-trips
back. The SQLite copy uses the online-backup API
(:meth:`sqlite3.Connection.backup`) so concurrent readers/writers are safe
(no torn writes).

Public API:
    create_backup, restore_backup, verify_backup
    BackupResult, RestoreResult, VerifyResult
    BackupCorrupt, BackupConflict
"""

from __future__ import annotations

from meridian.backup.backup import (
    BACKUP_SCHEMA_VERSION,
    SIBLING_DIR_SUFFIXES,
    BackupConflict,
    BackupCorrupt,
    BackupResult,
    RestoreResult,
    VerifyResult,
    create_backup,
    list_backups,
    restore_backup,
    verify_backup,
)

__all__ = [
    "BACKUP_SCHEMA_VERSION",
    "BackupConflict",
    "BackupCorrupt",
    "BackupResult",
    "RestoreResult",
    "SIBLING_DIR_SUFFIXES",
    "VerifyResult",
    "create_backup",
    "list_backups",
    "restore_backup",
    "verify_backup",
]
