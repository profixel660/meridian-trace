"""Persistent setup-wizard state — CLI/GUI compatible.

State file
==========

Single source of truth: ``<settings.projects_dir>/_meridian/onboarding_state.json``
— the same path used by ``meridian.onboarding.wizard``. This module never
defines its own location; ``state_path()`` is re-exported from the CLI
module so a relocation there propagates here.

Schema (CLI-compatible superset)
--------------------------------

The CLI wizard's ``OnboardingState`` dataclass owns these fields:

* ``api_key_configured: bool``
* ``first_project_slug: str | None``
* ``first_doc_imported: bool``
* ``first_bootstrap_run: bool``
* ``totp_enrolled: bool``
* ``license_installed: bool``
* ``completed_steps: list[str]``
* ``last_step_at: str | None``

The GUI wizard reads/writes that dataclass via the CLI's ``load_state`` /
``save_state`` primitives. It additionally stores **GUI-only sidecar keys**
in the same JSON file:

* ``gui_first_project_name: str | None`` — human-readable name (CLI only
  records the slug).
* ``gui_first_project_dir: str | None`` — projects_dir the GUI wrote into.
* ``gui_documents_imported: int`` — count of files genuinely ingested
  (deduped files don't increment).
* ``gui_documents_skipped: bool`` — user pressed "Skip" rather than
  importing any documents.
* ``gui_wizard_completed_at: str | None`` — ISO-8601 UTC timestamp the
  user pressed Finish.

The CLI ``load_state`` filters JSON keys it doesn't recognise (see the
``known = {f for f in OnboardingState.__dataclass_fields__}`` filter in
``meridian.onboarding.wizard.load_state``), so these GUI-only keys round-
trip safely without ever crashing the CLI.

Concurrency
-----------

There is exactly one user, one process, and a single-digit number of
state writes per setup run. We do NOT take a lock on the state file — a
torn write window (write -> Ctrl-C -> half-written file) is recovered by
the CLI's defensive json.JSONDecodeError handling, which falls back to
``OnboardingState()``. If the GUI runs alongside the CLI, the last writer
wins; this is acceptable because both are user-driven and the user is
always in front of exactly one of them at a time.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from meridian.onboarding.wizard import (
    OnboardingState,
    state_path,
)
from meridian.onboarding.wizard import (
    load_state as _cli_load_state,
)

# Re-export so callers don't need to reach into the CLI module directly.
__all__ = [
    "OnboardingState",
    "WizardState",
    "anthropic_key_outcome",
    "load_wizard_state",
    "mark_documents_imported",
    "mark_documents_skipped",
    "mark_first_project",
    "mark_setup_complete",
    "save_wizard_state",
    "set_api_key_configured",
    "state_path",
    "validate_anthropic_key_str",
    "with_api_key",
]


# --------------------------------------------------------------------------
# GUI sidecar keys — kept as a constant so typos surface in code review.
# --------------------------------------------------------------------------

_GUI_KEY_PROJECT_NAME = "gui_first_project_name"
_GUI_KEY_PROJECT_DIR = "gui_first_project_dir"
_GUI_KEY_DOCS_IMPORTED = "gui_documents_imported"
_GUI_KEY_DOCS_SKIPPED = "gui_documents_skipped"
_GUI_KEY_COMPLETED_AT = "gui_wizard_completed_at"

# Step names tracked in completed_steps that gate "complete".
_REQUIRED_GUI_STEPS = ("api_key", "first_project")
# documents_imported > 0 OR documents_skipped == True covers the third gate.


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# WizardState — CLI dataclass + GUI sidecar fields, returned to API handlers.
# --------------------------------------------------------------------------


class WizardState:
    """Aggregated CLI + GUI state. Returned by :func:`load_wizard_state`.

    Constructed from raw JSON so handlers can inspect every persisted key
    without re-reading the file. Writes go back through the CLI's
    ``save_state`` (for ``OnboardingState`` fields) and a thin JSON re-
    write that preserves GUI-only sidecar keys (which the CLI's loader
    filters out and would therefore lose on a CLI-side save).
    """

    def __init__(
        self,
        cli: OnboardingState,
        *,
        gui_first_project_name: str | None = None,
        gui_first_project_dir: str | None = None,
        gui_documents_imported: int = 0,
        gui_documents_skipped: bool = False,
        gui_wizard_completed_at: str | None = None,
    ) -> None:
        self.cli = cli
        self.gui_first_project_name = gui_first_project_name
        self.gui_first_project_dir = gui_first_project_dir
        self.gui_documents_imported = gui_documents_imported
        self.gui_documents_skipped = gui_documents_skipped
        self.gui_wizard_completed_at = gui_wizard_completed_at

    # --- Derived flags --------------------------------------------------

    @property
    def api_key_set(self) -> bool:
        return self.cli.api_key_configured

    @property
    def first_project_slug(self) -> str | None:
        return self.cli.first_project_slug

    @property
    def documents_imported(self) -> int:
        return self.gui_documents_imported

    @property
    def documents_skipped(self) -> bool:
        return self.gui_documents_skipped

    @property
    def is_complete(self) -> bool:
        """True iff the GUI considers setup finished. NOT the same as the
        CLI's ``OnboardingState.is_complete`` — the GUI wizard skips the
        TOTP / bootstrap / next-steps CLI sub-steps."""
        if self.gui_wizard_completed_at is None:
            return False
        return self._has_required_gates()

    @property
    def next_step(self) -> str:
        """One of api_key / first_documents / first_project / ready / complete.

        Order swapped in alpha-2: the user picks their project folder BEFORE
        confirming a project name (the name auto-derives from the folder).
        ``/setup/import-folder`` creates the project on first call, so by the
        time the user reaches first_project it is a confirm/rename step rather
        than a create step.
        """
        if self.is_complete:
            return "complete"
        if not self.cli.api_key_configured:
            return "api_key"
        if not (self.gui_documents_imported > 0 or self.gui_documents_skipped):
            return "first_documents"
        if not self.cli.first_project_slug:
            return "first_project"
        return "ready"

    def _has_required_gates(self) -> bool:
        return (
            self.cli.api_key_configured
            and bool(self.cli.first_project_slug)
            and (self.gui_documents_imported > 0 or self.gui_documents_skipped)
        )


def _read_raw_json() -> dict[str, Any]:
    """Read the on-disk JSON unfiltered so we can pluck GUI sidecar keys.

    Returns ``{}`` for missing / corrupt files so callers don't have to
    branch.
    """
    p = state_path()
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return raw


def load_wizard_state() -> WizardState:
    """Load CLI + GUI state. Always returns a state object, never None."""
    cli = _cli_load_state() or OnboardingState()
    raw = _read_raw_json()
    return WizardState(
        cli=cli,
        gui_first_project_name=_str_or_none(raw.get(_GUI_KEY_PROJECT_NAME)),
        gui_first_project_dir=_str_or_none(raw.get(_GUI_KEY_PROJECT_DIR)),
        gui_documents_imported=_int_or_zero(raw.get(_GUI_KEY_DOCS_IMPORTED)),
        gui_documents_skipped=bool(raw.get(_GUI_KEY_DOCS_SKIPPED, False)),
        gui_wizard_completed_at=_str_or_none(raw.get(_GUI_KEY_COMPLETED_AT)),
    )


def save_wizard_state(state: WizardState) -> None:
    """Persist CLI + GUI fields atomically (well, as atomic as a single
    ``write_text`` is — see module-level concurrency note)."""
    payload: dict[str, Any] = asdict(state.cli)
    payload[_GUI_KEY_PROJECT_NAME] = state.gui_first_project_name
    payload[_GUI_KEY_PROJECT_DIR] = state.gui_first_project_dir
    payload[_GUI_KEY_DOCS_IMPORTED] = state.gui_documents_imported
    payload[_GUI_KEY_DOCS_SKIPPED] = state.gui_documents_skipped
    payload[_GUI_KEY_COMPLETED_AT] = state.gui_wizard_completed_at

    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _str_or_none(v: Any) -> str | None:
    return v if isinstance(v, str) and v else None


def _int_or_zero(v: Any) -> int:
    return v if isinstance(v, int) and v >= 0 else 0


# --------------------------------------------------------------------------
# Mutators — small, named helpers so handlers don't sprinkle field writes.
# --------------------------------------------------------------------------


def set_api_key_configured(state: WizardState) -> None:
    state.cli.api_key_configured = True
    state.cli.mark("api_key")
    save_wizard_state(state)


def mark_first_project(
    state: WizardState,
    *,
    slug: str,
    name: str,
    projects_dir: str,
) -> None:
    state.cli.first_project_slug = slug
    state.cli.mark("first_project")
    state.gui_first_project_name = name
    state.gui_first_project_dir = projects_dir
    save_wizard_state(state)


def mark_documents_imported(state: WizardState, *, count: int) -> None:
    """Increment the persisted import counter; mark the CLI's first_doc step."""
    state.gui_documents_imported = max(0, state.gui_documents_imported) + count
    if count > 0:
        state.cli.first_doc_imported = True
        state.cli.mark("first_doc")
    save_wizard_state(state)


def mark_documents_skipped(state: WizardState) -> None:
    state.gui_documents_skipped = True
    state.cli.mark("first_doc")
    save_wizard_state(state)


def mark_setup_complete(state: WizardState) -> None:
    """Idempotent: record completion timestamp; safe to call repeatedly."""
    if state.gui_wizard_completed_at is None:
        state.gui_wizard_completed_at = _utc_now_iso()
    save_wizard_state(state)


# --------------------------------------------------------------------------
# Anthropic-key validation — non-interactive variant of the CLI helper.
# --------------------------------------------------------------------------
#
# COPY-DON'T-CALL: ``meridian.onboarding.wizard._validate_anthropic_key`` is
# pure (it makes a network call but takes no args), and it reads the key
# from ``os.environ["ANTHROPIC_API_KEY"]``. Calling it directly from an
# HTTP handler is fine, but the handler receives the key in the request
# body and must avoid mutating process-wide env state for longer than the
# call lasts. The :func:`with_api_key` context manager below scopes the
# env-var override.
#
# We could alternatively re-implement the validation ladder here, but the
# CLI's three-outcome detection of "401 / unauthorized / invalid_api_key /
# authentication" string-matching is the load-bearing detail and we'd
# rather not duplicate it. Single source of truth: the CLI helper.


@contextmanager
def with_api_key(key: str):
    """Temporarily set ``ANTHROPIC_API_KEY`` for the duration of a call.

    Restores the prior value (or absence) on exit, even if the underlying
    call raises. The setup wizard never persists the key into ``os.environ``
    long-term — keyring storage is the durable home (see
    :func:`anthropic_key_outcome`'s caller).
    """
    sentinel = object()
    prior: object = os.environ.get("ANTHROPIC_API_KEY", sentinel)
    os.environ["ANTHROPIC_API_KEY"] = key
    try:
        yield
    finally:
        if prior is sentinel:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = prior  # type: ignore[assignment]


def validate_anthropic_key_str(key: str) -> tuple[str, str]:
    """Validate ``key`` via the CLI helper, scoped to a single call.

    Returns ``(outcome, detail)`` where outcome is one of
    ``valid`` / ``invalid`` / ``unable_to_verify``. Mirrors the CLI's
    contract so the GUI surfaces the same three-outcome semantics.
    """
    # Local import: CLI module imports rich + console at top level, but
    # we want this module importable in a no-rich-stub environment too.
    from meridian.onboarding.wizard import _validate_anthropic_key

    with with_api_key(key):
        return _validate_anthropic_key()


# Convenience alias matching the spec's wording.
anthropic_key_outcome = validate_anthropic_key_str
