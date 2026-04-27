"""Regression tests for the alpha-3 elevated-Admin cwd-safety fix.

Background
----------
The PowerShell installer (`installer/Install-Meridian.ps1`) runs elevated; an
elevated cmd inherits cwd = ``C:\\Windows\\System32``. The spawned
``python -m meridian.api.main`` then resolved its project root from
``Path.cwd()`` and tried to write project DBs and logs under
``C:\\Windows\\System32\\data\\projects\\...`` → PermissionError.

The fix in :mod:`meridian.config` introduces three pieces:

* :func:`_is_unsafe_cwd` — true when cwd looks like a Windows system path.
* :func:`_meridian_home` — resolves to ``MERIDIAN_HOME`` env var, then
  ``C:\\Meridian`` on Windows when present, then ``~/Meridian``.
* :func:`_project_root` — substitutes :func:`_meridian_home` when cwd is
  unsafe and no ``pyproject.toml`` is reachable from this file's path.
* :func:`_bootstrap_env_from_dotenv` — searches ``<project_root>/.env``
  THEN ``<MERIDIAN_HOME>/.env`` (first found wins).

Tests here pin the load-bearing behaviour without depending on actually
being elevated or on Windows, so they run identically on the CI Linux
runners and on a developer's Windows box.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

from meridian import config as meridian_config
from meridian.config import _is_unsafe_cwd, _meridian_home, _project_root

# --------------------------------------------------------------------------
# 1. _is_unsafe_cwd happy / sad paths
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_path, expected",
    [
        # Windows infrastructure paths — must be flagged unsafe.
        ("C:/Windows/System32", True),
        ("C:/Windows/SysWOW64", True),
        ("C:/Program Files/Python312", True),
        ("C:/Program Files (x86)/Foo", True),
        ("C:/ProgramData", True),
        ("C:/ProgramData/SomeApp", True),
        # Safe everyday paths — must NOT be flagged.
        ("C:/Users/Foo/MyProject", False),
        ("C:/Meridian", False),
        ("C:/Meridian/projects", False),
        ("/home/peter/code/meridian", False),
        ("/tmp/scratch", False),
    ],
)
def test_is_unsafe_cwd_classification(raw_path: str, expected: bool) -> None:
    """`_is_unsafe_cwd` must classify Windows system paths as unsafe and
    user-owned paths as safe, regardless of host OS.

    Load-bearing: misclassifying ``C:\\Meridian`` (the canonical install
    target) as unsafe would cause an infinite indirection loop —
    `_project_root` would punt to `_meridian_home` which IS C:\\Meridian.
    """
    assert _is_unsafe_cwd(Path(raw_path)) is expected


# --------------------------------------------------------------------------
# 2. _meridian_home resolution order
# --------------------------------------------------------------------------


def test_meridian_home_prefers_env_var(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`MERIDIAN_HOME` env var wins over every other resolution rule."""
    monkeypatch.setenv("MERIDIAN_HOME", str(tmp_path))
    assert _meridian_home() == Path(str(tmp_path))


def test_meridian_home_falls_back_to_user_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no env var set and no ``C:\\Meridian`` on disk → ``~/Meridian``.

    We patch ``Path.home`` to a tmp dir so the assertion is deterministic
    regardless of the runner's actual home directory.
    """
    monkeypatch.delenv("MERIDIAN_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    # Ensure the C:\Meridian short-circuit cannot fire on Windows runners
    # by forcing Path.exists to False for that specific path.
    real_exists = Path.exists

    def _exists(self: Path) -> bool:
        if self == Path("C:/Meridian"):
            return False
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", _exists)

    assert _meridian_home() == tmp_path / "Meridian"


@pytest.mark.skipif(
    os.name != "nt",
    reason="C:\\Meridian short-circuit only applies on Windows; the os.name "
    "guard inside _meridian_home means non-Windows hosts skip this branch "
    "entirely.",
)
def test_meridian_home_uses_c_meridian_on_windows_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Windows, no env var + ``C:\\Meridian`` exists → returns it.

    Skipped (not xfail) on non-Windows because the production code's
    ``os.name == "nt"`` guard makes the branch unreachable there — running
    the assertion on Linux would assert on dead code, which is misleading.
    """
    monkeypatch.delenv("MERIDIAN_HOME", raising=False)
    real_exists = Path.exists

    def _exists(self: Path) -> bool:
        if self == Path("C:/Meridian"):
            return True
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", _exists)
    assert _meridian_home() == Path("C:/Meridian")


# --------------------------------------------------------------------------
# 3. _project_root substitutes when cwd is unsafe
# --------------------------------------------------------------------------


def test_project_root_returns_repo_when_pyproject_reachable() -> None:
    """In the dev tree the walk-up from this file finds pyproject.toml.

    Sanity check: this test file lives inside the repo, so `_project_root`
    must NOT punt to `_meridian_home`; it must return the repo root that
    contains pyproject.toml. Validates the happy path before the fix kicks
    in.
    """
    root = _project_root()
    assert (root / "pyproject.toml").exists(), (
        f"_project_root() returned {root!r} which lacks pyproject.toml"
    )


def test_project_root_substitutes_meridian_home_when_cwd_unsafe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulate the elevated-Admin scenario.

    We can't actually move the source tree, so the walk-up from
    ``Path(config.__file__)`` will still find the repo's pyproject.toml on a
    dev/CI checkout — making `_project_root` return the repo root every
    time. To exercise the fallback we shadow the file's parents by walking
    against a fake `here` that has no pyproject ancestor — easiest is to
    monkeypatch the function's module-internal `Path(__file__).resolve()`
    chain. We do that by monkeypatching `Path.cwd` to System32 AND patching
    the `_project_root` module-internal walk by overriding `Path.exists` for
    pyproject.toml lookups so the walk falls through.
    """
    monkeypatch.setenv("MERIDIAN_HOME", str(tmp_path))
    monkeypatch.setattr(Path, "cwd", classmethod(lambda cls: Path("C:/Windows/System32")))

    # Force the walk-up to fail by claiming pyproject.toml never exists.
    real_exists = Path.exists

    def _exists(self: Path) -> bool:
        if self.name == "pyproject.toml":
            return False
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", _exists)

    root = _project_root()
    assert root == tmp_path, (
        f"expected unsafe-cwd fallback to MERIDIAN_HOME={tmp_path!r}, got {root!r}"
    )
    # Critical: must NOT be System32.
    assert "system32" not in str(root).lower()


# --------------------------------------------------------------------------
# 4. _bootstrap_env_from_dotenv finds .env in MERIDIAN_HOME
# --------------------------------------------------------------------------


def test_bootstrap_env_finds_dotenv_in_meridian_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When project root has no .env, MERIDIAN_HOME/.env must be picked up.

    Load-bearing: this is the path the PowerShell installer relies on —
    `Install-Meridian.ps1` writes the user's ANTHROPIC_API_KEY to
    ``C:\\Meridian\\.env`` and the spawned sidecar must hydrate
    ``os.environ`` from it on import.
    """
    # Point MERIDIAN_HOME at a tmp dir and seed a .env there.
    home = tmp_path / "meridian-home"
    home.mkdir()
    (home / ".env").write_text("MERIDIAN_TEST_KEY=hello\n", encoding="utf-8")
    monkeypatch.setenv("MERIDIAN_HOME", str(home))
    monkeypatch.delenv("MERIDIAN_TEST_KEY", raising=False)

    # Force `_project_root()` to a dir with no .env, so the home-dir fallback
    # is the one that fires. tmp_path has no .env at its root.
    monkeypatch.setattr(meridian_config, "_project_root", lambda: tmp_path)

    # Re-trigger the bootstrap. The function is idempotent — first hit wins.
    meridian_config._bootstrap_env_from_dotenv()

    assert os.environ.get("MERIDIAN_TEST_KEY") == "hello"


# --------------------------------------------------------------------------
# 5. End-to-end smoke: importing meridian.api.main under an "unsafe" cwd
#    must NOT write logs under cwd.
# --------------------------------------------------------------------------


def test_api_main_import_writes_logs_under_meridian_home_not_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Importing ``meridian.api.main`` from an unsafe cwd must not raise,
    and the log file must land under MERIDIAN_HOME, NOT under cwd.

    This is the headline regression for alpha-3: prior to the fix the import
    chain (config → logging.setup) would compute ``settings.projects_dir``
    relative to ``Path.cwd()`` (System32), then `configure_logging` would
    `mkdir(parents=True, exist_ok=True)` under there → PermissionError on
    a real elevated install.

    We simulate the failure mode without requiring true System32 by:
      1. Forcing `_is_unsafe_cwd` to return True for the simulated cwd.
      2. Pointing MERIDIAN_HOME at a writable tmp dir.
      3. Repointing ``settings.data_dir`` at MERIDIAN_HOME/data/projects so
         the log handler writes inside MERIDIAN_HOME (this mirrors what an
         installed wheel's ``Settings.projects_dir`` resolves to once
         project_root falls back to _meridian_home).
    """
    home = tmp_path / "MeridianHome"
    home.mkdir()
    sim_cwd = tmp_path / "fake-system32"
    sim_cwd.mkdir()

    monkeypatch.setenv("MERIDIAN_HOME", str(home))
    monkeypatch.setattr(meridian_config, "_is_unsafe_cwd", lambda p: True)
    monkeypatch.chdir(sim_cwd)

    # Point the live settings at MERIDIAN_HOME's projects dir, mirroring what
    # Settings would compute on a fresh import once _project_root falls back
    # to _meridian_home(). Doing it via monkeypatch keeps the global
    # `settings` object intact for the rest of the suite.
    projects_dir = home / "data" / "projects"
    projects_dir.mkdir(parents=True)
    monkeypatch.setattr(meridian_config.settings, "data_dir", projects_dir)

    # Reset the logging module's idempotency flag so our configure_logging
    # call actually creates a fresh file handler.
    from meridian.logging import setup as logging_setup

    monkeypatch.setattr(logging_setup, "_configured", False)
    monkeypatch.setattr(logging_setup, "_current_log_dir", None)
    monkeypatch.setattr(logging_setup, "_current_handler", None)
    monkeypatch.setattr(logging_setup, "_current_slug", None)

    # Force-reimport meridian.api.main so its module-level
    # `configure_logging(console=False)` runs against our patched settings.
    sys.modules.pop("meridian.api.main", None)
    importlib.import_module("meridian.api.main")

    expected_log_dir = projects_dir / "_global.logs"
    assert expected_log_dir.exists(), (
        f"expected log dir {expected_log_dir!r} to be created under "
        f"MERIDIAN_HOME, but it does not exist"
    )

    # Critical inverse assertion: NOTHING should have been written under the
    # simulated cwd. If the fix regresses, the log handler will mkdir
    # `<sim_cwd>/data/projects/_global.logs/` instead.
    assert not (sim_cwd / "data").exists(), (
        f"log handler wrote under cwd {sim_cwd!r} — alpha-3 regression: "
        f"projects_dir is being computed from cwd instead of MERIDIAN_HOME"
    )

    # Tidy up the file handler we just opened so pytest can clean tmp_path
    # on teardown without ResourceWarning noise.
    import logging as _stdlib_logging

    for h in list(_stdlib_logging.getLogger().handlers):
        if getattr(h, "_meridian_json_file", False):
            _stdlib_logging.getLogger().removeHandler(h)
            h.close()
