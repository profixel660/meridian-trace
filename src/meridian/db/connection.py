"""SQLite connection helpers — one file per project (CONTEXT.md §6).

Per-project SQLite files live under `data/projects/<project-slug>.sqlite`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

SCHEMA_VERSION = 6


def _schema_sql() -> str:
    return resources.files("meridian.db").joinpath("schema.sql").read_text(encoding="utf-8")


def connect(db_path: Path, *, busy_timeout_ms: int = 5000) -> sqlite3.Connection:
    """Open a connection with foreign keys + JSON1 + row factory.

    ``busy_timeout_ms`` controls how long SQLite will wait for a lock to be
    released before raising ``sqlite3.OperationalError: database is locked``.
    Pass a positive integer (milliseconds). Pass ``0`` to disable the wait
    entirely — the engine raises immediately on contention (the legacy
    behaviour before round-14 hardening). Negative values are rejected.

    The default of 5000 ms (5 seconds) is appropriate for short single-
    statement writes such as reviewer accept/reject. Long-running writers
    (extraction worker, bulk import) should pass a longer timeout (e.g.
    ``busy_timeout_ms=30000``).

    See ``docs/concurrency-analysis.md`` Hazard 2 for the rationale.
    """
    if busy_timeout_ms < 0:
        raise ValueError(
            f"busy_timeout_ms must be >= 0 (got {busy_timeout_ms!r}); "
            "use 0 to disable waiting and raise immediately on lock."
        )
    conn = sqlite3.connect(db_path, isolation_level=None)  # autocommit; we manage txns explicitly
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)};")
    return conn


def initialise(db_path: Path) -> sqlite3.Connection:
    """Create the SQLite file (if absent), run schema, record migrations."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)

    # Pre-executescript ALTER TABLE work: schema.sql now declares an index
    # over `source_document_chunk(extraction_status)` (a v5 column). On an
    # existing pre-v5 DB the chunk table predates that column, so the
    # executescript would fail trying to CREATE INDEX on a missing column.
    # Apply the v5 ADD COLUMN migration up-front when the legacy chunk
    # shape is detected. Idempotent: PRAGMA-guarded inside _migrate_to_v5.
    has_chunk_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_document_chunk'"
    ).fetchone() is not None
    if has_chunk_table:
        existing_cols = {r["name"] for r in conn.execute(
            "PRAGMA table_info(source_document_chunk)"
        ).fetchall()}
        if "extraction_status" not in existing_cols:
            _migrate_to_v5(conn)

    conn.executescript(_schema_sql())

    # Determine the highest applied version (None if fresh / pre-migration).
    row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
    current_version = row["v"] if row is not None else None

    # Fresh DB (no migrations recorded): the script above already applied the
    # latest schema, so we just stamp every version up to SCHEMA_VERSION.
    if current_version is None:
        for v in range(1, SCHEMA_VERSION + 1):
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (v, _now()),
            )
        return conn

    # Apply incremental migrations for any unapplied versions.
    if current_version < 2:
        _migrate_to_v2(conn)
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (2, _now()),
        )
    if current_version < 3:
        # v3 adds cross_reference_sweep_run and cross_reference_sweep_result.
        # Both are CREATE TABLE IF NOT EXISTS in schema.sql so the executescript
        # above already applied the DDL. We just stamp the migrations row.
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (3, _now()),
        )
    if current_version < 4:
        # v4 adds 'external_reference' to the cross_reference_sweep_result.outcome
        # CHECK constraint. SQLite cannot ALTER a CHECK in place — rebuild the
        # table. Idempotent if already present.
        _migrate_to_v4(conn)
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (4, _now()),
        )
    if current_version < 5:
        # v5 adds chunk-level resume columns to source_document_chunk:
        #   extraction_status, extraction_started_at, extraction_finished_at,
        #   extraction_job_id. Uses ALTER TABLE ADD COLUMN (no CHECK rebuild
        #   needed). Idempotent: re-detects existing columns via PRAGMA.
        _migrate_to_v5(conn)
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (5, _now()),
        )
    if current_version < 6:
        # v6 adds bootstrap-LLM-sweep auto-assessment fields to the three
        # taxonomy tables (trade/service/category): llm_recommended_action,
        # llm_merge_target, llm_confidence, llm_reasoning. Also widens the
        # `source` CHECK to allow 'llm_proposed_high_confidence' and
        # 'llm_auto_merged' (CHECK widening requires a table rebuild).
        # Idempotent — PRAGMA + sqlite_master probes detect prior application.
        _migrate_to_v6(conn)
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (6, _now()),
        )

    return conn


def _migrate_to_v2(conn: sqlite3.Connection) -> None:
    """Migrate v1 → v2: add 'extract_demarcation' to llm_call.purpose CHECK
    and 'demarcation' to source_document.extraction_path CHECK.

    SQLite cannot ALTER a CHECK in place — rebuild the affected tables.
    Idempotent: if the existing CHECKs already permit the new values, this
    function is a no-op.
    """
    # Detect whether migration is required by inspecting the stored CREATE
    # statements. If the new tokens are present, treat as already-migrated.
    purpose_sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='llm_call'"
    ).fetchone()
    extraction_sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='source_document'"
    ).fetchone()

    purpose_needs_migration = (
        purpose_sql_row is not None
        and "extract_demarcation" not in (purpose_sql_row["sql"] or "")
    )
    extraction_needs_migration = (
        extraction_sql_row is not None
        and "'demarcation'" not in (extraction_sql_row["sql"] or "")
    )

    if not purpose_needs_migration and not extraction_needs_migration:
        return

    # Foreign keys must be off during a table-rebuild so referencing rows
    # don't get nuked when we drop+rename. We also wrap the whole thing in
    # a single transaction so a failure rolls back cleanly.
    conn.execute("PRAGMA foreign_keys = OFF;")
    try:
        with transaction(conn):
            if extraction_needs_migration:
                _rebuild_source_document(conn)
            if purpose_needs_migration:
                _rebuild_llm_call(conn)
    finally:
        conn.execute("PRAGMA foreign_keys = ON;")


def _rebuild_source_document(conn: sqlite3.Connection) -> None:
    """Recreate source_document with the v2 extraction_path CHECK."""
    conn.execute(
        """
        CREATE TABLE source_document_new (
            id                          TEXT PRIMARY KEY,
            filename                    TEXT NOT NULL,
            relative_path               TEXT NOT NULL,
            content_hash                TEXT NOT NULL UNIQUE,
            mime_type                   TEXT NOT NULL,
            size_bytes                  INTEGER NOT NULL,
            imported_at                 TEXT NOT NULL,
            document_class              TEXT CHECK (
                document_class IS NULL OR document_class IN (
                    'customer_requirements','global_tr','global_ose_spec',
                    'project_amendment','project_clarification','drawing',
                    'demarcation_schedule','methodology','template','unknown'
                )
            ),
            document_state              TEXT CHECK (
                document_state IS NULL OR document_state IN (
                    'concept','30%','50%','90%','100%','IFC','as-built'
                )
            ),
            revision                    TEXT,
            revision_detected_via       TEXT CHECK (
                revision_detected_via IS NULL OR revision_detected_via IN (
                    'filename_pattern','embedded_metadata','content_scan','user_pinned'
                )
            ),
            is_authoritative_revision   INTEGER NOT NULL DEFAULT 1 CHECK (is_authoritative_revision IN (0,1)),
            superseded_by_id            TEXT REFERENCES source_document(id),
            is_template                 INTEGER NOT NULL DEFAULT 0 CHECK (is_template IN (0,1)),
            is_demarcation_schedule     INTEGER NOT NULL DEFAULT 0 CHECK (is_demarcation_schedule IN (0,1)),
            quality_scan_id             TEXT,
            extraction_path             TEXT NOT NULL CHECK (extraction_path IN (
                'text_spec','bod_import','drawing','demarcation','excluded','pending'
            )) DEFAULT 'pending',
            notes                       TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO source_document_new (
            id, filename, relative_path, content_hash, mime_type, size_bytes,
            imported_at, document_class, document_state, revision,
            revision_detected_via, is_authoritative_revision, superseded_by_id,
            is_template, is_demarcation_schedule, quality_scan_id,
            extraction_path, notes
        )
        SELECT
            id, filename, relative_path, content_hash, mime_type, size_bytes,
            imported_at, document_class, document_state, revision,
            revision_detected_via, is_authoritative_revision, superseded_by_id,
            is_template, is_demarcation_schedule, quality_scan_id,
            extraction_path, notes
        FROM source_document
        """
    )
    conn.execute("DROP TABLE source_document")
    conn.execute("ALTER TABLE source_document_new RENAME TO source_document")
    # Recreate indexes (mirrors schema.sql).
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_source_document_class ON source_document(document_class)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_source_document_filename ON source_document(filename)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_source_document_authoritative "
        "ON source_document(filename, is_authoritative_revision)"
    )


def _rebuild_llm_call(conn: sqlite3.Connection) -> None:
    """Recreate llm_call with the v2 purpose CHECK."""
    conn.execute(
        """
        CREATE TABLE llm_call_new (
            id                      TEXT PRIMARY KEY,
            job_id                  TEXT REFERENCES extraction_job(id) ON DELETE SET NULL,
            purpose                 TEXT NOT NULL CHECK (purpose IN (
                'quality_scan','triage','extract_text_spec','extract_demarcation',
                'extract_bod','conflict_pass','error_explain'
            )),
            provider                TEXT NOT NULL,
            model                   TEXT NOT NULL,
            provider_api_version    TEXT,
            system_prompt           TEXT NOT NULL,
            user_prompt             TEXT NOT NULL,
            prompt_version_ref      TEXT,
            temperature             REAL,
            top_p                   REAL,
            max_tokens              INTEGER,
            input_hash              TEXT NOT NULL,
            response_text           TEXT NOT NULL,
            parsed_response         TEXT CHECK (parsed_response IS NULL OR json_valid(parsed_response)),
            prompt_tokens           INTEGER,
            completion_tokens       INTEGER,
            cache_read_tokens       INTEGER,
            cache_write_tokens      INTEGER,
            cost_cents              INTEGER,
            started_at              TEXT NOT NULL,
            finished_at             TEXT,
            error                   TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO llm_call_new (
            id, job_id, purpose, provider, model, provider_api_version,
            system_prompt, user_prompt, prompt_version_ref,
            temperature, top_p, max_tokens, input_hash,
            response_text, parsed_response,
            prompt_tokens, completion_tokens,
            cache_read_tokens, cache_write_tokens, cost_cents,
            started_at, finished_at, error
        )
        SELECT
            id, job_id, purpose, provider, model, provider_api_version,
            system_prompt, user_prompt, prompt_version_ref,
            temperature, top_p, max_tokens, input_hash,
            response_text, parsed_response,
            prompt_tokens, completion_tokens,
            cache_read_tokens, cache_write_tokens, cost_cents,
            started_at, finished_at, error
        FROM llm_call
        """
    )
    conn.execute("DROP TABLE llm_call")
    conn.execute("ALTER TABLE llm_call_new RENAME TO llm_call")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_call_job ON llm_call(job_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_call_purpose ON llm_call(purpose)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_call_input_hash ON llm_call(input_hash)")


def _migrate_to_v4(conn: sqlite3.Connection) -> None:
    """Migrate v3 → v4: add 'external_reference' to cross_reference_sweep_result.outcome.

    SQLite cannot ALTER a CHECK in place — rebuild the affected table.
    Idempotent: if the existing CHECK already permits 'external_reference',
    this function is a no-op.
    """
    sweep_sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' "
        "AND name='cross_reference_sweep_result'"
    ).fetchone()
    if sweep_sql_row is None:
        # Table doesn't exist yet — fresh DB after CREATE IF NOT EXISTS will
        # have already applied the v4 schema. Nothing to do.
        return
    if "external_reference" in (sweep_sql_row["sql"] or ""):
        return

    conn.execute("PRAGMA foreign_keys = OFF;")
    try:
        with transaction(conn):
            _rebuild_cross_reference_sweep_result(conn)
    finally:
        conn.execute("PRAGMA foreign_keys = ON;")


def _rebuild_cross_reference_sweep_result(conn: sqlite3.Connection) -> None:
    """Recreate cross_reference_sweep_result with 'external_reference' allowed."""
    conn.execute(
        """
        CREATE TABLE cross_reference_sweep_result_new (
            id                          TEXT PRIMARY KEY,
            sweep_run_id                TEXT NOT NULL REFERENCES cross_reference_sweep_run(id) ON DELETE CASCADE,
            from_source_id              TEXT NOT NULL REFERENCES source_document(id),
            to_source_id                TEXT REFERENCES source_document(id),
            to_deliverable_id           TEXT REFERENCES deliverable(id),
            anchor_kind                 TEXT NOT NULL CHECK (anchor_kind IN (
                'section','clause','drawing','spec','standard','equipment_tag',
                'vendor','owner_id','short_spec','other'
            )),
            raw_match                   TEXT NOT NULL,
            normalised_match            TEXT NOT NULL,
            from_excerpt                TEXT,
            detection_method            TEXT NOT NULL CHECK (detection_method IN (
                'regex','string','llm'
            )),
            outcome                     TEXT NOT NULL CHECK (outcome IN (
                'confirmed','borderline','external_reference','rejected'
            )),
            confidence                  TEXT NOT NULL CHECK (confidence IN ('high','medium','low')),
            reason                      TEXT,
            review_question_id          TEXT REFERENCES question(id),
            created_at                  TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO cross_reference_sweep_result_new (
            id, sweep_run_id, from_source_id, to_source_id, to_deliverable_id,
            anchor_kind, raw_match, normalised_match, from_excerpt,
            detection_method, outcome, confidence, reason, review_question_id,
            created_at
        )
        SELECT
            id, sweep_run_id, from_source_id, to_source_id, to_deliverable_id,
            anchor_kind, raw_match, normalised_match, from_excerpt,
            detection_method, outcome, confidence, reason, review_question_id,
            created_at
        FROM cross_reference_sweep_result
        """
    )
    conn.execute("DROP TABLE cross_reference_sweep_result")
    conn.execute(
        "ALTER TABLE cross_reference_sweep_result_new RENAME TO cross_reference_sweep_result"
    )
    # Recreate indexes (mirrors schema.sql §10).
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_xref_sweep_run ON cross_reference_sweep_result(sweep_run_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_xref_sweep_from ON cross_reference_sweep_result(from_source_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_xref_sweep_to ON cross_reference_sweep_result(to_source_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_xref_sweep_outcome ON cross_reference_sweep_result(outcome)"
    )


def _migrate_to_v5(conn: sqlite3.Connection) -> None:
    """Migrate v4 → v5: add chunk-level resume columns to source_document_chunk.

    Adds (if absent):
        extraction_status TEXT NOT NULL DEFAULT 'pending'
            CHECK (extraction_status IN ('pending','in_progress','extracted','skipped'))
        extraction_started_at  TEXT
        extraction_finished_at TEXT
        extraction_job_id      TEXT REFERENCES extraction_job(id) ON DELETE SET NULL

    Backfill: existing chunks whose ``triage_marked_for_extraction`` is
    populated are stamped with the corresponding terminal extraction_status
    so a resume after migration does not re-triage them. Chunks with NULL
    triage state stay 'pending'.

    Idempotent — uses PRAGMA table_info to detect already-applied columns.
    SQLite ALTER TABLE ADD COLUMN cannot embed a non-constant DEFAULT in a
    NOT NULL column, but a string literal default is allowed.
    """
    cols_row = conn.execute("PRAGMA table_info(source_document_chunk)").fetchall()
    existing = {row["name"] for row in cols_row}

    needed: list[tuple[str, str]] = []
    if "extraction_status" not in existing:
        needed.append(
            (
                "extraction_status",
                "TEXT NOT NULL DEFAULT 'pending' "
                "CHECK (extraction_status IN ('pending','in_progress','extracted','skipped'))",
            )
        )
    if "extraction_started_at" not in existing:
        needed.append(("extraction_started_at", "TEXT"))
    if "extraction_finished_at" not in existing:
        needed.append(("extraction_finished_at", "TEXT"))
    if "extraction_job_id" not in existing:
        # NB: ALTER TABLE ADD COLUMN supports REFERENCES but cannot validate
        # existing rows — fine here because every existing row will have NULL
        # in this column.
        needed.append(
            (
                "extraction_job_id",
                "TEXT REFERENCES extraction_job(id) ON DELETE SET NULL",
            )
        )

    if not needed:
        # Migration already applied (perhaps by the schema.sql executescript
        # on a fresh DB, or by a re-run). Still ensure the index is present
        # and the backfill has been applied.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chunk_extraction_status "
            "ON source_document_chunk(source_id, extraction_status)"
        )
        return

    with transaction(conn):
        for col_name, col_def in needed:
            conn.execute(
                f"ALTER TABLE source_document_chunk ADD COLUMN {col_name} {col_def}"
            )
        # Backfill terminal state from the existing triage flag so previously
        # triaged chunks are not re-processed on the next resume. Conservative:
        # only touches rows whose triage decision was recorded.
        conn.execute(
            """
            UPDATE source_document_chunk
            SET extraction_status = 'extracted',
                extraction_finished_at = COALESCE(extraction_finished_at, ?)
            WHERE triage_marked_for_extraction = 1
              AND extraction_status = 'pending'
            """,
            (_now(),),
        )
        conn.execute(
            """
            UPDATE source_document_chunk
            SET extraction_status = 'skipped',
                extraction_finished_at = COALESCE(extraction_finished_at, ?)
            WHERE triage_marked_for_extraction = 0
              AND extraction_status = 'pending'
            """,
            (_now(),),
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunk_extraction_status "
        "ON source_document_chunk(source_id, extraction_status)"
    )


def _migrate_to_v6(conn: sqlite3.Connection) -> None:
    """Migrate v5 → v6: add bootstrap-LLM-sweep assessment fields to taxonomy tables.

    For each of ``trade_taxonomy``, ``service_taxonomy``, ``category_taxonomy``:

    * Widen ``source`` CHECK to allow ``'llm_proposed_high_confidence'`` and
      ``'llm_auto_merged'`` (table rebuild — SQLite cannot ALTER a CHECK).
    * Add four nullable columns:
        - ``llm_recommended_action TEXT CHECK (..IN ('confirm','merge_into','defer_to_user'))``
        - ``llm_merge_target       TEXT``
        - ``llm_confidence         REAL CHECK (..BETWEEN 0 AND 1)``
        - ``llm_reasoning          TEXT``

    Idempotent: detects prior application via ``sqlite_master.sql`` (looks for
    the new ``llm_auto_merged`` source token AND the ``llm_recommended_action``
    column). If both are already present, returns immediately.

    See docs/DECISIONS.md §3.10 and Stream B's column-name contract.
    """
    targets = ("trade_taxonomy", "service_taxonomy", "category_taxonomy")

    needs_rebuild: list[str] = []
    for table in targets:
        sql_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if sql_row is None:
            # Table absent — fresh DB after CREATE IF NOT EXISTS already
            # applied the v6 schema. Nothing to do for this table.
            continue
        sql = sql_row["sql"] or ""
        existing_cols = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        wants_check_widen = "llm_auto_merged" not in sql
        wants_new_cols = "llm_recommended_action" not in existing_cols
        if wants_check_widen or wants_new_cols:
            needs_rebuild.append(table)

    if not needs_rebuild:
        return

    conn.execute("PRAGMA foreign_keys = OFF;")
    try:
        with transaction(conn):
            for table in needs_rebuild:
                _rebuild_taxonomy_table_v6(conn, table=table)
    finally:
        conn.execute("PRAGMA foreign_keys = ON;")


def _rebuild_taxonomy_table_v6(conn: sqlite3.Connection, *, table: str) -> None:
    """Rebuild one taxonomy table with the v6 shape (CHECK widening + new columns).

    Preserves all existing rows. ``trade_taxonomy`` carries an extra
    ``category_hint`` column relative to the other two — handled per branch.
    """
    if table == "trade_taxonomy":
        conn.execute(
            """
            CREATE TABLE trade_taxonomy_new (
                id                       TEXT PRIMARY KEY,
                value                    TEXT NOT NULL UNIQUE,
                category_hint            TEXT NOT NULL CHECK (category_hint IN ('specialist','cross_cutting','vendor')),
                synonyms                 TEXT CHECK (synonyms IS NULL OR json_valid(synonyms)),
                source                   TEXT NOT NULL CHECK (source IN (
                    'default','user_added','user_merged',
                    'llm_proposed_high_confidence','llm_auto_merged'
                )),
                confirmed_at             TEXT,
                is_active                INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
                created_at               TEXT NOT NULL,
                notes                    TEXT,
                llm_recommended_action   TEXT CHECK (
                    llm_recommended_action IS NULL OR llm_recommended_action IN (
                        'confirm','merge_into','defer_to_user'
                    )
                ),
                llm_merge_target         TEXT,
                llm_confidence           REAL CHECK (
                    llm_confidence IS NULL OR (llm_confidence BETWEEN 0 AND 1)
                ),
                llm_reasoning            TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO trade_taxonomy_new (
                id, value, category_hint, synonyms, source, confirmed_at,
                is_active, created_at, notes
            )
            SELECT
                id, value, category_hint, synonyms, source, confirmed_at,
                is_active, created_at, notes
            FROM trade_taxonomy
            """
        )
        conn.execute("DROP TABLE trade_taxonomy")
        conn.execute("ALTER TABLE trade_taxonomy_new RENAME TO trade_taxonomy")
        return

    # service_taxonomy / category_taxonomy share the same shape (no category_hint).
    conn.execute(
        f"""
        CREATE TABLE {table}_new (
            id                       TEXT PRIMARY KEY,
            value                    TEXT NOT NULL UNIQUE,
            synonyms                 TEXT CHECK (synonyms IS NULL OR json_valid(synonyms)),
            source                   TEXT NOT NULL CHECK (source IN (
                'default','user_added','user_merged',
                'llm_proposed_high_confidence','llm_auto_merged'
            )),
            confirmed_at             TEXT,
            is_active                INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
            created_at               TEXT NOT NULL,
            notes                    TEXT,
            llm_recommended_action   TEXT CHECK (
                llm_recommended_action IS NULL OR llm_recommended_action IN (
                    'confirm','merge_into','defer_to_user'
                )
            ),
            llm_merge_target         TEXT,
            llm_confidence           REAL CHECK (
                llm_confidence IS NULL OR (llm_confidence BETWEEN 0 AND 1)
            ),
            llm_reasoning            TEXT
        )
        """
    )
    conn.execute(
        f"""
        INSERT INTO {table}_new (
            id, value, synonyms, source, confirmed_at,
            is_active, created_at, notes
        )
        SELECT
            id, value, synonyms, source, confirmed_at,
            is_active, created_at, notes
        FROM {table}
        """
    )
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Explicit transaction. Use because we set isolation_level=None for autocommit."""
    conn.execute("BEGIN IMMEDIATE;")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK;")
        raise
    else:
        conn.execute("COMMIT;")


__all__ = ["SCHEMA_VERSION", "connect", "initialise", "transaction"]
