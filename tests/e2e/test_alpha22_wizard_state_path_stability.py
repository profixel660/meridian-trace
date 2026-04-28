"""Alpha-22 regression: wizard state must survive a mid-process
mutation of settings.data_dir.

Before alpha-22 the wizard mutated settings.data_dir at
POST /api/setup/projects time, which relocated state_path() to a new
folder, orphaning prior wizard progress (api_key + import counters).
This test pins the contract: state_path() is stable regardless of
settings.data_dir.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meridian.config import settings
from meridian.onboarding.wizard import (
    _legacy_state_path,
    _stable_user_state_dir,
    state_path,
)
from meridian.wizard.state import (
    load_wizard_state,
    mark_documents_imported,
    save_wizard_state,
)


def test_state_path_independent_of_settings_data_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutating settings.data_dir must NOT relocate the wizard state file."""
    # M3: use monkeypatch so teardown restores settings.data_dir.
    monkeypatch.setattr(settings, "data_dir", tmp_path / "before")
    path_before = state_path()

    monkeypatch.setattr(settings, "data_dir", tmp_path / "after")
    path_after = state_path()

    assert path_before == path_after, (
        f"state_path() relocated when settings.data_dir changed: "
        f"{path_before} != {path_after}. "
        "Wizard state must live at a fixed location, not under projects_dir."
    )

    # M2: pin resolution rule — state_path() must resolve under _stable_user_state_dir()
    # when no MERIDIAN_WIZARD_STATE_DIR override is set.
    # (The conftest.py tmp_projects_dir fixture sets the env override; here we
    # check the parent directly so the test passes in both contexts.)
    assert state_path().parent == _stable_user_state_dir(), (
        f"state_path() resolves under {state_path().parent!r} but "
        f"_stable_user_state_dir() returned {_stable_user_state_dir()!r}. "
        "The resolution rule has drifted — fix state_path()."
    )


def test_wizard_state_round_trips_across_data_dir_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Save state, mutate data_dir, load state — values must match."""
    # Force the fixed state path to a tmp location for hermetic testing.
    # Patch both the source module AND the re-exported reference in wizard.state
    # (``from meridian.onboarding.wizard import state_path`` binds by value
    # at import time, so the wizard.state module's own ``state_path`` name
    # must be patched separately).
    fake_state = tmp_path / "wizard_state.json"
    monkeypatch.setattr(
        "meridian.onboarding.wizard.state_path",
        lambda: fake_state,
    )
    monkeypatch.setattr(
        "meridian.wizard.state.state_path",
        lambda: fake_state,
    )

    # M3: use monkeypatch for data_dir mutations.
    monkeypatch.setattr(settings, "data_dir", tmp_path / "first")
    state = load_wizard_state()
    mark_documents_imported(state, count=4)

    monkeypatch.setattr(settings, "data_dir", tmp_path / "second")
    reloaded = load_wizard_state()

    assert reloaded.gui_documents_imported == 4, (
        "wizard documents-imported counter was orphaned by data_dir change"
    )


def test_alpha22_legacy_state_file_migrates_on_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Alpha-21 -> alpha-22 upgrade path: legacy state migrates on first load.

    Scenario: user had alpha-21 state at <projects_dir>/_meridian/onboarding_state.json.
    After upgrade to alpha-22 the legacy file must be read once, its content
    promoted to the stable new path, and the legacy file removed — so a
    subsequent mutation of settings.data_dir no longer orphans the data.
    """
    # --- set up legacy state file -----------------------------------------
    legacy_content = {
        "api_key_configured": True,
        "first_doc_imported": True,
        "first_project_slug": "legacy-test",
        "completed_steps": ["api_key", "first_doc"],
        "first_bootstrap_run": False,
        "totp_enrolled": False,
        "license_installed": False,
        "last_step_at": None,
    }
    # settings.projects_dir == settings.data_dir when data_dir is set
    # (see meridian.config: projects_dir returns data_dir directly).
    # So we set data_dir = tmp_path and the legacy file lives at
    # tmp_path/_meridian/onboarding_state.json.
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    legacy_dir = tmp_path / "_meridian"
    legacy_dir.mkdir(parents=True)
    legacy_file = legacy_dir / "onboarding_state.json"
    legacy_file.write_text(json.dumps(legacy_content), encoding="utf-8")

    # Redirect wizard state dir to a fresh tmp location (new path empty).
    new_state_dir = tmp_path / "wizard_state"
    new_state_dir.mkdir()
    monkeypatch.setenv("MERIDIAN_WIZARD_STATE_DIR", str(new_state_dir))

    # --- call load_state via load_wizard_state() ---------------------------
    from meridian.onboarding.wizard import load_state

    loaded = load_state()

    # Assert loaded state reflects the legacy content.
    assert loaded is not None, "load_state() returned None — migration did not run"
    assert loaded.api_key_configured is True, "api_key_configured not migrated"
    assert loaded.first_project_slug == "legacy-test", (
        f"first_project_slug not migrated: got {loaded.first_project_slug!r}"
    )
    assert "api_key" in loaded.completed_steps
    assert "first_doc" in loaded.completed_steps

    # Assert new stable path now EXISTS (migration wrote it).
    new_path = _stable_user_state_dir() / "onboarding_state.json"
    assert new_path.exists(), (
        f"Migration did not write to new stable path {new_path}"
    )

    # Assert legacy file was deleted (not just left behind).
    assert not legacy_file.exists(), (
        f"Legacy file {legacy_file} still exists after migration; "
        "it should have been deleted."
    )

    # --- M5 scenario: mutate settings.data_dir and re-load ----------------
    # This is the original bug: before the fix, a data_dir mutation would
    # relocate state_path() to a new _meridian/ subdir under the new projects_dir,
    # orphaning the migrated state.  After the fix, state_path() is stable.
    monkeypatch.setattr(settings, "data_dir", tmp_path / "mutated_data_dir")
    reloaded = load_state()

    assert reloaded is not None, (
        "load_state() returned None after data_dir mutation — state was orphaned"
    )
    assert reloaded.first_project_slug == "legacy-test", (
        f"first_project_slug lost after data_dir mutation: got {reloaded.first_project_slug!r}"
    )
