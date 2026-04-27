"""Onboarding state-machinery tests for ``meridian.onboarding.wizard``.

These tests exercise the *persistent state* primitives only — ``save_state``,
``load_state``, and ``state_path`` — plus the non-TTY refusal path on
``run_wizard``. The interactive wizard itself is intentionally not exercised
(it requires a real terminal); the canonical pattern in the codebase is to
test scripted callers via the underlying primitives instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from meridian.onboarding.wizard import (
    OnboardingState,
    load_state,
    run_wizard,
    save_state,
    state_path,
)


def test_onboarding_state_round_trip(tmp_projects_dir: Path) -> None:
    """``save_state`` then ``load_state`` must round-trip every flat field."""
    state = OnboardingState(
        api_key_configured=True,
        first_project_slug="syd2-shell-cd",
        first_doc_imported=True,
        first_bootstrap_run=False,
        totp_enrolled=True,
        license_installed=False,
        completed_steps=["api_key", "totp_enrol", "first_project", "first_doc"],
        last_step_at="2026-04-27T10:00:00Z",
    )
    save_state(state)

    loaded = load_state()
    assert loaded is not None, "load_state must return state after save_state"
    assert loaded == state, "round-trip must preserve every field exactly"


def test_onboarding_non_tty_refuses(
    tmp_projects_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``run_wizard`` must refuse to run when stdin is not an interactive TTY.

    The documented contract (see wizard.py): non-TTY callers (CI, redirected
    stdin) get a clear refuse message at entry — never a hang on piped input.
    """
    # Force both isatty() probes to report False.
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)

    with pytest.raises(RuntimeError) as excinfo:
        run_wizard()
    assert "interactive terminal" in str(excinfo.value).lower(), (
        f"refuse message should mention the interactive-terminal requirement; "
        f"got {excinfo.value!r}"
    )


def test_onboarding_state_path_under_underscore_meridian(
    tmp_projects_dir: Path,
) -> None:
    """The state file must live at ``<projects_dir>/_meridian/onboarding_state.json``."""
    expected = tmp_projects_dir / "_meridian" / "onboarding_state.json"
    assert state_path() == expected, (
        f"state_path() should resolve under _meridian/, got {state_path()!r}"
    )

    # Until save_state() is called, the parent dir need not exist.
    assert load_state() is None, "load_state on a virgin tmp dir must return None"

    save_state(OnboardingState())
    assert expected.exists(), "save_state must materialise the state file at the expected path"
    assert expected.parent.name == "_meridian", (
        "the parent directory must be the underscored meridian state dir, "
        "so it stays out of the project listing UI"
    )
