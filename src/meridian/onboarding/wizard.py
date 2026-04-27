"""Interactive onboarding wizard for first-run setup.

Six steps, persisted after every change so a partial run is resumable:

  1. ``api_key``       — detect or prompt for ``ANTHROPIC_API_KEY``; validate
                         with a tiny live SDK call (three-outcome:
                         valid / invalid / unable-to-verify).
  2. ``totp_enrol``    — OPTIONAL. Provision a TOTP secret + recovery codes
                         via the existing ``meridian.auth`` primitives.
  3. ``first_project`` — Slugified ``create_project(name=...)``.
  4. ``first_doc``     — Path prompt -> ``ingest_file(...)``. Dedup is
                         surfaced gracefully (not treated as an error).
  5. ``bootstrap``     — OPTIONAL. ``run_bootstrap_sweep(...)`` with the
                         default sample size; explained in one sentence.
  6. ``next_steps``    — Print the §5-style first-look agenda tailored to
                         the new project.

Every step prints a "what is this?" panel before the prompt so the
non-technical PM target user (CONTEXT.md §2) is never asked a question
without context. Skippable steps are explicitly labelled "OPTIONAL".

Non-TTY callers (CI, redirected stdin) get a clear refuse message at
``run_wizard`` entry — the wizard never hangs on a piped input.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from meridian.config import settings

if TYPE_CHECKING:  # pragma: no cover — type-only
    pass


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

# These are the canonical step names persisted in OnboardingState.completed_steps
# and accepted by run_step(). Keep in sync with the docstring above.
STEP_NAMES: tuple[str, ...] = (
    "api_key",
    "totp_enrol",
    "first_project",
    "first_doc",
    "bootstrap",
    "next_steps",
)

_STATE_DIR_NAME = "_meridian"
_STATE_FILE_NAME = "onboarding_state.json"


@dataclass
class OnboardingState:
    """Persistent record of how far through the wizard the user has got.

    Fields are intentionally flat / JSON-serialisable. The wizard saves this
    after every step so a Ctrl-C in the middle of a long bootstrap doesn't
    cost the user the work they already did (project + first import survive).
    """

    api_key_configured: bool = False
    first_project_slug: str | None = None
    first_doc_imported: bool = False
    first_bootstrap_run: bool = False
    totp_enrolled: bool = False
    license_installed: bool = False
    completed_steps: list[str] = field(default_factory=list)
    last_step_at: str | None = None

    def mark(self, step_name: str) -> None:
        """Record completion of a step (idempotent)."""
        if step_name not in self.completed_steps:
            self.completed_steps.append(step_name)
        self.last_step_at = _utc_now_iso()

    def is_complete(self) -> bool:
        """True iff every required step has been ticked off."""
        return all(s in self.completed_steps for s in STEP_NAMES)


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def state_path() -> Path:
    """Where the JSON state file lives — under ``<projects_dir>/_meridian/``."""
    return settings.projects_dir / _STATE_DIR_NAME / _STATE_FILE_NAME


def load_state() -> OnboardingState | None:
    """Return the persisted state, or None if no run has ever happened."""
    p = state_path()
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    # Defensive: filter to known fields so a future schema rev doesn't crash
    # on an old file.
    known = {f for f in OnboardingState.__dataclass_fields__}
    cleaned = {k: v for k, v in raw.items() if k in known}
    if not isinstance(cleaned.get("completed_steps", []), list):
        cleaned["completed_steps"] = []
    return OnboardingState(**cleaned)


def save_state(state: OnboardingState) -> None:
    """Persist state to disk. Creates the directory tree if missing."""
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(state), indent=2, sort_keys=True), encoding="utf-8")


# --------------------------------------------------------------------------
# Console / UX helpers
# --------------------------------------------------------------------------

# Use a wizard-local console so output stays neat even if the caller has a
# differently-configured one. Mirrors meridian.cli.console style.
_console = Console()


def _is_tty() -> bool:
    """True only if BOTH stdin and stdout are interactive."""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def _what_is_this(title: str, body: str) -> None:
    """Render the per-step "what is this?" explanation panel."""
    _console.print(Panel.fit(body, title=f"[bold cyan]{title}[/bold cyan]", border_style="cyan"))


def _step_header(num: int, total: int, title: str) -> None:
    _console.rule(f"[bold]Step {num}/{total} — {title}[/bold]")


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower())
    return slug.strip("-") or "project"


# --------------------------------------------------------------------------
# Step implementations
#
# Each ``_step_*`` returns the (possibly mutated) state. They all call
# ``save_state`` themselves on success — that's how partial completion
# becomes resumable. None of them may swallow KeyboardInterrupt.
# --------------------------------------------------------------------------


def _step_api_key(state: OnboardingState) -> OnboardingState:
    """Step 1 — Anthropic API key. REQUIRED (cannot be skipped)."""
    _step_header(1, 6, "Anthropic API key")
    _what_is_this(
        "What is this?",
        "Meridian sends your project documents to Anthropic's Claude models to "
        "extract per-trade deliverables. This step configures the API key the "
        "tool will use. The key is read from the [bold]ANTHROPIC_API_KEY[/bold] "
        "environment variable and is [bold]never[/bold] written to disk by this "
        "wizard.",
    )

    existing = settings.anthropic_api_key
    use_existing = False
    if existing:
        masked = existing[:6] + "..." + existing[-4:] if len(existing) > 12 else "<short>"
        _console.print(f"[green]Detected[/green] ANTHROPIC_API_KEY={masked}")
        use_existing = Confirm.ask("Use this existing key?", default=True)

    if not use_existing:
        _console.print(
            "[dim]Paste your key (starts with [bold]sk-ant-[/bold]). It will "
            "stay in this process only — set it permanently via your shell's "
            "env-var mechanism (or the project [bold].env[/bold] file) when "
            "you're done.[/dim]"
        )
        key = Prompt.ask("API key", password=True).strip()
        if not key:
            _console.print("[red]No key entered — cannot continue.[/red]")
            raise _AbortWizard("API key is required.")
        os.environ["ANTHROPIC_API_KEY"] = key

    # Validate with a tiny live call — three-outcome.
    outcome, detail = _validate_anthropic_key()
    if outcome == "valid":
        _console.print(f"[green]Key validated.[/green] {detail}")
    elif outcome == "invalid":
        _console.print(f"[red]Key rejected by API:[/red] {detail}")
        raise _AbortWizard("API key did not validate.")
    else:  # unable_to_verify
        _console.print(
            f"[yellow]Could not verify key (network / SDK issue): {detail}.[/yellow]"
        )
        _console.print(
            "[dim]Continuing — first real LLM call will surface any auth "
            "errors clearly.[/dim]"
        )

    state.api_key_configured = True
    state.mark("api_key")
    save_state(state)
    return state


def _validate_anthropic_key() -> tuple[str, str]:
    """Return one of ``valid`` / ``invalid`` / ``unable_to_verify`` plus detail.

    Three-outcome (per user MEMORY: classification tasks default to
    pass/fail/borderline). "unable_to_verify" covers offline /
    transient-network so we don't punish a user for a flaky connection.

    Resolution ladder (first importable wins):
        1. ``anthropic`` SDK — preferred when present (richer error
           reporting via ``models.list``); but it is NOT a hard meridian
           dependency so a wheel install will typically miss it.
        2. ``litellm`` — hard dependency of meridian, routes to Anthropic
           via its own transport. Used as the fallback so the wizard's
           validator does not silently degrade to ``unable_to_verify``
           (and the user does not see the misleading "couldn't reach
           Anthropic" warning) on every install that lacks the optional
           ``anthropic`` SDK.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "invalid", "ANTHROPIC_API_KEY not set"
    try:
        # Lightweight: list models. Anthropic's SDK exposes ``models.list()``.
        # We import lazily so a missing dep falls through to litellm rather
        # than crashing the wizard.
        from anthropic import Anthropic  # type: ignore[import-not-found]  # noqa: PLC0415
    except ImportError:
        return _validate_anthropic_key_via_litellm()
    try:
        client = Anthropic()
        # Some SDK versions expose .models.list(); fall back to a tiny
        # messages.create with max_tokens=1 if not.
        models = getattr(client, "models", None)
        if models is not None and hasattr(models, "list"):
            models.list(limit=1)
            return "valid", "models.list() succeeded"
        # Fallback probe.
        client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1,
            messages=[{"role": "user", "content": "."}],
        )
        return "valid", "messages.create() probe succeeded"
    except Exception as exc:  # pragma: no cover — env-dependent
        msg = str(exc).lower()
        if any(t in msg for t in ("401", "unauthorized", "invalid_api_key", "authentication")):
            return "invalid", str(exc)
        return "unable_to_verify", str(exc)


def _validate_anthropic_key_via_litellm() -> tuple[str, str]:
    """Fallback validator when the optional ``anthropic`` SDK isn't installed.

    Issues a 1-token completion through litellm (a hard meridian dep) and
    classifies the result the same way the SDK path does:

    * Success      → ``valid``.
    * ``litellm.exceptions.AuthenticationError`` /
      ``PermissionDeniedError`` → ``invalid`` (Anthropic rejected the key).
    * Anything else (network error, rate-limit, server-side, import
      failure) → ``unable_to_verify``. We deliberately do NOT classify
      ``BadRequestError`` etc. as ``invalid`` — it is a poor proxy for
      "key is wrong" and misclassifying a transient as invalid is the
      worst possible UX (it blocks a user with a perfectly good key).
    """
    try:
        import litellm  # noqa: PLC0415
        from litellm.exceptions import (  # noqa: PLC0415
            AuthenticationError,
            PermissionDeniedError,
        )
    except ImportError as exc:  # pragma: no cover — litellm is a hard dep
        return "unable_to_verify", f"litellm not importable: {exc}"

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    try:
        litellm.completion(
            model="anthropic/claude-haiku-4-5-20251001",
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            api_key=api_key,
        )
    except AuthenticationError as exc:
        return "invalid", f"litellm AuthenticationError: {exc}"
    except PermissionDeniedError as exc:
        # 403 — typically means the key is valid but lacks model access.
        # Treat as ``invalid`` for wizard purposes: the user cannot use
        # this key with Meridian's chosen model and re-prompting is the
        # right remediation (vs. silently advancing).
        return "invalid", f"litellm PermissionDeniedError: {exc}"
    except Exception as exc:  # noqa: BLE001 — surface anything else as transient
        msg = str(exc).lower()
        # Belt-and-braces: some litellm versions surface 401 via APIError
        # rather than AuthenticationError. String-match the canonical
        # auth markers as a last-resort classification.
        if any(t in msg for t in ("401", "unauthorized", "invalid_api_key", "authentication")):
            return "invalid", str(exc)
        return "unable_to_verify", str(exc)
    return "valid", "litellm.completion() probe succeeded"


def _step_totp_enrol(state: OnboardingState) -> OnboardingState:
    """Step 2 — TOTP enrolment. OPTIONAL."""
    _step_header(2, 6, "Two-factor enrolment (OPTIONAL)")
    _what_is_this(
        "What is this?",
        "Meridian's HTTP API can require a 6-digit code from an authenticator "
        "app (Google Authenticator, 1Password, Authy...) before letting "
        "anyone hit protected endpoints. This is recommended if you'll be "
        "exposing the API beyond localhost. CLI-only users can safely skip.",
    )

    from meridian.auth.secrets import default_store

    store = default_store()
    if store.is_configured():
        _console.print("[green]TOTP already enrolled on this install — skipping.[/green]")
        state.totp_enrolled = True
        state.mark("totp_enrol")
        save_state(state)
        return state

    _console.print("[dim]OPTIONAL — press Enter to skip and continue.[/dim]")
    if not Confirm.ask("Enrol TOTP now?", default=False):
        _console.print(
            "[dim]Skipped. You can enrol later by re-running [bold]meridian init[/bold] "
            "(this step will be offered again).[/dim]"
        )
        state.mark("totp_enrol")  # mark as visited even if skipped
        save_state(state)
        return state

    _do_totp_enrolment(store)
    state.totp_enrolled = True
    state.mark("totp_enrol")
    save_state(state)
    return state


def _do_totp_enrolment(store: object) -> None:
    """Generate secret, print QR + recovery codes, save to store.

    Mirrors the inline pattern used by the API enrolment endpoint — we call
    the existing primitives directly rather than shelling out.
    """
    from meridian.auth.qr import render_ascii_qr
    from meridian.auth.recovery import generate_recovery_codes, hash_recovery_code
    from meridian.auth.secrets import get_recovery_salt
    from meridian.auth.totp import generate_secret, provisioning_uri

    account = Prompt.ask("Account name (shown in your authenticator app)", default="meridian-user")
    secret = generate_secret()
    uri = provisioning_uri(secret, account_name=account)
    qr = render_ascii_qr(uri)
    _console.print(qr)
    _console.print(f"[dim]Or enter the secret manually: [bold]{secret}[/bold][/dim]")

    salt = get_recovery_salt()
    recovery_plain = generate_recovery_codes()
    recovery_hashed = [hash_recovery_code(c, salt=salt) for c in recovery_plain]

    table = Table(title="One-time recovery codes — save these somewhere safe NOW")
    table.add_column("#", justify="right")
    table.add_column("Code")
    for i, code in enumerate(recovery_plain, 1):
        table.add_row(str(i), code)
    _console.print(table)
    _console.print(
        "[yellow]These will not be shown again. Each can be used once if you "
        "lose your authenticator.[/yellow]"
    )

    Prompt.ask("Press Enter once you have copied the codes", default="")

    store.save(  # type: ignore[attr-defined]
        secret=secret,
        recovery_codes_hashed=recovery_hashed,
        created_at=_utc_now_iso(),
    )
    _console.print("[green]TOTP secret saved.[/green]")


def _step_first_project(state: OnboardingState) -> OnboardingState:
    """Step 3 — Create first project."""
    from meridian.projects import create_project, project_db_path

    _step_header(3, 6, "Create your first project")
    _what_is_this(
        "What is this?",
        "Each project gets its own SQLite file under [bold]data/projects/[/bold]. "
        "All extracted deliverables, taxonomies, and LLM call records for that "
        "job stay scoped to it. Pick a short, file-system-friendly name "
        "(e.g. [bold]syd2-shell-cd[/bold]).",
    )
    _console.print("[dim]OPTIONAL — press Enter at the prompt to skip.[/dim]")

    raw = Prompt.ask("Project name (Enter to skip)", default="").strip()
    if not raw:
        _console.print("[dim]Skipped — you can run [bold]meridian project-create[/bold] later.[/dim]")
        state.mark("first_project")
        save_state(state)
        return state

    slug = _slugify(raw)
    db_path = project_db_path(raw)
    if db_path.exists():
        _console.print(f"[yellow]Project file already exists at {db_path} — reusing it.[/yellow]")
        state.first_project_slug = slug
        state.mark("first_project")
        save_state(state)
        return state

    try:
        project_id, db_path = create_project(name=raw)
    except Exception as exc:
        _console.print(f"[red]Failed to create project: {exc}[/red]")
        raise _AbortWizard(f"create_project failed: {exc}") from exc

    _console.print(f"[green]Project created.[/green] id={project_id} file={db_path}")
    state.first_project_slug = slug
    state.mark("first_project")
    save_state(state)
    return state


def _step_first_doc(state: OnboardingState) -> OnboardingState:
    """Step 4 — Import first document.

    Handles both single-file and folder paths. The original implementation
    blew up with an ``Errno 13`` permission error when the user typed a
    folder (Windows refuses ``open()`` on a directory) — see the SME's
    alpha-1 screenshot. We now ``is_dir()``-check up front and offer to
    walk the folder via :func:`meridian.ingest.dispatcher.walk_directory`.
    """
    from meridian.db.connection import connect
    from meridian.ingest import ingest_file
    from meridian.projects import project_db_path

    _step_header(4, 6, "Add your project documents")
    _what_is_this(
        "What is this?",
        "Point Meridian at your [bold]project folder[/bold] (the one with your "
        "PDFs, drawings, specs, BOD spreadsheets, emails, etc.) and we'll import "
        "everything inside it. You can also pick a single file if you'd rather. "
        "Supported types: PDF, Word (.docx), Excel (.xlsx), AutoCAD (.dwg), "
        "email (.eml/.msg). Meridian deduplicates by content hash, so importing "
        "the same folder twice is safe.",
    )

    if not state.first_project_slug:
        _console.print(
            "[yellow]No project from step 3 — skipping document import.[/yellow]"
        )
        state.mark("first_doc")
        save_state(state)
        return state

    db_path = project_db_path(state.first_project_slug)

    # Re-prompt loop: if the user picks "no, give me a single file" after a
    # folder prompt, we come back to the prompt without aborting the wizard.
    while True:
        _console.print(
            "[dim]Tip: paste the path to your [bold]project folder[/bold] "
            "(everything inside will be imported), or to a single file. "
            "Press Enter to skip and add documents later.[/dim]"
        )
        raw = Prompt.ask(
            "Project folder or file path (Enter to skip)", default=""
        ).strip()
        if not raw:
            _console.print(
                "[dim]Skipped — you can run [bold]meridian import-doc[/bold] later.[/dim]"
            )
            state.mark("first_doc")
            save_state(state)
            return state

        path = Path(raw.strip('"').strip("'")).expanduser()
        if not path.exists():
            _console.print(f"[red]Path not found: {path}[/red]")
            raise _AbortWizard(f"missing path: {path}")

        if path.is_dir():
            # Stream-A guard: if walk_directory isn't importable yet, bail
            # gracefully rather than crashing the wizard with an ImportError.
            try:
                from meridian.ingest.dispatcher import walk_directory
            except ImportError as exc:
                _console.print(
                    f"[yellow]Folder import isn't available in this build "
                    f"({exc.__class__.__name__}: {exc}) — please give a single "
                    f"file path, or upgrade Meridian.[/yellow]"
                )
                continue  # re-prompt for a file path

            if not Confirm.ask(
                f"That looks like a folder. Want to import everything in it? "
                f"({path})",
                default=True,
            ):
                _console.print("[dim]OK — give me a file path instead.[/dim]")
                continue  # re-prompt for a file path

            count = _ingest_folder(path, db_path, walk_directory)
            if count > 0:
                state.first_doc_imported = True
            state.mark("first_doc")
            save_state(state)
            return state

        # Single-file path.
        conn = connect(db_path)
        try:
            try:
                result = ingest_file(
                    conn, file_path=path, project_root=settings.project_root
                )
            except Exception as exc:
                _console.print(f"[red]Import failed: {exc}[/red]")
                raise _AbortWizard(f"ingest_file failed: {exc}") from exc
            if result.deduped:
                _console.print(
                    f"[yellow]Already imported (same content hash): "
                    f"{result.filename} — treated as success.[/yellow]"
                )
            else:
                _console.print(
                    f"[green]Imported.[/green] {result.filename} "
                    f"({result.text_length} chars, {result.chunk_count} chunks)"
                )
        finally:
            conn.close()

        state.first_doc_imported = True
        state.mark("first_doc")
        save_state(state)
        return state


def _ingest_folder(folder: Path, db_path: Path, walk_directory) -> int:  # type: ignore[no-untyped-def]
    """Walk ``folder`` and ingest every file the dispatcher accepts.

    Returns the number of files successfully ingested (deduped counts as
    successful). Renders a "Importing X of Y: <filename>" progress line per
    file. Failed files are reported but don't abort the run — better to land
    most of the user's corpus than nothing.
    """
    from meridian.db.connection import connect
    from meridian.ingest import ingest_file

    walk_result = walk_directory(folder)
    # walk_result.files_by_kind is dict[kind, list[str_paths]]; flatten.
    file_paths: list[Path] = []
    for paths in walk_result.files_by_kind.values():
        file_paths.extend(Path(p) for p in paths)
    file_paths.sort()

    if not file_paths:
        skipped = len(getattr(walk_result, "skipped", []))
        _console.print(
            f"[yellow]Found 0 ingestable files in {folder} "
            f"({skipped} skipped — usually unsupported extensions or hidden "
            f"system files).[/yellow]"
        )
        return 0

    total = len(file_paths)
    _console.print(f"[cyan]Importing {total} files from {folder}...[/cyan]")

    conn = connect(db_path)
    success = 0
    try:
        for i, p in enumerate(file_paths, start=1):
            _console.print(f"  [{i}/{total}] {p.name}")
            try:
                result = ingest_file(
                    conn, file_path=p, project_root=settings.project_root
                )
                if result.deduped:
                    _console.print(
                        "      [yellow]deduped[/yellow] (same content hash already imported)"
                    )
                else:
                    _console.print(
                        f"      [green]ok[/green] "
                        f"({result.text_length} chars, {result.chunk_count} chunks)"
                    )
                success += 1
            except Exception as exc:  # noqa: BLE001 — keep ingesting siblings
                _console.print(f"      [red]failed[/red]: {exc}")
    finally:
        conn.close()

    _console.print(
        f"[green]Folder import complete.[/green] {success}/{total} files imported."
    )
    return success


def _step_bootstrap(state: OnboardingState) -> OnboardingState:
    """Step 5 — Bootstrap LLM sweep. OPTIONAL."""
    _step_header(5, 6, "Bootstrap LLM sweep (OPTIONAL)")
    _what_is_this(
        "What is this?",
        "A first-pass recon: Claude reads a sample of your imported documents "
        "and proposes document classes, taxonomy extensions, and an authority "
        "chain — so the real extraction has sensible defaults out of the gate.",
    )

    if not state.first_project_slug or not state.first_doc_imported:
        _console.print(
            "[yellow]Skipping — bootstrap needs at least one imported document.[/yellow]"
        )
        state.mark("bootstrap")
        save_state(state)
        return state

    _console.print("[dim]OPTIONAL — press Enter to skip.[/dim]")
    if not Confirm.ask("Run the bootstrap sweep now?", default=True):
        state.mark("bootstrap")
        save_state(state)
        return state

    from meridian.bootstrap import render_proposal_summary, run_bootstrap_sweep
    from meridian.db.connection import connect
    from meridian.projects import project_db_path

    db_path = project_db_path(state.first_project_slug)
    conn = connect(db_path)
    try:
        try:
            result = run_bootstrap_sweep(conn, sample_size=15)
        except ValueError as exc:
            _console.print(f"[yellow]Bootstrap skipped: {exc}[/yellow]")
            state.mark("bootstrap")
            save_state(state)
            return state
        except Exception as exc:
            _console.print(f"[red]Bootstrap failed: {exc}[/red]")
            raise _AbortWizard(f"bootstrap sweep failed: {exc}") from exc

        _console.print(
            f"[green]Bootstrap sweep complete.[/green] llm_call_id={result.llm_call_id}"
        )
        _console.print(render_proposal_summary(result.proposal))
    finally:
        conn.close()

    state.first_bootstrap_run = True
    state.mark("bootstrap")
    save_state(state)
    return state


def _step_next_steps(state: OnboardingState) -> OnboardingState:
    """Step 6 — Print the tailored "first-look-tomorrow" agenda."""
    _step_header(6, 6, "Where to go next")
    slug = state.first_project_slug or "<your-project>"
    body = (
        "[bold]Suggested next session — your first-look agenda:[/bold]\n\n"
        f"  1. [cyan]meridian review-status {slug}[/cyan]\n"
        "       Glance at extraction progress + outstanding review queues.\n\n"
        f"  2. [cyan]meridian review walk-quarantine {slug}[/cyan]\n"
        "       Walk anything the extractor flagged as borderline.\n\n"
        f"  3. [cyan]meridian review walk-taxonomy {slug}[/cyan]\n"
        "       Approve / reject the taxonomy proposals from the bootstrap sweep.\n\n"
        f"  4. [cyan]meridian export-xlsx {slug}[/cyan]\n"
        "       Render the per-trade Excel register once you're happy.\n\n"
        "[dim]Re-run [bold]meridian init[/bold] any time — it's idempotent. "
        "Use [bold]--restart[/bold] to redo from step 1, or "
        "[bold]--status[/bold] to see this state without prompting.[/dim]"
    )
    _console.print(Panel(body, title="[green]All set[/green]", border_style="green"))
    state.mark("next_steps")
    save_state(state)
    return state


# --------------------------------------------------------------------------
# Dispatcher
# --------------------------------------------------------------------------


_STEP_FUNCS = {
    "api_key": _step_api_key,
    "totp_enrol": _step_totp_enrol,
    "first_project": _step_first_project,
    "first_doc": _step_first_doc,
    "bootstrap": _step_bootstrap,
    "next_steps": _step_next_steps,
}


class _AbortWizardError(RuntimeError):
    """Raised by a step to abort the run cleanly with a message."""


# Backwards-compat alias used internally; keeps the old short name readable
# at call sites without tripping the N818 lint.
_AbortWizard = _AbortWizardError


def run_step(state: OnboardingState, step_name: str) -> OnboardingState:
    """Re-run a single step (used by the resume path)."""
    if step_name not in _STEP_FUNCS:
        raise ValueError(
            f"Unknown onboarding step '{step_name}'. Known: {sorted(_STEP_FUNCS)}"
        )
    if not _is_tty():
        raise RuntimeError(
            "Onboarding steps require an interactive terminal."
        )
    return _STEP_FUNCS[step_name](state)


def run_wizard(*, force_restart: bool = False) -> OnboardingState:
    """Drive the full wizard end-to-end.

    Parameters
    ----------
    force_restart:
        If True, ignore any prior state and start from step 1 with a fresh
        ``OnboardingState``. The old state file is overwritten as steps
        complete.
    """
    if not _is_tty():
        _console.print(
            "[red]`meridian init` requires an interactive terminal.[/red]\n"
            "[dim]For scripted setup, use the underlying commands directly: "
            "[bold]meridian project-create[/bold], [bold]meridian import-doc[/bold], "
            "[bold]meridian bootstrap[/bold].[/dim]"
        )
        raise RuntimeError("init requires an interactive terminal")

    existing = None if force_restart else load_state()
    state = existing or OnboardingState()

    _console.print(
        Panel.fit(
            "[bold]Welcome to Meridian - Trace.[/bold]\n\n"
            "Meridian extracts per-trade design deliverables from mixed project "
            "documents (drawings, specs, BODs, emails) into a single review-ready "
            "Excel register. This wizard will walk you through first-time setup "
            "in six short steps. You can press Ctrl-C any time — your progress "
            "is saved after every step.",
            title="[cyan]meridian init[/cyan]",
            border_style="cyan",
        )
    )

    if existing and existing.is_complete() and not force_restart:
        _console.print(
            "[green]Onboarding complete.[/green] Re-run with "
            "[bold]--restart[/bold] to redo from step 1."
        )
        return state

    if existing and existing.completed_steps and not force_restart:
        last = existing.completed_steps[-1]
        next_idx = STEP_NAMES.index(last) + 1 if last in STEP_NAMES else 0
        if next_idx < len(STEP_NAMES):
            next_step = STEP_NAMES[next_idx]
            if Confirm.ask(
                f"Resume from step '{next_step}'? (No = restart from step 1)",
                default=True,
            ):
                start_idx = next_idx
            else:
                start_idx = 0
                state = OnboardingState()
        else:
            start_idx = 0
    else:
        start_idx = 0

    try:
        for step_name in STEP_NAMES[start_idx:]:
            state = _STEP_FUNCS[step_name](state)
    except _AbortWizard as exc:
        _console.print(f"[red]Wizard aborted: {exc}[/red]")
        _console.print("[dim]Re-run [bold]meridian init[/bold] to resume.[/dim]")
    except KeyboardInterrupt:
        _console.print("\n[yellow]Interrupted. Progress saved — re-run to resume.[/yellow]")

    return state
