"""CLI registration for Agent-B analytics modules.

Wired into the `meridian analytics` Typer subgroup by the main thread; the
parent app passes itself in via `register_cli`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from meridian.analytics.compliance import compute as compliance_compute
from meridian.analytics.compliance import write_xlsx as compliance_write
from meridian.analytics.ose_procurement import compute as ose_compute
from meridian.analytics.ose_procurement import write_xlsx as ose_write
from meridian.analytics.trade_overlap import compute as trade_overlap_compute
from meridian.analytics.trade_overlap import write_xlsx as trade_overlap_write
from meridian.projects import open_project


def register_cli(parent_app: typer.Typer) -> None:
    """Register the analytics-B commands on the given Typer parent app."""

    @parent_app.command("compliance")
    def compliance_cmd(
        name: Annotated[str, typer.Argument(help="Project name.")],
        output: Annotated[
            Path,
            typer.Option(
                "--output",
                "-o",
                help="Path for the compliance traceability xlsx.",
            ),
        ],
    ) -> None:
        """Compliance traceability — pivot deliverables x applicable_standards."""
        conn = open_project(name)
        try:
            result = compliance_compute(conn)
            compliance_write(result, output_path=output)
        finally:
            conn.close()
        typer.echo(
            f"compliance: {result.total_deliverables} deliverables, "
            f"{result.distinct_standards} distinct standards "
            f"({len(result.one_off_standards)} one-offs); wrote {output}"
        )

    @parent_app.command("trade-overlap")
    def trade_overlap_cmd(
        name: Annotated[str, typer.Argument(help="Project name.")],
        output: Annotated[
            Path,
            typer.Option(
                "--output",
                "-o",
                help="Path for the trade-overlap xlsx.",
            ),
        ],
    ) -> None:
        """Trade overlap — co-occurrence pairs across extraction groups."""
        conn = open_project(name)
        try:
            result = trade_overlap_compute(conn)
            trade_overlap_write(result, output_path=output)
        finally:
            conn.close()
        typer.echo(
            f"trade-overlap: {result.total_extraction_groups} groups, "
            f"{result.multi_trade_groups} multi-trade ({result.multi_trade_pct}%), "
            f"{len(result.pairs)} pairs; wrote {output}"
        )

    @parent_app.command("ose-procurement")
    def ose_procurement_cmd(
        name: Annotated[str, typer.Argument(help="Project name.")],
        output: Annotated[
            Path,
            typer.Option(
                "--output",
                "-o",
                help="Path for the OSE procurement completeness xlsx.",
            ),
        ],
    ) -> None:
        """OSE procurement completeness — per-vendor scope checklist."""
        conn = open_project(name)
        try:
            result = ose_compute(conn)
            ose_write(result, output_path=output)
        finally:
            conn.close()
        typer.echo(
            f"ose-procurement: {len(result.vendors)} vendors, "
            f"overall completeness {result.overall_completeness_pct}%; wrote {output}"
        )


__all__ = ["register_cli"]
