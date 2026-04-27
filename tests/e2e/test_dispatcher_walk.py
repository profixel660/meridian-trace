"""Tests for ``meridian.ingest.dispatcher.walk_directory`` (round 18 / Stream A).

The wizard's HTTP integration tests in ``test_wizard_api.py`` already cover
the end-to-end path through the FastAPI router. These tests pin the
unit-level contract of :func:`walk_directory` so refactors of the
dispatcher don't quietly break:

  * Pure scan — no DB / LLM.
  * Stable bucket layout (every supported kind always present as a key).
  * Recursive descent.
  * Skip semantics: hidden files, system files, pruned dirs, access-denied.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from meridian.ingest.dispatcher import (
    SUPPORTED_EXTENSIONS_BY_KIND,
    walk_directory,
)


def test_walk_directory_buckets_by_extension(tmp_path: Path) -> None:
    (tmp_path / "spec.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "schedule.xlsx").write_bytes(b"PK\x03\x04")
    (tmp_path / "report.docx").write_bytes(b"PK\x03\x04")
    (tmp_path / "drawing.dwg").write_bytes(b"AC")
    (tmp_path / "thread.eml").write_text("From: x\r\n", encoding="utf-8")
    (tmp_path / "outlook.msg").write_bytes(b"\xd0\xcf\x11\xe0")

    result = walk_directory(tmp_path)
    assert result.folder_name == tmp_path.name
    assert result.total_ingestable == 6

    # Every supported kind always present, even when empty.
    for kind in SUPPORTED_EXTENSIONS_BY_KIND:
        assert kind in result.files_by_kind

    assert len(result.files_by_kind["pdf"]) == 1
    assert result.files_by_kind["pdf"][0].endswith("spec.pdf")
    assert len(result.files_by_kind["xlsx"]) == 1
    assert len(result.files_by_kind["docx"]) == 1
    assert len(result.files_by_kind["dwg"]) == 1
    assert len(result.files_by_kind["eml"]) == 1
    assert len(result.files_by_kind["msg"]) == 1


def test_walk_directory_recurses(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    (nested / "deep.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "shallow.pdf").write_bytes(b"%PDF-1.4")

    result = walk_directory(tmp_path)
    assert result.total_ingestable == 2
    assert any("deep.pdf" in p for p in result.files_by_kind["pdf"])
    assert any("shallow.pdf" in p for p in result.files_by_kind["pdf"])


def test_walk_directory_skips_unsupported_extensions(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")
    (tmp_path / "image.png").write_bytes(b"\x89PNG")

    result = walk_directory(tmp_path)
    assert result.total_ingestable == 0
    reasons = {s.reason for s in result.skipped}
    assert reasons == {"unsupported_extension"}
    assert len(result.skipped) == 2


def test_walk_directory_skips_system_files(tmp_path: Path) -> None:
    (tmp_path / "Thumbs.db").write_bytes(b"junk")
    (tmp_path / ".DS_Store").write_bytes(b"junk")
    (tmp_path / "desktop.ini").write_bytes(b"junk")

    result = walk_directory(tmp_path)
    assert result.total_ingestable == 0
    assert all(s.reason == "hidden_or_system" for s in result.skipped)
    assert len(result.skipped) == 3


def test_walk_directory_prunes_infrastructure_dirs(tmp_path: Path) -> None:
    for noisy in (".git", "node_modules", "__pycache__", "_meridian"):
        d = tmp_path / noisy
        d.mkdir()
        (d / "fake.pdf").write_bytes(b"%PDF-1.4")

    (tmp_path / "real.pdf").write_bytes(b"%PDF-1.4")

    result = walk_directory(tmp_path)
    assert result.total_ingestable == 1
    assert result.files_by_kind["pdf"][0].endswith("real.pdf")
    # Pruned wholesale: the noisy dirs' contents are NOT in `skipped`.
    assert all("fake.pdf" not in s.path for s in result.skipped)
    assert result.skipped == []


def test_walk_directory_handles_hidden_dot_file_posix_style(tmp_path: Path) -> None:
    (tmp_path / ".hidden.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "visible.pdf").write_bytes(b"%PDF-1.4")

    result = walk_directory(tmp_path)
    assert result.total_ingestable == 1
    assert result.files_by_kind["pdf"][0].endswith("visible.pdf")
    skipped_paths = [s.path for s in result.skipped]
    assert any(".hidden.pdf" in p for p in skipped_paths)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX-only: Windows ACL semantics differ; covered indirectly by os.access path.",
)
def test_walk_directory_records_access_denied(tmp_path: Path) -> None:
    secret = tmp_path / "secret.pdf"
    secret.write_bytes(b"%PDF-1.4")
    # Strip read permission. POSIX-only — Windows os.access semantics
    # don't honour chmod 0o000 the same way.
    os.chmod(secret, 0o000)
    try:
        result = walk_directory(tmp_path)
    finally:
        os.chmod(secret, 0o644)  # restore so pytest can clean up tmp_path
    assert result.total_ingestable == 0
    assert any(
        s.reason == "access_denied" and s.path.endswith("secret.pdf")
        for s in result.skipped
    )
