"""Idempotency-Key registry used by the wizard import-folder endpoint and
the alpha-25 pipeline endpoint.

Process-local in-memory registry — a backend bounce forfeits the dedup
window. Lookups opportunistically GC expired entries; no background
thread, no persistent storage. UUIDv4 keys only.
"""

from __future__ import annotations

import re
import time
from threading import Lock

from fastapi import HTTPException, status

from meridian.logging import get_logger

_log = get_logger("meridian.api.idempotency")

IDEMPOTENCY_TTL_SECONDS: float = 15 * 60.0
IDEMPOTENCY_KEY_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

_registry: dict[str, tuple[str, float]] = {}
_lock = Lock()


def lookup(key: str) -> tuple[str, float] | None:
    """Return (job_id, age_seconds) if `key` is on file and unexpired."""
    now = time.monotonic()
    with _lock:
        expired = [
            k for k, (_jid, recorded_at) in _registry.items()
            if now - recorded_at > IDEMPOTENCY_TTL_SECONDS
        ]
        for k in expired:
            del _registry[k]
        record = _registry.get(key)
        if record is None:
            return None
        job_id, recorded_at = record
        return job_id, now - recorded_at


def claim(key: str, job_id: str) -> str:
    """Atomically claim `key` for `job_id` or return the existing winner."""
    now = time.monotonic()
    with _lock:
        expired = [
            k for k, (_jid, recorded_at) in _registry.items()
            if now - recorded_at > IDEMPOTENCY_TTL_SECONDS
        ]
        for k in expired:
            del _registry[k]
        record = _registry.get(key)
        if record is not None:
            existing_job_id, _ = record
            return existing_job_id
        _registry[key] = (job_id, now)
        return job_id


def validate(value: str | None, *, log_event: str = "idempotency.token_rejected") -> str | None:
    """Return the UUIDv4 unchanged if valid; None if absent. Raise 400 on malformed."""
    if value is None:
        return None
    if not IDEMPOTENCY_KEY_PATTERN.match(value):
        _log.info(log_event, reason="invalid_format")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_idempotency_key",
                "message": (
                    "Idempotency-Key header must be a UUIDv4 "
                    "(lower-case, e.g. '11111111-1111-4111-8111-111111111111')."
                ),
            },
        )
    return value


# Test seams — DO NOT call from production code.
def _reset_for_tests() -> None:
    with _lock:
        _registry.clear()


def _get_registry_for_tests() -> dict[str, tuple[str, float]]:
    """Return a snapshot of the registry. For testing only."""
    with _lock:
        return dict(_registry)


__all__ = [
    "IDEMPOTENCY_KEY_PATTERN",
    "IDEMPOTENCY_TTL_SECONDS",
    "claim",
    "lookup",
    "validate",
]
