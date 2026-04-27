"""``meridian crash`` sub-app — list / preview / send / opt-in (CONTEXT.md §19).

UX rules baked in:

- ``send`` always shows the redacted payload before transmission and asks
  for explicit confirmation.
- ``preview`` lets the user inspect a payload without any network call.
- The opt-in flag is **off by default**; ``send`` refuses unless it is on.
- Every command has a clear ``--help`` body and an actionable failure path.
"""

from __future__ import annotations

import json
from typing import Annotated

import typer
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from meridian.crash.sender import (
    crash_opt_in_path,
    get_crash_opt_in,
    list_local_dossiers,
    prepare_crash_payload,
    send_crash_report,
    set_crash_opt_in,
)

console = Console()

crash_app = typer.Typer(
    name="crash",
    help=(
        "Inspect and send local crash reports (CONTEXT.md §19). "
        "Reports are written to disk by the error handler; this sub-app "
        "lets you list them, preview the redacted payload, and (with "
        "explicit opt-in) send one to the crash endpoint."
    ),
    no_args_is_help=True,
)


def _resolve_dossier(dossier_id: str):
    """Resolve a dossier ID (filename, prefix, or list-index) to a Path."""
    dossiers = list_local_dossiers()
    if not dossiers:
        return None

    # Numeric → list index (1-based, as printed by ``crash list``).
    if dossier_id.isdigit():
        idx = int(dossier_id) - 1
        if 0 <= idx < len(dossiers):
            return dossiers[idx]
        return None

    # Exact filename match.
    for p in dossiers:
        if p.name == dossier_id:
            return p

    # Filename-stem prefix match.
    matches = [p for p in dossiers if p.stem.startswith(dossier_id)]
    if len(matches) == 1:
        return matches[0]
    return None


@crash_app.command("list")
def crash_list() -> None:
    """List all local crash dossiers, newest first."""
    dossiers = list_local_dossiers()
    if not dossiers:
        console.print(
            "[green]No local crash dossiers found.[/green] "
            "(Reports are written under <projects_dir>/*.logs/crash_*.json "
            "when the error handler captures a crash.)"
        )
        return

    table = Table(title=f"Local crash dossiers ({len(dossiers)})")
    table.add_column("#", justify="right")
    table.add_column("Filename")
    table.add_column("Path")
    table.add_column("Size", justify="right")
    for i, p in enumerate(dossiers, 1):
        try:
            size = f"{p.stat().st_size:,} B"
        except OSError:
            size = "?"
        table.add_row(str(i), p.name, str(p.parent), size)
    console.print(table)
    console.print(
        "[dim]Use 'meridian crash preview <#>' to inspect a payload, "
        "or 'meridian crash send <#>' to transmit it.[/dim]"
    )


@crash_app.command("preview")
def crash_preview(
    dossier_id: Annotated[
        str,
        typer.Argument(
            help="Dossier list-index (from 'crash list') or filename / filename-prefix.",
        ),
    ],
) -> None:
    """Show the (redacted) payload that ``send`` would transmit."""
    path = _resolve_dossier(dossier_id)
    if path is None:
        console.print(
            f"[red]No dossier matched {dossier_id!r}.[/red] "
            "Run 'meridian crash list' to see available IDs."
        )
        raise typer.Exit(code=1)

    try:
        payload = prepare_crash_payload(path)
    except Exception as exc:  # noqa: BLE001 — preview must never crash
        console.print(f"[red]Could not prepare payload from {path}: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    rendered = json.dumps(payload, indent=2)
    console.print("[bold]Payload (would be sent to crash endpoint, redacted):[/bold]")
    console.print(Syntax(rendered, "json", theme="ansi_dark", line_numbers=False))
    console.print(
        f"\n[dim]Source dossier: {path}[/dim]\n"
        f"[dim]Approx. size if sent: {len(rendered):,} bytes.[/dim]"
    )


@crash_app.command("send")
def crash_send(
    dossier_id: Annotated[
        str,
        typer.Argument(
            help="Dossier list-index (from 'crash list') or filename / filename-prefix.",
        ),
    ],
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Build + redact + show the payload, but do not POST.",
        ),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Skip the interactive 'send this?' confirmation.",
        ),
    ] = False,
) -> None:
    """Send a crash report to the configured endpoint.

    Refuses if the opt-in flag is off (run 'meridian crash opt-in --enable'
    first). Always shows the redacted payload and asks for confirmation
    before transmitting unless --yes is given.
    """
    path = _resolve_dossier(dossier_id)
    if path is None:
        console.print(
            f"[red]No dossier matched {dossier_id!r}.[/red] "
            "Run 'meridian crash list' to see available IDs."
        )
        raise typer.Exit(code=1)

    if not dry_run and not get_crash_opt_in():
        console.print(
            "[red]Crash reporting is disabled.[/red]\n"
            "  → Run 'meridian crash opt-in --enable' to turn it on, then re-try.\n"
            "  → Or pass --dry-run to exercise the pipeline without sending."
        )
        raise typer.Exit(code=1)

    # Show the payload first — UX rule: never send blindly.
    try:
        payload = prepare_crash_payload(path)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Could not prepare payload: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    rendered = json.dumps(payload, indent=2)
    console.print("[bold]Payload (will be sent if you confirm):[/bold]")
    console.print(Syntax(rendered, "json", theme="ansi_dark"))
    console.print(
        f"[dim]Source dossier: {path}[/dim]\n"
        f"[dim]Size: {len(rendered):,} bytes[/dim]\n"
    )

    if dry_run:
        result = send_crash_report(path, dry_run=True)
        console.print(f"[cyan]Dry run:[/cyan] {result.message}")
        return

    if not yes:
        confirmed = typer.confirm("Send this report now?", default=False)
        if not confirmed:
            console.print("[yellow]Aborted — nothing sent.[/yellow]")
            raise typer.Exit(code=0)

    result = send_crash_report(path)
    if result.success:
        console.print(f"[green]Sent.[/green] {result.message}")
        return
    console.print(f"[red]Send failed.[/red] {result.message}")
    raise typer.Exit(code=1)


@crash_app.command("opt-in")
def crash_opt_in(
    enable: Annotated[
        bool,
        typer.Option(
            "--enable",
            help="Turn crash reporting ON.",
        ),
    ] = False,
    disable: Annotated[
        bool,
        typer.Option(
            "--disable",
            help="Turn crash reporting OFF.",
        ),
    ] = False,
) -> None:
    """Show or change the crash-reporting opt-in flag.

    With no flags: prints the current state. With --enable / --disable:
    flips it and prints the new state.
    """
    if enable and disable:
        console.print("[red]Pass either --enable or --disable, not both.[/red]")
        raise typer.Exit(code=2)

    if enable or disable:
        new_value = bool(enable)
        # Preview before persisting.
        verb = "ENABLE" if new_value else "DISABLE"
        console.print(
            f"[yellow]About to {verb} crash reporting.[/yellow] "
            "When enabled, 'meridian crash send' may POST a redacted "
            f"crash dossier to the configured endpoint. Flag persisted at "
            f"{crash_opt_in_path()}."
        )
        confirmed = typer.confirm("Proceed?", default=True)
        if not confirmed:
            console.print("[yellow]Aborted — no change.[/yellow]")
            raise typer.Exit(code=0)
        set_crash_opt_in(new_value)

    state = "[green]ENABLED[/green]" if get_crash_opt_in() else "[red]DISABLED[/red]"
    console.print(f"Crash reporting is currently {state}.")
    console.print(f"[dim]Flag file: {crash_opt_in_path()}[/dim]")


__all__ = ["crash_app"]
