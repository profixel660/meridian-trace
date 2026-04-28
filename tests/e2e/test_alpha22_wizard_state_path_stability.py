"""Alpha-22 regression: wizard state must survive a mid-process
mutation of settings.data_dir.

Before alpha-22 the wizard mutated settings.data_dir at
POST /api/setup/projects time, which relocated state_path() to a new
folder, orphaning prior wizard progress (api_key + import counters).
This test pins the contract: state_path() is stable regardless of
settings.data_dir.
"""

from __future__ import annotations

from pathlib import Path

from meridian.config import settings
from meridian.onboarding.wizard import state_path
from meridian.wizard.state import (
    load_wizard_state,
    mark_documents_imported,
    save_wizard_state,
)


def test_state_path_independent_of_settings_data_dir(tmp_path: Path) -> None:
    """Mutating settings.data_dir must NOT relocate the wizard state file."""
    settings.data_dir = tmp_path / "before"
    path_before = state_path()

    settings.data_dir = tmp_path / "after"
    path_after = state_path()

    assert path_before == path_after, (
        f"state_path() relocated when settings.data_dir changed: "
        f"{path_before} != {path_after}. "
        "Wizard state must live at a fixed location, not under projects_dir."
    )


def test_wizard_state_round_trips_across_data_dir_change(
    tmp_path: Path,
    monkeypatch,
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

    settings.data_dir = tmp_path / "first"
    state = load_wizard_state()
    mark_documents_imported(state, count=4)

    settings.data_dir = tmp_path / "second"
    reloaded = load_wizard_state()

    assert reloaded.gui_documents_imported == 4, (
        "wizard documents-imported counter was orphaned by data_dir change"
    )
