"""LiteLLM wrapper. Every call is persisted to the `llm_call` table for reproducibility (CONTEXT.md §14)."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import litellm
from litellm import completion as litellm_completion
from litellm import completion_cost

from meridian.config import LOCAL_PROVIDERS, settings
from meridian.db.connection import transaction
from meridian.logging import get_logger

_log = get_logger("meridian.llm")

LlmPurpose = Literal[
    "quality_scan",
    "triage",
    "extract_text_spec",
    "extract_demarcation",
    "extract_bod",
    "conflict_pass",
    "error_explain",
]


@dataclass
class LlmCall:
    id: str
    purpose: LlmPurpose
    response_text: str
    parsed_json: Any | None
    cost_cents: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    error: str | None = None


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    return str(uuid.uuid4())


def _input_hash(system_prompt: str, user_prompt: str) -> str:
    h = hashlib.sha256()
    h.update(system_prompt.encode("utf-8"))
    h.update(b"\n---\n")
    h.update(user_prompt.encode("utf-8"))
    return h.hexdigest()


def _provider_model(provider: str, model: str) -> str:
    """LiteLLM expects '<provider>/<model>' — except for openai which it accepts bare."""
    if model.startswith(f"{provider}/"):
        return model
    return f"{provider}/{model}"


def _try_parse_json(text: str) -> Any | None:
    """Best-effort JSON extraction. Models often wrap output in ```json ... ``` fences."""
    stripped = text.strip()
    candidates: list[str] = [stripped]

    # Strip a single fenced block if present.
    if "```" in stripped:
        parts = stripped.split("```")
        for i, part in enumerate(parts):
            if i % 2 == 1:  # inside a fence
                # Drop optional language tag on first line.
                inner = part.split("\n", 1)
                inner_text = inner[1] if len(inner) > 1 and inner[0].strip().isalpha() else part
                candidates.append(inner_text.strip())

    # As a last resort, find the first '{' or '[' and try to parse from there.
    for start in (stripped.find("{"), stripped.find("[")):
        if start >= 0:
            candidates.append(stripped[start:])

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def call_llm(
    conn: sqlite3.Connection,
    *,
    purpose: LlmPurpose,
    provider: str | None = None,
    model: str | None = None,
    system_prompt: str,
    user_prompt: str,
    prompt_version_ref: str | None = None,
    job_id: str | None = None,
    temperature: float = 0.0,
    top_p: float | None = None,
    max_tokens: int = 16000,
    parse_json: bool = True,
) -> LlmCall:
    """Call the LLM via LiteLLM and persist a full reproducibility record.

    `provider` and `model` are optional. When omitted, they are resolved via
    `settings.resolve_route(purpose)` against the per-purpose routing chain
    (env var > project setting > process settings > defaults). Project-level
    routing is read here from `app_setting` (no extra plumbing required at
    every call site).
    """
    # 1. Resolve provider/model via the routing chain when not explicitly given.
    #    Per-call args (when both are passed) sit at the top of the hierarchy.
    if not provider or not model:
        try:
            from meridian.projects import get_project_routing
            project_routing = get_project_routing(conn)
        except Exception:  # noqa: BLE001 — bootstrap robustness
            project_routing = None
        resolved_provider, resolved_model = settings.resolve_route(
            purpose, project_routing=project_routing
        )
        provider = provider or resolved_provider
        model = model or resolved_model

    # 2. Air-gap preflight: if air-gapped (settings or project-level) and the
    #    resolved route is not local, fail fast BEFORE any network call.
    air_gapped = bool(settings.air_gapped)
    if not air_gapped:
        try:
            from meridian.projects import get_air_gapped
            air_gapped = get_air_gapped(conn)
        except Exception:  # noqa: BLE001 — bootstrap robustness
            air_gapped = False
    if air_gapped and provider not in LOCAL_PROVIDERS:
        raise RuntimeError(
            f"air-gapped mode: cloud route resolved for purpose '{purpose}' "
            f"(provider={provider!r}, model={model!r}). "
            f"Allowed local providers: {sorted(LOCAL_PROVIDERS)}."
        )

    # 3. Conditional auth precheck — only require the cloud key if the
    #    resolved route is for that provider.
    if provider == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set. Export your key before running an extraction."
        )
    if provider == "openai" and not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY not set. Export your key before running an extraction."
        )

    call_id = _new_id()
    started_at = _now()
    full_model = _provider_model(provider, model)
    input_hash = _input_hash(system_prompt, user_prompt)

    _log.info(
        "llm.call.start",
        call_id=call_id,
        purpose=purpose,
        provider=provider,
        model=model,
        prompt_version_ref=prompt_version_ref,
        max_tokens=max_tokens,
        job_id=job_id,
        input_hash=input_hash,
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    response_text = ""
    parsed: Any | None = None
    cost_cents: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cache_read: int | None = None
    cache_write: int | None = None
    error: str | None = None

    try:
        response = litellm_completion(
            model=full_model,
            messages=messages,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
        )
        response_text = (
            response.choices[0].message.content if response.choices else ""
        ) or ""
        usage = getattr(response, "usage", None)
        if usage is not None:
            prompt_tokens = getattr(usage, "prompt_tokens", None)
            completion_tokens = getattr(usage, "completion_tokens", None)
            # Cache hits live on usage in newer LiteLLM versions.
            cache_read = getattr(usage, "cache_read_input_tokens", None)
            cache_write = getattr(usage, "cache_creation_input_tokens", None)

        try:
            cost_dollars = completion_cost(completion_response=response)
            cost_cents = int(round(cost_dollars * 100)) if cost_dollars else None
        except Exception:  # noqa: BLE001 — cost calc is best-effort
            cost_cents = None

        if parse_json:
            parsed = _try_parse_json(response_text)
    except Exception as exc:  # noqa: BLE001 — surface error to caller via record
        error = f"{type(exc).__name__}: {exc}"

    finished_at = _now()
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO llm_call (
                id, job_id, purpose, provider, model, provider_api_version,
                system_prompt, user_prompt, prompt_version_ref,
                temperature, top_p, max_tokens, input_hash,
                response_text, parsed_response,
                prompt_tokens, completion_tokens,
                cache_read_tokens, cache_write_tokens, cost_cents,
                started_at, finished_at, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                call_id,
                job_id,
                purpose,
                provider,
                model,
                getattr(litellm, "ANTHROPIC_API_VERSION", None) if provider == "anthropic" else None,
                system_prompt,
                user_prompt,
                prompt_version_ref,
                temperature,
                top_p,
                max_tokens,
                input_hash,
                response_text,
                json.dumps(parsed) if parsed is not None else None,
                prompt_tokens,
                completion_tokens,
                cache_read,
                cache_write,
                cost_cents,
                started_at,
                finished_at,
                error,
            ),
        )

    if error:
        _log.error(
            "llm.call.finish",
            call_id=call_id,
            purpose=purpose,
            provider=provider,
            model=model,
            job_id=job_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_cents=cost_cents,
            error=error,
        )
        raise RuntimeError(f"LLM call failed (id={call_id}): {error}")

    _log.info(
        "llm.call.finish",
        call_id=call_id,
        purpose=purpose,
        provider=provider,
        model=model,
        job_id=job_id,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_cents=cost_cents,
        error=None,
    )
    return LlmCall(
        id=call_id,
        purpose=purpose,
        response_text=response_text,
        parsed_json=parsed,
        cost_cents=cost_cents,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
    )


__all__ = ["LlmCall", "LlmPurpose", "call_llm"]
