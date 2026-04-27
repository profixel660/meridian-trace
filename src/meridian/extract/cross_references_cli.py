"""Typer sub-app for the cross-reference exhaustive sweep.

Wired into the main CLI by `src/meridian/cli.py` (main agent will add a single
line: `app.add_typer(xref_app, name="xref")`). Until then this module is
self-contained and can be invoked directly via:

    python -m meridian.extract.cross_references_cli sweep <project>

Commands:
  - meridian xref sweep <project> [--llm-assist] [--dry-run]
  - meridian xref report <project> [--format csv|md]
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from meridian.db.connection import connect
from meridian.extract.cross_references import (
    latest_sweep_findings,
    run_exhaustive_sweep,
)
from meridian.logging import bind_project_context, get_logger
from meridian.projects import project_db_path

_log = get_logger("meridian.cli.xref")
_console = Console()

xref_app = typer.Typer(
    name="xref",
    help=(
        "Exhaustive cross-reference sweep — scans every ingested doc for "
        "explicit textual references to other project sources (sections, "
        "drawings, specs, standards, equipment tags, vendors). Runs after "
        "all ingestion is complete; complements the best-effort references "
        "captured during single-doc extraction."
    ),
    no_args_is_help=True,
)


def _bind(name: str) -> None:
    # Logging-context binding must never break the user-facing command.
    with contextlib.suppress(Exception):
        bind_project_context(project_slug=name)


@xref_app.command("sweep")
def sweep_cmd(
    name: Annotated[str, typer.Argument(help="Project name (will be slugified).")],
    llm_assist: Annotated[
        bool,
        typer.Option(
            "--llm-assist/--no-llm-assist",
            help=(
                "Engage the optional LLM pass for ambiguous references the "
                "deterministic regex pass could not resolve. Default: off "
                "(zero LLM cost). Borderline findings are still queued for "
                "SME review even without --llm-assist."
            ),
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run/--commit",
            help=(
                "Generate the CSV + Markdown report WITHOUT writing to the DB. "
                "Use this for a no-risk preview before committing."
            ),
        ),
    ] = False,
) -> None:
    """Run the exhaustive cross-reference sweep over every ingested doc.

    Examples:

      Run a normal sweep:
        meridian xref sweep AT-Sydney-2026

      Preview without DB writes:
        meridian xref sweep AT-Sydney-2026 --dry-run

      Engage the optional LLM pass:
        meridian xref sweep AT-Sydney-2026 --llm-assist
    """
    db_path = project_db_path(name)
    if not db_path.exists():
        _console.print(f"[red]Project not found:[/red] {db_path}")
        _console.print(
            "[yellow]Hint:[/yellow] Run [bold]meridian projects create "
            f"{name}[/bold] first, then ingest sources."
        )
        raise typer.Exit(code=1)
    _bind(name)
    try:
        report = run_exhaustive_sweep(name, llm_assist=llm_assist, dry_run=dry_run)
    except FileNotFoundError as exc:
        _console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    except Exception as exc:  # noqa: BLE001
        _console.print(f"[red]Sweep failed:[/red] {exc}")
        _log.exception("xref.sweep.cli_failed", project=name)
        raise typer.Exit(code=2) from exc

    mode = "[yellow]DRY RUN[/yellow]" if report.dry_run else "[green]COMMITTED[/green]"
    _console.print(f"\n{mode} cross-reference sweep complete.")
    _console.print(f"  sweep_run_id: {report.sweep_run_id}")
    _console.print(
        f"  scanned: {report.sources_scanned} sources, "
        f"{report.deliverables_scanned} deliverables"
    )
    _console.print(f"  findings: {report.findings_total}")
    _console.print(f"    [green]confirmed:[/green]  {report.confirmed}")
    _console.print(f"    [yellow]borderline:[/yellow] {report.borderline} (queued for SME)")
    _console.print(
        f"    [cyan]external_reference:[/cyan] {report.external_reference} "
        "(citations to docs not in corpus — informational only)"
    )
    _console.print(f"    [dim]rejected:[/dim]   {report.rejected}")
    _console.print(f"  CSV report:      {report.report_csv_path}")
    _console.print(f"  Markdown report: {report.report_md_path}")
    if report.findings_total == 0:
        _console.print(
            "\n[dim]No cross-references were detected. This usually means the "
            "project has only one source, or sources don't use detectable "
            "anchor patterns. Try [bold]--llm-assist[/bold] for a semantic "
            "pass over ambiguous remainder.[/dim]"
        )


@xref_app.command("report")
def report_cmd(
    name: Annotated[str, typer.Argument(help="Project name.")],
    fmt: Annotated[
        str,
        typer.Option(
            "--format",
            "-f",
            help="Output format: csv or md (Markdown). Default: md.",
        ),
    ] = "md",
) -> None:
    """Re-render the most recent sweep results from the DB.

    The CSV / Markdown files written during the sweep are the canonical
    artefacts; this command is a convenience for re-printing them by path
    or echoing the Markdown to stdout.
    """
    fmt_norm = fmt.lower().strip()
    if fmt_norm not in {"csv", "md"}:
        _console.print(f"[red]Unknown format:[/red] {fmt} (expected csv or md)")
        raise typer.Exit(code=1)
    db_path = project_db_path(name)
    if not db_path.exists():
        _console.print(f"[red]Project not found:[/red] {db_path}")
        raise typer.Exit(code=1)
    _bind(name)
    conn = connect(db_path)
    try:
        run_row, result_rows = latest_sweep_findings(conn)
    finally:
        conn.close()

    if run_row is None:
        _console.print(
            "[yellow]No cross-reference sweep has been run yet.[/yellow]"
        )
        _console.print(
            "Run [bold]meridian xref sweep " + name + "[/bold] to generate one."
        )
        raise typer.Exit(code=1)

    target_path = run_row["report_csv_path"] if fmt_norm == "csv" else run_row["report_md_path"]
    if target_path and Path(target_path).exists():
        _console.print(f"[green]Report file:[/green] {target_path}")
        if fmt_norm == "md":
            _console.print(Path(target_path).read_text(encoding="utf-8"))
        return

    # File missing — fall back to rendering directly from DB.
    _console.print(
        f"[yellow]Original report file missing[/yellow] ({target_path}); "
        "rendering from DB."
    )
    if fmt_norm == "md":
        _console.print(f"# Cross-reference sweep report — {name}")
        _console.print(f"sweep_run_id: {run_row['id']}")
        _console.print(f"started_at: {run_row['started_at']}")
        _console.print(f"status: {run_row['status']}")
    else:
        _console.print("from_source_id,to_source_id,anchor_kind,raw_match,outcome,confidence")

    if not result_rows:
        _console.print("\n[dim]_No cross-references in this sweep._[/dim]")
        return

    if fmt_norm == "md":
        table = Table(title="Findings")
        table.add_column("From")
        table.add_column("To")
        table.add_column("Kind")
        table.add_column("Match")
        table.add_column("Outcome")
        table.add_column("Conf.")
        for r in result_rows:
            table.add_row(
                r["from_source_id"][:8],
                (r["to_source_id"] or "?")[:8],
                r["anchor_kind"],
                r["raw_match"],
                r["outcome"],
                r["confidence"],
            )
        _console.print(table)
    else:
        for r in result_rows:
            _console.print(
                f"{r['from_source_id']},{r['to_source_id'] or ''},"
                f"{r['anchor_kind']},{r['raw_match']},{r['outcome']},{r['confidence']}"
            )


__all__ = ["xref_app"]
