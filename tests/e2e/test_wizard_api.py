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


def test_validate_anthropic_key_falls_back_to_litellm_when_sdk_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """anthropic SDK ImportError → litellm.completion() probe → 'valid'."""
    import builtins
    import sys

    from meridian.onboarding import wizard as cli_wizard

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fallback-test")

    # Force `from anthropic import Anthropic` to raise ImportError, even if
    # the SDK happens to be installed in the test environment.
    real_import = builtins.__import__

    def _fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "anthropic" or name.startswith("anthropic."):
            raise ImportError("simulated: anthropic SDK not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    # Also evict any cached module so the lazy import re-runs the hook.
    monkeypatch.delitem(sys.modules, "anthropic", raising=False)

    # Stub litellm.completion to short-circuit network. We only care that
    # the function is *called* with the right shape and returns success.
    captured: dict[str, object] = {}

    def _fake_completion(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()  # the validator only checks for non-exception

    import litellm

    monkeypatch.setattr(litellm, "completion", _fake_completion)

    outcome, detail = cli_wizard._validate_anthropic_key()
    assert outcome == "valid", (outcome, detail)
    # Verify the fallback actually used litellm, not some other path.
    assert captured["api_key"] == "sk-ant-fallback-test"
    assert captured["max_tokens"] == 1
    # litellm uses the "anthropic/" prefix to route to Anthropic.
    assert str(captured["model"]).startswith("anthropic/")


def test_validate_anthropic_key_litellm_fallback_classifies_auth_error_as_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """anthropic SDK ImportError + litellm AuthenticationError → 'invalid'."""
    import builtins
    import sys

    from meridian.onboarding import wizard as cli_wizard

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-bad-key")

    real_import = builtins.__import__

    def _fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "anthropic" or name.startswith("anthropic."):
            raise ImportError("simulated: anthropic SDK not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    monkeypatch.delitem(sys.modules, "anthropic", raising=False)

    import litellm
    from litellm.exceptions import AuthenticationError

    def _raise_auth(**kwargs: object) -> object:
        raise AuthenticationError(
            message="401 Unauthorized: invalid x-api-key",
            llm_provider="anthropic",
            model="claude-haiku-4-5-20251001",
        )

    monkeypatch.setattr(litellm, "completion", _raise_auth)

    outcome, detail = cli_wizard._validate_anthropic_key()
    assert outcome == "invalid", (outcome, detail)
    assert "AuthenticationError" in detail


def test_validate_anthropic_key_litellm_fallback_network_error_is_unable_to_verify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """anthropic SDK ImportError + transient network → 'unable_to_verify'.

    Belt-and-braces: misclassifying a network blip as 'invalid' would block
    a user with a perfectly good key. The fallback must surface the
    middle-ground outcome.
    """
    import builtins
    import sys

    from meridian.onboarding import wizard as cli_wizard

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-some-key")

    real_import = builtins.__import__

    def _fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "anthropic" or name.startswith("anthropic."):
            raise ImportError("simulated: anthropic SDK not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    monkeypatch.delitem(sys.modules, "anthropic", raising=False)

    import litellm

    def _raise_connection(**kwargs: object) -> object:
        raise ConnectionError("simulated DNS failure")

    monkeypatch.setattr(litellm, "completion", _raise_connection)

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
