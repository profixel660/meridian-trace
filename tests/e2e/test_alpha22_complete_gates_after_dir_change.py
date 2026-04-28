"""Alpha-22 end-to-end: simulate the full wizard walk including a
projects_dir override at the project-creation step. Verify:
 - The named project DB exists at the chosen target_dir + slug.
 - That DB contains the rows imported during folder-import (NOT empty).
 - /api/setup/complete returns 200 (gates pass), NOT 400.

This is the regression test for the original bod-2 zero-sources bug.
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

import pytest
from docx import Document
from fastapi.testclient import TestClient


def _make_synthetic_docx(target: Path) -> Path:
    """Write a tiny .docx so ingest_file can extract text without LLM."""
    doc = Document()
    doc.add_heading("Sample Specification Section", level=1)
    doc.add_paragraph("Contractor shall supply and install one (1) air handling unit.")
    doc.save(target)
    return target


def test_full_wizard_with_projects_dir_override_completes_successfully(
    fastapi_client: TestClient,
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Hermetic wizard state — MERIDIAN_WIZARD_STATE_DIR is already set by
    # the ``tmp_projects_dir`` fixture (via ``fastapi_client``), but set it
    # explicitly here to a sub-dir so this test doesn't share state with
    # other tests that share the same tmp_path root.
    monkeypatch.setenv("MERIDIAN_WIZARD_STATE_DIR", str(tmp_path / "wizard_state"))

    # Reset rate-limit bucket and job registry from prior tests.
    from meridian.wizard import api as wizard_api
    wizard_api._rate_buckets.clear()
    with wizard_api._jobs_lock:
        wizard_api._jobs.clear()

    # Stub api-key validation so it returns "valid" without hitting Anthropic.
    # api.py imports validate_anthropic_key_str by name; patch that binding.
    monkeypatch.setattr(
        wizard_api,
        "validate_anthropic_key_str",
        lambda key: ("valid", "stubbed"),
    )

    # Stub keyring so the api-key step does not touch the OS keychain.
    class _FakeKeyring:
        _store: dict[tuple[str, str], str] = {}

        @staticmethod
        def set_password(service: str, user: str, value: str) -> None:
            _FakeKeyring._store[(service, user)] = value

        @staticmethod
        def get_password(service: str, user: str) -> str | None:
            return _FakeKeyring._store.get((service, user))

    monkeypatch.setitem(sys.modules, "keyring", _FakeKeyring)

    # 1. API key
    r = fastapi_client.post(
        "/api/setup/api-key",
        json={"key": "sk-ant-fake-key-not-real-just-for-test"},
    )
    assert r.status_code == 200, r.text

    # 2. Folder import — small folder with one synthetic .docx.
    import_src = tmp_path / "input"
    import_src.mkdir()
    _make_synthetic_docx(import_src / "sample.docx")

    r = fastapi_client.post(
        "/api/setup/import-folder",
        json={"folder_path": str(import_src), "project_name": "MYPROJ"},
    )
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]

    # Poll until job completes.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        r = fastapi_client.get(f"/api/setup/import-folder/{job_id}")
        assert r.status_code == 200, r.text
        if r.json()["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.1)
    assert r.json()["status"] == "succeeded", r.json()

    # 3. Pick a DIFFERENT projects_dir at project-creation time.
    final_dir = tmp_path / "user-chosen-projects-dir"
    final_dir.mkdir()

    # Suggest-name.
    r = fastapi_client.post(
        "/api/setup/projects/suggest-name",
        json={"folder_path": str(import_src)},
    )
    assert r.status_code == 200, r.text
    suggested = r.json()["suggested_name"]

    r = fastapi_client.post(
        "/api/setup/projects",
        json={
            "name": suggested,
            "slug": suggested,
            "projects_dir": str(final_dir),
            "notes": None,
        },
    )
    assert r.status_code == 200, r.text

    # 4. Final DB must exist at user-chosen dir AND contain the imported source.
    from meridian.projects import _slugify
    slug = _slugify(suggested)
    final_db = final_dir / f"{slug}.sqlite"
    assert final_db.exists(), f"final DB missing at {final_db}"

    with sqlite3.connect(final_db) as conn:
        sources = list(conn.execute("SELECT * FROM source_document"))
        assert len(sources) >= 1, (
            f"final DB has 0 sources — staging DB was not adopted. "
            f"sources={sources}"
        )

    # 5. /api/setup/complete must succeed (gates satisfied).
    r = fastapi_client.post("/api/setup/complete")
    assert r.status_code == 200, (
        f"setup/complete returned {r.status_code}: {r.text} — "
        "wizard state is inconsistent across projects_dir change."
    )
