"""End-to-end project lifecycle tests.

Exercises ``project-create`` → schema-init → idempotent migrate → import
dedup → status / review-status reporting. Every test is offline; the only
LLM-bearing path here is the (no-op) bootstrap, which is suppressed by
running ``import-doc`` with ``--no-auto-bootstrap``.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from meridian.cli import app as cli_app
from meridian.db.connection import SCHEMA_VERSION, connect
from meridian.ingest import ingest_file
from meridian.projects import create_project

runner = CliRunner()


def test_create_project_initialises_schema_at_current_version(tmp_projects_dir: Path) -> None:
    """A freshly-created project must report the current SCHEMA_VERSION in both
    the ``project`` row and the ``schema_migrations`` table."""
    _project_id, db_path = create_project(name="lifecycle-1", notes=None)

    assert db_path.exists(), "create_project should write a SQLite file"
    expected = SCHEMA_VERSION

    conn = connect(db_path)
    try:
        row = conn.execute("SELECT schema_version FROM project").fetchone()
        assert row["schema_version"] == expected

        max_v = conn.execute(
            "SELECT MAX(version) AS v FROM schema_migrations"
        ).fetchone()["v"]
        assert max_v == expected
    finally:
        conn.close()


def test_db_migrate_idempotent(tmp_projects_dir: Path) -> None:
    """Running ``db-migrate`` against a current-version project must be a no-op."""
    _project_id, db_path = create_project(name="lifecycle-migrate", notes=None)

    pre = _max_schema_version(db_path)
    result = runner.invoke(cli_app, ["db-migrate", "lifecycle-migrate"])
    assert result.exit_code == 0, result.stdout
    post = _max_schema_version(db_path)

    assert pre == post == SCHEMA_VERSION
    assert f"Already at schema v{SCHEMA_VERSION}" in result.stdout


def test_import_doc_dedups_on_reimport(
    fresh_project: tuple[str, Path, object],
    synthetic_docx: Path,
) -> None:
    """Same content_hash imported twice → second result is flagged ``deduped``
    and no second source_document row is inserted."""
    name, db_path, conn = fresh_project

    first = ingest_file(conn, file_path=synthetic_docx, project_root=synthetic_docx.parent)
    assert first.deduped is False

    second = ingest_file(conn, file_path=synthetic_docx, project_root=synthetic_docx.parent)
    assert second.deduped is True
    assert second.source_id == first.source_id

    src_count = conn.execute("SELECT COUNT(*) FROM source_document").fetchone()[0]
    assert src_count == 1, "dedup must NOT create a second source row"


def test_status_command_after_create(
    fresh_project_with_sample_doc: tuple[str, Path, object, str],
) -> None:
    """``meridian status <slug>`` runs cleanly and prints expected sections."""
    name, _db, _conn, _src_id = fresh_project_with_sample_doc
    # Close the fixture's connection first — typer command opens its own.
    _conn.close()

    result = runner.invoke(cli_app, ["status", name])
    assert result.exit_code == 0, result.stdout

    out = result.stdout
    for label in ("sources", "deliverables (all)", "questions (pending)", "llm calls"):
        assert label in out, f"status output missing label {label!r}\n--- output ---\n{out}"


def test_review_status_no_data(fresh_project: tuple[str, Path, object]) -> None:
    """``review-status`` on an empty project returns 0 and reports zero counts."""
    name, _db, conn = fresh_project
    conn.close()

    result = runner.invoke(cli_app, ["review-status", name, "--json"])
    assert result.exit_code == 0, result.stdout

    # The --json variant emits a ProjectCoverage payload; spot-check the
    # zero-count fields.
    import json

    payload = json.loads(result.stdout)
    assert payload["sources_imported"] == 0
    assert payload["sources_extracted"] == 0
    assert payload["pending_decisions"] == 0
    assert payload["schema_version"] == SCHEMA_VERSION


def test_routing_aliases_resolve(
    fresh_project: tuple[str, Path, object],
) -> None:
    """`routing apply` accepts EITHER the operator alias OR the technical name
    and produces an identical persisted routing for the project.

    Guards the round-13 reconciliation: CONTEXT.md §12 documents
    `cloud-default` / `hybrid` / `air-gapped`; the CLI ships
    `cloud-sonnet-default` / `ollama-5090-balanced` / `ollama-air-gapped`.
    The alias layer (config.PRESET_ALIASES + config.resolve_preset_name)
    must keep both forms equivalent at apply time and must keep stored
    routes stable regardless of which form the operator typed.
    """
    from meridian.config import LOCAL_PRESETS, PRESET_ALIASES
    from meridian.projects import create_project, get_project_routing

    name_alias = "routing-via-alias"
    name_tech = "routing-via-technical"
    _project_id_a, _db_path = create_project(name=name_alias, notes=None)
    _project_id_t, _db_path_t = create_project(name=name_tech, notes=None)

    # Spot-check the contract that anchors this test BEFORE we exercise the
    # CLI — if the alias map ever drops `air-gapped`, this assertion gives
    # a clearer failure message than the downstream CLI exit code.
    assert "air-gapped" in PRESET_ALIASES
    assert PRESET_ALIASES["air-gapped"] == "ollama-air-gapped"
    assert "ollama-air-gapped" in LOCAL_PRESETS

    # Apply via alias.
    result_alias = runner.invoke(cli_app, ["routing", "apply", name_alias, "air-gapped"])
    assert result_alias.exit_code == 0, result_alias.stdout

    # Apply via technical name on a separate project.
    result_tech = runner.invoke(
        cli_app, ["routing", "apply", name_tech, "ollama-air-gapped"]
    )
    assert result_tech.exit_code == 0, result_tech.stdout

    # Read back both project DBs and compare.
    db_alias = connect(_db_path)
    db_tech = connect(_db_path_t)
    try:
        routing_alias = get_project_routing(db_alias)
        routing_tech = get_project_routing(db_tech)
    finally:
        db_alias.close()
        db_tech.close()

    assert routing_alias is not None, "alias apply must persist a routing config"
    assert routing_tech is not None, "technical apply must persist a routing config"
    assert routing_alias == routing_tech, (
        f"alias 'air-gapped' must produce identical routing to "
        f"technical 'ollama-air-gapped'. Alias: {routing_alias!r}; "
        f"technical: {routing_tech!r}"
    )

    # Sanity: the persisted routing actually matches the source recipe in
    # LOCAL_PRESETS (so we'd notice if either side silently drifted).
    expected = {p: tuple(r) for p, r in LOCAL_PRESETS["ollama-air-gapped"].items()}
    assert routing_alias == expected

    # Negative: an unknown name resolves to neither alias nor technical and
    # exits 1 (preset-not-found outcome of the three-outcome contract).
    result_bad = runner.invoke(
        cli_app, ["routing", "apply", name_alias, "no-such-preset"]
    )
    assert result_bad.exit_code == 1
    assert "Unknown preset" in result_bad.stdout


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _max_schema_version(db_path: Path) -> int:
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    finally:
        conn.close()
