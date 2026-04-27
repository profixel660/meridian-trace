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
    """Clear the rate-limit bucket and the in-memory job registry before each test.

    ``tmp_projects_dir`` already isolates the JSON state file under a
    per-test tmp dir; we additionally clear the module-level dicts.
    """
    wizard_api._rate_buckets.clear()
    with wizard_api._jobs_lock:
        wizard_api._jobs.clear()
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
    assert state_response["next_step"] == "first_project"


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
