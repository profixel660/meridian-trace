"""``meridian updates`` sub-app — manual check / skip / inspect (CONTEXT.md §18).

The actual install of an update is deferred (CONTEXT.md §3.7). For now the
CLI surfaces "an update is available — install with <X>" guidance only.
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel

from meridian.updates.client import (
    check_for_updates,
    list_skipped_versions,
    record_skipped_version,
)

console = Console()

updates_app = typer.Typer(
    name="updates",
    help=(
        "Check for and manage Meridian updates (CONTEXT.md §18). "
        "Use 'check' to see if a new release exists, 'skip' to dismiss a "
        "specific version, and 'show-skipped' to review the list."
    ),
    no_args_is_help=True,
)


@updates_app.command("check")
def updates_check(
    include_skipped: Annotated[
        bool,
        typer.Option(
            "--include-skipped",
            help="Show updates even if you have previously chosen to skip them.",
        ),
    ] = False,
    timeout_s: Annotated[
        int,
        typer.Option("--timeout", help="Manifest-fetch timeout (seconds)."),
    ] = 10,
) -> None:
    """Check the update manifest. Prints 'up to date' or 'version X.Y available'."""
    available = check_for_updates(
        timeout_s=timeout_s,
        include_skipped=include_skipped,
    )
    if available is None:
        console.print(
            "[green]You are up to date[/green] (or the update server could "
            "not be reached — check the structured log for details)."
        )
        return

    m = available.manifest
    badge = "[red bold]MANDATORY[/red bold]" if available.is_mandatory else "[cyan]new[/cyan]"
    body = (
        f"Current version:   {available.current_version}\n"
        f"Available version: {m.latest_version}  {badge}\n"
        f"Download:          {m.download_url}\n"
    )
    if m.release_notes:
        body += f"\nRelease notes:\n{m.release_notes}\n"
    if available.is_mandatory:
        body += (
            "\n[red]This update is required — older versions are no longer "
            "supported (security or data-integrity reasons). Please install.[/red]"
        )
    body += (
        "\nTo install: download the file above and run it. "
        "(In-app auto-install is coming in a future release — see "
        "CONTEXT.md §3.7.)\n"
        f"To dismiss this version: meridian updates skip {m.latest_version}"
    )
    console.print(Panel(body, title="Meridian update available", border_style="cyan"))


@updates_app.command("skip")
def updates_skip(
    version: Annotated[
        str,
        typer.Argument(help="The version string to skip (e.g. '1.2.0')."),
    ],
) -> None:
    """Record 'skip this version' so future 'updates check' calls ignore it."""
    # Preview before persisting (UX discoverability — show the user what
    # is about to happen).
    console.print(
        f"[yellow]About to mark version {version!r} as skipped.[/yellow] "
        "Future 'meridian updates check' calls will not surface it unless "
        "you pass --include-skipped."
    )
    confirmed = typer.confirm("Proceed?", default=True)
    if not confirmed:
        console.print("[yellow]Aborted — no changes made.[/yellow]")
        raise typer.Exit(code=0)
    path = record_skipped_version(version)
    console.print(f"[green]Skipped {version} (recorded at {path}).[/green]")


@updates_app.command("show-skipped")
def updates_show_skipped() -> None:
    """List the versions you have previously chosen to skip."""
    skipped = list_skipped_versions()
    if not skipped:
        console.print("[dim]No versions have been skipped.[/dim]")
        return
    console.print("[bold]Skipped versions:[/bold]")
    for v in skipped:
        console.print(f"  - {v}")
    console.print(
        "\n[dim]To re-surface a skipped version, use "
        "'meridian updates check --include-skipped'.[/dim]"
    )


__all__ = ["updates_app"]
