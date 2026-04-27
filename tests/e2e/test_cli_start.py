"""Smoke test for the round-17 ``meridian start`` command.

The command is the entry point used by the post-install browser-launch
flow (installer/Install-Meridian.ps1) and by the future Tauri sidecar
(round 18). The full happy-path flow needs a live uvicorn process and is
exercised end-to-end by the installer dry-run; here we only confirm the
``--help`` page renders without import errors and the documented flags
are present.

This protects against regressions where someone breaks an import inside
``meridian.cli.start`` and silently takes the entire boot path offline.
"""

from __future__ import annotations

from typer.testing import CliRunner

from meridian.cli import app as cli_app

runner = CliRunner()


def test_start_help_renders() -> None:
    """``meridian start --help`` exits 0 with non-empty output."""
    result = runner.invoke(cli_app, ["start", "--help"])
    assert result.exit_code == 0, (
        f"meridian start --help exited {result.exit_code}; "
        f"stdout={result.stdout!r}; exc={result.exception!r}"
    )
    assert result.stdout.strip(), "meridian start --help produced empty stdout"


def test_start_help_documents_no_browser_flag() -> None:
    """``--no-browser`` must be advertised — the Tauri sidecar relies on it."""
    result = runner.invoke(cli_app, ["start", "--help"])
    assert result.exit_code == 0
    assert "--no-browser" in result.stdout, (
        "Expected --no-browser flag in `meridian start --help` output; "
        "the headless / Tauri-already-launched code path depends on it."
    )


def test_start_help_documents_port_flag() -> None:
    """``--port`` must be advertised so users can dodge port conflicts."""
    result = runner.invoke(cli_app, ["start", "--help"])
    assert result.exit_code == 0
    assert "--port" in result.stdout, (
        "Expected --port flag in `meridian start --help` output."
    )
