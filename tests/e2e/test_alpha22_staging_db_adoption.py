"""Alpha-22: adopt_project moves a SQLite project file to a new path
under a new slug AND updates the project.name row inside it.

This is the operation /api/setup/projects must perform when the user
chose a different projects_dir than where the staging import landed.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from meridian.projects import adopt_project, create_project


def test_adopt_project_moves_file_and_renames(tmp_path: Path, monkeypatch) -> None:
    # Arrange: a "staging" project at one location with one name.
    staging_dir = tmp_path / "staging"
    final_dir = tmp_path / "final"
    staging_dir.mkdir()
    final_dir.mkdir()

    # Force settings.projects_dir to staging_dir for create_project's path resolution.
    from meridian.config import settings
    monkeypatch.setattr(settings, "data_dir", staging_dir)

    _project_id, staging_db = create_project(name="bod")
    assert staging_db.exists(), staging_db

    # Act: adopt to a new dir + new name.
    final_db = final_dir / "my-project.sqlite"
    adopt_project(
        old_db_path=staging_db,
        new_db_path=final_db,
        new_name="My Project",
    )

    # Assert: file moved.
    assert not staging_db.exists(), "staging DB should have been moved"
    assert final_db.exists(), "final DB should exist at new path"

    # Assert: project.name row was updated.
    with sqlite3.connect(final_db) as conn:
        rows = list(conn.execute("SELECT name FROM project"))
        assert len(rows) == 1
        assert rows[0][0] == "My Project", rows


def test_adopt_project_refuses_to_overwrite(tmp_path: Path, monkeypatch) -> None:
    """Pre-existing target file => raise (no silent overwrite)."""
    from meridian.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    _id, src = create_project(name="src")
    dst = tmp_path / "dst.sqlite"
    dst.write_bytes(b"pre-existing content")

    with pytest.raises(FileExistsError):
        adopt_project(old_db_path=src, new_db_path=dst, new_name="x")


def test_adopt_project_creates_deeply_nested_parent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """`mkdir(parents=True)` should create multi-level missing parents."""
    from meridian.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path / "staging")
    (tmp_path / "staging").mkdir()

    _id, src = create_project(name="src")
    deeply_nested = tmp_path / "a" / "b" / "c" / "final.sqlite"
    assert not deeply_nested.parent.exists(), "precondition: deep dirs missing"

    adopt_project(old_db_path=src, new_db_path=deeply_nested, new_name="X")

    assert deeply_nested.exists()
    assert deeply_nested.parent.is_dir()


def test_adopt_project_handles_unicode_name(tmp_path: Path, monkeypatch) -> None:
    """`new_name` containing Unicode + apostrophes must round-trip
    via parameter binding (no string interpolation)."""
    from meridian.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path / "staging")
    (tmp_path / "staging").mkdir()

    _id, src = create_project(name="src")
    dst = tmp_path / "final.sqlite"
    tricky_name = "Café Project · 'quotes' «test»"

    adopt_project(old_db_path=src, new_db_path=dst, new_name=tricky_name)

    with sqlite3.connect(dst) as conn:
        rows = list(conn.execute("SELECT name FROM project"))
    assert rows == [(tricky_name,)], rows
