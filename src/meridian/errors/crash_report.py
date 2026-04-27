"""Local crash-report assembly (CONTEXT.md §19).

V1 scope: write a JSON crash report to disk for the user to review and
optionally email to support. The network send-endpoint URL is on the
deferred-decision list, so this module deliberately does NOT make any
outbound HTTP call. The report file is the artefact the user owns.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from meridian.errors.explain import build_error_context


def write_crash_report(
    *,
    log_dir: Path,
    exception: BaseException,
    llm_explanation: str,
    recent_log_events: list[dict[str, Any]],
) -> Path:
    """Write a JSON crash report to ``<log_dir>/crash_<timestamp>.json``.

    The report bundles:

    - the redacted exception context (via ``build_error_context``),
    - the LLM-generated plain-English explanation the user has approved,
    - the last N log events that led up to the crash.

    Returns the path written. Caller (the CLI) is expected to print this
    path so the user can attach the file to a support email if they choose.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = log_dir / f"crash_{ts}.json"

    context = build_error_context(
        exception=exception,
        recent_log_events=recent_log_events,
        redact_secrets=True,
    )
    payload = {
        "report_version": 1,
        "captured_at": context.get("captured_at"),
        "exception_type": context.get("exception_type"),
        "exception_message": context.get("exception_message"),
        "stack_trace": context.get("stack_trace"),
        "python_version": context.get("python_version"),
        "platform": context.get("platform"),
        "recent_log_events": context.get("recent_log_events", []),
        "llm_explanation": llm_explanation,
        "send_endpoint": None,  # deferred — see CONTEXT.md §19 + §23
    }
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target


__all__ = ["write_crash_report"]
