"""Build redacted error context + call the LLM for a plain-English explanation.

CONTEXT.md §19: "stack trace + redacted context → user's LLM produces
plain-English summary + suggested workaround". The redaction step strips
common API-key shapes (Anthropic ``sk-ant-*``, OpenAI ``sk-*``) and the
literal env-var names ``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY`` from
values before any context leaves the process.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import traceback
from datetime import UTC, datetime
from typing import Any

# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------

# sk-ant-... — Anthropic API keys (long alphanumerics/dashes after the prefix).
_ANT_KEY_RE = re.compile(r"sk-ant-[A-Za-z0-9_\-]{6,}")
# Generic OpenAI-style keys (sk-... not preceded by ant- already).
_OAI_KEY_RE = re.compile(r"\bsk-(?!ant-)[A-Za-z0-9_\-]{16,}")
# Bearer tokens.
_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}")
# Env-var-style references in values: ANTHROPIC_API_KEY=value, OPENAI_API_KEY=value.
_ENV_VAR_VALUE_RE = re.compile(
    r"((?:ANTHROPIC|OPENAI)_API_KEY)\s*=\s*[\S]+",
)

_REDACTION = "[REDACTED]"


def _redact_text(value: str) -> str:
    """Strip API keys, bearer tokens, and ENV=value pairs from a string."""
    out = _ANT_KEY_RE.sub(_REDACTION, value)
    out = _OAI_KEY_RE.sub(_REDACTION, out)
    out = _BEARER_RE.sub(f"Bearer {_REDACTION}", out)
    out = _ENV_VAR_VALUE_RE.sub(rf"\1={_REDACTION}", out)
    return out


def _redact(obj: Any) -> Any:
    """Recursively redact strings in dict / list / tuple / scalar."""
    if isinstance(obj, str):
        return _redact_text(obj)
    if isinstance(obj, dict):
        return {k: _redact(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_redact(v) for v in obj)
    return obj


# --------------------------------------------------------------------------
# Context assembly
# --------------------------------------------------------------------------


def build_error_context(
    *,
    exception: BaseException,
    recent_log_events: list[dict[str, Any]] | None = None,
    redact_secrets: bool = True,
) -> dict[str, Any]:
    """Assemble context for the error-explain LLM call.

    Redacts ``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY`` / ``sk-ant-*``
    tokens from values when ``redact_secrets=True`` (default).
    """
    # Stack trace from the live exception. Use __traceback__ when present,
    # falling back to format_exception_only for synthesised exceptions.
    tb = getattr(exception, "__traceback__", None)
    if tb is not None:
        stack = "".join(
            traceback.format_exception(type(exception), exception, tb)
        )
    else:
        stack = "".join(traceback.format_exception_only(type(exception), exception))

    payload: dict[str, Any] = {
        "captured_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "exception_type": type(exception).__name__,
        "exception_message": str(exception),
        "stack_trace": stack,
        "python_version": sys.version,
        "platform": sys.platform,
        "recent_log_events": list(recent_log_events or []),
    }

    if redact_secrets:
        payload = _redact(payload)
    return payload


# --------------------------------------------------------------------------
# LLM call
# --------------------------------------------------------------------------

_ERROR_EXPLAIN_PROMPT_FILENAME = "PROMPT_V1_error_explain.md"

_SYSTEM_PROMPT = (
    "You are a senior software engineer triaging a production error from a "
    "desktop tool used by non-technical project managers. Read the stack "
    "trace and the recent log events, identify the most likely root cause, "
    "and suggest 1-3 concrete next steps the user can take. Reply with "
    "valid JSON only — no markdown fences, no preamble."
)


def explain_error(
    conn: sqlite3.Connection,
    *,
    context: dict[str, Any],
    provider: str | None = None,
    model: str | None = None,
) -> str:
    """Call the LLM with ``purpose='error_explain'`` and return the explanation.

    Persists the call to the ``llm_call`` table via the standard ``call_llm``
    path. The returned string is the plain-English explanation; if the LLM
    produced structured JSON ``{"explanation": "...", "suggested_steps":
    [...]}`` the explanation field is preferred and the suggested steps are
    appended as a bullet list.
    """
    # Local imports keep the module importable even if optional deps fail.
    from meridian.llm.client import call_llm
    from meridian.prompts.loader import (
        extract_prompt_body,
        load_prompt,
        render_prompt,
    )

    body = extract_prompt_body(load_prompt(_ERROR_EXPLAIN_PROMPT_FILENAME))
    user_prompt = render_prompt(
        body,
        {
            "exception_type": context.get("exception_type", ""),
            "exception_message": context.get("exception_message", ""),
            "stack_trace": context.get("stack_trace", ""),
            "recent_log_events_json": json.dumps(
                context.get("recent_log_events", []), indent=2
            )[:20000],
            "python_version": context.get("python_version", ""),
            "platform": context.get("platform", ""),
        },
    )

    call = call_llm(
        conn,
        purpose="error_explain",
        provider=provider,
        model=model,
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        prompt_version_ref="error_explain_v1.0",
        max_tokens=2048,
    )

    parsed = call.parsed_json if isinstance(call.parsed_json, dict) else None
    if parsed is None:
        return call.response_text or "(no explanation produced)"
    explanation = str(parsed.get("explanation", "")).strip()
    steps = parsed.get("suggested_steps") or []
    if isinstance(steps, list) and steps:
        bullet_lines = "\n".join(f"  - {str(s).strip()}" for s in steps)
        return f"{explanation}\n\nSuggested next steps:\n{bullet_lines}"
    return explanation or call.response_text or "(no explanation produced)"


__all__ = ["build_error_context", "explain_error"]
