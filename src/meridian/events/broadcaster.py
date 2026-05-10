"""In-process structured-event broadcaster for the alpha-26 SSE surface.

Subscribers register via subscribe(slug) which returns a token + an
asyncio.Queue. The structlog processor chain calls emit(event_dict) on
every event; the broadcaster filters by allow-list + slug and pushes
copies to each matching queue. Bounded by settings.events_max_subscribers.

Lock posture: a threading.Lock guards subscriber registration / removal
and the targets-list snapshot inside emit(). asyncio.Queue.put_nowait is
thread-safe for cross-thread fan-out (structlog runs in the calling
thread; subscribers consume from the FastAPI event loop).
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any

from meridian.config import settings
from meridian.logging import get_logger

_log = get_logger("meridian.events.broadcaster")

_BROADCAST_ALLOW_LIST: frozenset[str] = frozenset({
    # Extraction lifecycle
    "extraction.job.start",
    "extraction.job.finish",
    "extraction.source.start",
    "extraction.source.committed",
    "extraction.source.finish",
    "extraction.source.skip",
    "extraction.source.fail",
    # Per-chunk progress (the load-bearing "is it working?" signal)
    "triage.chunk.completed",
    "triage.chunk.orphan_in_progress",
    # LLM call ledger
    "llm_call.completed",
    # Pipeline (alpha-25 family + alpha-26 done emit)
    "pipeline.bootstrap_soft_failed",
    "pipeline.conflict_pass_skipped_empty_corpus",
    "pipeline.conflict_pass_soft_failed",
    "pipeline.busy",
    "pipeline.failed",
    "pipeline.done",
})


@dataclass
class _Subscriber:
    slug: str
    queue: asyncio.Queue[dict[str, Any]] = field(
        default_factory=lambda: asyncio.Queue(maxsize=200),
    )


_subscribers: dict[int, _Subscriber] = {}
_subscribers_lock = threading.Lock()
_next_token: int = 0


class SubscriberLimitExceeded(Exception):
    """Raised when subscribe() is called past settings.events_max_subscribers."""


def active_count() -> int:
    with _subscribers_lock:
        return len(_subscribers)


def subscribe(slug: str) -> tuple[int, asyncio.Queue[dict[str, Any]]]:
    """Register a subscriber. Raises SubscriberLimitExceeded if at cap."""
    global _next_token
    with _subscribers_lock:
        if len(_subscribers) >= settings.events_max_subscribers:
            raise SubscriberLimitExceeded(
                f"Subscriber cap reached ({settings.events_max_subscribers})"
            )
        token = _next_token
        _next_token += 1
        sub = _Subscriber(slug=slug)
        _subscribers[token] = sub
    _log.info(
        "events.subscriber.registered",
        token=token, slug=slug, active=active_count(),
    )
    return token, sub.queue


def unsubscribe(token: int) -> None:
    with _subscribers_lock:
        _subscribers.pop(token, None)
    _log.info(
        "events.subscriber.unregistered",
        token=token, active=active_count(),
    )


def emit(event_dict: dict[str, Any]) -> None:
    """Called by the structlog processor on every event. Filters + fans out."""
    event_name = event_dict.get("event")
    if event_name not in _BROADCAST_ALLOW_LIST:
        return
    target_slug = event_dict.get("project_slug")
    payload = {
        "ts": event_dict.get("timestamp"),
        "level": event_dict.get("level", "info"),
        "event": event_name,
        "ctx": {
            k: v
            for k, v in event_dict.items()
            if k not in {"event", "level", "timestamp", "project_slug"}
        },
        "project_slug": target_slug,
    }
    with _subscribers_lock:
        targets = [
            s.queue
            for s in _subscribers.values()
            if target_slug is None or s.slug == target_slug or s.slug == "*"
        ]
    for q in targets:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
                q.put_nowait(payload)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass


# Test seam — DO NOT call from production code.
def _reset_for_tests() -> None:
    global _next_token
    with _subscribers_lock:
        _subscribers.clear()
        _next_token = 0


__all__ = [
    "SubscriberLimitExceeded",
    "active_count",
    "emit",
    "subscribe",
    "unsubscribe",
]
