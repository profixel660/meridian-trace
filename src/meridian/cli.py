"""Meridian CLI — drives the prototype end-to-end while the Next.js shell catches up."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from meridian.config import (
    DEFAULT_PURPOSE_ROUTING,
    LOCAL_PRESETS,
    PRESET_ALIASES,
    PRESET_DESCRIPTIONS,
    resolve_preset_name,
    settings,
)
from meridian.cost import (
    estimate_project_cost,
    summarise_project_cost,
)
from meridian.db.connection import connect
from meridian.export.excel import export_to_xlsx
from meridian.extract.conflict_pass import run_conflict_pass
from meridian.extract.orchestrator import (
    resume_job,
    run_job_over_sources,
    run_job_over_sources_isolated,
)
from meridian.ingest import ingest_file
from meridian.logging import bind_project_context, configure_logging, get_logger
from meridian.projects import (
    ProjectBusy,
    create_project,
    get_air_gapped,
    get_project_routing,
    project_db_path,
    set_air_gapped,
    set_project_routing,
)

_log = get_logger("meridian.cli")

_DOCS_BASE_URL = "https://github.com/profixel660/meridian-trace/tree/main/docs"
_RELEASES_URL = "https://github.com/profixel660/meridian-trace/releases"

# Maps `meridian docs <topic>` shortcuts to the on-repo file path.
# Keep in sync with the actual file names under docs/.
_DOCS_TOPICS: dict[str, str] = {
    "readme": "README.md",
    "getting-started": "getting-started.md",
    "install": "INSTALL.md",
    "concepts": "concepts.md",
    "cli": "cli-reference.md",
    "cli-reference": "cli-reference.md",
    "troubleshooting": "troubleshooting.md",
    "security": "security.md",
    "architecture": "architecture.md",
    "release-notes": "release-notes.md",
    "releases": "release-notes.md",
    "decisions": "DECISIONS.md",
    "concurrency": "concurrency-analysis.md",
}

app = typer.Typer(
    name="meridian",
    help="Meridian — extract per-trade deliverables from project documents.",
    no_args_is_help=True,
    epilog=(
        f"Docs: {_DOCS_BASE_URL}  ·  Releases: {_RELEASES_URL}  ·  "
        "`meridian docs` opens the docs in your browser; "
        "`meridian docs <topic>` jumps to a specific page."
    ),
)
console = Console()


@app.callback()
def _root_callback(ctx: typer.Context) -> None:
    """Configure structured logging for every CLI invocation (CONTEXT.md §19)."""
    # Idempotent — safe to call once per process. Console renderer is enabled
    # so interactive runs see structured events on stderr alongside the
    # human-friendly Rich tables on stdout. JSON-lines always go to file.
    configure_logging(console=True)
    _log.info(
        "cli.invoke",
        command=ctx.invoked_subcommand,
        argv=sys.argv[1:],
    )


def _bind_project(name: str) -> None:
    """Re-bind the structured-logging context to this project's log dir."""
    import contextlib

    with contextlib.suppress(Exception):  # logging must never crash a command
        bind_project_context(project_slug=name)


def _log_command_exception(command: str, exc: BaseException) -> None:
    """Capture a command-level exception in the JSONL log before re-raising.

    typer.Exit is the normal "graceful exit" path and is NOT a crash; we
    skip logging it as an error. Everything else is captured with stack info
    so `meridian explain-last-error` has something to read.
    """
    if isinstance(exc, typer.Exit):
        return
    _log.exception("cli.error", command=command, error_type=type(exc).__name__)


@app.command("project-create")
def project_create(
    name: Annotated[str, typer.Argument(help="Project name (slugified for the SQLite filename).")],
    notes: Annotated[str | None, typer.Option(help="Free-form project notes.")] = None,
) -> None:
    """Create a new project SQLite file under data/projects/."""
    try:
        project_id, db_path = create_project(name=name, notes=notes)
    except FileExistsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    _bind_project(name)
    console.print("[green]Project created.[/green]")
    console.print(f"  id:   {project_id}")
    console.print(f"  file: {db_path}")


@app.command("import-doc")
def import_doc(
    name: Annotated[str, typer.Argument(help="Project name.")],
    paths: Annotated[list[Path], typer.Argument(help="One or more files to import.")],
    auto_bootstrap: Annotated[
        bool,
        typer.Option(
            "--auto-bootstrap/--no-auto-bootstrap",
            help=(
                "When the project's first documents are imported, offer to run "
                "the bootstrap LLM sweep (proposes taxonomies + authority chain). "
                "Default: prompt interactively. Use --no-auto-bootstrap for scripted runs."
            ),
        ),
    ] = True,
    bootstrap_sample_size: Annotated[
        int,
        typer.Option(
            help="Sample size for the bootstrap sweep, if it runs.",
        ),
    ] = 15,
) -> None:
    """Hash, dedup, and extract text from one or more source documents.

    On the FIRST import for a project (i.e. when the project has zero source
    documents before this call), this command can offer to run the bootstrap
    LLM sweep — a first-pass recon over a sample of the corpus that proposes
    document classes, taxonomy extensions, BOD service mappings, and an
    authority chain. Lower friction for the SME's first session with a new
    project (per overnight report §4 #6).
    """
    db_path = project_db_path(name)
    if not db_path.exists():
        console.print(f"[red]Project not found: {db_path}[/red]")
        raise typer.Exit(code=1)
    _bind_project(name)
    conn = connect(db_path)
    try:
        pre_source_count = conn.execute(
            "SELECT COUNT(*) FROM source_document"
        ).fetchone()[0]
        was_empty = pre_source_count == 0

        table = Table(title="Imported sources")
        table.add_column("Filename")
        table.add_column("source_id")
        table.add_column("Method")
        table.add_column("Chars", justify="right")
        table.add_column("Chunks", justify="right")
        table.add_column("Note")

        any_imported = False
        for path in paths:
            if not path.exists():
                console.print(f"[red]Missing: {path}[/red]")
                continue
            try:
                result = ingest_file(conn, file_path=path, project_root=settings.project_root)
            except NotImplementedError as exc:
                console.print(f"[yellow]Skipping {path.name}: {exc}[/yellow]")
                continue
            any_imported = True
            note = "deduped" if result.deduped else ""
            table.add_row(
                result.filename,
                result.source_id,
                result.extraction_method,
                str(result.text_length),
                str(result.chunk_count),
                note,
            )
        console.print(table)

        if was_empty and any_imported and auto_bootstrap:
            _maybe_run_bootstrap_after_first_import(
                conn, project=name, sample_size=bootstrap_sample_size
            )
    finally:
        conn.close()


def _maybe_run_bootstrap_after_first_import(
    conn,
    *,
    project: str,
    sample_size: int,
) -> None:
    """Prompt the SME to run the bootstrap LLM sweep on the first import.

    Per the overnight report §4 #6 — running bootstrap as an explicit later
    step adds friction; offering it inline keeps the SME on a clean path.
    Skipped automatically in non-interactive contexts (no TTY) so this never
    blocks scripted ingest pipelines.
    """
    from meridian.bootstrap import render_proposal_summary, run_bootstrap_sweep

    if not sys.stdin.isatty():
        console.print(
            "[dim]First import detected. Run "
            f"`meridian bootstrap {project}` to propose taxonomies + authority chain.[/dim]"
        )
        return

    console.print("")
    console.print(
        "[bold cyan]First documents imported for this project.[/bold cyan]"
    )
    console.print(
        "The bootstrap LLM sweep can now scan a sample of the corpus and "
        "propose document classes, taxonomy extensions, BOD service mappings, "
        "and an authority chain. Proposals land in the standard taxonomy "
        "review queue — nothing is auto-applied."
    )
    try:
        proceed = typer.confirm(
            "Run bootstrap sweep now?", default=True
        )
    except (typer.Abort, EOFError, KeyboardInterrupt):
        proceed = False

    if not proceed:
        console.print(
            f"[dim]Skipped. You can run it later via "
            f"`meridian bootstrap {project}`.[/dim]"
        )
        return

    try:
        result = run_bootstrap_sweep(conn, sample_size=sample_size)
    except ValueError as exc:
        console.print(f"[yellow]Bootstrap sweep skipped: {exc}[/yellow]")
        return
    console.print(
        f"[green]Bootstrap sweep complete.[/green] "
        f"llm_call_id={result.llm_call_id}"
    )
    console.print(
        f"  new taxonomy proposals persisted: "
        f"{result.new_taxonomy_proposals_persisted}"
    )
    console.print("")
    console.print(render_proposal_summary(result.proposal))
    console.print("")
    console.print(
        "[dim]Review proposed taxonomy values via "
        "`meridian review walk-taxonomy`.[/dim]"
    )


def _project_stop_signal_path(name: str) -> Path:
    """Sibling-of-the-sqlite stop-signal file (see CONTEXT.md §6, §19).

    The orchestrator polls this between source iterations; the worker
    subprocess does NOT poll it.
    """
    db_path = project_db_path(name)
    return db_path.with_suffix(".stop_signal")


@app.command("extract")
def extract(
    name: Annotated[str, typer.Argument(help="Project name.")],
    source_ids: Annotated[
        list[str] | None,
        typer.Option(
            "--source-id",
            help="Specific source_id(s) to extract. Repeat for multiple. If omitted, extract all sources without prior runs.",
        ),
    ] = None,
    provider: Annotated[str | None, typer.Option(help="Override default provider.")] = None,
    model: Annotated[str | None, typer.Option(help="Override default model.")] = None,
    isolated: Annotated[
        bool,
        typer.Option(
            "--isolated/--in-process",
            help=(
                "Run each source in a subprocess worker (default; CONTEXT.md §6 "
                "crash isolation). Use --in-process for debugging."
            ),
        ),
    ] = True,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help=(
                "Re-extract sources even if they have a completed prior extraction "
                "(default behaviour skips them). When force is used and the prior "
                "extraction has reviewer-touched rows, a warning is printed naming "
                "the orphaned counts; the underlying rows remain in the DB."
            ),
        ),
    ] = False,
) -> None:
    """Run quality scan + extraction across the requested sources."""
    db_path = project_db_path(name)
    if not db_path.exists():
        console.print(f"[red]Project not found: {db_path}[/red]")
        raise typer.Exit(code=1)
    _bind_project(name)
    # Clear any stale stop-signal from a prior run before starting.
    stop_path = _project_stop_signal_path(name)
    if stop_path.exists():
        stop_path.unlink()
    conn = connect(db_path)
    try:
        if source_ids:
            ids = list(source_ids)
        else:
            ids = [
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM source_document ORDER BY imported_at"
                ).fetchall()
            ]
        if not ids:
            console.print("[yellow]No sources to extract.[/yellow]")
            raise typer.Exit(code=0)

        try:
            if isolated:
                result = run_job_over_sources_isolated(
                    conn,
                    db_path=db_path,
                    source_ids=ids,
                    provider=provider,
                    model=model,
                    stop_signal_path=stop_path,
                    force=force,
                )
            else:
                result = run_job_over_sources(
                    conn, source_ids=ids, provider=provider, model=model, force=force
                )
        except ProjectBusy as exc:
            console.print(
                f"[red]Project '{exc.slug}' is currently being used by "
                f"PID {exc.pid} on {exc.hostname} for {exc.purpose!r} "
                f"(since {exc.acquired_at}).[/red]\n"
                f"Wait for it to finish, or — if you are sure that process is "
                f"gone — manually delete the lock file at "
                f"[yellow]{project_db_path(exc.slug).with_suffix('.lock')}[/yellow]."
            )
            raise typer.Exit(code=2) from exc
        console.print(f"[green]Job complete.[/green] job_id={result.job_id}")

        table = Table(title="Per-source extraction results")
        table.add_column("Filename")
        table.add_column("Path")
        table.add_column("Inside", justify="right")
        table.add_column("Borderline", justify="right")
        table.add_column("Outside", justify="right")
        table.add_column("Questions", justify="right")
        table.add_column("Note")

        for src in result.sources:
            counts = src.counts or {}
            note = src.skipped_reason or src.error or ""
            table.add_row(
                src.filename,
                src.extraction_path,
                str(counts.get("inside", 0)),
                str(counts.get("borderline", 0)),
                str(counts.get("outside", 0)),
                str(counts.get("questions", 0)),
                note,
            )
        console.print(table)
    finally:
        conn.close()


@app.command("pause")
def pause(
    name: Annotated[str, typer.Argument(help="Project name.")],
    job_id: Annotated[
        str | None,
        typer.Option("--job-id", help="Job id to target. Defaults to the most recent running job."),
    ] = None,
) -> None:
    """Signal a running extraction job to pause after the current source.

    Writes a stop-signal file the orchestrator polls between sources. The
    worker subprocess does not poll — pause is granular at the source
    boundary so an in-flight LLM call is never interrupted mid-flight.
    """
    db_path = project_db_path(name)
    if not db_path.exists():
        console.print(f"[red]Project not found: {db_path}[/red]")
        raise typer.Exit(code=1)
    _bind_project(name)
    conn = connect(db_path)
    try:
        if job_id is None:
            row = conn.execute(
                "SELECT id FROM extraction_job WHERE status = 'running' "
                "ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if row is None:
                console.print("[yellow]No running extraction job to pause.[/yellow]")
                raise typer.Exit(code=0)
            job_id = row["id"]
    finally:
        conn.close()

    stop_path = _project_stop_signal_path(name)
    stop_path.parent.mkdir(parents=True, exist_ok=True)
    stop_path.write_text(f"pause requested for job {job_id}\n", encoding="utf-8")
    console.print(
        f"[green]Pause signal written:[/green] {stop_path}\n"
        f"  job_id={job_id}\n"
        f"  The orchestrator will pause after the current source completes."
    )


@app.command("resume")
def resume(
    name: Annotated[str, typer.Argument(help="Project name.")],
    job_id: Annotated[
        str | None,
        typer.Option("--job-id", help="Job id to resume. Defaults to the most recent paused job."),
    ] = None,
) -> None:
    """Resume a paused extraction job.

    Re-attaches every source row that didn't reach completed/skipped status
    and runs them through the isolated subprocess workers. Chunk-level
    resume (skipping chunks already processed within a source) is not yet
    wired — see the extraction_worker module docstring.
    """
    db_path = project_db_path(name)
    if not db_path.exists():
        console.print(f"[red]Project not found: {db_path}[/red]")
        raise typer.Exit(code=1)
    _bind_project(name)
    # Clear any lingering stop-signal so the resumed job isn't immediately
    # paused again.
    stop_path = _project_stop_signal_path(name)
    if stop_path.exists():
        stop_path.unlink()
    conn = connect(db_path)
    try:
        if job_id is None:
            row = conn.execute(
                "SELECT id FROM extraction_job WHERE status = 'paused' "
                "ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if row is None:
                console.print("[yellow]No paused extraction job to resume.[/yellow]")
                raise typer.Exit(code=0)
            job_id = row["id"]

        try:
            result = resume_job(
                conn, db_path=db_path, job_id=job_id, stop_signal_path=stop_path
            )
        except ProjectBusy as exc:
            console.print(
                f"[red]Project '{exc.slug}' is currently being used by "
                f"PID {exc.pid} on {exc.hostname} for {exc.purpose!r} "
                f"(since {exc.acquired_at}).[/red]\n"
                f"Wait for it to finish, or manually delete the lock file at "
                f"[yellow]{project_db_path(exc.slug).with_suffix('.lock')}[/yellow]."
            )
            raise typer.Exit(code=2) from exc
        console.print(f"[green]Resume complete.[/green] job_id={result.job_id}")

        if result.sources:
            table = Table(title="Per-source resume results")
            table.add_column("Filename")
            table.add_column("Path")
            table.add_column("Inside", justify="right")
            table.add_column("Borderline", justify="right")
            table.add_column("Outside", justify="right")
            table.add_column("Questions", justify="right")
            table.add_column("Note")
            for src in result.sources:
                counts = src.counts or {}
                note = src.skipped_reason or src.error or ""
                table.add_row(
                    src.filename,
                    src.extraction_path,
                    str(counts.get("inside", 0)),
                    str(counts.get("borderline", 0)),
                    str(counts.get("outside", 0)),
                    str(counts.get("questions", 0)),
                    note,
                )
            console.print(table)
    finally:
        conn.close()


@app.command("export")
def export(
    name: Annotated[str, typer.Argument(help="Project name.")],
    output: Annotated[Path, typer.Option("--output", "-o", help="Destination .xlsx path.")],
) -> None:
    """Export the master register + pivots to an Excel workbook."""
    db_path = project_db_path(name)
    if not db_path.exists():
        console.print(f"[red]Project not found: {db_path}[/red]")
        raise typer.Exit(code=1)
    _bind_project(name)
    conn = connect(db_path)
    try:
        path = export_to_xlsx(conn, output_path=output)
        console.print(f"[green]Exported to:[/green] {path}")
    finally:
        conn.close()


@app.command("conflicts")
def conflicts(
    name: Annotated[str, typer.Argument(help="Project name.")],
    provider: Annotated[str | None, typer.Option(help="Override default provider.")] = None,
    model: Annotated[str | None, typer.Option(help="Override default model.")] = None,
) -> None:
    """Run the cross-source conflict-detection pass over the master register + audit."""
    db_path = project_db_path(name)
    if not db_path.exists():
        console.print(f"[red]Project not found: {db_path}[/red]")
        raise typer.Exit(code=1)
    _bind_project(name)
    conn = connect(db_path)
    try:
        result = run_conflict_pass(conn, provider=provider, model=model)
        console.print(f"[green]Conflict pass complete.[/green] job_id={result.job_id}")
        console.print(
            f"  inputs: {result.deliverables_in_input} deliverables, "
            f"{result.audit_in_input} audit rows"
        )
        console.print(f"  conflicts persisted: {result.conflicts_persisted}")
    finally:
        conn.close()


@app.command("bootstrap")
def bootstrap_cmd(
    name: Annotated[str, typer.Argument(help="Project name.")],
    sample_size: Annotated[
        int,
        typer.Option(help="Number of sources to sample (capped to corpus size)."),
    ] = 15,
) -> None:
    """Run the per-project bootstrap LLM sweep — proposes classes / taxonomies / authority chain."""
    from meridian.bootstrap import render_proposal_summary, run_bootstrap_sweep

    db_path = project_db_path(name)
    if not db_path.exists():
        console.print(f"[red]Project not found: {db_path}[/red]")
        raise typer.Exit(code=1)
    _bind_project(name)
    conn = connect(db_path)
    try:
        try:
            result = run_bootstrap_sweep(conn, sample_size=sample_size)
        except ValueError as exc:
            console.print(f"[yellow]{exc}[/yellow]")
            raise typer.Exit(code=1) from exc
        console.print(
            f"[green]Bootstrap sweep complete.[/green] "
            f"llm_call_id={result.llm_call_id}"
        )
        console.print(
            f"  storage_key:                       {result.storage_key}"
        )
        console.print(
            f"  new taxonomy proposals persisted:  {result.new_taxonomy_proposals_persisted}"
        )
        console.print("")
        console.print(render_proposal_summary(result.proposal))
        console.print("")
        console.print(
            "[dim]Review proposed taxonomy values via "
            "`meridian review walk-taxonomy`.[/dim]"
        )
    finally:
        conn.close()


@app.command("bootstrap-show")
def bootstrap_show(
    name: Annotated[str, typer.Argument(help="Project name.")],
) -> None:
    """Show the latest bootstrap proposal as a readable summary."""
    from meridian.bootstrap import load_latest_proposal, render_proposal_summary

    db_path = project_db_path(name)
    if not db_path.exists():
        console.print(f"[red]Project not found: {db_path}[/red]")
        raise typer.Exit(code=1)
    _bind_project(name)
    conn = connect(db_path)
    try:
        proposal = load_latest_proposal(conn)
        if proposal is None:
            console.print(
                "[yellow]No bootstrap proposal recorded yet. "
                "Run `meridian bootstrap <project>` first.[/yellow]"
            )
            raise typer.Exit(code=0)
        console.print(render_proposal_summary(proposal))
    finally:
        conn.close()


@app.command("status")
def status(
    name: Annotated[str, typer.Argument(help="Project name.")],
) -> None:
    """Print a summary of project contents."""
    db_path = project_db_path(name)
    if not db_path.exists():
        console.print(f"[red]Project not found: {db_path}[/red]")
        raise typer.Exit(code=1)
    _bind_project(name)
    conn = connect(db_path)
    try:
        counts = {}
        for label, sql in {
            "sources": "SELECT COUNT(*) FROM source_document",
            "sources scanned": "SELECT COUNT(*) FROM source_document WHERE quality_scan_id IS NOT NULL",
            "deliverables (all)": "SELECT COUNT(*) FROM deliverable",
            "deliverables (master)": "SELECT COUNT(*) FROM v_master_register",
            "audit (outside)": "SELECT COUNT(*) FROM audit_record",
            "questions (pending)": "SELECT COUNT(*) FROM question WHERE status = 'pending'",
            "conflicts (pending)": "SELECT COUNT(*) FROM conflict WHERE status = 'pending'",
            "extraction jobs": "SELECT COUNT(*) FROM extraction_job",
            "llm calls": "SELECT COUNT(*) FROM llm_call",
        }.items():
            counts[label] = conn.execute(sql).fetchone()[0]
        for label, value in counts.items():
            console.print(f"  {label:<24} {value}")
    finally:
        conn.close()


@app.command("list-questions")
def list_questions(
    name: Annotated[str, typer.Argument(help="Project name.")],
) -> None:
    """Dump pending HITL questions as JSON to stdout (review them before resolving)."""
    db_path = project_db_path(name)
    if not db_path.exists():
        console.print(f"[red]Project not found: {db_path}[/red]")
        raise typer.Exit(code=1)
    _bind_project(name)
    conn = connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, kind, context, question_text, candidate_source_refs, proposed_resolution
            FROM question
            WHERE status = 'pending'
            ORDER BY created_at
            """
        ).fetchall()
        out = []
        for row in rows:
            out.append(
                {
                    "id": row["id"],
                    "kind": row["kind"],
                    "context": row["context"],
                    "question": row["question_text"],
                    "candidate_source_refs": json.loads(row["candidate_source_refs"] or "[]"),
                    "proposed_resolution": row["proposed_resolution"],
                }
            )
        json.dump(out, sys.stdout, indent=2)
        sys.stdout.write("\n")
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Cost preview + summary (CONTEXT.md §13)
# --------------------------------------------------------------------------


def _fmt_cents(cents: int) -> str:
    """Render integer cents as '$X.XX'."""
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    return f"{sign}${cents // 100}.{cents % 100:02d}"


@app.command("cost-preview")
def cost_preview(
    name: Annotated[str, typer.Argument(help="Project name.")],
    source_ids: Annotated[
        list[str] | None,
        typer.Option(
            "--source-id",
            help=(
                "Specific source_id(s) to estimate. Repeat for multiple. "
                "If omitted, estimate covers every source in the project."
            ),
        ),
    ] = None,
) -> None:
    """Show estimated LLM spend BEFORE running an extraction (CONTEXT.md §13)."""
    db_path = project_db_path(name)
    if not db_path.exists():
        console.print(f"[red]Project not found: {db_path}[/red]")
        raise typer.Exit(code=1)
    _bind_project(name)
    conn = connect(db_path)
    try:
        project_routing = get_project_routing(conn)
        preview = estimate_project_cost(
            conn, source_ids=source_ids, project_routing=project_routing
        )

        if not preview.breakdown:
            console.print(
                "[yellow]No sources to estimate. Import documents first.[/yellow]"
            )
            return

        table = Table(title=f"Cost preview — {name}")
        table.add_column("Purpose")
        table.add_column("Provider/Model")
        table.add_column("Sources", justify="right")
        table.add_column("Calls", justify="right")
        table.add_column("Input tok", justify="right")
        table.add_column("Output tok", justify="right")
        table.add_column("Already run", justify="right")
        table.add_column("Est. cost", justify="right")
        table.add_column("Note")

        for leg in preview.breakdown:
            note = "[red]rate unknown[/red]" if leg.rate_unknown else ""
            cost_text = "—" if leg.rate_unknown else _fmt_cents(leg.total_cents)
            n_sources = len(leg.source_ids) if leg.source_ids else (
                1 if leg.purpose == "conflict_pass" else 0
            )
            table.add_row(
                leg.purpose,
                f"{leg.provider}/{leg.model}",
                str(n_sources),
                str(leg.n_calls_estimated),
                f"{leg.input_tokens_estimated:,}",
                f"{leg.output_tokens_estimated:,}",
                str(leg.n_calls_already_run),
                cost_text,
                note,
            )

        console.print(table)
        total_str = _fmt_cents(preview.total_cents)
        if preview.total_unknown:
            console.print(
                f"[yellow]TOTAL (partial — unknown rates excluded): "
                f"{total_str}[/yellow]"
            )
            console.print(
                "[yellow]One or more legs use a model not in the rate table. "
                "Add it to meridian.cost.rates._RATES to include in the total."
                "[/yellow]"
            )
        else:
            console.print(f"[bold]TOTAL: {total_str}[/bold]")

        console.print(
            "\n[dim]Estimates assume worst-case output tokens; real costs are "
            "typically 30-70% lower. Local providers (ollama/vllm/etc.) are "
            "always shown as $0.00 — local inference cost is electrons, not "
            "API dollars.[/dim]"
        )
    finally:
        conn.close()


@app.command("cost-summary")
def cost_summary(
    name: Annotated[str, typer.Argument(help="Project name.")],
) -> None:
    """Show realised LLM spend across this project (CONTEXT.md §13)."""
    db_path = project_db_path(name)
    if not db_path.exists():
        console.print(f"[red]Project not found: {db_path}[/red]")
        raise typer.Exit(code=1)
    _bind_project(name)
    conn = connect(db_path)
    try:
        summary = summarise_project_cost(conn)

        console.print(
            f"[bold]TOTAL: {_fmt_cents(summary.total_cents)}[/bold] "
            f"across {summary.n_calls} llm_call rows."
        )
        if summary.unknown_cost_cents > 0:
            console.print(
                f"[yellow]Warning: {summary.unknown_cost_cents} llm_call row(s) "
                "have NULL cost_cents — they are excluded from the total. "
                "Likely causes: failed call before usage was recorded, or "
                "litellm.completion_cost did not recognise the model.[/yellow]"
            )

        if summary.by_purpose:
            t1 = Table(title="By purpose")
            t1.add_column("Purpose")
            t1.add_column("Calls", justify="right")
            t1.add_column("Cost", justify="right")
            for row in summary.by_purpose:
                t1.add_row(
                    row.purpose, str(row.n_calls), _fmt_cents(row.cost_cents)
                )
            console.print(t1)

        if summary.by_provider_model:
            t2 = Table(title="By provider/model")
            t2.add_column("Provider")
            t2.add_column("Model")
            t2.add_column("Calls", justify="right")
            t2.add_column("Input tok", justify="right")
            t2.add_column("Output tok", justify="right")
            t2.add_column("Cache R", justify="right")
            t2.add_column("Cache W", justify="right")
            t2.add_column("Cost", justify="right")
            for row in summary.by_provider_model:
                t2.add_row(
                    row.provider,
                    row.model,
                    str(row.n_calls),
                    f"{row.input_tokens:,}",
                    f"{row.output_tokens:,}",
                    f"{row.cache_read_tokens:,}",
                    f"{row.cache_write_tokens:,}",
                    _fmt_cents(row.cost_cents),
                )
            console.print(t2)

        if summary.by_job:
            t3 = Table(title="By extraction job")
            t3.add_column("Job ID")
            t3.add_column("Started")
            t3.add_column("Finished")
            t3.add_column("Status")
            t3.add_column("Calls", justify="right")
            t3.add_column("Cost", justify="right")
            for row in summary.by_job:
                t3.add_row(
                    row.job_id,
                    row.started_at or "",
                    row.finished_at or "",
                    row.status or "",
                    str(row.n_calls),
                    _fmt_cents(row.cost_cents),
                )
            console.print(t3)
    finally:
        conn.close()


# --------------------------------------------------------------------------
# `meridian explain-last-error` — LLM-assisted error explanation (CONTEXT.md §19)
# --------------------------------------------------------------------------


def _resolve_log_dir_for_project(name: str | None) -> Path:
    """Mirror logging.setup._resolve_log_dir without importing the private fn."""
    base = settings.projects_dir
    if name:
        return base / f"{name}.logs"
    return base / "_global.logs"


def _find_latest_log_file(log_dir: Path) -> Path | None:
    candidates = sorted(
        log_dir.glob("meridian-*.log"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _read_recent_log_events(log_file: Path, *, limit: int = 50) -> list[dict]:
    """Tail the JSONL log file and return the last `limit` parsed events."""
    if not log_file.exists():
        return []
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = text.splitlines()[-limit:]
    out: list[dict] = []
    for raw in lines:
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out


@app.command("explain-last-error")
def explain_last_error(
    name: Annotated[str | None, typer.Argument()] = None,
    log_file: Annotated[
        Path | None,
        typer.Option(help="Specific log file to read; defaults to most recent."),
    ] = None,
) -> None:
    """Use the LLM to explain the most recent error in the structured log."""
    from meridian.errors import build_error_context, explain_error

    if log_file is None:
        log_dir = _resolve_log_dir_for_project(name)
        if not log_dir.exists():
            console.print(f"[red]No log directory found at {log_dir}[/red]")
            raise typer.Exit(code=1)
        log_file = _find_latest_log_file(log_dir)
        if log_file is None:
            console.print(f"[red]No log files in {log_dir}[/red]")
            raise typer.Exit(code=1)

    events = _read_recent_log_events(log_file, limit=50)
    if not events:
        console.print(f"[yellow]No parseable events in {log_file}[/yellow]")
        raise typer.Exit(code=1)

    error_event = None
    for ev in reversed(events):
        level = (ev.get("level") or "").lower()
        ev_name = ev.get("event") or ""
        if level == "error" or ev_name == "cli.error" or ev_name == "error":
            error_event = ev
            break
    if error_event is None:
        console.print("[yellow]No error events found in the recent log.[/yellow]")
        raise typer.Exit(code=0)

    # Reconstruct a synthetic exception. We don't have the live exception
    # object, but the captured `exception` field on the structlog record
    # contains the formatted traceback; we surface it through stack_trace.
    exc_message = error_event.get("exception_message") or error_event.get(
        "event"
    ) or "captured error"
    err = RuntimeError(exc_message)
    context = build_error_context(
        exception=err,
        recent_log_events=events,
    )
    # Overlay the captured stack from the log if present.
    captured_stack = error_event.get("exception")
    if isinstance(captured_stack, str) and captured_stack.strip():
        context["stack_trace"] = captured_stack

    # Need a project DB for the LLM call (llm_call row persistence). When no
    # project name is supplied, we cannot persist — surface that up-front.
    if name is None:
        console.print(
            "[yellow]No project name supplied; LLM call needs a project SQLite "
            "to persist the call record. Re-run with `meridian explain-last-error "
            "<project>`.[/yellow]"
        )
        raise typer.Exit(code=1)

    db_path = project_db_path(name)
    if not db_path.exists():
        console.print(f"[red]Project not found: {db_path}[/red]")
        raise typer.Exit(code=1)
    _bind_project(name)
    conn = connect(db_path)
    try:
        explanation = explain_error(conn, context=context)
        console.print("[bold]Plain-English explanation:[/bold]")
        console.print(explanation)
    finally:
        conn.close()


# --------------------------------------------------------------------------
# `meridian routing ...` subcommand group (PROVIDER_ROUTING_V1.md §4)
# --------------------------------------------------------------------------

routing_app = typer.Typer(
    name="routing",
    help="Inspect and configure per-purpose LLM provider routing for a project.",
    no_args_is_help=True,
)
app.add_typer(routing_app, name="routing")


def _open_project_or_exit(name: str):
    db_path = project_db_path(name)
    if not db_path.exists():
        console.print(f"[red]Project not found: {db_path}[/red]")
        raise typer.Exit(code=1)
    _bind_project(name)
    return connect(db_path)


@routing_app.command("show")
def routing_show(
    name: Annotated[str, typer.Argument(help="Project name.")],
) -> None:
    """Print resolved (provider, model) for every purpose for this project."""
    conn = _open_project_or_exit(name)
    try:
        project_routing = get_project_routing(conn)
        air_gapped_proj = get_air_gapped(conn)
        air_gapped = bool(settings.air_gapped) or air_gapped_proj

        table = Table(title=f"Routing — {name}")
        table.add_column("Purpose")
        table.add_column("Provider")
        table.add_column("Model")
        table.add_column("Source")

        for purpose in DEFAULT_PURPOSE_ROUTING:
            provider, model = settings.resolve_route(
                purpose, project_routing=project_routing
            )
            env_key = f"MERIDIAN_ROUTE_{purpose.upper()}"
            if env_key in os.environ:
                source = f"env ({env_key})"
            elif project_routing and purpose in project_routing:
                source = "project"
            elif settings.purpose_routing and purpose in settings.purpose_routing:
                source = "settings"
            else:
                source = "default"
            table.add_row(purpose, provider, model, source)
        console.print(table)
        if air_gapped:
            origin = "settings" if settings.air_gapped else "project"
            console.print(f"[yellow]air-gap mode: ON[/yellow] (source: {origin})")
        else:
            console.print("air-gap mode: off")
    finally:
        conn.close()


def _validate_ollama_preset_or_warn(technical: str) -> str | None:
    """Soft-validate that an Ollama-based preset is reachable.

    Returns None on success, or a human-readable warning string when the
    preset references the Ollama provider but no Ollama endpoint is
    configured / reachable in this process. We DO NOT block the apply on
    this — the user may be staging config ahead of bringing Ollama up — but
    we surface it so the third outcome (preset-found-but-validation-failed)
    is visible to the operator. Hard validation lives in
    ``meridian.llm.client.call_llm`` preflight.
    """
    routes = LOCAL_PRESETS.get(technical, {})
    if not any(provider == "ollama" for provider, _model in routes.values()):
        return None
    # Fast non-blocking check: an env var override or a default localhost
    # endpoint. We deliberately do NOT make a network call here — the CLI
    # apply path must stay snappy and offline-safe.
    base_url = os.environ.get("OLLAMA_BASE_URL") or os.environ.get("OLLAMA_HOST")
    if base_url:
        return None
    return (
        "preset references the 'ollama' provider but neither OLLAMA_BASE_URL "
        "nor OLLAMA_HOST is set in this environment. The preset has been "
        "applied to the project DB; calls will fail at preflight until "
        "Ollama is reachable."
    )


@routing_app.command("apply")
def routing_apply(
    name: Annotated[str, typer.Argument(help="Project name.")],
    preset: Annotated[
        str,
        typer.Argument(
            help=(
                "Preset name. Operator aliases: "
                f"{', '.join(PRESET_ALIASES)}. "
                f"Technical names: {', '.join(LOCAL_PRESETS)}."
            )
        ),
    ],
) -> None:
    """Apply a named routing preset to the project.

    Accepts either an operator-facing alias (``cloud-default``, ``hybrid``,
    ``air-gapped``) or a technical preset name. Three outcomes:
      - success: preset resolved and persisted to project_routing.
      - preset-not-found: name resolves to neither alias nor technical → exit 1.
      - preset-found-but-validation-failed: persisted, but a warning is
        printed (e.g. an Ollama preset with no OLLAMA_BASE_URL configured).
    """
    technical = resolve_preset_name(preset)
    if technical is None:
        console.print(
            f"[red]Unknown preset '{preset}'.[/red] "
            f"Operator aliases: {', '.join(PRESET_ALIASES) or '(none)'}. "
            f"Technical names: {', '.join(LOCAL_PRESETS)}."
        )
        _log.warning(
            "routing.apply.preset_not_found",
            project=name,
            requested_preset=preset,
            known_aliases=list(PRESET_ALIASES),
            known_technical=list(LOCAL_PRESETS),
        )
        raise typer.Exit(code=1)

    conn = _open_project_or_exit(name)
    try:
        existing = get_project_routing(conn) or {}
        merged = dict(existing)
        for purpose, route in LOCAL_PRESETS[technical].items():
            merged[purpose] = route
        set_project_routing(conn, merged)
        alias_note = f" (alias '{preset}' → '{technical}')" if preset != technical else ""
        console.print(
            f"[green]Applied preset '{technical}' to project '{name}'.[/green]"
            f"{alias_note} ({len(LOCAL_PRESETS[technical])} purpose(s) updated)"
        )
        _log.info(
            "routing.apply.success",
            project=name,
            requested_preset=preset,
            resolved_preset=technical,
            purposes_updated=len(LOCAL_PRESETS[technical]),
        )
        warning = _validate_ollama_preset_or_warn(technical)
        if warning:
            console.print(f"[yellow]Validation warning: {warning}[/yellow]")
            _log.warning(
                "routing.apply.validation_warning",
                project=name,
                resolved_preset=technical,
                reason=warning,
            )
    finally:
        conn.close()


@routing_app.command("list-presets")
def routing_list_presets() -> None:
    """List every routing preset — operator alias, technical name, description.

    Operator aliases (``cloud-default``, ``hybrid``, ``air-gapped``) are the
    deployment-intent vocabulary documented in CONTEXT.md §12. Technical
    names describe the underlying provider/model recipe. Either form is
    accepted by ``routing apply``.
    """
    table = Table(title="Routing presets")
    table.add_column("Operator alias", style="cyan")
    table.add_column("Technical name")
    table.add_column("Description")

    # Reverse the alias map so we can show alias next to its technical target.
    technical_to_alias: dict[str, str] = {v: k for k, v in PRESET_ALIASES.items()}

    for technical in LOCAL_PRESETS:
        alias = technical_to_alias.get(technical, "—")
        description = PRESET_DESCRIPTIONS.get(technical, "")
        table.add_row(alias, technical, description)

    console.print(table)
    console.print(
        "\nApply a preset with: "
        "[bold]meridian routing apply <project> <alias-or-technical-name>[/bold]"
    )


@routing_app.command("set")
def routing_set(
    name: Annotated[str, typer.Argument(help="Project name.")],
    purpose: Annotated[str, typer.Argument(help="LLM purpose to route.")],
    provider: Annotated[str, typer.Argument(help="Provider id (e.g. anthropic, ollama).")],
    model: Annotated[str, typer.Argument(help="Model id (e.g. claude-sonnet-4-6).")],
) -> None:
    """Set the route for a single purpose at the project level."""
    if purpose not in DEFAULT_PURPOSE_ROUTING:
        console.print(
            f"[red]Unknown purpose '{purpose}'. Known: {', '.join(DEFAULT_PURPOSE_ROUTING)}[/red]"
        )
        raise typer.Exit(code=1)
    conn = _open_project_or_exit(name)
    try:
        existing = dict(get_project_routing(conn) or {})
        existing[purpose] = (provider, model)
        set_project_routing(conn, existing)
        console.print(
            f"[green]Set {purpose} -> {provider}/{model} for project '{name}'.[/green]"
        )
    finally:
        conn.close()


@routing_app.command("unset")
def routing_unset(
    name: Annotated[str, typer.Argument(help="Project name.")],
    purpose: Annotated[str, typer.Argument(help="LLM purpose to clear (falls back to default).")],
) -> None:
    """Remove a per-project route override for one purpose."""
    conn = _open_project_or_exit(name)
    try:
        existing = dict(get_project_routing(conn) or {})
        if purpose not in existing:
            console.print(
                f"[yellow]No project-level override for '{purpose}'.[/yellow]"
            )
            return
        del existing[purpose]
        set_project_routing(conn, existing or None)
        console.print(
            f"[green]Cleared project override for '{purpose}'. "
            f"Will inherit from settings/env/default.[/green]"
        )
    finally:
        conn.close()


@routing_app.command("air-gap-on")
def routing_air_gap_on(
    name: Annotated[str, typer.Argument(help="Project name.")],
) -> None:
    """Enable air-gap mode for this project (block any cloud route)."""
    conn = _open_project_or_exit(name)
    try:
        set_air_gapped(conn, True)
        console.print(f"[green]Air-gap mode ENABLED for project '{name}'.[/green]")
        # Quick sanity-check: warn if any resolved route is non-local.
        from meridian.config import LOCAL_PROVIDERS
        project_routing = get_project_routing(conn)
        bad = []
        for purpose in DEFAULT_PURPOSE_ROUTING:
            provider, model = settings.resolve_route(
                purpose, project_routing=project_routing
            )
            if provider not in LOCAL_PROVIDERS:
                bad.append((purpose, provider, model))
        if bad:
            console.print(
                "[yellow]Warning: the following purposes still resolve to a "
                "cloud route. They will fail at call time until you change "
                "them (e.g. `meridian routing set` or `meridian routing apply air-gapped`):[/yellow]"
            )
            for purpose, provider, model in bad:
                console.print(f"  - {purpose}: {provider}/{model}")
    finally:
        conn.close()


@routing_app.command("air-gap-off")
def routing_air_gap_off(
    name: Annotated[str, typer.Argument(help="Project name.")],
) -> None:
    """Disable air-gap mode for this project."""
    conn = _open_project_or_exit(name)
    try:
        set_air_gapped(conn, False)
        console.print(f"[green]Air-gap mode DISABLED for project '{name}'.[/green]")
    finally:
        conn.close()


# ────────────────────────────────────────────────────────────────────────────
# Review subgroup — accept/reject/edit deliverables, promote audit, resolve
# questions/conflicts, manage taxonomy proposals (CONTEXT.md §5, §9).
# ────────────────────────────────────────────────────────────────────────────


review_app = typer.Typer(
    name="review",
    help="Review and categorise extraction outputs (CONTEXT.md §9).",
    no_args_is_help=True,
)
app.add_typer(review_app, name="review")


@app.command("review-status")
def review_status(
    name: Annotated[str, typer.Argument(help="Project name.")],
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit the full ProjectCoverage as JSON."),
    ] = False,
) -> None:
    """Show baseline-trustworthiness dashboard (CONTEXT.md §9 + §14)."""
    from meridian.coverage import project_coverage, render_coverage_text

    conn = _open_project_or_exit(name)
    try:
        coverage = project_coverage(conn)
        if json_out:
            console.print_json(coverage.model_dump_json())
        else:
            console.print(render_coverage_text(coverage))
    finally:
        conn.close()


# ── Quarantined deliverables ─────────────────────────────────────────────


@review_app.command("walk-quarantine")
def review_walk_quarantine(
    name: Annotated[str, typer.Argument(help="Project name.")],
    limit: Annotated[int, typer.Option(help="Max items to walk this session.")] = 20,
) -> None:
    """Walk through quarantined deliverables one at a time."""
    from meridian.review.deliverables import (
        accept_deliverable,
        list_quarantined,
        reject_deliverable,
    )

    conn = _open_project_or_exit(name)
    try:
        items = list_quarantined(conn, limit=limit)
        if not items:
            console.print("[green]No quarantined deliverables. ✓[/green]")
            return
        console.print(f"[bold]{len(items)} quarantined item(s); walking up to {limit}.[/bold]\n")
        for i, item in enumerate(items, 1):
            console.print(f"[cyan]── {i}/{len(items)} ──[/cyan]")
            console.print(f"  trade:    {item.trade or '-'}")
            console.print(f"  service:  {item.service or '-'}")
            console.print(f"  category: {item.category or '-'}")
            console.print(f"  conf:     {item.confidence}")
            console.print(f"  flags:    {', '.join(item.flags) or '-'}")
            console.print(f"  source:   {item.source_filename}")
            console.print(f"  summary:  {item.summary}")
            choice = typer.prompt("  [a]ccept / [r]eject / [s]kip / [q]uit", default="s")
            choice = choice.strip().lower()
            if choice == "q":
                break
            if choice == "a":
                r = accept_deliverable(conn, item.deliverable_id)
                console.print(f"  [green]→ {r.before_status} → {r.after_status}[/green]")
            elif choice == "r":
                reason = typer.prompt("  rejection reason (optional)", default="")
                r = reject_deliverable(conn, item.deliverable_id, reason=reason or None)
                console.print(f"  [yellow]→ {r.before_status} → {r.after_status}[/yellow]")
            console.print()
    finally:
        conn.close()


@review_app.command("accept")
def review_accept(
    name: Annotated[str, typer.Argument(help="Project name.")],
    deliverable_id: Annotated[str, typer.Argument()],
) -> None:
    """Accept one quarantined deliverable into the master register."""
    from meridian.review.deliverables import accept_deliverable

    conn = _open_project_or_exit(name)
    try:
        r = accept_deliverable(conn, deliverable_id)
        console.print(f"[green]{deliverable_id}: {r.before_status} → {r.after_status}[/green]")
    finally:
        conn.close()


@review_app.command("reject")
def review_reject(
    name: Annotated[str, typer.Argument(help="Project name.")],
    deliverable_id: Annotated[str, typer.Argument()],
    reason: Annotated[str | None, typer.Option(help="Optional rejection reason.")] = None,
) -> None:
    """Reject a deliverable (kept in audit trail; excluded from master)."""
    from meridian.review.deliverables import reject_deliverable

    conn = _open_project_or_exit(name)
    try:
        r = reject_deliverable(conn, deliverable_id, reason=reason)
        console.print(f"[yellow]{deliverable_id}: {r.before_status} → {r.after_status}[/yellow]")
    finally:
        conn.close()


@review_app.command("edit")
def review_edit(
    name: Annotated[str, typer.Argument(help="Project name.")],
    deliverable_id: Annotated[str, typer.Argument()],
    summary: Annotated[str | None, typer.Option(help="New summary.")] = None,
    trade: Annotated[str | None, typer.Option(help="New trade value.")] = None,
    service: Annotated[str | None, typer.Option(help="New service value.")] = None,
    category: Annotated[str | None, typer.Option(help="New category value.")] = None,
) -> None:
    """Create an edited child row (parent kept for audit trail)."""
    from meridian.review.deliverables import edit_deliverable

    conn = _open_project_or_exit(name)
    try:
        r = edit_deliverable(
            conn,
            deliverable_id,
            summary=summary,
            trade_value=trade,
            service_value=service,
            category_value=category,
        )
        console.print(f"[green]{deliverable_id} → new child {r.deliverable_id} (status={r.after_status})[/green]")
    finally:
        conn.close()


# ── Audit (OUTSIDE) rows ─────────────────────────────────────────────────


@review_app.command("walk-audit")
def review_walk_audit(
    name: Annotated[str, typer.Argument(help="Project name.")],
    limit: Annotated[int, typer.Option()] = 20,
) -> None:
    """Walk audit (OUTSIDE) rows; promote any the LLM was wrong to reject."""
    from meridian.review.audit import list_audit, promote_audit_to_deliverable

    conn = _open_project_or_exit(name)
    try:
        items = list_audit(conn, promoted_only=False, limit=limit)
        items = [i for i in items if i.user_promoted_to_deliverable_id is None]
        if not items:
            console.print("[green]No pending audit rows. ✓[/green]")
            return
        for i, item in enumerate(items, 1):
            console.print(f"[cyan]── {i}/{len(items)} ──[/cyan]")
            console.print(f"  source:   {item.source_filename}")
            console.print(f"  candidate: {item.candidate_text}")
            console.print(f"  why OUT:   {item.rejection_reason}")
            choice = typer.prompt("  [p]romote / [k]eep-out / [q]uit", default="k")
            choice = choice.strip().lower()
            if choice == "q":
                break
            if choice == "p":
                trade = typer.prompt("  trade", default="") or None
                service = typer.prompt("  service", default="") or None
                summary = typer.prompt("  summary", default=item.candidate_text)
                r = promote_audit_to_deliverable(
                    conn, item.id, trade_value=trade, service_value=service, summary=summary
                )
                console.print(f"  [green]→ promoted to deliverable {r.new_deliverable_id}[/green]")
            console.print()
    finally:
        conn.close()


@review_app.command("promote-audit")
def review_promote_audit(
    name: Annotated[str, typer.Argument(help="Project name.")],
    audit_id: Annotated[str, typer.Argument()],
    trade: Annotated[str | None, typer.Option()] = None,
    service: Annotated[str | None, typer.Option()] = None,
    summary: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Promote a single audit row into a deliverable."""
    from meridian.review.audit import promote_audit_to_deliverable

    conn = _open_project_or_exit(name)
    try:
        r = promote_audit_to_deliverable(
            conn, audit_id, trade_value=trade, service_value=service, summary=summary
        )
        console.print(f"[green]audit {audit_id} → deliverable {r.new_deliverable_id}[/green]")
    finally:
        conn.close()


# ── HITL questions ───────────────────────────────────────────────────────


@review_app.command("walk-questions")
def review_walk_questions(
    name: Annotated[str, typer.Argument(help="Project name.")],
    limit: Annotated[int, typer.Option()] = 50,
) -> None:
    """Walk pending HITL questions; resolve or dismiss each."""
    from meridian.review.questions import (
        dismiss_question,
        list_questions,
        resolve_question,
    )

    conn = _open_project_or_exit(name)
    try:
        items = list_questions(conn, status="pending", limit=limit)
        if not items:
            console.print("[green]No pending questions. ✓[/green]")
            return
        for i, q in enumerate(items, 1):
            console.print(f"[cyan]── {i}/{len(items)} — {q.kind} ──[/cyan]")
            console.print(f"  context:  {q.context}")
            console.print(f"  question: {q.question_text}")
            choice = typer.prompt("  [r]esolve / [d]ismiss / [s]kip / [q]uit", default="s")
            choice = choice.strip().lower()
            if choice == "q":
                break
            if choice == "r":
                ans = typer.prompt("  answer (free-text)", default="")
                r = resolve_question(conn, q.id, resolution_payload={"answer": ans})
                console.print(f"  [green]→ {r.before_status} → {r.after_status}[/green]")
            elif choice == "d":
                reason = typer.prompt("  dismiss reason (optional)", default="")
                r = dismiss_question(conn, q.id, reason=reason or None)
                console.print(f"  [yellow]→ {r.before_status} → {r.after_status}[/yellow]")
            console.print()
    finally:
        conn.close()


# ── Conflicts ────────────────────────────────────────────────────────────


@review_app.command("walk-conflicts")
def review_walk_conflicts(
    name: Annotated[str, typer.Argument(help="Project name.")],
    limit: Annotated[int, typer.Option()] = 50,
) -> None:
    """Walk pending conflicts; resolve via accept-A / accept-B / reject-both / hybrid."""
    from meridian.review.conflicts import list_conflicts, resolve_conflict

    conn = _open_project_or_exit(name)
    try:
        items = list_conflicts(conn, status="pending", limit=limit)
        if not items:
            console.print("[green]No pending conflicts. ✓[/green]")
            return
        for i, c in enumerate(items, 1):
            console.print(f"[cyan]── {i}/{len(items)} — {c.kind} ──[/cyan]")
            console.print(f"  most-onerous reasoning: {c.most_onerous_reasoning}")
            for j, p in enumerate(c.parties):
                marker = "  [A]" if j == 0 else f"  [{chr(ord('A') + j)}]"
                summary = getattr(p, "summary_or_text", "") or getattr(p, "party_position", "") or ""
                console.print(f"{marker} {p.party_kind}: {summary[:120]}")
            choice = typer.prompt(
                "  [accept-A]/[accept-B]/[reject-both]/[skip]/[quit]",
                default="skip",
            ).strip().lower()
            if choice in {"q", "quit"}:
                break
            if choice in {"accept-a", "a"}:
                r = resolve_conflict(
                    conn, c.id, action="accept_a", accept_party_id=c.parties[0].party_id
                )
                console.print(f"  [green]→ {r.action} → {r.after_status}[/green]")
            elif choice in {"accept-b", "b"}:
                if len(c.parties) < 2:
                    console.print("  [red]No party B![/red]")
                    continue
                r = resolve_conflict(
                    conn, c.id, action="accept_b", accept_party_id=c.parties[1].party_id
                )
                console.print(f"  [green]→ {r.action} → {r.after_status}[/green]")
            elif choice in {"reject-both", "r"}:
                r = resolve_conflict(conn, c.id, action="reject_both")
                console.print(f"  [yellow]→ {r.action} → {r.after_status}[/yellow]")
            console.print()
    finally:
        conn.close()


# ── Taxonomy proposals ───────────────────────────────────────────────────


@review_app.command("walk-taxonomy")
def review_walk_taxonomy(
    name: Annotated[str, typer.Argument(help="Project name.")],
) -> None:
    """Walk unconfirmed taxonomy proposals; confirm / merge / reject each."""
    from meridian.review.taxonomy import (
        confirm_taxonomy,
        list_pending_taxonomy,
        merge_taxonomy,
        reject_taxonomy,
    )

    conn = _open_project_or_exit(name)
    try:
        items = list_pending_taxonomy(conn)
        if not items:
            console.print("[green]No unconfirmed taxonomy proposals. ✓[/green]")
            return
        for i, t in enumerate(items, 1):
            console.print(f"[cyan]── {i}/{len(items)} ──[/cyan]")
            console.print(f"  table:      {t.table}")
            console.print(f"  value:      {t.value!r}")
            console.print(f"  source:     {t.source}")
            console.print(f"  in_use_by:  {t.in_use_count} deliverables")

            # ── Round-15: render the bootstrap LLM's auto-assessment so the
            # SME has a recommendation in front of them. The LLM only
            # *recommends* — the SME still presses a key (three-outcome
            # discipline; no auto-confirm in the review queue).
            if t.llm_recommended_action is None:
                console.print(
                    "  [dim]LLM recommendation: not available "
                    "(proposal pre-dates auto-assessment)[/dim]"
                )
            elif t.llm_recommended_action == "confirm":
                pct = (
                    f"{int(round(t.llm_confidence * 100))}% confidence"
                    if t.llm_confidence is not None
                    else "no confidence reported"
                )
                reason = f' — "{t.llm_reasoning}"' if t.llm_reasoning else ""
                console.print(
                    f"  [bold]LLM recommends:[/bold] confirm ({pct}){reason}"
                )
            elif t.llm_recommended_action == "merge_into":
                pct = (
                    f"{int(round(t.llm_confidence * 100))}% confidence"
                    if t.llm_confidence is not None
                    else "no confidence reported"
                )
                reason = f' — "{t.llm_reasoning}"' if t.llm_reasoning else ""
                tgt = t.llm_merge_target or "<unspecified>"
                console.print(
                    f"  [bold]LLM recommends:[/bold] merge into {tgt} ({pct}){reason}"
                )
            else:  # defer_to_user
                reason = f' — "{t.llm_reasoning}"' if t.llm_reasoning else ""
                console.print(
                    f"  [dim]LLM recommends: defer to your judgement "
                    f"(mixed signal){reason}[/dim]"
                )

            has_reco = t.llm_recommended_action in {"confirm", "merge_into"}
            prompt_text = (
                "  [A]ccept LLM recommendation / [c]onfirm / [m]erge / "
                "[r]eject / [s]kip / [q]uit"
                if has_reco
                else "  [c]onfirm / [m]erge / [r]eject / [s]kip / [q]uit"
            )
            default_choice = "a" if has_reco else "s"
            choice = typer.prompt(prompt_text, default=default_choice).strip().lower()
            if choice == "q":
                break
            if choice == "a" and has_reco:
                # Quick-accept follows the LLM's recommendation. The reviewer
                # still pressed a key — the LLM did not auto-act.
                if t.llm_recommended_action == "confirm":
                    confirm_taxonomy(conn, table=t.table, value=t.value)
                    console.print("  [green]→ confirmed (per LLM recommendation)[/green]")
                else:  # merge_into
                    target = t.llm_merge_target or ""
                    if not target:
                        console.print(
                            "  [red]→ LLM did not specify a merge target; "
                            "use [m] to choose manually.[/red]"
                        )
                    else:
                        r = merge_taxonomy(
                            conn, table=t.table, source_value=t.value, target_value=target
                        )
                        console.print(
                            f"  [green]→ merged into {r.target_value!r} "
                            f"(per LLM recommendation); "
                            f"{r.affected_deliverable_count} deliverable(s) repointed[/green]"
                        )
            elif choice == "c":
                confirm_taxonomy(conn, table=t.table, value=t.value)
                console.print("  [green]→ confirmed[/green]")
            elif choice == "m":
                target = typer.prompt(f"  merge {t.value!r} INTO which existing {t.table} value?")
                r = merge_taxonomy(conn, table=t.table, source_value=t.value, target_value=target)
                console.print(
                    f"  [green]→ merged into {r.target_value!r}; "
                    f"{r.affected_deliverable_count} deliverable(s) repointed[/green]"
                )
            elif choice == "r":
                try:
                    reject_taxonomy(conn, table=t.table, value=t.value)
                    console.print("  [yellow]→ rejected (deactivated)[/yellow]")
                except ValueError as exc:
                    console.print(f"  [red]→ {exc}[/red]")
            console.print()
    finally:
        conn.close()


@review_app.command("confirm-taxonomy")
def review_confirm_taxonomy(
    name: Annotated[str, typer.Argument()],
    table: Annotated[str, typer.Option(help="trade | service | category")],
    value: Annotated[str, typer.Option()],
) -> None:
    """Confirm one taxonomy proposal as canonical."""
    from meridian.review.taxonomy import confirm_taxonomy

    conn = _open_project_or_exit(name)
    try:
        confirm_taxonomy(conn, table=table, value=value)  # type: ignore[arg-type]
        console.print(f"[green]Confirmed {table}/{value!r} as canonical.[/green]")
    finally:
        conn.close()


@review_app.command("merge-taxonomy")
def review_merge_taxonomy(
    name: Annotated[str, typer.Argument()],
    table: Annotated[str, typer.Option(help="trade | service | category")],
    source: Annotated[str, typer.Option(help="The taxonomy value to retire.")],
    target: Annotated[str, typer.Option(help="The canonical value to merge into.")],
) -> None:
    """Merge one taxonomy value into another (cascades all deliverable rows)."""
    from meridian.review.taxonomy import merge_taxonomy

    conn = _open_project_or_exit(name)
    try:
        r = merge_taxonomy(conn, table=table, source_value=source, target_value=target)  # type: ignore[arg-type]
        console.print(
            f"[green]Merged {table}/{source!r} → {table}/{r.target_value!r}; "
            f"{r.affected_deliverable_count} deliverable(s) repointed.[/green]"
        )
    finally:
        conn.close()


# ────────────────────────────────────────────────────────────────────────────
# Analytics subgroup — modules that ride on top of the existing data foundation
# (CONTEXT.md §23 #9 v1.x analyses + Tier 2 add-ons).
# ────────────────────────────────────────────────────────────────────────────


# ────────────────────────────────────────────────────────────────────────────
# Auth — TOTP enrolment, status, verify, logout, reset (CONTEXT.md §16).
# ────────────────────────────────────────────────────────────────────────────


auth_app = typer.Typer(
    name="auth",
    help="TOTP enrolment and per-session login (single-user, self-enrolled).",
    no_args_is_help=True,
)
app.add_typer(auth_app, name="auth")


@auth_app.command("enroll")
def auth_enroll(
    account: Annotated[
        str,
        typer.Option("--account", help="Label shown in the authenticator app."),
    ] = "user@meridian",
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite an existing enrolment."),
    ] = False,
) -> None:
    """Self-enrol a TOTP secret + recovery codes (CONTEXT.md §16)."""
    from meridian.auth.qr import render_ascii_qr
    from meridian.auth.recovery import (
        generate_recovery_codes,
        hash_recovery_code,
    )
    from meridian.auth.secrets import default_store, get_recovery_salt
    from meridian.auth.totp import (
        generate_secret,
        provisioning_uri,
        verify_totp,
    )

    store = default_store()
    if store.is_configured() and not force:
        console.print(
            "[red]TOTP is already enrolled.[/red] Re-run with --force to overwrite "
            "(this destroys the existing secret + recovery codes)."
        )
        raise typer.Exit(code=1)

    secret = generate_secret()
    uri = provisioning_uri(secret, account_name=account)
    recovery = generate_recovery_codes(10)

    console.print("[bold]Scan this QR with your authenticator app[/bold]")
    console.print(render_ascii_qr(uri))
    console.print(f"\n[dim]Or enter this secret manually:[/dim] [bold]{secret}[/bold]")
    console.print(f"[dim]Provisioning URI:[/dim] {uri}\n")

    console.print("[yellow bold]Recovery codes — save these NOW (shown once):[/yellow bold]")
    for c in recovery:
        console.print(f"  {c}")
    console.print()

    code = typer.prompt("Enter the 6-digit code from your app to confirm enrolment").strip()
    if not verify_totp(secret, code):
        console.print("[red]Code did not verify — enrolment aborted.[/red]")
        raise typer.Exit(code=1)

    from datetime import UTC, datetime

    salt = get_recovery_salt()
    hashed = [hash_recovery_code(c, salt=salt) for c in recovery]
    store.save(
        secret=secret,
        recovery_codes_hashed=hashed,
        created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    console.print("[green]Enrolment confirmed.[/green]")
    console.print(
        "[yellow bold]Recovery codes (last reminder — they will not be shown again):[/yellow bold]"
    )
    for c in recovery:
        console.print(f"  {c}")
    console.print(
        "\n[dim]Stored at:[/dim] "
        f"{getattr(store, 'path', '<keyring>')}"
    )


@auth_app.command("status")
def auth_status() -> None:
    """Show whether TOTP is enrolled (no secrets exposed)."""
    from meridian.auth.secrets import default_store

    store = default_store()
    data = store.load()
    if data is None:
        console.print("[yellow]Not enrolled.[/yellow] Run `meridian auth enroll`.")
        return
    console.print("[green]Enrolled.[/green]")
    console.print(f"  Created at:    {data.get('created_at', '<unknown>')}")
    console.print(
        f"  Recovery codes remaining: {len(data.get('recovery_codes_hashed', []))}/10"
    )
    console.print(f"  Backend: {type(store).__name__}")
    if hasattr(store, "path"):
        console.print(f"  Path: {store.path}")


@auth_app.command("verify")
def auth_verify(
    code: Annotated[
        str | None,
        typer.Option("--code", help="Code to verify (otherwise prompted)."),
    ] = None,
) -> None:
    """Verify a TOTP code against the stored secret (testing helper)."""
    from meridian.auth.recovery import verify_recovery_code
    from meridian.auth.secrets import default_store, get_recovery_salt
    from meridian.auth.totp import verify_totp

    store = default_store()
    data = store.load()
    if data is None:
        console.print("[red]Not enrolled — run `meridian auth enroll` first.[/red]")
        raise typer.Exit(code=1)

    submitted = code or typer.prompt("Enter 6-digit code (or recovery code)").strip()
    if verify_totp(data["secret"], submitted):
        console.print("[green]TOTP verified.[/green]")
        return
    # Fall through to recovery-code path.
    salt = get_recovery_salt()
    matched, remaining = verify_recovery_code(
        data.get("recovery_codes_hashed", []), submitted, salt=salt
    )
    if matched:
        # Burn the used code.
        if hasattr(store, "update_recovery_codes"):
            store.update_recovery_codes(remaining)
        console.print(
            f"[green]Recovery code accepted.[/green] {len(remaining)} remaining."
        )
        return
    console.print("[red]Code rejected.[/red]")
    raise typer.Exit(code=1)


@auth_app.command("logout")
def auth_logout() -> None:
    """Revoke every active session token."""
    from meridian.auth.session import revoke_all_sessions

    n = revoke_all_sessions()
    console.print(f"[green]Revoked {n} session(s).[/green]")


@auth_app.command("reset")
def auth_reset(
    confirm: Annotated[
        str,
        typer.Option(
            "--confirm",
            help="Type the literal string 'RESET' to confirm. "
            "Destroys the TOTP secret and all recovery codes.",
        ),
    ] = "",
) -> None:
    """Wipe the TOTP secret + recovery codes. Requires explicit confirmation."""
    from meridian.auth.secrets import default_store
    from meridian.auth.session import revoke_all_sessions

    if confirm != "RESET":
        console.print(
            "[red]Refusing to reset.[/red] Re-run with --confirm RESET to proceed."
        )
        raise typer.Exit(code=1)
    store = default_store()
    store.clear()
    revoke_all_sessions()
    console.print("[green]TOTP secret + recovery codes cleared.[/green]")


# ────────────────────────────────────────────────────────────────────────────
# Analytics — round-6 standalone analytics tools.
# ────────────────────────────────────────────────────────────────────────────


analytics_app = typer.Typer(
    name="analytics",
    help="Standalone analytics over the deliverables / audit / conflict data.",
    no_args_is_help=True,
)
app.add_typer(analytics_app, name="analytics")

# Register sub-commands from the round-6 analytics agents.
from meridian.analytics.cli_a import register_cli as _register_analytics_a  # noqa: E402
from meridian.analytics.cli_b import register_cli as _register_analytics_b  # noqa: E402

_register_analytics_a(analytics_app)
_register_analytics_b(analytics_app)


# ────────────────────────────────────────────────────────────────────────────
# Round-10 modules — registered as Typer sub-apps.
# ────────────────────────────────────────────────────────────────────────────

from meridian.tender.cli import tender_app  # noqa: E402

app.add_typer(tender_app, name="tender")

from meridian.evidence.cli import evidence_app  # noqa: E402

app.add_typer(evidence_app, name="evidence")

from meridian.extract.cross_references_cli import xref_app  # noqa: E402

app.add_typer(xref_app, name="xref")

# ── Round 12: production-readiness client scaffolds ──
# Each ships with a placeholder URL/key clearly marked DEFERRED in-source;
# wire-ready for §3.4–§3.8 once those decisions are made.
from meridian.licensing.cli import license_app  # noqa: E402

app.add_typer(license_app, name="license")

from meridian.updates.cli import updates_app  # noqa: E402

app.add_typer(updates_app, name="updates")

from meridian.crash.cli import crash_app  # noqa: E402

app.add_typer(crash_app, name="crash")

# ── Round 13: onboarding wizard ──
from meridian.onboarding.cli import init_command, onboarding_app  # noqa: E402

app.add_typer(onboarding_app, name="init-cmd")
app.command("init")(init_command)

# ── Round 13: project backup/restore ──
from meridian.backup.cli import backup_app  # noqa: E402

app.add_typer(backup_app, name="backup")


# Round-10 also bumped the schema (v2 → v3, adds cross_reference_sweep_*).
# ────────────────────────────────────────────────────────────────────────────
# Docs convenience — a one-shot URL opener so newcomers don't have to
# remember the GitHub URL. Pure stdlib (webbrowser); offline-safe (just
# prints the URL + lists topics if the open call fails).
# ────────────────────────────────────────────────────────────────────────────


@app.command("docs")
def docs(
    topic: Annotated[
        str | None,
        typer.Argument(
            help=(
                "Optional topic shortcut: getting-started, install, concepts, "
                "cli, troubleshooting, security, architecture, release-notes, "
                "decisions. Omit to open the docs index."
            ),
        ),
    ] = None,
    list_topics: Annotated[
        bool,
        typer.Option(
            "--list", help="Print available topic shortcuts and exit."
        ),
    ] = False,
    print_only: Annotated[
        bool,
        typer.Option(
            "--print",
            help="Print the URL without opening a browser (useful in SSH / headless contexts).",
        ),
    ] = False,
) -> None:
    """Open the Meridian docs in your default browser.

    Examples:
      meridian docs                          # opens the docs index
      meridian docs getting-started          # opens the 5-minute quickstart
      meridian docs troubleshooting          # opens the troubleshooting guide
      meridian docs --list                   # lists every available topic
      meridian docs install --print          # prints the URL only
    """
    if list_topics:
        console.print("[bold]Available topics:[/bold]")
        for shortcut, filename in sorted(_DOCS_TOPICS.items()):
            console.print(f"  [cyan]{shortcut:18}[/cyan] -> docs/{filename}")
        console.print(f"\nDocs base URL: [dim]{_DOCS_BASE_URL}[/dim]")
        return

    if topic is None:
        url = _DOCS_BASE_URL
    else:
        key = topic.strip().lower()
        if key not in _DOCS_TOPICS:
            console.print(f"[yellow]Unknown topic: {topic!r}[/yellow]")
            console.print(
                "Run [bold]meridian docs --list[/bold] to see available topics, "
                f"or browse the index at {_DOCS_BASE_URL}"
            )
            raise typer.Exit(code=2)
        url = f"{_DOCS_BASE_URL}/{_DOCS_TOPICS[key]}"

    if print_only:
        console.print(url)
        return

    import webbrowser  # noqa: PLC0415

    opened = webbrowser.open(url)
    if opened:
        console.print(f"Opening: [cyan]{url}[/cyan]")
    else:
        # webbrowser.open returns False on some headless / locked-down hosts
        # (corporate Windows, SSH sessions). Fall back to printing the URL
        # so the user can copy/paste it.
        console.print(
            f"[yellow]Could not open a browser automatically.[/yellow] "
            f"Copy this URL: [cyan]{url}[/cyan]"
        )


# Existing project DBs need a one-time migration; new projects get v3 free
# via create_project. Migration is idempotent (CREATE TABLE IF NOT EXISTS +
# INSERT OR IGNORE on schema_migrations) so re-running is safe.
@app.command("db-migrate")
def db_migrate(
    name: Annotated[str, typer.Argument(help="Project name to migrate.")],
) -> None:
    """Apply pending schema migrations to an existing project DB.

    Round 10 introduced schema v3 (cross-reference sweep tables). Existing
    projects created before round 10 are at v2 and will fail xref-sweep
    commands until this migration runs. Idempotent — safe to re-run.
    """
    from meridian.db.connection import SCHEMA_VERSION, initialise

    db_path = project_db_path(name)
    if not db_path.exists():
        console.print(f"[red]Project not found: {db_path}[/red]")
        raise typer.Exit(code=1)
    _bind_project(name)

    import sqlite3

    pre_conn = sqlite3.connect(db_path)
    try:
        pre_v = pre_conn.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0]
    finally:
        pre_conn.close()

    conn = initialise(db_path)
    try:
        post_v = conn.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0]
    finally:
        conn.close()

    if pre_v == post_v:
        console.print(
            f"[green]Already at schema v{post_v}.[/green] "
            f"(target: v{SCHEMA_VERSION}; no changes applied)"
        )
    else:
        console.print(
            f"[green]Migrated[/green] {name}: "
            f"schema v{pre_v} -> v{post_v} (target: v{SCHEMA_VERSION})"
        )


@app.command("start")
def start(
    no_browser: Annotated[
        bool,
        typer.Option(
            "--no-browser",
            help=(
                "Don't open the default browser after the backend is healthy. "
                "Use this for headless boxes or when a Tauri shell has already "
                "launched and will navigate itself."
            ),
        ),
    ] = False,
    port: Annotated[
        int,
        typer.Option(
            "--port",
            help=(
                "Port the backend listens on. Default 8000 — only change this "
                "if you have a port conflict."
            ),
        ),
    ] = 8000,
) -> None:
    """Start the Meridian backend and open the GUI in your browser.

    If a Meridian backend is already responding on ``http://localhost:<port>/health``,
    this command just opens the browser at the right page (the setup wizard
    if onboarding isn't finished, the main app otherwise).

    Otherwise it starts uvicorn in the foreground — Ctrl-C stops it. This is
    the same command the future Tauri sidecar (round 18) invokes; keep the
    surface tiny.
    """
    import time
    import webbrowser
    from urllib.error import URLError
    from urllib.request import Request, urlopen

    # 127.0.0.1, not localhost: Windows resolves "localhost" to ::1 (IPv6)
    # first, but uvicorn binds to 127.0.0.1 (IPv4). The 1s probe timeout
    # in urllib doesn't fall back to IPv4 fast enough -> the health check
    # hangs against an empty IPv6 socket. See alpha-5 release notes.
    base_url = f"http://127.0.0.1:{port}"
    health_url = f"{base_url}/health"
    setup_state_url = f"{base_url}/setup/state"
    welcome_url = f"{base_url}/setup/"
    home_url = f"{base_url}/"

    def _probe(url: str, timeout: float = 1.0) -> int | None:
        """Return HTTP status code from a GET, or None if the call failed."""
        try:
            req = Request(url, headers={"User-Agent": "meridian-cli/start"})
            with urlopen(req, timeout=timeout) as resp:  # noqa: S310 — localhost only
                return resp.status
        except (URLError, TimeoutError, OSError):
            return None

    def _pick_target_url() -> str:
        """Welcome page when setup isn't complete; main app otherwise."""
        try:
            req = Request(setup_state_url, headers={"User-Agent": "meridian-cli/start"})
            with urlopen(req, timeout=2.0) as resp:  # noqa: S310 — localhost only
                if resp.status == 200:
                    payload = json.loads(resp.read().decode("utf-8"))
                    if isinstance(payload, dict) and payload.get("is_complete") is True:
                        return home_url
                    if isinstance(payload, dict) and payload.get("complete") is True:
                        return home_url
        except (URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError):
            pass
        # Default: setup wizard. Safer to over-show the wizard than skip it.
        return welcome_url

    def _open(url: str) -> None:
        if no_browser:
            console.print(f"[dim](--no-browser) Skipping browser open. URL: {url}[/dim]")
            return
        opened = webbrowser.open(url)
        if opened:
            console.print(f"Opening: [cyan]{url}[/cyan]")
        else:
            console.print(
                f"[yellow]Could not open a browser automatically.[/yellow] "
                f"Paste this URL into one yourself: [cyan]{url}[/cyan]"
            )

    # alpha-3 — print the resolved Meridian-home + projects-dir up front so a
    # config-resolution surprise (the alpha-2 elevated-cwd PermissionError
    # class of bug) is visible at a glance instead of failing silently inside
    # configure_logging during uvicorn's import of the app module.
    from meridian.config import _meridian_home  # local import — avoid widening cli's top-level surface
    console.print(
        f"[dim]Meridian home: {_meridian_home()}[/dim]\n"
        f"[dim]Projects dir: {settings.projects_dir}[/dim]"
    )

    # Fast path: backend already up.
    if _probe(health_url) == 200:
        target = _pick_target_url()
        console.print(f"[green]Meridian is already running at {base_url}.[/green]")
        _open(target)
        return

    # Slow path: spawn uvicorn in-process (foreground) and wait for /health.
    console.print(f"[cyan]Starting Meridian backend on port {port}...[/cyan]")
    try:
        import uvicorn  # noqa: PLC0415
    except ImportError as exc:
        console.print(f"[red]uvicorn is not installed: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    # Run uvicorn in a daemon thread so the main thread can poll /health
    # and trigger the browser open before blocking on the server.
    import threading

    config = uvicorn.Config(
        "meridian.api.main:app",
        host="127.0.0.1",
        port=port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)

    def _serve() -> None:
        try:
            server.run()
        except Exception:  # pragma: no cover — surfaced via the health timeout
            _log.exception("cli.start.server_crash")

    thread = threading.Thread(target=_serve, name="meridian-uvicorn", daemon=True)
    thread.start()

    # Poll /health for up to 30 s. Uvicorn cold-starts in well under that
    # even on slow disks; fail loud if we exceed it.
    deadline = time.monotonic() + 30.0
    healthy = False
    while time.monotonic() < deadline:
        if not thread.is_alive():
            break
        if _probe(health_url, timeout=0.5) == 200:
            healthy = True
            break
        time.sleep(0.25)

    if not healthy:
        console.print(
            "[red]Backend did not come up in 30 seconds.[/red] "
            "Check stderr above for uvicorn errors."
        )
        # Ask the server to shut down cleanly so we don't leak the thread.
        server.should_exit = True
        raise typer.Exit(code=1)

    target = _pick_target_url()
    console.print(
        f"[green]Meridian is running at {base_url} — Ctrl-C to stop.[/green]"
    )
    _open(target)

    # Block on the server thread so foreground Ctrl-C reaches uvicorn's
    # signal handler. join() with no timeout returns when the thread exits
    # (graceful shutdown) or when the process is killed.
    try:
        thread.join()
    except KeyboardInterrupt:
        console.print("[yellow]Shutting down...[/yellow]")
        server.should_exit = True
        thread.join(timeout=5.0)


def _wrap_app(app_obj: typer.Typer) -> typer.Typer:
    """Wrap the Typer app so any uncaught exception is captured to JSONL."""
    original_call = app_obj.__call__

    def _wrapped(*args: object, **kwargs: object) -> object:
        try:
            return original_call(*args, **kwargs)
        except SystemExit:
            # Click's normal exit path — the result_callback / typer.Exit
            # cycle. Don't log as error.
            raise
        except BaseException as exc:
            # Capture full stack to JSONL before propagating so
            # `meridian explain-last-error` can find it.
            if not isinstance(exc, typer.Exit):
                _log.exception("cli.error", error_type=type(exc).__name__)
            raise

    app_obj.__call__ = _wrapped  # type: ignore[method-assign]
    return app_obj


_wrap_app(app)


if __name__ == "__main__":  # pragma: no cover
    app()
