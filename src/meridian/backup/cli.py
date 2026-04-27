"""``meridian backup`` Typer subcommand group.

Wired into the root CLI with::

    from meridian.backup.cli import backup_app
    app.add_typer(backup_app, name="backup")

UX discoverability:
    * Every command shows a preview before destructive ops.
    * ``restore --overwrite`` requires interactive Y/N confirmation
      (skipped automatically when stdin isn't a TTY — same pattern as
      the bootstrap auto-trigger).
    * ``verify`` reports one of three outcomes: valid / invalid / incomplete.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from meridian.backup.backup import (
    BackupConflict,
    BackupCorrupt,
    create_backup,
    list_backups,
    restore_backup,
    verify_backup,
)
from meridian.logging import get_logger

_log = get_logger("meridian.backup.cli")
console = Console()

backup_app = typer.Typer(
    name="backup",
    help=(
        "Create, verify, restore, and list project backups. A backup zip "
        "captures the per-project SQLite file plus every sibling artefact "
        "directory (.logs/.tenders/.evidence/.reports) with SHA-256 of "
        "every file in BACKUP_MANIFEST.json. SQLite is captured via the "
        "online-backup API so it is safe to back up while the project is "
        "in use."
    ),
    no_args_is_help=True,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _human_bytes(n: int) -> str:
    """Render ``n`` bytes as a short human-readable string."""
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(n)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024
    return f"{n} B"


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_interactive() -> bool:
    """True if stdin is a real TTY (so we can prompt safely)."""
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


@backup_app.command("create")
def backup_create(
    project: Annotated[
        str,
        typer.Argument(help="Project name (slugified to find the SQLite file)."),
    ],
    output_dir: Annotated[
        Path | None,
        typer.Option(
            "--output-dir", "-o",
            help=(
                "Destination directory for the zip. Defaults to "
                "<projects_dir>/<slug>.backups/."
            ),
        ),
    ] = None,
) -> None:
    """Create a backup zip for the project.

    Captures the SQLite file via the online-backup API (lock-safe — no
    risk of torn writes if the project is mid-use) plus every sibling
    artefact directory that exists. Missing sibling dirs are skipped
    silently — that is a valid project state.

    Output: ``<projects_dir>/<slug>.backups/<UTC-stamp>.zip`` by default.
    """
    try:
        result = create_backup(project, output_dir=output_dir)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        console.print(
            "[dim]Tip: run 'meridian project-create <name>' first, or check "
            "your projects directory for the correct slug.[/dim]"
        )
        raise typer.Exit(code=1) from exc

    sha256_of_zip = _sha256_of_file(result.output_path)

    console.print(f"[green]Backup written:[/green] {result.output_path}")
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column("k", style="dim")
    table.add_column("v")
    table.add_row("project", result.project)
    table.add_row("file count", str(result.file_count))
    table.add_row(
        "total uncompressed",
        f"{_human_bytes(result.total_bytes)} ({result.total_bytes:,} bytes)",
    )
    table.add_row(
        "zip size",
        f"{_human_bytes(result.output_path.stat().st_size)} "
        f"({result.output_path.stat().st_size:,} bytes)",
    )
    table.add_row("sha256 of zip", sha256_of_zip)
    table.add_row("schema version", str(result.schema_version))
    table.add_row("generated at", result.generated_at)
    console.print(table)
    console.print(
        f"\n[dim]Next: verify integrity with "
        f"'meridian backup verify {result.output_path}', or list all "
        f"backups with 'meridian backup list {result.project}'.[/dim]"
    )


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------


@backup_app.command("restore")
def backup_restore(
    zip_path: Annotated[
        Path,
        typer.Argument(
            help="Path to a backup zip created by 'meridian backup create'.",
            exists=True, dir_okay=False, readable=True,
        ),
    ],
    as_slug: Annotated[
        str | None,
        typer.Option(
            "--as",
            help=(
                "Restore as a NEW slug (clone). Useful for copying a "
                "production project into a test slug. If omitted, the "
                "backup restores to the slug it was taken from."
            ),
        ),
    ] = None,
    overwrite: Annotated[
        bool,
        typer.Option(
            "--overwrite",
            help=(
                "Replace any existing project files at the target slug. "
                "Requires interactive Y/N confirmation. Without this flag "
                "the restore refuses to touch an existing project."
            ),
        ),
    ] = False,
) -> None:
    """Restore a backup zip into the projects directory.

    Verifies every SHA-256 in the manifest BEFORE extracting anything;
    aborts on any mismatch. After extraction, runs initialise() on the
    restored DB to auto-apply any pending schema migrations (e.g. backup
    at v3, current schema at v5 → migrated forward).
    """
    # ── Preview ────────────────────────────────────────────────────────
    try:
        verify = verify_backup(zip_path)
    except BackupCorrupt as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    manifest = verify.manifest
    source_slug = str(manifest.get("project_slug", "<unknown>"))
    target_slug = as_slug or source_slug
    schema_at_backup = manifest.get("schema_version", "?")
    file_count = len(manifest.get("file_index", {}))

    console.print("[bold]Restore preview[/bold]")
    preview = Table(show_header=False, box=None, pad_edge=False)
    preview.add_column("k", style="dim")
    preview.add_column("v")
    preview.add_row("backup file", str(zip_path))
    preview.add_row("verify status", verify.status)
    preview.add_row("source slug", source_slug)
    preview.add_row("target slug", target_slug)
    preview.add_row("schema at backup", str(schema_at_backup))
    preview.add_row("file count", str(file_count))
    preview.add_row("on conflict", "overwrite" if overwrite else "refuse")
    console.print(preview)

    if verify.status != "valid":
        console.print(
            f"[red]Refusing to restore — backup is {verify.status}.[/red] "
            f"Run 'meridian backup verify {zip_path}' for details."
        )
        raise typer.Exit(code=2)

    # ── Confirm destructive overwrite ──────────────────────────────────
    if overwrite:
        if _is_interactive():
            confirmed = typer.confirm(
                f"This will DELETE existing files at slug '{target_slug}' "
                f"before restoring. Are you sure?",
                default=False,
            )
            if not confirmed:
                console.print("[yellow]Restore cancelled.[/yellow]")
                raise typer.Exit(code=1)
        else:
            console.print(
                "[yellow]Non-interactive shell detected — skipping Y/N "
                "prompt and proceeding with --overwrite as requested.[/yellow]"
            )

    on_conflict = "overwrite" if overwrite else "refuse"

    # ── Execute ────────────────────────────────────────────────────────
    try:
        result = restore_backup(
            zip_path,
            target_project_slug=as_slug,
            on_conflict=on_conflict,
        )
    except BackupConflict as exc:
        console.print(f"[red]{exc}[/red]")
        console.print(
            "[dim]Tip: re-run with --overwrite to replace, or pass "
            "--as <new-slug> to restore as a clone.[/dim]"
        )
        raise typer.Exit(code=1) from exc
    except BackupCorrupt as exc:
        console.print(f"[red]{exc}[/red]")
        if exc.failed_files:
            console.print("[red]Failed entries:[/red]")
            for n in exc.failed_files[:20]:
                console.print(f"  - {n}")
            if len(exc.failed_files) > 20:
                console.print(f"  ... and {len(exc.failed_files) - 20} more")
        raise typer.Exit(code=2) from exc
    except NotImplementedError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(f"[green]Restored:[/green] {result.restored_to}")
    console.print(
        f"  files: {result.file_count}  "
        f"schema: v{result.schema_version_at_backup} -> "
        f"v{result.schema_version_after_restore}"
    )
    if result.conflicts_resolved:
        console.print(
            f"[yellow]Conflicts resolved ({len(result.conflicts_resolved)}):[/yellow]"
        )
        for c in result.conflicts_resolved[:20]:
            console.print(f"  - {c}")
        if len(result.conflicts_resolved) > 20:
            console.print(f"  ... and {len(result.conflicts_resolved) - 20} more")
    console.print(
        f"\n[dim]Next: open the project with any 'meridian' command "
        f"using slug '{result.project}'.[/dim]"
    )


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


@backup_app.command("verify")
def backup_verify(
    zip_path: Annotated[
        Path,
        typer.Argument(
            help="Path to a backup zip to verify.",
            exists=True, dir_okay=False, readable=True,
        ),
    ],
) -> None:
    """Re-hash every file in a backup zip and compare to BACKUP_MANIFEST.json.

    Reports one of three outcomes:
      * VALID      — every file matches its manifest hash; no extras.
      * INVALID    — at least one SHA-256 mismatch (data corruption / tamper).
      * INCOMPLETE — manifest references files not in the zip, or vice versa.
    """
    try:
        result = verify_backup(zip_path)
    except (FileNotFoundError, BackupCorrupt) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    if result.status == "valid":
        console.print(f"[green]VALID[/green] — {zip_path} is intact.")
    elif result.status == "invalid":
        console.print(
            f"[red]INVALID[/red] — {zip_path} has SHA-256 mismatches "
            f"(data corruption or tampering)."
        )
    else:  # "incomplete"
        console.print(
            f"[yellow]INCOMPLETE[/yellow] — {zip_path} has manifest/zip "
            f"mismatches but no hash failures (review borderline)."
        )

    table = Table(title="File hash check")
    table.add_column("File")
    table.add_column("OK", justify="center")
    table.add_column("Expected sha256")
    table.add_column("Actual sha256")
    for entry in result.file_results:
        table.add_row(
            entry["name"],
            "[green]yes[/green]" if entry["ok"] else "[red]NO[/red]",
            (entry["expected"] or "")[:16] + "...",
            (entry["actual"] or "")[:16] + "...",
        )
    console.print(table)

    if result.missing_files:
        console.print("[red]Missing (in manifest but not in zip):[/red]")
        for name in result.missing_files:
            console.print(f"  - {name}")
    if result.extra_files:
        console.print("[yellow]Extra (in zip but not in manifest):[/yellow]")
        for name in result.extra_files:
            console.print(f"  - {name}")

    if result.status == "invalid":
        raise typer.Exit(code=2)
    if result.status == "incomplete":
        raise typer.Exit(code=3)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@backup_app.command("list")
def backup_list(
    project: Annotated[
        str,
        typer.Argument(help="Project name (slugified)."),
    ],
) -> None:
    """List previous backups for the project under ``<slug>.backups/``."""
    entries = list_backups(project)
    if not entries:
        console.print(
            f"[dim]No backups found for '{project}'. "
            f"Run 'meridian backup create {project}' to create the first one.[/dim]"
        )
        return

    table = Table(title=f"Backups for {project}")
    table.add_column("Filename")
    table.add_column("Generated (UTC)")
    table.add_column("Schema", justify="right")
    table.add_column("Files", justify="right")
    table.add_column("Size", justify="right")
    for e in entries:
        table.add_row(
            e["path"].name,
            str(e["generated_at"] or e["modified_at"]),
            str(e["schema_version"] if e["schema_version"] is not None else "?"),
            str(e["file_count"] if e["file_count"] is not None else "?"),
            _human_bytes(e["size_bytes"]),
        )
    console.print(table)
    console.print(
        "\n[dim]Verify any backup with "
        "'meridian backup verify <path>'. Restore with "
        "'meridian backup restore <path> [--as new-slug] [--overwrite]'.[/dim]"
    )


__all__ = ["backup_app"]
