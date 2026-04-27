"""CLI ``--help`` smoke test for every top-level command.

This is a single omnibus test: it walks every ``@app.command(...)`` and every
``app.add_typer(..., name=...)`` registered on ``meridian.cli.app`` and runs
``--help`` against each. A regression where someone breaks an import inside,
say, ``meridian.cli`` -> ``meridian.tender`` would silently take the entire
sub-command tree offline; this test would catch that immediately.

Costs ~one fast invoke per command — cheap enough to live in the e2e suite.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from meridian.cli import app as cli_app

runner = CliRunner()


def _enumerate_command_names() -> list[list[str]]:
    """Collect every top-level command-path on ``cli_app`` as ``argv`` slices.

    Returns a list like ``[["status"], ["routing"], ["backup"], ...]`` —
    one entry per registered command or sub-typer. We deliberately stop at
    the top level: each sub-typer's own ``--help`` page covers its leaves.
    """
    names: list[list[str]] = []

    # Direct ``@app.command("foo")`` registrations.
    for cmd in app_registered_commands():
        # CommandInfo.name can be None when the function name is used as-is.
        name = cmd.name or (cmd.callback.__name__ if cmd.callback else None)
        if name:
            names.append([name])

    # ``app.add_typer(sub, name="bar")`` registrations.
    for grp in app_registered_groups():
        if grp.name:
            names.append([grp.name])

    return names


def app_registered_commands() -> list:
    """Compatibility shim — typer's attribute name is ``registered_commands``."""
    return list(getattr(cli_app, "registered_commands", []))


def app_registered_groups() -> list:
    """Compatibility shim — typer's attribute name is ``registered_groups``."""
    return list(getattr(cli_app, "registered_groups", []))


def test_every_top_level_command_has_help(tmp_projects_dir: Path) -> None:
    """``meridian <command> --help`` must exit 0 with non-empty output for ALL.

    Guards against regressions where an import error inside a sub-command
    module silently breaks the entire command tree at CLI invocation time.
    """
    command_paths = _enumerate_command_names()
    assert len(command_paths) >= 5, (
        f"sanity: expected at least 5 top-level commands on the CLI app, "
        f"got {len(command_paths)} ({command_paths!r})"
    )

    failures: list[str] = []
    for path in command_paths:
        argv = [*path, "--help"]
        result = runner.invoke(cli_app, argv)
        if result.exit_code != 0:
            failures.append(
                f"  {' '.join(argv)} -> exit={result.exit_code}\n"
                f"    stdout={result.stdout!r}\n"
                f"    exc={result.exception!r}"
            )
            continue
        if not result.stdout.strip():
            failures.append(
                f"  {' '.join(argv)} -> exit=0 but empty stdout"
            )

    assert not failures, (
        "the following top-level commands failed `--help` smoke check:\n"
        + "\n".join(failures)
    )
