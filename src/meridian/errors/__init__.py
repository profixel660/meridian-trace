"""Error explanation + crash-reporting helpers (CONTEXT.md §19).

LLM-assisted error explanation: stack trace + redacted log context →
plain-English summary + suggested workaround. Crash report assembly is
local-only in v1; the network send endpoint is a deferred decision.
"""

from __future__ import annotations

from meridian.errors.crash_report import write_crash_report
from meridian.errors.explain import build_error_context, explain_error

__all__ = [
    "build_error_context",
    "explain_error",
    "write_crash_report",
]
