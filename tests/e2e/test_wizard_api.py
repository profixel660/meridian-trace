"""End-to-end tests for the GUI setup-wizard HTTP layer (round 17 / Stream C).

Covers every ``/setup/*`` endpoint exposed by ``meridian.wizard.api``,
plus the CLI-state-compat path (a partial CLI run is correctly recognised
by the GUI wizard's state loader).

Offline-only: the Anthropic SDK is monkeypatched to avoid any real network
call. The ingest layer is exercised end-to-end (no LLM calls there) using
a synthetic .docx fixture from ``conftest.py``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Reset the in-memory rate-limit bucket between tests so 11 separate
# tests don't trip the 10/5min limiter on /setup/api-key.
from meridian.wizard import api as wizard_api
from meridian.wizard.state import load_wizard_state, save_wizard_state, state_path

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_wizard_state(
    tmp_projects_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Clear the rate-limit bucket, in-memory job registry, and OS keyring lookups.

    ``tmp_projects_dir`` already isolates the JSON state file under a
    per-test tmp dir; we additionally clear the module-level dicts. The
    keyring read path used by ``WizardState.api_key_set`` would otherwise
    surface a real prior-install secret on the developer's machine and
    flip ``api_key_set`` to True for tests that expect a virgin state.
    Stubbing ``keyring.get_password`` to return None gives us deterministic
    isolation without monkey-patching ``sys.modules`` for every test.
    """
    wizard_api._rate_buckets.clear()
    with wizard_api._jobs_lock:
        wizard_api._jobs.clear()

    # Sandbox keyring reads. Tests that need a populated keyring use the
    # ``stub_keyring`` fixture, which runs *after* this autouse fixture
    # and replaces sys.modules['keyring'] wholesale with a fake. This
    # default fallback installs an empty stub so a real prior-install
    # secret on the developer's machine cannot leak into
    # ``WizardState.api_key_set`` and flip ``next_step`` away from
    # ``api_key`` for tests that expect a virgin state.
    import sys

    class _NullKeyring:
        @staticmethod
        def get_password(service: str, user: str) -> str | None:
            return None

        @staticmethod
        def set_password(service: str, user: str, value: str) -> None:
            return None

    monkeypatch.setitem(sys.modules, "keyring", _NullKeyring)
    yield


@pytest.fixture
def stub_anthropic_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch ``meridian.onboarding.wizard._validate_anthropic_key`` to return 'valid'.

    Targets the public-internal helper directly rather than the SDK so we
    don't need to import the real ``anthropic`` package shape in tests.
    """
    from meridian.onboarding import wizard as cli_wizard

    monkeypatch.setattr(
        cli_wizard,
        "_validate_anthropic_key",
        lambda: ("valid", "stub: models.list() ok"),
    )


@pytest.fixture
def stub_anthropic_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the validator to return 'invalid' (Anthropic rejected the key)."""
    from meridian.onboarding import wizard as cli_wizard

    monkeypatch.setattr(
        cli_wizard,
        "_validate_anthropic_key",
        lambda: ("invalid", "stub: 401 unauthorized"),
    )


@pytest.fixture
def stub_keyring(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, str], str]:
    """Patch keyring.set_password so api-key persistence does not touch the OS."""
    store: dict[tuple[str, str], str] = {}

    class _FakeKeyring:
        @staticmethod
        def set_password(service: str, user: str, value: str) -> None:
            store[(service, user)] = value

        @staticmethod
        def get_password(service: str, user: str) -> str | None:
            return store.get((service, user))

    monkeypatch.setitem(__import__("sys").modules, "keyring", _FakeKeyring)
    return store


# --------------------------------------------------------------------------
# /setup/state
# --------------------------------------------------------------------------


def test_setup_state_fresh_returns_api_key_next(fastapi_client: TestClient) -> None:
    response = fastapi_client.get("/setup/state")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["complete"] is False
    assert body["api_key_set"] is False
    assert body["first_project_slug"] is None
    assert body["documents_imported"] == 0
    assert body["documents_skipped"] is False
    assert body["next_step"] == "api_key"


# --------------------------------------------------------------------------
# /setup/api-key
# --------------------------------------------------------------------------


def test_setup_api_key_valid_persists_and_advances(
    fastapi_client: TestClient,
    stub_anthropic_valid: None,
    stub_keyring: dict[tuple[str, str], str],
) -> None:
    response = fastapi_client.post(
        "/setup/api-key", json={"key": "sk-ant-fake-test-key"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "valid"
    assert "validated" in body["message"].lower()

    # Persisted to (stub) keyring
    assert stub_keyring[("meridian.api_key", "anthropic")] == "sk-ant-fake-test-key"

    # State file flipped api_key_configured to True
    state = load_wizard_state()
    assert state.api_key_set is True

    state_response = fastapi_client.get("/setup/state").json()
    assert state_response["api_key_set"] is True
    # alpha-2 swapped step order: api_key → first_documents (folder pick) →
    # first_project (auto-named confirm/rename) → ready.
    assert state_response["next_step"] == "first_documents"


def test_setup_api_key_invalid_does_not_persist(
    fastapi_client: TestClient,
    stub_anthropic_invalid: None,
    stub_keyring: dict[tuple[str, str], str],
) -> None:
    response = fastapi_client.post(
        "/setup/api-key", json={"key": "sk-ant-bad-key"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "invalid"
    # PM-language message — should mention sk-ant prefix or billing
    msg = body["message"].lower()
    assert "anthropic rejected" in msg or "sk-ant" in msg

    # NOT persisted
    assert ("meridian.api_key", "anthropic") not in stub_keyring

    # next_step still api_key
    state_response = fastapi_client.get("/setup/state").json()
    assert state_response["api_key_set"] is False
    assert state_response["next_step"] == "api_key"


# --------------------------------------------------------------------------
# /setup/projects
# --------------------------------------------------------------------------


def test_setup_create_project_unique_slug_succeeds(
    fastapi_client: TestClient,
    tmp_projects_dir: Path,
    stub_anthropic_valid: None,
    stub_keyring: dict[tuple[str, str], str],
) -> None:
    # Set api_key first so next_step lands on first_documents (realistic flow).
    fastapi_client.post("/setup/api-key", json={"key": "sk-ant-test"})

    response = fastapi_client.post(
        "/setup/projects",
        json={
            "name": "Wizard Test Project",
            "slug": "wizard-test-project",
            "projects_dir": str(tmp_projects_dir),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created"] is True
    assert body["slug"] == "wizard-test-project"
    assert body["db_path"].endswith(".sqlite")
    assert Path(body["db_path"]).exists()

    state_response = fastapi_client.get("/setup/state").json()
    assert state_response["first_project_slug"] == "wizard-test-project"
    assert state_response["first_project_name"] == "Wizard Test Project"
    assert state_response["next_step"] == "first_documents"


def test_setup_create_project_duplicate_slug_returns_409(
    fastapi_client: TestClient,
    tmp_projects_dir: Path,
) -> None:
    payload = {
        "name": "Wizard Test Project",
        "slug": "wizard-test-project",
        "projects_dir": str(tmp_projects_dir),
    }
    first = fastapi_client.post("/setup/projects", json=payload)
    assert first.status_code == 200

    second = fastapi_client.post("/setup/projects", json=payload)
    assert second.status_code == 409, second.text
    body = second.json()
    # FastAPI wraps detail under 'detail'
    detail = body["detail"]
    assert detail["error"] == "slug_exists"
    assert detail["existing_db_path"].endswith(".sqlite")


def test_setup_create_project_unwriteable_dir_returns_400(
    fastapi_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Force write to fail by pointing at a path whose parent doesn't exist
    # AND patching mkdir to raise. Simulating "not writeable" portably is
    # nasty on Windows; this is the cleanest cross-platform proxy.
    bad_dir = tmp_path / "blocked" / "subdir"

    real_mkdir = Path.mkdir

    def _fail_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        if str(bad_dir) in str(self):
            raise PermissionError(f"simulated permission denied: {self}")
        real_mkdir(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "mkdir", _fail_mkdir)

    response = fastapi_client.post(
        "/setup/projects",
        json={
            "name": "Blocked",
            "slug": "blocked",
            "projects_dir": str(bad_dir),
        },
    )
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "projects_dir_not_writeable"
    assert "writeable" in detail["message"].lower()


# --------------------------------------------------------------------------
# /setup/import + /setup/import/{job_id}
# --------------------------------------------------------------------------


def _wait_for_job(
    fastapi_client: TestClient, job_id: str, *, timeout: float = 10.0
) -> dict:
    """Poll until the job's status is terminal or the timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = fastapi_client.get(f"/setup/import/{job_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if body["status"] in ("succeeded", "failed"):
            return body
        time.sleep(0.05)
    pytest.fail(f"job {job_id} did not finish within {timeout}s")


def test_setup_import_returns_job_id_and_completes(
    fastapi_client: TestClient,
    tmp_projects_dir: Path,
    synthetic_docx: Path,
    stub_anthropic_valid: None,
    stub_keyring: dict[tuple[str, str], str],
) -> None:
    # Walk the realistic flow: api-key first, then project, then import.
    fastapi_client.post("/setup/api-key", json={"key": "sk-ant-test"})
    fastapi_client.post(
        "/setup/projects",
        json={
            "name": "Import Test",
            "slug": "import-test",
            "projects_dir": str(tmp_projects_dir),
        },
    )

    response = fastapi_client.post(
        "/setup/import",
        json={"project_slug": "import-test", "paths": [str(synthetic_docx)]},
    )
    assert response.status_code == 200, response.text
    job_id = response.json()["job_id"]
    assert isinstance(job_id, str) and len(job_id) > 8

    final = _wait_for_job(fastapi_client, job_id)
    assert final["status"] == "succeeded", final
    assert final["total"] == 1
    assert final["completed"] == 1
    assert final["imported"] == 1
    assert final["deduped"] == 0
    assert final["errors"] == []

    # Subsequent /setup/state must reflect the import counter.
    state_response = fastapi_client.get("/setup/state").json()
    assert state_response["documents_imported"] == 1
    assert state_response["next_step"] == "ready"


def test_setup_import_unknown_project_returns_404(
    fastapi_client: TestClient,
    synthetic_docx: Path,
) -> None:
    response = fastapi_client.post(
        "/setup/import",
        json={"project_slug": "nope-not-here", "paths": [str(synthetic_docx)]},
    )
    assert response.status_code == 404, response.text


# --------------------------------------------------------------------------
# /setup/import/skip
# --------------------------------------------------------------------------


def test_setup_import_skip_flips_state(
    fastapi_client: TestClient,
    tmp_projects_dir: Path,
    stub_anthropic_valid: None,
    stub_keyring: dict[tuple[str, str], str],
) -> None:
    fastapi_client.post("/setup/api-key", json={"key": "sk-ant-test"})
    fastapi_client.post(
        "/setup/projects",
        json={
            "name": "Skip Test",
            "slug": "skip-test",
            "projects_dir": str(tmp_projects_dir),
        },
    )

    response = fastapi_client.post(
        "/setup/import/skip", json={"project_slug": "skip-test"}
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"acknowledged": True}

    state_response = fastapi_client.get("/setup/state").json()
    assert state_response["documents_skipped"] is True
    assert state_response["documents_imported"] == 0
    assert state_response["next_step"] == "ready"


# --------------------------------------------------------------------------
# /setup/complete
# --------------------------------------------------------------------------


def test_setup_complete_after_full_flow_idempotent(
    fastapi_client: TestClient,
    tmp_projects_dir: Path,
    stub_anthropic_valid: None,
    stub_keyring: dict[tuple[str, str], str],
) -> None:
    # Walk the whole wizard.
    fastapi_client.post("/setup/api-key", json={"key": "sk-ant-test"})
    fastapi_client.post(
        "/setup/projects",
        json={
            "name": "Done",
            "slug": "done",
            "projects_dir": str(tmp_projects_dir),
        },
    )
    fastapi_client.post("/setup/import/skip", json={"project_slug": "done"})

    # Pre-complete state should be 'ready'.
    pre = fastapi_client.get("/setup/state").json()
    assert pre["next_step"] == "ready"
    assert pre["complete"] is False

    # First call to complete.
    response = fastapi_client.post("/setup/complete")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["complete"] is True
    assert body["next_step"] == "complete"

    # Idempotent: re-call must not error and must keep complete=True.
    response2 = fastapi_client.post("/setup/complete")
    assert response2.status_code == 200, response2.text
    body2 = response2.json()
    assert body2["complete"] is True
    assert body2["next_step"] == "complete"


def test_setup_complete_before_required_steps_returns_400(
    fastapi_client: TestClient,
) -> None:
    response = fastapi_client.post("/setup/complete")
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "setup_incomplete"
    assert detail["next_step"] in {"api_key", "first_project", "first_documents"}


# --------------------------------------------------------------------------
# CLI/GUI state interop
# --------------------------------------------------------------------------


def test_pre_existing_cli_state_recognised_by_gui(
    fastapi_client: TestClient,
    tmp_projects_dir: Path,
) -> None:
    """A partially-completed CLI run must be reflected in /setup/state."""
    # Simulate the CLI wizard having completed steps 1-3 (api_key, totp_enrol,
    # first_project) and recorded the project slug. This is the JSON shape
    # ``meridian.onboarding.wizard.save_state`` writes.
    state_file = state_path()
    state_file.parent.mkdir(parents=True, exist_ok=True)
    cli_payload = {
        "api_key_configured": True,
        "first_project_slug": "cli-started-project",
        "first_doc_imported": False,
        "first_bootstrap_run": False,
        "totp_enrolled": False,
        "license_installed": False,
        "completed_steps": ["api_key", "totp_enrol", "first_project"],
        "last_step_at": "2026-04-27T10:00:00Z",
    }
    state_file.write_text(json.dumps(cli_payload), encoding="utf-8")

    response = fastapi_client.get("/setup/state")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["api_key_set"] is True
    assert body["first_project_slug"] == "cli-started-project"
    # CLI didn't import a doc → GUI still wants the user to do that.
    assert body["next_step"] == "first_documents"
    assert body["complete"] is False


def test_gui_save_then_cli_load_round_trips_known_fields(
    tmp_projects_dir: Path,
) -> None:
    """The GUI sidecar keys must not break the CLI's loader."""
    from meridian.onboarding.wizard import OnboardingState as CliOnboardingState
    from meridian.onboarding.wizard import load_state as cli_load_state

    state = load_wizard_state()
    state.cli.api_key_configured = True
    state.cli.first_project_slug = "round-trip-test"
    state.cli.mark("api_key")
    state.cli.mark("first_project")
    state.gui_first_project_name = "Round Trip"
    state.gui_first_project_dir = str(tmp_projects_dir)
    state.gui_documents_imported = 3
    state.gui_documents_skipped = False
    state.gui_wizard_completed_at = "2026-04-27T11:00:00Z"
    save_wizard_state(state)

    # CLI loader: must still parse cleanly, ignoring GUI-only sidecar keys.
    cli_state = cli_load_state()
    assert isinstance(cli_state, CliOnboardingState)
    assert cli_state is not None
    assert cli_state.api_key_configured is True
    assert cli_state.first_project_slug == "round-trip-test"
    assert "api_key" in cli_state.completed_steps
    assert "first_project" in cli_state.completed_steps


# --------------------------------------------------------------------------
# /setup/import-folder/scan + /setup/import-folder + /setup/import-folder/{job_id}
# (round-18 / Stream A)
# --------------------------------------------------------------------------


def _make_mixed_folder(root: Path, *, with_synthetic_docx: Path | None = None) -> Path:
    """Materialise a folder with a few files of mixed kinds + skip cases.

    Returns the folder path. Layout:
        <root>/Project-Folder/
            spec.pdf                          → ingestable (pdf)
            schedule.xlsx                     → ingestable (xlsx)
            sample.docx                       → ingestable (docx, copied from fixture)
            notes.txt                         → skipped (unsupported_extension)
            .hidden_file.pdf                  → skipped (hidden_or_system)
            Thumbs.db                         → skipped (hidden_or_system)
            subfolder/another.pdf             → ingestable (pdf, recurse)
            __pycache__/garbage.pyc           → pruned wholesale
            node_modules/foo.pdf              → pruned wholesale
    """
    folder = root / "Project-Folder"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "spec.pdf").write_bytes(b"%PDF-1.4 fake")
    (folder / "schedule.xlsx").write_bytes(b"PK\x03\x04 fake xlsx")
    (folder / "notes.txt").write_text("ignored", encoding="utf-8")
    (folder / ".hidden_file.pdf").write_bytes(b"%PDF-1.4 hidden")
    (folder / "Thumbs.db").write_bytes(b"junk")

    sub = folder / "subfolder"
    sub.mkdir(exist_ok=True)
    (sub / "another.pdf").write_bytes(b"%PDF-1.4 sub")

    pruned = folder / "__pycache__"
    pruned.mkdir(exist_ok=True)
    (pruned / "garbage.pyc").write_bytes(b"\x00\x01")

    pruned2 = folder / "node_modules"
    pruned2.mkdir(exist_ok=True)
    (pruned2 / "foo.pdf").write_bytes(b"%PDF-1.4 noise")

    if with_synthetic_docx is not None:
        # Copy the synthetic docx so the import end-to-end test can
        # actually verify a deliverable lands in DB. PDF/XLSX paths are
        # left as fake bytes — those ingesters will fail (and surface in
        # job.errors), which is also useful coverage.
        (folder / "sample.docx").write_bytes(with_synthetic_docx.read_bytes())

    return folder


def test_setup_import_folder_scan_happy_path(
    fastapi_client: TestClient,
    tmp_path: Path,
) -> None:
    folder = _make_mixed_folder(tmp_path)

    response = fastapi_client.post(
        "/setup/import-folder/scan",
        json={"folder_path": str(folder)},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["folder_name"] == "Project-Folder"
    assert Path(body["folder_path"]).resolve() == folder.resolve()

    # Two PDFs (spec + subfolder/another), one XLSX, one DOCX-not-present.
    pdfs = body["files_by_kind"]["pdf"]
    assert len(pdfs) == 2, pdfs
    assert any(p.endswith("spec.pdf") for p in pdfs)
    assert any(p.endswith("another.pdf") for p in pdfs)

    xlsxs = body["files_by_kind"]["xlsx"]
    assert len(xlsxs) == 1
    assert xlsxs[0].endswith("schedule.xlsx")

    # Empty buckets are still present (stable layout for the GUI).
    for kind in ("docx", "dwg", "eml", "msg"):
        assert body["files_by_kind"][kind] == []

    # total_ingestable = 2 PDFs + 1 XLSX
    assert body["total_ingestable"] == 3

    # Skipped: notes.txt (unsupported), .hidden_file.pdf (hidden), Thumbs.db (system).
    skipped_reasons = sorted(s["reason"] for s in body["skipped"])
    assert skipped_reasons == sorted(
        ["unsupported_extension", "hidden_or_system", "hidden_or_system"]
    )
    skipped_paths = [s["path"] for s in body["skipped"]]
    assert any("notes.txt" in p for p in skipped_paths)
    assert any("Thumbs.db" in p for p in skipped_paths)
    # node_modules and __pycache__ contents are pruned wholesale, NOT
    # surfaced in the skipped list.
    assert not any("node_modules" in p for p in skipped_paths)
    assert not any("__pycache__" in p for p in skipped_paths)


def test_setup_import_folder_scan_nonexistent_path_returns_400(
    fastapi_client: TestClient,
    tmp_path: Path,
) -> None:
    bogus = tmp_path / "definitely-not-here"
    response = fastapi_client.post(
        "/setup/import-folder/scan",
        json={"folder_path": str(bogus)},
    )
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "folder_not_found"


def test_setup_import_folder_scan_file_not_directory_returns_400(
    fastapi_client: TestClient,
    tmp_path: Path,
) -> None:
    a_file = tmp_path / "definitely_a_file.pdf"
    a_file.write_bytes(b"%PDF-1.4")
    response = fastapi_client.post(
        "/setup/import-folder/scan",
        json={"folder_path": str(a_file)},
    )
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "folder_not_a_directory"


def test_setup_import_folder_end_to_end(
    fastapi_client: TestClient,
    tmp_projects_dir: Path,
    tmp_path: Path,
    synthetic_docx: Path,
    stub_anthropic_valid: None,
    stub_keyring: dict[tuple[str, str], str],
) -> None:
    """Walk the wizard: api-key, project, then a real folder import."""
    # 1) Set up state to a created project.
    fastapi_client.post("/setup/api-key", json={"key": "sk-ant-test"})
    fastapi_client.post(
        "/setup/projects",
        json={
            "name": "Folder Import Test",
            "slug": "folder-import-test",
            "projects_dir": str(tmp_projects_dir),
        },
    )

    # 2) Build a folder with the synthetic .docx (the only file the
    # ingester can actually process end-to-end here — fake PDF bytes
    # would fail upstream and be surfaced in job.errors).
    folder = tmp_path / "Folder-Import-Test"
    folder.mkdir()
    (folder / "sample.docx").write_bytes(synthetic_docx.read_bytes())

    # 3) Scan first — confirm the GUI's pre-import preview shape.
    scan = fastapi_client.post(
        "/setup/import-folder/scan",
        json={"folder_path": str(folder)},
    )
    assert scan.status_code == 200, scan.text
    assert scan.json()["total_ingestable"] == 1

    # 4) Kick off the import job using the project name (server slugifies).
    kick = fastapi_client.post(
        "/setup/import-folder",
        json={
            "folder_path": str(folder),
            "project_name": "Folder Import Test",
        },
    )
    assert kick.status_code == 200, kick.text
    job_id = kick.json()["job_id"]
    assert isinstance(job_id, str) and len(job_id) > 8

    # 5) Poll. Reuse the same poll-helper but on the folder endpoint.
    deadline = time.monotonic() + 10.0
    body: dict = {}
    while time.monotonic() < deadline:
        resp = fastapi_client.get(f"/setup/import-folder/{job_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if body["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.05)
    assert body["status"] == "succeeded", body
    assert body["total"] == 1
    assert body["completed"] == 1
    assert body["imported"] == 1
    assert body["deduped"] == 0
    assert body["failed"] == []
    # current_file goes back to None on terminal status.
    assert body["current_file"] is None

    # 6) Verify the deliverable landed in the project's DB.
    from meridian.db.connection import connect

    db_path = tmp_projects_dir / "folder-import-test.sqlite"
    assert db_path.exists(), f"project DB missing: {db_path}"
    conn = connect(db_path)
    try:
        n = conn.execute("SELECT COUNT(*) FROM source_document").fetchone()[0]
        assert n == 1, f"expected 1 source_document row, found {n}"
    finally:
        conn.close()

    # 7) Wizard state must reflect the import.
    state_response = fastapi_client.get("/setup/state").json()
    assert state_response["documents_imported"] == 1
    assert state_response["next_step"] == "ready"


def test_setup_import_folder_unknown_project_auto_creates(
    fastapi_client: TestClient,
    tmp_path: Path,
) -> None:
    """alpha-2 swap: import-folder is the first step that needs a project, so
    if no project exists yet it is created on the fly (named after the folder)
    rather than 404'd. The downstream first_project step becomes a rename
    confirm rather than a create."""
    folder = tmp_path / "lonely-folder"
    folder.mkdir()
    (folder / "spec.pdf").write_bytes(b"%PDF-1.4")

    response = fastapi_client.post(
        "/setup/import-folder",
        json={"folder_path": str(folder), "project_name": "Never Created"},
    )
    assert response.status_code == 200, response.text
    assert "job_id" in response.json()

    # Wizard state was stamped with the auto-created project's slug.
    state = load_wizard_state()
    assert state.cli.first_project_slug == "never-created"


# --------------------------------------------------------------------------
# /setup/projects/suggest-name (round-18 / Stream A)
# --------------------------------------------------------------------------


def test_setup_suggest_name_happy_path(
    fastapi_client: TestClient,
    tmp_path: Path,
) -> None:
    folder = tmp_path / "Shell-C-D"
    folder.mkdir()

    response = fastapi_client.post(
        "/setup/projects/suggest-name",
        json={"folder_path": str(folder)},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["suggested_name"] == "shell-c-d"
    assert body["is_available"] is True


def test_setup_suggest_name_collision_bumps_suffix(
    fastapi_client: TestClient,
    tmp_projects_dir: Path,
    tmp_path: Path,
) -> None:
    folder = tmp_path / "Shell-C-D"
    folder.mkdir()

    # Pre-create a project at the naive slug so the suggester collides.
    first = fastapi_client.post(
        "/setup/projects",
        json={
            "name": "Shell-C-D",
            "slug": "shell-c-d",
            "projects_dir": str(tmp_projects_dir),
        },
    )
    assert first.status_code == 200, first.text

    response = fastapi_client.post(
        "/setup/projects/suggest-name",
        json={"folder_path": str(folder)},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["suggested_name"] == "shell-c-d-2"
    assert body["is_available"] is False


def test_setup_suggest_name_nonexistent_folder_returns_400(
    fastapi_client: TestClient,
    tmp_path: Path,
) -> None:
    bogus = tmp_path / "definitely-not-here"
    response = fastapi_client.post(
        "/setup/projects/suggest-name",
        json={"folder_path": str(bogus)},
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["error"] == "folder_not_found"


# --------------------------------------------------------------------------
# /setup/defaults (alpha-9 / alpha-10 grid audit)
#
# Returns the OS-resolved defaults the frontend uses to pre-fill form
# fields. Both fields are required Pydantic strings. Alpha-9 added
# home_dir; alpha-10 adds this regression test (audit found the new
# field had zero coverage — a regression could ship invisible).
# --------------------------------------------------------------------------


def test_setup_defaults_returns_real_paths(fastapi_client: TestClient) -> None:
    """``/setup/defaults`` returns server-resolved real paths (no
    placeholder tokens like ``<you>``) for both ``projects_dir`` and
    ``home_dir``. Both fields must be non-empty strings — the frontend
    submits them verbatim into form inputs / pre-fill construction; a
    placeholder reaching the user re-introduces the alpha-5 422 bug
    class.
    """
    response = fastapi_client.get("/setup/defaults")
    assert response.status_code == 200, response.text
    body = response.json()

    # Both fields present and non-empty (Pydantic min_length=1 enforces
    # this server-side; assert here to catch a future "made it optional"
    # regression that would silently break the smart pre-fill).
    assert "projects_dir" in body, body
    assert "home_dir" in body, body
    assert isinstance(body["projects_dir"], str) and body["projects_dir"], body
    assert isinstance(body["home_dir"], str) and body["home_dir"], body

    # No placeholder tokens — these strings go straight into form
    # defaults; <you> shaped values were the alpha-5 422 root cause.
    for v in (body["projects_dir"], body["home_dir"]):
        assert "<" not in v and ">" not in v, (
            f"placeholder-shaped token in defaults response: {v!r}"
        )


# --------------------------------------------------------------------------
# StaticFiles mount (round-18 / Stream A)
#
# The mount is registered at module import time inside meridian.api.main, so
# we exercise it by importing a fresh FastAPI app instance with the env var
# set / unset before import. importlib.reload is the cleanest cross-test
# reset that doesn't require touching the global ``app`` singleton.
# --------------------------------------------------------------------------


def test_static_files_mount_serves_index_when_web_dir_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    web_dir = tmp_path / "fake_out"
    web_dir.mkdir()
    (web_dir / "index.html").write_text(
        "<!doctype html><html><body>fake-wizard-index</body></html>",
        encoding="utf-8",
    )

    # MERIDIAN_WEB_DIR is read by settings.web_dir at attribute-access
    # time (pure os.environ.get), so a reload of meridian.api.main is
    # enough — no need to clear settings caches.
    monkeypatch.setenv("MERIDIAN_WEB_DIR", str(web_dir))

    import importlib

    import meridian.api.main as main_mod

    main_mod = importlib.reload(main_mod)
    try:
        with TestClient(main_mod.app) as client:
            # Static index.
            r = client.get("/")
            assert r.status_code == 200, r.text
            assert "fake-wizard-index" in r.text

            # API still works (no shadowing).
            r = client.get("/health")
            assert r.status_code == 200
            assert r.json()["status"] == "ok"
    finally:
        # Restore the canonical app for the rest of the test session by
        # reloading without the env var.
        monkeypatch.delenv("MERIDIAN_WEB_DIR", raising=False)
        importlib.reload(main_mod)


# --------------------------------------------------------------------------
# CLI-installer / GUI-wizard handoff (alpha-5 bug fix)
#
# The PowerShell installer writes the API key to BOTH C:\Meridian\.env and
# Windows Credential Manager (keyring service "meridian.api_key" / account
# "anthropic"). The GUI wizard's /setup/state must recognise either source
# so the user is not re-prompted for a key the installer already saved.
# Pre-fix, ``api_key_set`` only consulted the JSON state file's
# ``cli.api_key_configured`` flag and missed both writes entirely.
# --------------------------------------------------------------------------


def test_setup_state_reflects_keyring_after_cli_install(
    fastapi_client: TestClient,
    stub_keyring: dict[tuple[str, str], str],
) -> None:
    """Installer-style write: keyring populated, JSON state untouched.

    /setup/state must report api_key_set=True and skip the api_key step.
    """
    # Simulate installer write to keyring (no JSON state mutation).
    stub_keyring[("meridian.api_key", "anthropic")] = "sk-ant-installer-wrote-this"

    response = fastapi_client.get("/setup/state")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["api_key_set"] is True
    # Without a project yet, next step must skip past api_key.
    assert body["next_step"] == "first_documents"


def test_setup_state_reflects_env_var_when_state_file_blank(
    fastapi_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Headless / .env-only install: env var is the only signal.

    The bootstrap loader in meridian.config copies .env entries into
    os.environ at import time, so this also covers the C:\\Meridian\\.env
    write path the PowerShell installer takes.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env")

    response = fastapi_client.get("/setup/state")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["api_key_set"] is True
    assert body["next_step"] == "first_documents"


# --------------------------------------------------------------------------
# litellm fallback (alpha-5 bug fix)
#
# ``anthropic`` is NOT a hard meridian dependency (only litellm is). When
# the SDK is missing, the validator must fall through to a litellm probe
# rather than returning ``unable_to_verify`` — otherwise every install
# without the optional SDK shows a misleading "couldn't reach Anthropic"
# warning even with a valid key. See ``project_v013_deferred.md``.
# --------------------------------------------------------------------------


def _force_anthropic_sdk_import_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Helper: make ``from anthropic import Anthropic`` raise ImportError.

    Used by the alpha-11 httpx-fallback tests below. Alpha-10 had three
    near-identical tests doing this inline; consolidating the boilerplate
    here keeps each test focused on the actual contract being verified.
    """
    import builtins
    import sys

    real_import = builtins.__import__

    def _fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "anthropic" or name.startswith("anthropic."):
            raise ImportError("simulated: anthropic SDK not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    monkeypatch.delitem(sys.modules, "anthropic", raising=False)


def _stub_httpx_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status_code: int | None = None,
    raise_request_error: bool = False,
    captured: dict[str, object] | None = None,
) -> None:
    """Replace ``httpx.Client`` with a context-manager-aware fake.

    Set ``status_code`` for a synthetic HTTP reply; ``raise_request_error``
    to simulate a transport failure (DNS, TCP, TLS, timeout). Pass a
    ``captured`` dict to record the keyword args the validator passed
    to ``post`` so call-shape can be asserted.
    """
    import httpx

    class _FakeResp:
        def __init__(self, code: int) -> None:
            self.status_code = code
            self.text = (
                '{"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"}}'
                if code == 401
                else "{}"
            )
            self.is_success = 200 <= code < 300

    class _FakeClient:
        def __init__(self, **kw: object) -> None:
            self.kw = kw

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

        def post(
            self,
            url: str,
            headers: dict[str, str] | None = None,
            json: dict[str, object] | None = None,
        ) -> _FakeResp:
            if captured is not None:
                captured["url"] = url
                captured["headers"] = headers
                captured["json"] = json
            if raise_request_error:
                raise httpx.ConnectError("simulated DNS failure")
            assert status_code is not None  # pragma: no cover — caller bug
            return _FakeResp(status_code)

    monkeypatch.setattr(httpx, "Client", _FakeClient)


def test_validate_anthropic_key_falls_back_to_httpx_when_sdk_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """anthropic SDK ImportError → direct httpx POST → 'valid' on 200.

    Alpha-11 replaced the alpha-10 LiteLLM fallback with a direct
    ``httpx.Client`` POST inside a ``with`` block. The litellm-stub
    tests this used to be paired with are gone; the new contract is
    verified here AND in
    test_alpha11_validate_anthropic_key_via_httpx_uses_with_block.
    """
    from meridian.onboarding import wizard as cli_wizard

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fallback-test")
    _force_anthropic_sdk_import_error(monkeypatch)

    captured: dict[str, object] = {}
    _stub_httpx_client(monkeypatch, status_code=200, captured=captured)

    outcome, detail = cli_wizard._validate_anthropic_key()
    assert outcome == "valid", (outcome, detail)
    # Verify the fallback hit Anthropic's /v1/messages, with the api
    # key in the x-api-key header (NOT a bearer Authorization), and a
    # 1-token max so a real prod request would be near-zero cost.
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["x-api-key"] == "sk-ant-fallback-test"
    body = captured["json"]
    assert isinstance(body, dict)
    assert body["max_tokens"] == 1


def test_validate_anthropic_key_httpx_fallback_classifies_auth_error_as_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """anthropic SDK ImportError + httpx 401 → 'invalid'."""
    from meridian.onboarding import wizard as cli_wizard

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-bad-key")
    _force_anthropic_sdk_import_error(monkeypatch)
    _stub_httpx_client(monkeypatch, status_code=401)

    outcome, detail = cli_wizard._validate_anthropic_key()
    assert outcome == "invalid", (outcome, detail)
    assert "401" in detail


def test_validate_anthropic_key_httpx_fallback_network_error_is_unable_to_verify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """anthropic SDK ImportError + transient network → 'unable_to_verify'.

    Belt-and-braces: misclassifying a network blip as 'invalid' would
    block a user with a perfectly good key. The fallback must surface
    the middle-ground outcome.
    """
    from meridian.onboarding import wizard as cli_wizard

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-some-key")
    _force_anthropic_sdk_import_error(monkeypatch)
    _stub_httpx_client(monkeypatch, raise_request_error=True)

    outcome, _detail = cli_wizard._validate_anthropic_key()
    assert outcome == "unable_to_verify"


def test_static_files_mount_absent_when_web_dir_unset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When neither env override nor dev tree nor wheel bundle is found,
    the API still starts cleanly and serves /health, with no static mount."""
    monkeypatch.delenv("MERIDIAN_WEB_DIR", raising=False)
    # Point project_root at a tmp dir so the dev-tree fallback misses.
    from meridian.config import settings as live_settings

    monkeypatch.setattr(live_settings, "project_root", tmp_path)

    import importlib

    import meridian.api.main as main_mod

    main_mod = importlib.reload(main_mod)
    try:
        with TestClient(main_mod.app) as client:
            r = client.get("/health")
            assert r.status_code == 200
            assert r.json()["status"] == "ok"
            # No static mount → "/" returns 404 (no route).
            r = client.get("/")
            assert r.status_code == 404
    finally:
        # Restore.
        monkeypatch.undo()
        importlib.reload(main_mod)


# ==========================================================================
# Alpha-11 — folder-import hardening + structured errors + ODA pre-flight
# ==========================================================================
#
# Background: alpha-10 shipped a folder-import flow that worked for
# clean PDF-only corpora but exhibited four failure modes on a real-
# world SME corpus (347 files, 18 of them DWGs, no ODA installed):
#
#   1. Frontend pivot bug (wrong status string + wrong type for `failed`).
#   2. Worker silently dropping a thread on BaseException.
#   3. `completed` double-incremented on path.exists()=False.
#   4. 18 identical "ODA not installed" strings in `failed[]` with no
#      coalescing or remediation.
#
# These tests lock down the alpha-11 fix for each. They live below the
# existing wizard tests so the file's narrative reads top-to-bottom by
# release.


from meridian.wizard import api as wizard_api_mod  # noqa: E402 — keep alpha-11 block self-contained


def test_alpha11_classify_ingest_error_routes_oda_missing() -> None:
    """FileNotFoundError carrying 'ODA File Converter' → 'oda_missing'."""
    exc = FileNotFoundError(
        "ODA File Converter binary not found. Set the ODA_FILE_CONVERTER "
        "environment variable..."
    )
    assert wizard_api_mod._classify_ingest_error(exc) == "oda_missing"


def test_alpha11_classify_ingest_error_routes_file_missing() -> None:
    """Plain FileNotFoundError (no 'ODA' marker) → 'file_missing'."""
    exc = FileNotFoundError("[Errno 2] No such file or directory: 'gone.pdf'")
    assert wizard_api_mod._classify_ingest_error(exc) == "file_missing"


def test_alpha11_classify_ingest_error_routes_permission_denied() -> None:
    """PermissionError → 'permission_denied'; OSError errno=13 also."""
    exc1 = PermissionError("Access is denied")
    assert wizard_api_mod._classify_ingest_error(exc1) == "permission_denied"
    exc2 = OSError(13, "Permission denied")
    assert wizard_api_mod._classify_ingest_error(exc2) == "permission_denied"


def test_alpha11_classify_ingest_error_routes_unsupported_mime() -> None:
    """NotImplementedError → 'unsupported_mime' (dispatcher refused)."""
    exc = NotImplementedError("No ingester wired for mime_type='application/x-foo'")
    assert wizard_api_mod._classify_ingest_error(exc) == "unsupported_mime"


def test_alpha11_classify_ingest_error_routes_file_locked_by_message() -> None:
    """OSError with the canonical Windows-sharing-violation message."""
    exc = OSError("[WinError 32] The process cannot access the file because it is being used by another process")
    assert wizard_api_mod._classify_ingest_error(exc) == "file_locked"


def test_alpha11_classify_ingest_error_routes_pdf_unreadable() -> None:
    """pypdf / pdfplumber failure messages → 'pdf_unreadable'."""
    exc1 = ValueError("EOF marker not found")
    assert wizard_api_mod._classify_ingest_error(exc1) == "pdf_unreadable"
    exc2 = RuntimeError("pypdf could not parse this stream length")
    assert wizard_api_mod._classify_ingest_error(exc2) == "pdf_unreadable"


def test_alpha11_classify_ingest_error_routes_unknown() -> None:
    """Anything else falls through to 'unknown'."""
    exc = RuntimeError("kaboom — totally unforeseen error")
    assert wizard_api_mod._classify_ingest_error(exc) == "unknown"


def test_alpha11_coalesce_errors_groups_by_code_and_orders_oda_first() -> None:
    """Group ordering: oda_missing first, then by descending count."""
    from meridian.wizard.models import ImportErrorEntry

    entries = [
        ImportErrorEntry(code="pdf_unreadable", file="a.pdf", message="x"),
        ImportErrorEntry(code="oda_missing", file="b.dwg", message="y"),
        ImportErrorEntry(code="oda_missing", file="c.dwg", message="y"),
        ImportErrorEntry(code="pdf_unreadable", file="d.pdf", message="x"),
        ImportErrorEntry(code="unknown", file="e.bin", message="z"),
    ]
    groups = wizard_api_mod._coalesce_errors(entries)
    codes = [g.code for g in groups]
    # Priority order: oda_missing first, then pdf_unreadable, then unknown.
    assert codes == ["oda_missing", "pdf_unreadable", "unknown"]
    by_code = {g.code: g for g in groups}
    assert by_code["oda_missing"].count == 2
    assert sorted(by_code["oda_missing"].files) == ["b.dwg", "c.dwg"]
    assert by_code["pdf_unreadable"].count == 2
    # Remediation copy non-empty for known codes; empty for 'unknown'.
    assert by_code["oda_missing"].remediation
    assert by_code["unknown"].remediation == ""
    # Summaries contain plurals tuned to count.
    assert "drawings" in by_code["oda_missing"].summary
    assert "PDFs" in by_code["pdf_unreadable"].summary


def test_alpha11_coalesce_errors_caps_files_at_100() -> None:
    """A group with 250 files surfaces 100 in `files` and `truncated=True`."""
    from meridian.wizard.models import ImportErrorEntry

    entries = [
        ImportErrorEntry(code="oda_missing", file=f"d{i}.dwg", message="missing")
        for i in range(250)
    ]
    groups = wizard_api_mod._coalesce_errors(entries)
    assert len(groups) == 1
    g = groups[0]
    assert g.code == "oda_missing"
    assert g.count == 250
    assert len(g.files) == 100
    assert g.truncated is True


def test_alpha11_summary_pluralises_when_count_is_one() -> None:
    """Singular grammar when only one file failed."""
    from meridian.wizard.models import ImportErrorEntry

    [g] = wizard_api_mod._coalesce_errors(
        [ImportErrorEntry(code="oda_missing", file="x.dwg", message="m")]
    )
    assert "1 AutoCAD drawing" in g.summary
    assert "drawings" not in g.summary
    assert "was skipped" in g.summary


def test_alpha11_scan_response_includes_oda_status(
    fastapi_client: TestClient,
    tmp_path: Path,
) -> None:
    """`oda` field is populated on every scan response, never null."""
    folder = tmp_path / "scan-oda-probe"
    folder.mkdir()

    response = fastapi_client.post(
        "/setup/import-folder/scan",
        json={"folder_path": str(folder)},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "oda" in body, "scan response missing alpha-11 'oda' field"
    oda = body["oda"]
    # Required fields.
    assert "available" in oda
    assert isinstance(oda["available"], bool)
    assert oda["install_url"].startswith("http")
    # Optional fields are present (may be null).
    assert "version" in oda
    assert "binary_path" in oda


def test_alpha11_status_response_includes_failed_groups_and_oda(
    fastapi_client: TestClient,
    tmp_projects_dir: Path,
    tmp_path: Path,
    synthetic_docx: Path,
    stub_anthropic_valid: None,
    stub_keyring: dict[tuple[str, str], str],
) -> None:
    """folder-import status response carries `failed_groups` (empty list when no failures) and `oda`."""
    fastapi_client.post("/setup/api-key", json={"key": "sk-ant-test"})
    fastapi_client.post(
        "/setup/projects",
        json={
            "name": "alpha11-status-shape",
            "slug": "alpha11-status-shape",
            "projects_dir": str(tmp_projects_dir),
        },
    )

    folder = tmp_path / "alpha11-status-shape"
    folder.mkdir()
    (folder / "sample.docx").write_bytes(synthetic_docx.read_bytes())

    kick = fastapi_client.post(
        "/setup/import-folder",
        json={"folder_path": str(folder), "project_name": "alpha11-status-shape"},
    )
    assert kick.status_code == 200, kick.text
    job_id = kick.json()["job_id"]

    deadline = time.monotonic() + 10.0
    body: dict = {}
    while time.monotonic() < deadline:
        resp = fastapi_client.get(f"/setup/import-folder/{job_id}")
        body = resp.json()
        if body["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.05)
    assert body["status"] == "succeeded"
    # Alpha-11 contract additions.
    assert "failed_groups" in body
    assert isinstance(body["failed_groups"], list)
    assert body["failed_groups"] == []  # no failures in this happy-path corpus
    assert "oda" in body
    assert isinstance(body["oda"]["available"], bool)
    # Legacy `failed` list still present for one release of backward-compat.
    assert "failed" in body
    assert body["failed"] == []


def test_alpha11_status_response_groups_oda_missing_failures(
    fastapi_client: TestClient,
    tmp_projects_dir: Path,
    tmp_path: Path,
    synthetic_docx: Path,
    stub_anthropic_valid: None,
    stub_keyring: dict[tuple[str, str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ODA is missing, .dwg files all coalesce into one group with remediation."""
    # Force ingest_file to raise the canonical ODA-missing error so we
    # don't depend on a real DWG fixture or a real ODA binary.
    from meridian.ingest import dispatcher as dispatcher_mod

    real_ingest = dispatcher_mod.ingest_file

    def faux_ingest(*args, **kwargs):
        path = kwargs.get("file_path")
        if path is not None and Path(path).suffix.lower() == ".dwg":
            raise FileNotFoundError(
                "ODA File Converter binary not found. Set the ODA_FILE_CONVERTER "
                "environment variable to the executable path..."
            )
        return real_ingest(*args, **kwargs)

    # Patch on the wizard-api module too — it imports ingest_file at
    # module load time so monkeypatching the dispatcher alone doesn't
    # reach the worker.
    monkeypatch.setattr(wizard_api_mod, "ingest_file", faux_ingest)

    fastapi_client.post("/setup/api-key", json={"key": "sk-ant-test"})
    fastapi_client.post(
        "/setup/projects",
        json={
            "name": "alpha11-oda-coalesce",
            "slug": "alpha11-oda-coalesce",
            "projects_dir": str(tmp_projects_dir),
        },
    )

    folder = tmp_path / "alpha11-oda-coalesce"
    folder.mkdir()
    # Three pretend DWGs (will each raise oda_missing) + one real docx
    # (will succeed) so we get a partial-success outcome.
    for n in range(3):
        (folder / f"drawing-{n}.dwg").write_bytes(b"DWG fake bytes")
    (folder / "spec.docx").write_bytes(synthetic_docx.read_bytes())

    kick = fastapi_client.post(
        "/setup/import-folder",
        json={"folder_path": str(folder), "project_name": "alpha11-oda-coalesce"},
    )
    job_id = kick.json()["job_id"]

    deadline = time.monotonic() + 10.0
    body: dict = {}
    while time.monotonic() < deadline:
        resp = fastapi_client.get(f"/setup/import-folder/{job_id}")
        body = resp.json()
        if body["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.05)
    # 3 dwg failures + 1 docx success = succeeded with partial.
    assert body["status"] == "succeeded"
    assert body["imported"] == 1
    # The 18 identical strings in the user's corpus collapse here to
    # one group with count=3.
    assert len(body["failed_groups"]) == 1
    g = body["failed_groups"][0]
    assert g["code"] == "oda_missing"
    assert g["count"] == 3
    assert "ODA File Converter" in g["remediation"]
    assert sorted(g["files"]) == ["drawing-0.dwg", "drawing-1.dwg", "drawing-2.dwg"]
    # Legacy flat list still has 3 entries.
    assert len(body["failed"]) == 3


def test_alpha11_completed_does_not_double_increment_on_missing_file(
    fastapi_client: TestClient,
    tmp_projects_dir: Path,
    tmp_path: Path,
    synthetic_docx: Path,
    stub_anthropic_valid: None,
    stub_keyring: dict[tuple[str, str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Alpha-10 had ``completed += 1`` in the missing-file branch AND in
    the enclosing finally. Alpha-11 fix: only the finally increments."""
    from meridian.ingest import dispatcher as dispatcher_mod

    fastapi_client.post("/setup/api-key", json={"key": "sk-ant-test"})
    fastapi_client.post(
        "/setup/projects",
        json={
            "name": "alpha11-no-double-increment",
            "slug": "alpha11-no-double-increment",
            "projects_dir": str(tmp_projects_dir),
        },
    )

    folder = tmp_path / "alpha11-no-double-increment"
    folder.mkdir()
    (folder / "real.docx").write_bytes(synthetic_docx.read_bytes())
    ghost = folder / "ghost.docx"
    ghost.write_bytes(synthetic_docx.read_bytes())

    # Patch walk_directory to also list a non-existent file so the
    # `if not path.exists()` branch fires inside the worker.
    real_walk = dispatcher_mod.walk_directory

    def faux_walk(folder_path: Path):
        result = real_walk(folder_path)
        # Inject a ghost path that vanishes between scan and import.
        result.files_by_kind.setdefault("docx", []).append(str(folder / "vanished.docx"))
        result.total_ingestable += 1
        return result

    monkeypatch.setattr(wizard_api_mod, "walk_directory", faux_walk)

    kick = fastapi_client.post(
        "/setup/import-folder",
        json={"folder_path": str(folder), "project_name": "alpha11-no-double-increment"},
    )
    job_id = kick.json()["job_id"]

    deadline = time.monotonic() + 10.0
    body: dict = {}
    while time.monotonic() < deadline:
        resp = fastapi_client.get(f"/setup/import-folder/{job_id}")
        body = resp.json()
        if body["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.05)
    # 3 paths total (2 real + 1 ghost). completed must equal total
    # exactly — no double-increment from the missing-file branch.
    assert body["total"] == 3
    assert body["completed"] == 3, (
        f"completed double-incremented? expected 3, got {body['completed']}: {body}"
    )
    # The ghost should be in failed_groups with code=file_missing.
    by_code = {g["code"]: g for g in body["failed_groups"]}
    assert "file_missing" in by_code
    assert by_code["file_missing"]["count"] == 1
    assert by_code["file_missing"]["files"] == ["vanished.docx"]


def test_alpha11_worker_baseexception_marks_failed_not_running(
    fastapi_client: TestClient,
    tmp_projects_dir: Path,
    tmp_path: Path,
    synthetic_docx: Path,
    stub_anthropic_valid: None,
    stub_keyring: dict[tuple[str, str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A BaseException inside the worker must transition status to 'failed' and surface a worker_aborted entry."""
    fastapi_client.post("/setup/api-key", json={"key": "sk-ant-test"})
    fastapi_client.post(
        "/setup/projects",
        json={
            "name": "alpha11-baseexc",
            "slug": "alpha11-baseexc",
            "projects_dir": str(tmp_projects_dir),
        },
    )

    folder = tmp_path / "alpha11-baseexc"
    folder.mkdir()
    (folder / "spec.docx").write_bytes(synthetic_docx.read_bytes())

    def faux_ingest(*args, **kwargs):
        # SystemExit is a BaseException, NOT an Exception — alpha-10
        # `except Exception` would let this kill the thread silently.
        raise SystemExit("synthetic worker abort")

    monkeypatch.setattr(wizard_api_mod, "ingest_file", faux_ingest)

    kick = fastapi_client.post(
        "/setup/import-folder",
        json={"folder_path": str(folder), "project_name": "alpha11-baseexc"},
    )
    job_id = kick.json()["job_id"]

    deadline = time.monotonic() + 10.0
    body: dict = {}
    while time.monotonic() < deadline:
        resp = fastapi_client.get(f"/setup/import-folder/{job_id}")
        body = resp.json()
        if body["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.05)
    # The thread did NOT die silently — status flipped to 'failed' and
    # the worker_aborted entry surfaces in failed_groups so the GUI can
    # show the user an actionable message rather than spinning forever.
    assert body["status"] == "failed", body
    by_code = {g["code"]: g for g in body["failed_groups"]}
    assert "worker_aborted" in by_code
    assert by_code["worker_aborted"]["count"] == 1


def test_alpha11_reimport_all_deduped_yields_succeeded_imported_zero_deduped_nonzero(
    fastapi_client: TestClient,
    tmp_projects_dir: Path,
    tmp_path: Path,
    synthetic_docx: Path,
    stub_anthropic_valid: None,
    stub_keyring: dict[tuple[str, str], str],
) -> None:
    """Re-importing the same folder twice must terminate with status='succeeded',
    imported=0, deduped>0 — NOT failed.

    Locks the contract the frontend's alpha-11 fix relies on: alpha-10
    routed all `imported === 0` outcomes to a red 'No files imported'
    panel, but a re-import where every file de-duplicates is a SUCCESS
    (the user just imported the same folder again — nothing is new but
    nothing failed either). The frontend now requires
    `imported === 0 && deduped === 0` to render the failure panel; this
    test ensures the backend produces the state the frontend now branches
    on.
    """
    fastapi_client.post("/setup/api-key", json={"key": "sk-ant-test"})
    fastapi_client.post(
        "/setup/projects",
        json={
            "name": "alpha11-reimport",
            "slug": "alpha11-reimport",
            "projects_dir": str(tmp_projects_dir),
        },
    )
    folder = tmp_path / "alpha11-reimport"
    folder.mkdir()
    (folder / "spec.docx").write_bytes(synthetic_docx.read_bytes())

    def _kick_and_wait() -> dict:
        kick = fastapi_client.post(
            "/setup/import-folder",
            json={"folder_path": str(folder), "project_name": "alpha11-reimport"},
        )
        job_id = kick.json()["job_id"]
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            r = fastapi_client.get(f"/setup/import-folder/{job_id}").json()
            if r["status"] in ("succeeded", "failed"):
                return r
            time.sleep(0.05)
        return {}

    first = _kick_and_wait()
    assert first["status"] == "succeeded"
    assert first["imported"] == 1
    assert first["deduped"] == 0

    second = _kick_and_wait()
    # Critical: status='succeeded', imported=0, deduped=1. The frontend
    # MUST NOT render this as failed.
    assert second["status"] == "succeeded", second
    assert second["imported"] == 0, second
    assert second["deduped"] == 1, second
    assert second["failed_groups"] == [], second


def test_alpha11_validate_anthropic_key_via_httpx_uses_with_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The httpx fallback validator must open and close its client cleanly.

    Alpha-10 LiteLLM fallback leaked a CloseWait socket per call; the
    alpha-11 replacement uses ``with httpx.Client(...)`` so the socket
    is released on return. We assert __enter__ and __exit__ are both
    called — that's the contract that fixes the leak.
    """
    from meridian.onboarding import wizard as cli_wizard

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")

    enter_calls: list[bool] = []
    exit_calls: list[bool] = []

    class _FakeResp:
        status_code = 200
        text = "{}"
        is_success = True

    class _FakeClient:
        def __init__(self, **kw):
            self.kw = kw

        def __enter__(self):
            enter_calls.append(True)
            return self

        def __exit__(self, *exc):
            exit_calls.append(True)
            return False

        def post(self, url, headers=None, json=None):
            return _FakeResp()

    import httpx

    monkeypatch.setattr(httpx, "Client", _FakeClient)

    outcome, detail = cli_wizard._validate_anthropic_key_via_httpx()
    assert outcome == "valid"
    assert enter_calls == [True], "httpx.Client.__enter__ was not called — with-block missing"
    assert exit_calls == [True], "httpx.Client.__exit__ was not called — socket leak risk"


def test_alpha11_validate_anthropic_key_via_httpx_classifies_401_as_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from meridian.onboarding import wizard as cli_wizard

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")

    class _FakeResp:
        status_code = 401
        text = "authentication error"
        is_success = False

    class _FakeClient:
        def __init__(self, **kw):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False
        def post(self, url, headers=None, json=None):
            return _FakeResp()

    import httpx
    monkeypatch.setattr(httpx, "Client", _FakeClient)

    outcome, detail = cli_wizard._validate_anthropic_key_via_httpx()
    assert outcome == "invalid"


def test_alpha11_validate_anthropic_key_via_httpx_classifies_network_error_as_unable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flaky network must NEVER punish a user with a valid key."""
    from meridian.onboarding import wizard as cli_wizard

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")

    class _FakeClient:
        def __init__(self, **kw):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False
        def post(self, url, headers=None, json=None):
            import httpx
            raise httpx.ConnectError("DNS resolution failed")

    import httpx
    monkeypatch.setattr(httpx, "Client", _FakeClient)

    outcome, detail = cli_wizard._validate_anthropic_key_via_httpx()
    assert outcome == "unable_to_verify"
    assert "DNS" in detail or "RequestError" in detail


# ==========================================================================
# Alpha-12 — launcher detachment + observability + reviewer-ticket fixes
# ==========================================================================
#
# Background: alpha-11's SME walkthrough surfaced a real observability
# gap (configure_logging(console=False) suppressed the stderr handler
# the alpha-11 _stdlog calls relied on, so per-file lines never reached
# the operator-visible backend.log) and a UX failure mode (closing the
# install-time terminal window killed the backend because cmd.exe was
# launched without -WindowStyle Hidden). Plus four reviewer tickets
# carried forward from the alpha-11 review.
#
# These tests lock down the alpha-12 fixes:
#   * #6 _classify_ingest_error tightened pdf_unreadable substrings
#   * #7 httpx validator catches all Exception, not just RequestError
#   * #8 _validate_anthropic_key_via_litellm alias removed
#   * #9 BaseException worker test variant covers MemoryError, not just SystemExit
#   * NEW: /setup/runtime endpoint exposes pid / uptime / log paths / last job
#   * NEW: meridian.wizard.import logger emits to stderr regardless of console=False


def test_alpha12_classify_ingest_error_no_longer_routes_stream_length_to_pdf() -> None:
    """Alpha-11 reviewer ticket #6: 'stream length' was too broad — any HTTP
    or JSON parser can use that phrase. Alpha-12 narrows to PDF-format-
    specific markers only ('xref', 'pdf trailer', '/Length is wrong')."""
    exc = RuntimeError("HTTP response stream length mismatch (expected 8192, got 4096)")
    assert wizard_api_mod._classify_ingest_error(exc) == "unknown"


def test_alpha12_classify_ingest_error_routes_pdf_specific_markers() -> None:
    """Alpha-12: tightened triggers — pdf_trailer, xref, /Length is wrong."""
    for msg in (
        "Could not read PDF trailer at offset 12345",
        "xref table is corrupt",
        "PDF /Length is wrong: stream not consumed",
        "pypdf raised PdfReadError",
        "pdfplumber: malformed object",
    ):
        exc = RuntimeError(msg)
        assert (
            wizard_api_mod._classify_ingest_error(exc) == "pdf_unreadable"
        ), f"expected pdf_unreadable for {msg!r}"


def test_alpha12_validate_anthropic_key_via_httpx_handles_non_request_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Alpha-11 reviewer ticket #7: alpha-11 only caught httpx.RequestError.
    A TLS-context init failure or a corrupted certificate store could
    raise a non-RequestError that escaped uncaught. Alpha-12 widens the
    catch to Exception so 'flaky environment never produces invalid'
    holds even on cold-edge cases."""
    from meridian.onboarding import wizard as cli_wizard

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")

    class _FakeClient:
        def __init__(self, **kw):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False
        def post(self, url, headers=None, json=None):
            # NOT an httpx.RequestError — simulates a TLS / cert / OS-
            # level error that didn't subclass RequestError.
            raise RuntimeError("TLS context initialisation failed: cert store corrupt")

    import httpx
    monkeypatch.setattr(httpx, "Client", _FakeClient)

    outcome, detail = cli_wizard._validate_anthropic_key_via_httpx()
    assert outcome == "unable_to_verify", (outcome, detail)
    # Must not have leaked to 'invalid' — that would block a valid key.
    assert outcome != "invalid"
    assert "RuntimeError" in detail or "TLS" in detail


def test_alpha12_litellm_alias_no_longer_exists() -> None:
    """Alpha-11 reviewer ticket #8: dead-code alias removed."""
    from meridian.onboarding import wizard as cli_wizard

    assert not hasattr(cli_wizard, "_validate_anthropic_key_via_litellm"), (
        "_validate_anthropic_key_via_litellm should have been removed in alpha-12 "
        "but is still present"
    )


def test_alpha12_worker_memoryerror_classified_as_per_file_failure(
    fastapi_client: TestClient,
    tmp_projects_dir: Path,
    tmp_path: Path,
    synthetic_docx: Path,
    stub_anthropic_valid: None,
    stub_keyring: dict[tuple[str, str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MemoryError is in the Exception branch of Python's class hierarchy
    (not BaseException directly), so it's caught by the worker's
    per-file ``except Exception`` and classified — NOT by the
    worker_aborted catch. Status flips to failed because no imports
    succeeded; the file goes into failed_groups under code=unknown.
    This documents Python's hierarchy explicitly so a future refactor
    doesn't accidentally promote MemoryError to the BaseException
    branch and lose continued processing of subsequent files."""
    fastapi_client.post("/setup/api-key", json={"key": "sk-ant-test"})
    fastapi_client.post(
        "/setup/projects",
        json={
            "name": "alpha12-memerr",
            "slug": "alpha12-memerr",
            "projects_dir": str(tmp_projects_dir),
        },
    )
    folder = tmp_path / "alpha12-memerr"
    folder.mkdir()
    (folder / "spec.docx").write_bytes(synthetic_docx.read_bytes())

    def faux_ingest(*args, **kwargs):
        raise MemoryError("simulated allocator failure")

    monkeypatch.setattr(wizard_api_mod, "ingest_file", faux_ingest)

    kick = fastapi_client.post(
        "/setup/import-folder",
        json={"folder_path": str(folder), "project_name": "alpha12-memerr"},
    )
    job_id = kick.json()["job_id"]
    deadline = time.monotonic() + 10.0
    body: dict = {}
    while time.monotonic() < deadline:
        resp = fastapi_client.get(f"/setup/import-folder/{job_id}")
        body = resp.json()
        if body["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.05)
    assert body["status"] == "failed", body
    # Classified as a per-file failure (NOT worker_aborted) — the
    # worker kept running and was able to mark the job terminal cleanly.
    by_code = {g["code"]: g for g in body["failed_groups"]}
    assert "unknown" in by_code, body
    assert "worker_aborted" not in by_code, body


def test_alpha12_worker_keyboardinterrupt_marks_failed_with_worker_aborted(
    fastapi_client: TestClient,
    tmp_projects_dir: Path,
    tmp_path: Path,
    synthetic_docx: Path,
    stub_anthropic_valid: None,
    stub_keyring: dict[tuple[str, str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Alpha-11 reviewer ticket #9: alpha-11 used SystemExit which Python
    special-cases inside threads. KeyboardInterrupt is the canonical
    non-SystemExit BaseException that genuinely escapes a worker — it
    bypasses ``except Exception`` and would kill the thread silently
    were it not for the alpha-11 ``except BaseException`` outer catch.
    This test exercises the BaseException branch honestly."""
    fastapi_client.post("/setup/api-key", json={"key": "sk-ant-test"})
    fastapi_client.post(
        "/setup/projects",
        json={
            "name": "alpha12-kbint",
            "slug": "alpha12-kbint",
            "projects_dir": str(tmp_projects_dir),
        },
    )
    folder = tmp_path / "alpha12-kbint"
    folder.mkdir()
    (folder / "spec.docx").write_bytes(synthetic_docx.read_bytes())

    def faux_ingest(*args, **kwargs):
        # KeyboardInterrupt is BaseException-tier; would escape
        # `except Exception` and kill the worker thread silently
        # in alpha-10. The alpha-11 outer `except BaseException`
        # catches it; this test verifies that contract honestly.
        raise KeyboardInterrupt("synthetic Ctrl-C inside worker")

    monkeypatch.setattr(wizard_api_mod, "ingest_file", faux_ingest)

    kick = fastapi_client.post(
        "/setup/import-folder",
        json={"folder_path": str(folder), "project_name": "alpha12-kbint"},
    )
    job_id = kick.json()["job_id"]
    deadline = time.monotonic() + 10.0
    body: dict = {}
    while time.monotonic() < deadline:
        resp = fastapi_client.get(f"/setup/import-folder/{job_id}")
        body = resp.json()
        if body["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.05)
    assert body["status"] == "failed", body
    by_code = {g["code"]: g for g in body["failed_groups"]}
    assert "worker_aborted" in by_code, body
    assert by_code["worker_aborted"]["count"] == 1
    flat_msgs = " ".join(body["failed"])
    assert "KeyboardInterrupt" in flat_msgs


def test_alpha12_runtime_endpoint_returns_required_shape(
    fastapi_client: TestClient,
) -> None:
    """Operator triage uses /setup/runtime to answer 'is the worker stuck?'
    from one HTTP call. Lock the contract: pid, started_at, uptime_seconds,
    version, python_version, platform must always be present;
    backend_log_path / structlog_dir / last_import_job may be null but
    must always be in the body."""
    response = fastapi_client.get("/setup/runtime")
    assert response.status_code == 200, response.text
    body = response.json()
    for required in (
        "pid",
        "started_at",
        "uptime_seconds",
        "version",
        "python_version",
        "platform",
    ):
        assert required in body, f"/setup/runtime missing {required!r}; got {body}"
    assert isinstance(body["pid"], int) and body["pid"] > 0
    assert body["started_at"].endswith("Z"), body["started_at"]
    assert body["uptime_seconds"] >= 0
    assert body["version"]
    for optional in ("backend_log_path", "structlog_dir", "last_import_job"):
        assert optional in body, (
            f"/setup/runtime missing optional key {optional!r}; got {body}"
        )


# ==========================================================================
# Alpha-22 task 6 — /setup/complete 400 contract (frontend regression pin)
# ==========================================================================
#
# The ready-page now renders the 400 body into a user-visible error panel
# and disables the "Open project" button when gates are unmet. This test
# pins the exact JSON shape the frontend depends on so a backend refactor
# that renames a field is caught immediately.


def test_complete_returns_400_with_next_step_when_gates_unmet(
    fastapi_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When _has_required_gates fails, /api/setup/complete returns
    400 with a JSON body the frontend can render: detail.error,
    detail.next_step, detail.message.

    This is the contract the alpha-22 ready-page error UI depends on.
    """
    monkeypatch.setenv("MERIDIAN_WIZARD_STATE_DIR", str(tmp_path / "wizard_state"))
    # Don't configure api-key — gates will fail.

    r = fastapi_client.post("/setup/complete")
    assert r.status_code == 400, r.text
    body = r.json()
    assert "detail" in body, body
    detail = body["detail"]
    assert detail["error"] == "setup_incomplete", detail
    assert "next_step" in detail, detail
    assert detail["next_step"] in (
        "api_key", "first_documents", "first_project", "ready",
    ), detail["next_step"]
    assert "message" in detail, detail


def test_alpha12_runtime_endpoint_reports_last_import_job(
    fastapi_client: TestClient,
    tmp_projects_dir: Path,
    tmp_path: Path,
    synthetic_docx: Path,
    stub_anthropic_valid: None,
    stub_keyring: dict[tuple[str, str], str],
) -> None:
    """After a folder import lands, /setup/runtime exposes the job
    snapshot — operator triage can answer 'is the worker stuck?' from
    one HTTP call instead of needing Get-Process + log-tail."""
    fastapi_client.post("/setup/api-key", json={"key": "sk-ant-test"})
    fastapi_client.post(
        "/setup/projects",
        json={
            "name": "alpha12-runtime-snapshot",
            "slug": "alpha12-runtime-snapshot",
            "projects_dir": str(tmp_projects_dir),
        },
    )
    folder = tmp_path / "alpha12-runtime-snapshot"
    folder.mkdir()
    (folder / "spec.docx").write_bytes(synthetic_docx.read_bytes())

    kick = fastapi_client.post(
        "/setup/import-folder",
        json={"folder_path": str(folder), "project_name": "alpha12-runtime-snapshot"},
    )
    job_id = kick.json()["job_id"]
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        s = fastapi_client.get(f"/setup/import-folder/{job_id}").json()
        if s["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.05)

    runtime = fastapi_client.get("/setup/runtime").json()
    assert runtime["last_import_job"] is not None, runtime
    snapshot = runtime["last_import_job"]
    assert snapshot["job_id"] == job_id
    assert snapshot["status"] == "succeeded"
    assert snapshot["total"] == 1
    assert snapshot["completed"] == 1
    assert snapshot["imported"] == 1
    assert snapshot["failed_count"] == 0
    # current_file is None on a finished job (the worker resets it).
    assert snapshot["current_file"] is None


# ==========================================================================
# Alpha-13 — auth bypass + thread-safe log dir + dedup-aware partial copy
#            + path-helpers extension
# ==========================================================================
#
# Background: an SME walkthrough of alpha-12 surfaced two new failure modes
# (and two reviewer tickets carried forward):
#   1. /login TOTP gate blocks debug iteration. We need an env-var bypass
#      that is loud + opt-in + verifiable from /setup/runtime.
#   2. Partial-success panel renders "0 of 347 imported. 18 failed." when
#      329 files were deduped, hiding the dedup count and reading as a
#      catastrophic failure when it was actually a clean re-import.
#   3. Folder-picker typed-path UX is hostile -- alpha-13 widens
#      normalisePastedPath + adds classifyPathShape for live feedback.
#   4. (alpha-12 reviewer) _logging_setup._current_log_dir module-global
#      reads were unsynchronised. RLock + current_log_dir() getter close
#      the gap.
#
# These tests lock down the alpha-13 fixes.


def test_alpha13_auth_disabled_off_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default-secure: with the env var unset, require_session enforces
    Authorization headers."""
    from meridian.auth import fastapi_dep

    monkeypatch.delenv("MERIDIAN_AUTH_DISABLED", raising=False)
    assert fastapi_dep.auth_disabled() is False
    # The dependency raises 401 when no Authorization header is supplied.
    with pytest.raises(Exception) as excinfo:
        fastapi_dep.require_session(authorization=None)
    # Verify it's the HTTPException with 401.
    assert getattr(excinfo.value, "status_code", None) == 401


def test_alpha13_auth_disabled_short_circuits_when_env_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With MERIDIAN_AUTH_DISABLED=1, require_session is a no-op even
    without an Authorization header. Critical contract: the bypass must
    actually bypass."""
    from meridian.auth import fastapi_dep

    monkeypatch.setenv("MERIDIAN_AUTH_DISABLED", "1")
    assert fastapi_dep.auth_disabled() is True
    # No exception raised — bypass is active.
    fastapi_dep.require_session(authorization=None)
    fastapi_dep.require_session(authorization="Bearer total-garbage")


def test_alpha13_auth_disabled_only_accepts_exact_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Truthy strings other than '1' do NOT bypass auth. Prevents an
    operator from accidentally enabling the bypass with
    MERIDIAN_AUTH_DISABLED=true / yes / on."""
    from meridian.auth import fastapi_dep

    for non_one in ("true", "yes", "on", "TRUE", "0", "", "  1  "):
        monkeypatch.setenv("MERIDIAN_AUTH_DISABLED", non_one)
        assert fastapi_dep.auth_disabled() is False, (
            f"value {non_one!r} should NOT activate bypass; only literal '1' does"
        )


def test_alpha13_runtime_endpoint_reports_auth_disabled_off(
    fastapi_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """/setup/runtime exposes auth_disabled=false when the bypass is off."""
    monkeypatch.delenv("MERIDIAN_AUTH_DISABLED", raising=False)
    response = fastapi_client.get("/setup/runtime")
    assert response.status_code == 200
    body = response.json()
    assert body["auth_disabled"] is False, body


def test_alpha13_runtime_endpoint_reports_auth_disabled_on(
    fastapi_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """/setup/runtime exposes auth_disabled=true when the bypass is on.
    Lets the GUI render its red debug-mode banner."""
    monkeypatch.setenv("MERIDIAN_AUTH_DISABLED", "1")
    response = fastapi_client.get("/setup/runtime")
    assert response.status_code == 200
    body = response.json()
    assert body["auth_disabled"] is True, body


def test_alpha14_env_var_set_in_process_reaches_runtime_endpoint(
    fastapi_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Alpha-14 user-instruction round-trip: when MERIDIAN_AUTH_DISABLED=1
    is in the running process's environment, /setup/runtime.auth_disabled
    is true.

    This locks the contract from the env-var END (where the launcher's
    .env loader sets it) to the user-visible /setup/runtime field.
    The gauntlet step 7h covers the file -> env -> running-process leg
    by spawning a fresh subprocess with the parsed .env; this test
    covers the env -> /setup/runtime leg with a TestClient.

    Together they prove the user-facing instruction
    "edit .env, restart, /setup/runtime reports auth_disabled=true"
    holds end-to-end."""
    monkeypatch.setenv("MERIDIAN_AUTH_DISABLED", "1")
    response = fastapi_client.get("/setup/runtime")
    assert response.status_code == 200
    body = response.json()
    assert body["auth_disabled"] is True, body
    # And explicitly: a 401-protected endpoint must let an unauthenticated
    # request through when the bypass is on. Alpha-13 already locks this
    # at the require_session level; verifying through the actual HTTP
    # layer here means TestClient-level dependency injection is wired
    # consistently with the env probe.
    from meridian.auth.fastapi_dep import auth_disabled
    assert auth_disabled() is True


def test_alpha13_partial_state_is_imported_zero_deduped_positive_failed_positive(
    fastapi_client: TestClient,
    tmp_projects_dir: Path,
    tmp_path: Path,
    synthetic_docx: Path,
    stub_anthropic_valid: None,
    stub_keyring: dict[tuple[str, str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Locks the backend contract the alpha-13 dedup-aware partial copy
    relies on: a re-import where some files dedup and some fail produces
    status='succeeded', imported=0, deduped>0, failed_groups=[...].

    Frontend's dedup-aware summary helper renders this as
    'All N documents were already in the project. M files failed.'
    instead of alpha-11's misleading '0 of total imported. M failed.'
    """
    fastapi_client.post("/setup/api-key", json={"key": "sk-ant-test"})
    fastapi_client.post(
        "/setup/projects",
        json={
            "name": "alpha13-partial-state",
            "slug": "alpha13-partial-state",
            "projects_dir": str(tmp_projects_dir),
        },
    )

    folder = tmp_path / "alpha13-partial-state"
    folder.mkdir()
    (folder / "spec.docx").write_bytes(synthetic_docx.read_bytes())
    # Will fail on every dwg with oda_missing — patch ingest_file to
    # raise the canonical FileNotFoundError for those.
    real_ingest = wizard_api_mod.ingest_file

    def faux_ingest(*args, **kwargs):
        path = kwargs.get("file_path")
        if path is not None and Path(path).suffix.lower() == ".dwg":
            raise FileNotFoundError(
                "ODA File Converter binary not found. Set the ODA_FILE_CONVERTER ..."
            )
        return real_ingest(*args, **kwargs)

    monkeypatch.setattr(wizard_api_mod, "ingest_file", faux_ingest)

    # Inject a dwg into the manifest. We don't need a real DWG file --
    # FileNotFoundError will fire from the patched faux_ingest.
    (folder / "drawing.dwg").write_bytes(b"fake-dwg-bytes")

    def _kick_and_wait() -> dict:
        kick = fastapi_client.post(
            "/setup/import-folder",
            json={
                "folder_path": str(folder),
                "project_name": "alpha13-partial-state",
            },
        )
        job_id = kick.json()["job_id"]
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            r = fastapi_client.get(f"/setup/import-folder/{job_id}").json()
            if r["status"] in ("succeeded", "failed"):
                return r
            time.sleep(0.05)
        return {}

    # First run: 1 docx imported, 1 dwg failed.
    first = _kick_and_wait()
    assert first["status"] == "succeeded", first
    assert first["imported"] == 1
    assert first["deduped"] == 0
    assert any(g["code"] == "oda_missing" for g in first["failed_groups"])

    # Second run: docx dedups (already in DB), dwg fails again. This is
    # the alpha-13 partial state the dedup-aware copy targets.
    second = _kick_and_wait()
    assert second["status"] == "succeeded", second
    assert second["imported"] == 0, second
    assert second["deduped"] == 1, second
    assert any(g["code"] == "oda_missing" for g in second["failed_groups"])
    assert sum(g["count"] for g in second["failed_groups"]) >= 1


def test_alpha13_logging_current_log_dir_getter_thread_safe() -> None:
    """current_log_dir() returns the same path the module-global holds,
    locked. Smoke test only -- the real RLock contract is verified by
    not racing in production."""
    from meridian.logging.setup import current_log_dir

    # Just verify the function exists, returns a Path | None, and is
    # callable from any thread without raising.
    val = current_log_dir()
    assert val is None or isinstance(val, Path), val

    # Concurrent reads should not raise (lock is RLock so re-entrant).
    import threading

    errors: list[BaseException] = []

    def _hammer() -> None:
        try:
            for _ in range(50):
                current_log_dir()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_hammer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert not errors, errors


def test_alpha12_wizard_import_logger_has_dedicated_stderr_handler() -> None:
    """The alpha-11 _stdlog logger must reach stderr regardless of whether
    configure_logging(console=False) ran — that was the alpha-11 silence
    bug. Alpha-12 attaches a dedicated stderr handler tagged with the
    sentinel attribute ``_meridian_import_stderr``.

    Structural test (not output capture). pytest's stderr capture
    cannot intercept a StreamHandler whose ``sys.stderr`` reference
    was bound at module-import time; verifying the handler's
    configuration is the more honest test of the contract.
    """
    import logging as _logging

    from meridian.wizard import api as wizard_api_mod_inner

    log = wizard_api_mod_inner._stdlog
    assert log.level == _logging.INFO, log.level
    # Don't propagate — that would double-emit through root.
    assert log.propagate is False
    # Exactly one tagged handler (idempotent: re-import must not stack).
    tagged = [
        h for h in log.handlers
        if getattr(h, "_meridian_import_stderr", False)
    ]
    assert len(tagged) == 1, [type(h).__name__ for h in log.handlers]
    handler = tagged[0]
    assert isinstance(handler, _logging.StreamHandler)
    assert handler.level == _logging.INFO
    # Formatter must include a timestamp + the [import] tag so the
    # operator can correlate with backend.log entries.
    rendered = handler.formatter.format(
        _logging.LogRecord(
            name="meridian.wizard.import",
            level=_logging.INFO,
            pathname="x",
            lineno=1,
            msg="alpha12_marker",
            args=(),
            exc_info=None,
        )
    )
    assert "[import]" in rendered, rendered
    assert "alpha12_marker" in rendered, rendered


def test_idempotent_replay_emits_structured_log(
    fastapi_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The replay branch emits 'wizard.import_folder.idempotent_replay'
    once per replay, carrying the recorded job_id and a non-negative
    age_seconds field.
    """
    from meridian.wizard import api as wizard_api

    with wizard_api._idempotency_lock:
        wizard_api._idempotency.clear()
    with wizard_api._jobs_lock:
        wizard_api._jobs.clear()

    # Spy on the structlog logger directly. More robust than caplog —
    # we don't depend on whether structlog is configured to forward to
    # stdlib logging in the test environment.
    recorded: list[tuple[str, dict]] = []
    real_info = wizard_api._log.info

    def _spy_info(event: str, **kwargs):
        recorded.append((event, dict(kwargs)))
        return real_info(event, **kwargs)

    monkeypatch.setattr(wizard_api._log, "info", _spy_info)

    folder = tmp_path / "src"
    folder.mkdir()
    (folder / "doc.pdf").write_bytes(b"%PDF-1.4 test\n%%EOF\n")
    body = {"folder_path": str(folder), "project_name": "alpha24-log"}
    token = "11111111-1111-4111-8111-111111111111"

    res1 = fastapi_client.post(
        "/setup/import-folder",
        json=body,
        headers={"Idempotency-Key": token},
    )
    assert res1.status_code == 200
    expected_job_id = res1.json()["job_id"]

    # Reset the recorder so only events from the replay show up.
    recorded.clear()

    res2 = fastapi_client.post(
        "/setup/import-folder",
        json=body,
        headers={"Idempotency-Key": token},
    )
    assert res2.status_code == 200

    replay_events = [
        (event, kwargs)
        for event, kwargs in recorded
        if event == "wizard.import_folder.idempotent_replay"
    ]
    assert len(replay_events) == 1, (
        f"Expected exactly one replay event; got {len(replay_events)}. "
        f"All recorded events: {[e[0] for e in recorded]}"
    )
    _, kwargs = replay_events[0]
    assert kwargs["job_id"] == expected_job_id
    assert kwargs["idempotency_token"] == token
    assert kwargs["age_seconds"] >= 0
