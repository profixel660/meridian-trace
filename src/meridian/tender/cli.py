"""Tender Package Builder — CLI sub-app.

Usage::

    meridian tender list <project>
    meridian tender build <project> --trade "Mechanical" \\
        --format xlsx --output-dir data/projects/tenders/

The sub-app is exported as ``tender_app`` and is wired into the root
:data:`meridian.cli.app` by the main CLI module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from meridian.logging import bind_project_context, get_logger
from meridian.tender.builder import (
    SUPPORTED_FORMATS,
    TenderBuildError,
    build_tender_package,
    list_trades_with_deliverables,
    tenders_dir_for,
)

_log = get_logger("meridian.tender.cli")

tender_app = typer.Typer(
    name="tender",
    help=(
        "Build per-trade tender packages from the accepted-deliverables "
        "register. Read-only on the project SQLite — produces xlsx or md "
        "files under <projects_dir>/<slug>.tenders/ by default."
    ),
    no_args_is_help=True,
)
console = Console()


def _bind(name: str) -> None:
    """Bind the project context for structured logs (best-effort)."""
    import contextlib

    with contextlib.suppress(Exception):
        bind_project_context(project_slug=name)


@tender_app.command("list")
def tender_list(
    name: Annotated[
        str,
        typer.Argument(
            help="Project name (or slug) — same value you pass to `meridian export`.",
        ),
    ],
) -> None:
    """List trades with at least one accepted deliverable in this project.

    Use the count column to decide which trades are worth packaging.
    A row labelled ``(unassigned)`` means accepted deliverables exist
    that have not been mapped to a trade — review these in the master
    register before issuing any tender package.
    """
    _bind(name)
    try:
        trades = list_trades_with_deliverables(name)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        console.print(
            "[dim]Hint: run `meridian project-create` first, or check the slug.[/dim]"
        )
        raise typer.Exit(code=1) from exc

    if not trades:
        console.print(
            Panel(
                "[yellow]No accepted deliverables yet.[/yellow]\n\n"
                "Run extraction (`meridian extract`) and review "
                "(`meridian review`) to accumulate accepted rows, "
                "then re-run this command.",
                title="Empty register",
                border_style="yellow",
            )
        )
        return

    table = Table(
        title=f"Trades with accepted deliverables — {name}",
        caption=(
            "Build a tender package with: "
            f"`meridian tender build {name} --trade \"<TRADE>\" --format xlsx`"
        ),
        show_lines=False,
    )
    table.add_column("Trade", style="cyan", no_wrap=True)
    table.add_column("Accepted deliverables", justify="right", style="green")

    for trade, count in trades:
        if trade == "(unassigned)":
            table.add_row(f"[yellow]{trade}[/yellow]", f"[yellow]{count}[/yellow]")
        else:
            table.add_row(trade, str(count))

    console.print(table)


@tender_app.command("build")
def tender_build(
    name: Annotated[
        str,
        typer.Argument(
            help="Project name (or slug).",
        ),
    ],
    trade: Annotated[
        str,
        typer.Option(
            "--trade",
            "-t",
            help=(
                "Trade name (case-insensitive). "
                "Run `meridian tender list <project>` to see what is available."
            ),
        ),
    ],
    fmt: Annotated[
        str,
        typer.Option(
            "--format",
            "-f",
            help=f"Output format. One of: {', '.join(SUPPORTED_FORMATS)}.",
        ),
    ] = "xlsx",
    output_dir: Annotated[
        Path | None,
        typer.Option(
            "--output-dir",
            "-o",
            help=(
                "Directory to write into. "
                "Defaults to <projects_dir>/<slug>.tenders/."
            ),
        ),
    ] = None,
) -> None:
    """Build a tender package for one trade and write it to disk.

    The package contains a cover sheet (project, trade, generation
    timestamp, source-doc list, applicable-standards summary, flag
    summary, items needing review) plus a deliverables sheet grouped
    by service then category.

    Only ``accepted`` deliverables are included — quarantined and
    rejected rows are excluded. Use the master register
    (`meridian export`) to see the full picture, including quarantine.
    """
    _bind(name)
    try:
        path = build_tender_package(
            name,
            trade,
            output_dir=output_dir,
            fmt=fmt,
        )
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    except TenderBuildError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    default_dir = output_dir or tenders_dir_for(name)
    console.print(
        Panel(
            f"[green]Tender package written:[/green] {path}\n"
            f"[dim]Format:[/dim] {fmt.lower()}\n"
            f"[dim]Output dir:[/dim] {default_dir}\n\n"
            "Open in Excel (xlsx) or paste into your editor (md). "
            "Re-running the command writes a new timestamped file — "
            "previous packages are preserved.",
            title=f"Tender — {trade}",
            border_style="green",
        )
    )


__all__ = ["tender_app"]
