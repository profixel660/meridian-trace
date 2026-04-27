"""Crash-report network SEND path (CONTEXT.md §19).

The local crash-report dossier is built by
:mod:`meridian.errors.crash_report` (already exists). This module adds the
**send** path on top: defensive secret redaction, opt-in gating, and a
small POST to the crash endpoint.

**Never auto-sends.** The user must invoke ``meridian crash send`` (and
the opt-in flag must be set). ``meridian crash preview`` shows the exact
(redacted) payload before it leaves the machine.
"""

from __future__ import annotations

from meridian.crash.sender import (
    SendResult,
    crash_opt_in_path,
    get_crash_opt_in,
    list_local_dossiers,
    prepare_crash_payload,
    send_crash_report,
    set_crash_opt_in,
)

__all__ = [
    "SendResult",
    "crash_opt_in_path",
    "get_crash_opt_in",
    "list_local_dossiers",
    "prepare_crash_payload",
    "send_crash_report",
    "set_crash_opt_in",
]
