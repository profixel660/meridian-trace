"""Auto-update CLIENT (CONTEXT.md §18).

Scope of v1: this module **checks** for updates only. The actual
download + install of an update is deferred until the installer technology
decision is made (CONTEXT.md §3.7 — PyInstaller + Inno Setup recommended).

Mechanism: fetch a JSON manifest (CDN-hosted, GitHub Releases recommended
per §3.5), compare semver to the running version, surface "version X.Y
available" with a "skip this version" option.

Pure stdlib — uses ``urllib.request`` to avoid pulling in ``httpx`` for a
~50-line client. Defensive: every network or parse failure returns ``None``
silently (logged at INFO) rather than raising.
"""

from __future__ import annotations

from meridian.updates.client import (
    UpdateAvailable,
    UpdateManifest,
    check_for_updates,
    list_skipped_versions,
    record_skipped_version,
)

__all__ = [
    "UpdateAvailable",
    "UpdateManifest",
    "check_for_updates",
    "list_skipped_versions",
    "record_skipped_version",
]
