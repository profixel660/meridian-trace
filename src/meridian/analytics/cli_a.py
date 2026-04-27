"""CLI registration for the analytics modules in this thread.

Exposes:
    meridian analytics risk-hotspots
    meridian analytics nrc-summary
    meridian analytics conflict-register

Each command opens the project DB read-only via `meridian.projects.open_project`,
runs the corresponding `compute` → `write_xlsx`, and prints a short summary.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from meridian.analytics.conflict_register import (
    compute as conflict_compute,
)
from meridian.analytics.conflict_register import (
    write_xlsx as conflict_write,
)
from meridian.analytics.nrc_summary import compute as nrc_compute
from meridian.analytics.nrc_summary import write_xlsx as nrc_write
from meridian.analytics.risk_hotspots import compute as risk_compute
from meridian.analytics.risk_hotspots import write_xlsx as risk_write
from meridian.projects import open_project


def _format_distribution(dist: dict[int, int]) -> str:
    if not dist:
        return "(empty)"
    parts = [f"{score}:{count}" for score, count in sorted(dist.items(), reverse=True)]
    return ", ".join(parts)


def register_cli(parent_app: typer.Typer) -> None:
    """Attach the three analytics commands to `parent_app`."""

    @parent_app.command("risk-hotspots")
    def risk_hotspots_cmd(
        name: Annotated[str, typer.Argument(help="Project name.")],
        output: Annotated[
            Path, typer.Option("--output", "-o", help="Output xlsx path.")
        ],
        top_n: Annotated[
            int, typer.Option(help="How many top-risk items to include.")
        ] = 50,
    ) -> None:
        """Compute and export the top-N risk hotspots."""
        conn = open_project(name)
        try:
            result = risk_compute(conn, top_n=top_n)
        finally:
            conn.close()
        risk_write(result, output_path=output)
        top_score = result.items[0].risk_score if result.items else 0
        typer.echo(
            f"Wrote {len(result.items)} risk hotspots to {output}. "
            f"Top score: {top_score}. "
            f"Distribution: {_format_distribution(result.score_distribution)}."
        )

    @parent_app.command("nrc-summary")
    def nrc_summary_cmd(
        name: Annotated[str, typer.Argument(help="Project name.")],
        output: Annotated[
            Path, typer.Option("--output", "-o", help="Output xlsx path.")
        ],
    ) -> None:
        """Export every deliverable carrying `scope_shifted_to_nrc`."""
        conn = open_project(name)
        try:
            result = nrc_compute(conn)
        finally:
            conn.close()
        nrc_write(result, output_path=output)
        typer.echo(
            f"Wrote NRC summary to {output}. "
            f"{result.total_items} item(s) across {len(result.items_by_trade)} trade(s)."
        )

    @parent_app.command("conflict-register")
    def conflict_register_cmd(
        name: Annotated[str, typer.Argument(help="Project name.")],
        output: Annotated[
            Path, typer.Option("--output", "-o", help="Output xlsx path.")
        ],
        status: Annotated[
            str | None,
            typer.Option(help="Filter to a single status (e.g. pending)."),
        ] = None,
    ) -> None:
        """Export the conflict register (Summary / Conflicts / Parties)."""
        conn = open_project(name)
        try:
            result = conflict_compute(conn, status_filter=status)
        finally:
            conn.close()
        conflict_write(result, output_path=output)
        kind_str = ", ".join(
            f"{k}={v}" for k, v in sorted(result.by_kind.items())
        ) or "(none)"
        typer.echo(
            f"Wrote conflict register to {output}. "
            f"{result.total} conflict(s); {result.pending} pending; by kind: {kind_str}."
        )


__all__ = ["register_cli"]
