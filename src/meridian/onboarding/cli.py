"""Typer surface for the onboarding wizard.

Exposes ``onboarding_app`` (a ``typer.Typer``) plus a top-level
``init_command`` callable. The main agent wires both into ``meridian.cli``::

    from meridian.onboarding.cli import init_command, onboarding_app

    app.add_typer(onboarding_app, name="init-cmd")        # sub-app namespace
    app.command("init")(init_command)                     # top-level alias

The top-level alias is what end-users actually type — ``meridian init``.
"""

from __future__ import annotations

import json
from typing import Annotated

import typer
from rich.console import Console

from meridian.onboarding.wizard import (
    STEP_NAMES,
    load_state,
    run_wizard,
    state_path,
)

_console = Console()

onboarding_app = typer.Typer(
    name="init",
    help="First-run onboarding wizard — guided setup for non-technical users.",
    no_args_is_help=False,
    invoke_without_command=True,
)


def init_command(
    restart: Annotated[
        bool,
        typer.Option(
            "--restart",
            help="Force-restart from step 1 even if state exists.",
        ),
    ] = False,
    status: Annotated[
        bool,
        typer.Option(
            "--status",
            help="Print current onboarding state without running anything.",
        ),
    ] = False,
) -> None:
    """Run (or resume) the first-run onboarding wizard.

    Re-running on a complete state is a no-op that prints "Onboarding
    complete" and exits 0.
    """
    if status:
        _print_status()
        raise typer.Exit(code=0)

    try:
        state = run_wizard(force_restart=restart)
    except RuntimeError as exc:
        # Most common: non-TTY refusal from run_wizard. Already printed.
        raise typer.Exit(code=1) from exc

    if not state.is_complete():
        # Partial completion is a soft success — exit 0 so re-running picks up.
        raise typer.Exit(code=0)


@onboarding_app.callback()
def _onboarding_callback(
    ctx: typer.Context,
    restart: Annotated[
        bool,
        typer.Option("--restart", help="Force-restart from step 1."),
    ] = False,
    status: Annotated[
        bool,
        typer.Option("--status", help="Print state without running."),
    ] = False,
) -> None:
    """Sub-app entry — same flags as the top-level ``init`` command."""
    if ctx.invoked_subcommand is not None:
        return
    init_command(restart=restart, status=status)


def _print_status() -> None:
    """Render the current onboarding state as a small report."""
    state = load_state()
    if state is None:
        _console.print(
            "[yellow]No onboarding state found.[/yellow]\n"
            f"[dim]Will be created at: {state_path()}[/dim]\n"
            "Run [bold]meridian init[/bold] to start the wizard."
        )
        return

    _console.print(f"[bold]Onboarding state[/bold] ({state_path()}):")
    for step in STEP_NAMES:
        marker = "[green]done[/green]" if step in state.completed_steps else "[dim]pending[/dim]"
        _console.print(f"  - {step:<16} {marker}")
    _console.print("")
    _console.print(f"  api_key_configured:  {state.api_key_configured}")
    _console.print(f"  totp_enrolled:       {state.totp_enrolled}")
    _console.print(f"  license_installed:   {state.license_installed}")
    _console.print(f"  first_project_slug:  {state.first_project_slug}")
    _console.print(f"  first_doc_imported:  {state.first_doc_imported}")
    _console.print(f"  first_bootstrap_run: {state.first_bootstrap_run}")
    _console.print(f"  last_step_at:        {state.last_step_at}")
    _console.print("")
    if state.is_complete():
        _console.print(
            "[green]Onboarding complete.[/green] "
            "Re-run with [bold]--restart[/bold] to redo."
        )
    else:
        remaining = [s for s in STEP_NAMES if s not in state.completed_steps]
        _console.print(f"[dim]Remaining steps: {', '.join(remaining)}[/dim]")


# Convenience for quick debugging via ``python -m`` — not the user-facing path.
def _dump_state_json() -> str:
    """Return the persisted state as JSON (or ``{}`` if absent). Test helper."""
    state = load_state()
    if state is None:
        return "{}"
    from dataclasses import asdict

    return json.dumps(asdict(state), indent=2, sort_keys=True)


__all__ = [
    "init_command",
    "onboarding_app",
]
