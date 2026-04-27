"""``meridian license`` sub-app — install / status / verify (CONTEXT.md §17)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from meridian.licensing.verify import (
    LicenseStatus,
    install_license,
    license_path,
    load_license_from_disk,
    verify_license,
)

console = Console()

license_app = typer.Typer(
    name="license",
    help=(
        "Manage the Meridian license (CONTEXT.md §17). "
        "Use 'install' after receiving a license file from support, "
        "'status' to inspect the current install, "
        "and 'verify' to check a license string without installing it."
    ),
    no_args_is_help=True,
)


def _print_status_table(status: LicenseStatus, message: str, payload) -> None:
    """Render the verification result in a Rich table for the user."""
    table = Table(title="License verification")
    table.add_column("Field")
    table.add_column("Value")
    colour = {
        LicenseStatus.valid: "green",
        LicenseStatus.expired: "red",
        LicenseStatus.signature_invalid: "red",
        LicenseStatus.revoked: "red",
        LicenseStatus.malformed: "yellow",
    }[status]
    table.add_row("Status", f"[{colour}]{status.value}[/{colour}]")
    table.add_row("Message", message)
    if payload is not None:
        table.add_row("Customer", f"{payload.customer_name} ({payload.customer_id})")
        table.add_row("Plan", payload.plan)
        table.add_row("Issued at", payload.issued_at)
        table.add_row("Expires at", payload.expires_at)
        days = payload.days_remaining()
        if days is not None:
            table.add_row("Days remaining", str(days))
        if payload.features:
            table.add_row("Features", ", ".join(payload.features))
    console.print(table)


@license_app.command("install")
def license_install(
    path: Annotated[
        Path,
        typer.Argument(
            help="Path to the license file you received from T-Bionic support.",
            exists=True,
            dir_okay=False,
            readable=True,
        ),
    ],
) -> None:
    """Copy a license file into place and verify it immediately."""
    try:
        dest = install_license(path)
    except (FileNotFoundError, OSError) as exc:
        console.print(f"[red]Could not install license: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(f"[green]License copied to[/green] {dest}")

    raw = dest.read_text(encoding="utf-8").strip()
    result = verify_license(raw)
    _print_status_table(result.status, result.message, result.payload)

    if result.status is LicenseStatus.valid:
        console.print("[green]Activation successful.[/green]")
        return
    if result.status is LicenseStatus.malformed:
        # Three-outcome discipline: borderline → "needs review", not silent fail.
        console.print(
            "[yellow]License could not be parsed (needs review). "
            "Please re-paste the license you received, or contact "
            "T-Bionic support.[/yellow]"
        )
        raise typer.Exit(code=2)
    console.print(
        f"[red]License is not usable ({result.status.value}). "
        "Contact T-Bionic support for a replacement.[/red]"
    )
    raise typer.Exit(code=1)


@license_app.command("status")
def license_status() -> None:
    """Show the currently installed license, expiry, plan, and features."""
    raw = load_license_from_disk()
    if raw is None:
        console.print(
            "[yellow]No license installed.[/yellow] "
            f"Expected at {license_path()}. "
            "Run 'meridian license install <path>' after receiving a "
            "license from T-Bionic support."
        )
        raise typer.Exit(code=1)

    result = verify_license(raw)
    _print_status_table(result.status, result.message, result.payload)

    if (
        result.status is LicenseStatus.valid
        and result.payload is not None
    ):
        days = result.payload.days_remaining()
        if days is not None and days <= 14:
            console.print(
                f"[yellow]Heads up: license expires in {days} day(s). "
                "Contact T-Bionic support to renew.[/yellow]"
            )


@license_app.command("verify")
def license_verify(
    license_string: Annotated[
        str,
        typer.Argument(
            help="A license string of the form '<base64url-payload>.<base64url-signature>'.",
        ),
    ],
) -> None:
    """Verify a license string without installing it.

    Use this to sanity-check a license you've been emailed before running
    'install'. Three outcomes:

    - valid → ready to install
    - expired / signature_invalid / revoked → won't work; contact support
    - malformed → needs review (couldn't parse — re-paste, check for whitespace)
    """
    result = verify_license(license_string)
    _print_status_table(result.status, result.message, result.payload)

    if result.status is LicenseStatus.valid:
        console.print(
            "[green]Looks good. Save the license to a file and run "
            "'meridian license install <path>'.[/green]"
        )
        return
    if result.status is LicenseStatus.malformed:
        console.print(
            "[yellow]Borderline (needs review): the license could not be "
            "parsed. Common causes: stray whitespace, copy-paste truncation, "
            "or the 'cryptography' library is not installed "
            "(pip install meridian[license]).[/yellow]"
        )
        raise typer.Exit(code=2)
    raise typer.Exit(code=1)


__all__ = ["license_app"]
