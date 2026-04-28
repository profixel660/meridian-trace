"""GUI setup-wizard HTTP layer.

This package exposes the existing CLI onboarding primitives
(``meridian.onboarding.wizard``) over HTTP so the Tauri-bundled SPA's
first-run wizard can drive setup. The state file at ``~/.meridian/onboarding_state.json``
(``MERIDIAN_WIZARD_STATE_DIR`` env var overrides) is shared with the CLI
wizard — a user who started in CLI and finished in GUI (or vice-versa)
sees consistent state.

Public surface:

* :data:`wizard_router` — FastAPI ``APIRouter`` mounted by
  ``meridian.api.main``. All endpoints are prefixed ``/setup``.
"""

from __future__ import annotations

from meridian.wizard.api import wizard_router

__all__ = ["wizard_router"]
