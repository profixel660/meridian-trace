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
    # The event loop that owns this subscriber's queue. Stored at subscribe()
    # time so emit() can use call_soon_threadsafe when called from a thread
    # other than the one running the event loop (e.g. structlog processor
    # called from a worker thread while the FastAPI event loop is in a
    # different thread).
    loop: asyncio.AbstractEventLoop | None = field(default=None)


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
    # Capture the running event loop at subscribe time so emit() can use
    # call_soon_threadsafe when invoked from a non-event-loop thread (the
    # typical case in production and in TestClient-based tests where the
    # ASGI app runs in a background thread while structlog fires from
    # worker threads or the test thread itself).
    try:
        loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        # No event loop running in this thread (e.g. sync unit tests).
        loop = None
    with _subscribers_lock:
        if len(_subscribers) >= settings.events_max_subscribers:
            raise SubscriberLimitExceeded(
                f"Subscriber cap reached ({settings.events_max_subscribers})"
            )
        token = _next_token
        _next_token += 1
        sub = _Subscriber(slug=slug, loop=loop)
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
            (s.queue, s.loop)
            for s in _subscribers.values()
            if target_slug is None or s.slug == target_slug or s.slug == "*"
        ]
    for q, loop in targets:
        _deliver(q, loop, payload)


def _deliver(
    q: asyncio.Queue[dict[str, Any]],
    loop: asyncio.AbstractEventLoop | None,
    payload: dict[str, Any],
) -> None:
    """Deliver payload to queue, handling cross-thread and drop-oldest semantics.

    When the subscriber's event loop is running in a different thread
    (e.g. the FastAPI event loop in TestClient's background thread while
    structlog fires from the test thread), use call_soon_threadsafe so the
    queue wakes up its awaiting getters correctly. Falls back to put_nowait
    for same-thread delivery (unit tests, same-loop calls).
    """
    # Determine if we need cross-thread delivery.
    use_threadsafe = False
    if loop is not None and loop.is_running():
        try:
            running = asyncio.get_running_loop()
            use_threadsafe = running is not loop
        except RuntimeError:
            # No running loop in the current thread — definitely cross-thread.
            use_threadsafe = True

    if use_threadsafe:
        # Cross-thread: schedule on the subscriber's event loop.
        def _put_or_drop() -> None:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    q.put_nowait(payload)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass
        loop.call_soon_threadsafe(_put_or_drop)
    else:
        # Same thread: direct put_nowait (works in unit tests and prod when
        # emit is called from inside the event loop, e.g. via an async
        # structlog pipeline).
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
