"""``meridian evidence`` Typer subcommand group.

Wired into the root CLI by ``meridian.cli`` via ``app.add_typer(evidence_app, name="evidence")``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from meridian.evidence.builder import build_evidence_pack, verify_pack
from meridian.logging import get_logger

_log = get_logger("meridian.evidence.cli")
console = Console()

evidence_app = typer.Typer(
    name="evidence",
    help=(
        "Assemble and verify Legal Evidence Packs — defensible audit-trail "
        "bundles a lawyer or claims team can hand to opposing counsel."
    ),
    no_args_is_help=True,
)


@evidence_app.command("build")
def evidence_build(
    project: Annotated[
        str,
        typer.Argument(help="Project name (slugified to find the SQLite file)."),
    ],
    output: Annotated[
        Path | None,
        typer.Option(
            "--output", "-o",
            help=(
                "Destination zip path. Defaults to "
                "<projects_dir>/<slug>.evidence/<UTC-timestamp>.zip."
            ),
        ),
    ] = None,
    deliverable_id: Annotated[
        list[str] | None,
        typer.Option(
            "--deliverable-id",
            help=(
                "Limit the pack to one or more specific deliverables (and their "
                "full provenance trail). Pass the option multiple times to scope "
                "to several rows. Omit to pack the whole project."
            ),
        ),
    ] = None,
) -> None:
    """Build a Legal Evidence Pack zip for the project.

    The pack contains MANIFEST.json (with SHA-256 of every file), the
    deliverables register, the audit trail, every LLM call (with secret
    redaction), the prompt files used, source-document metadata, and a
    human-readable cover letter + chain-of-custody narrative.

    Run ``meridian evidence verify <pack.zip>`` to re-check tamper status.
    """
    try:
        result = build_evidence_pack(
            project,
            output_path=output,
            deliverable_ids=deliverable_id,
        )
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(f"[green]Evidence pack written:[/green] {result.pack_path}")
    console.print(
        f"  deliverables: {result.deliverable_count}  "
        f"llm_calls: {result.llm_call_count}  "
        f"sources: {result.source_count}"
    )
    if result.incomplete_provenance:
        console.print(
            f"[yellow]Borderline: {len(result.incomplete_provenance)} deliverable(s) "
            f"marked 'incomplete_provenance' in the manifest "
            f"(missing source/llm_call/extraction_job linkage). They are still "
            f"included in the pack but flagged for review.[/yellow]"
        )
    if result.schema_notes:
        console.print("[yellow]Schema notes:[/yellow]")
        for note in result.schema_notes:
            console.print(f"  - {note}")
    console.print(
        "\n[dim]Next: hand the .zip to your reviewer. To re-verify integrity later, "
        f"run: meridian evidence verify {result.pack_path}[/dim]"
    )


@evidence_app.command("verify")
def evidence_verify(
    pack: Annotated[
        Path,
        typer.Argument(
            help="Path to a previously-generated evidence pack zip.",
            exists=True, dir_okay=False, readable=True,
        ),
    ],
) -> None:
    """Re-compute SHA-256 hashes inside a pack and compare to MANIFEST.json.

    A clean verification means the pack has not been altered since it was
    generated. Any mismatch, missing file, or extra file is reported.
    """
    try:
        result = verify_pack(pack)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    if result.ok:
        console.print(f"[green]VERIFIED[/green] — {pack} is intact.")
    else:
        console.print(f"[red]TAMPER DETECTED[/red] — {pack} differs from its manifest.")

    table = Table(title="File hash check")
    table.add_column("File")
    table.add_column("OK", justify="center")
    table.add_column("Expected sha256")
    table.add_column("Actual sha256")
    for entry in result.file_results:
        table.add_row(
            entry["name"],
            "[green]yes[/green]" if entry["ok"] else "[red]NO[/red]",
            entry["expected"][:16] + "...",
            entry["actual"][:16] + "...",
        )
    console.print(table)

    if result.missing_files:
        console.print("[red]Missing files (in manifest but not in pack):[/red]")
        for name in result.missing_files:
            console.print(f"  - {name}")
    if result.extra_files:
        console.print("[yellow]Extra files (in pack but not in manifest):[/yellow]")
        for name in result.extra_files:
            console.print(f"  - {name}")

    if not result.ok:
        raise typer.Exit(code=2)


__all__ = ["evidence_app"]
