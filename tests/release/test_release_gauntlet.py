"""Pytest wrapper around scripts/release_gauntlet.py.

Marked ``slow`` — excluded from the default ``pytest tests/e2e`` invocation.
Skipped on non-Windows because step 1 (PowerShell parser check) needs PS
and the cwd-System32 simulation is the bug class we shipped, on Windows.

Run explicitly:

    uv run pytest tests/release -m slow -v
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def _repo_root() -> Path:
    here = Path(__file__).resolve().parent
    # tests/release/ → tests/ → <repo>
    return here.parent.parent


@pytest.mark.slow
@pytest.mark.skipif(
    os.name != "nt",
    reason="release gauntlet exercises PowerShell parser + Windows cwd-System32 bug class",
)
def test_release_gauntlet_passes() -> None:
    """The full gauntlet must exit 0.

    Output goes to pytest's captured-log on failure; this test only asserts
    the exit code. ~60s on a warm tree, longer on a cold tree (wheel build
    + npm build). Use ``pytest -s`` to stream gauntlet output live.
    """
    repo = _repo_root()
    script = repo / "scripts" / "release_gauntlet.py"
    assert script.exists(), f"release gauntlet missing at {script}"

    # 10-minute outer cap: covers npm build + uv build + venv install + lifecycle.
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        # Stitch stdout+stderr into the assertion message so pytest's report
        # explains *why* the gauntlet failed without the user re-running it.
        details = (
            "release gauntlet exited "
            f"{result.returncode}\n"
            "----- stdout -----\n"
            f"{result.stdout}\n"
            "----- stderr -----\n"
            f"{result.stderr}"
        )
        pytest.fail(details)
