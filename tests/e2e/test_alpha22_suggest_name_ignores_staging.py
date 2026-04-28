"""Alpha-22: /api/setup/projects/suggest-name should not bump the
suffix when the only collision is the wizard's own staging DB
(created during the import-folder step). The user expects to keep
the name they typed.
"""

from __future__ import annotations

import time
import zipfile
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient


def _make_synthetic_docx(path: Path) -> None:
    """Minimal valid .docx for ingest-pipeline acceptance.

    Mirrors the helper used in test_alpha22_complete_gates_after_dir_change.py.
    """
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '</Types>',
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>',
        )
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>placeholder</w:t></w:r></w:p></w:body>'
            '</w:document>',
        )
    path.write_bytes(buf.getvalue())


def test_suggest_name_returns_base_when_only_staging_collides(
    fastapi_client: TestClient,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """If the only DB at the candidate slug is the wizard's own staging
    file (created moments earlier by /setup/import-folder), the
    suggester must return the un-bumped base name.
    """
    monkeypatch.setenv("MERIDIAN_WIZARD_STATE_DIR", str(tmp_path / "wizard_state"))
    from meridian.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path / "projects_dir")
    (tmp_path / "projects_dir").mkdir()

    folder = tmp_path / "BOD"
    folder.mkdir()
    _make_synthetic_docx(folder / "sample.docx")

    # Run folder-import to create the staging "bod" DB.
    r = fastapi_client.post(
        "/api/setup/import-folder",
        json={"folder_path": str(folder), "project_name": "BOD"},
    )
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]

    # Wait for import to finish so the staging DB is fully written.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        r = fastapi_client.get(f"/api/setup/import-folder/{job_id}")
        if r.json()["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.1)
    assert r.json()["status"] == "succeeded", r.json()

    # Now ask the suggester for a name. The candidate slug "bod" has a
    # SQLite file at it (the staging DB), but it's OURS — so the suggester
    # must return "bod" (un-bumped) with is_available=True.
    r = fastapi_client.post(
        "/api/setup/projects/suggest-name",
        json={"folder_path": str(folder)},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["suggested_name"] == "bod", (
        f"suggester bumped the name despite the only collision being "
        f"the wizard's own staging DB. Got: {body}"
    )
    assert body["is_available"] is True, body


def test_suggest_name_still_bumps_for_real_collisions(
    fastapi_client: TestClient,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Sanity check: pre-existing UNRELATED project at the candidate slug
    should still cause a suffix bump. The "ignore staging" logic must not
    open a hole that lets users overwrite real prior projects."""
    monkeypatch.setenv("MERIDIAN_WIZARD_STATE_DIR", str(tmp_path / "wizard_state"))
    from meridian.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path / "projects_dir")
    (tmp_path / "projects_dir").mkdir()

    # Create a "real" project at "bod" via the public API (NOT via the
    # wizard's import-folder, so it isn't tracked in _jobs / wizard state).
    from meridian.projects import create_project
    create_project(name="bod")

    folder = tmp_path / "BOD"
    folder.mkdir()
    _make_synthetic_docx(folder / "sample.docx")

    r = fastapi_client.post(
        "/api/setup/projects/suggest-name",
        json={"folder_path": str(folder)},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["suggested_name"] == "bod-2", body
    assert body["is_available"] is False, body
