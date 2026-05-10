"""Unit + integration tests for the alpha-26 SSE event broadcaster."""

from __future__ import annotations

import asyncio

import pytest


def test_subscribe_returns_unique_tokens(monkeypatch):
    from meridian.events import broadcaster
    broadcaster._reset_for_tests()
    t1, _q1 = broadcaster.subscribe("slug-a")
    t2, _q2 = broadcaster.subscribe("slug-b")
    assert t1 != t2
    assert broadcaster.active_count() == 2
    broadcaster.unsubscribe(t1)
    broadcaster.unsubscribe(t2)
    assert broadcaster.active_count() == 0


def test_subscriber_cap_raises(monkeypatch):
    from meridian.config import settings
    from meridian.events import broadcaster
    broadcaster._reset_for_tests()
    monkeypatch.setattr(settings, "events_max_subscribers", 2)
    broadcaster.subscribe("a")
    broadcaster.subscribe("b")
    with pytest.raises(broadcaster.SubscriberLimitExceeded):
        broadcaster.subscribe("c")


def test_emit_filters_by_allow_list():
    from meridian.events import broadcaster
    broadcaster._reset_for_tests()
    _t, q = broadcaster.subscribe("*")
    broadcaster.emit({
        "event": "api.request",  # NOT in allow-list
        "level": "info",
        "method": "GET",
        "path": "/health",
    })
    assert q.empty()
    broadcaster.emit({
        "event": "triage.chunk.completed",  # IN allow-list
        "level": "info",
        "chunk_id": "c001",
    })
    assert not q.empty()
    payload = q.get_nowait()
    assert payload["event"] == "triage.chunk.completed"
    assert payload["ctx"]["chunk_id"] == "c001"


def test_emit_filters_by_project_slug():
    from meridian.events import broadcaster
    broadcaster._reset_for_tests()
    _t_a, q_a = broadcaster.subscribe("project-a")
    _t_b, q_b = broadcaster.subscribe("project-b")
    broadcaster.emit({
        "event": "extraction.source.start",
        "level": "info",
        "project_slug": "project-a",
        "filename": "spec.pdf",
    })
    assert not q_a.empty()
    assert q_b.empty()


def test_emit_fan_out_to_wildcard():
    """A subscriber with slug='*' receives events from every project."""
    from meridian.events import broadcaster
    broadcaster._reset_for_tests()
    _t, q = broadcaster.subscribe("*")
    broadcaster.emit({
        "event": "extraction.source.start",
        "level": "info",
        "project_slug": "project-x",
    })
    broadcaster.emit({
        "event": "extraction.source.start",
        "level": "info",
        "project_slug": "project-y",
    })
    assert q.qsize() == 2


def test_emit_drops_oldest_on_queue_full():
    """A slow subscriber whose queue fills up loses the oldest event, not the newest."""
    from meridian.events import broadcaster
    broadcaster._reset_for_tests()
    _t, q = broadcaster.subscribe("*")
    for i in range(205):  # queue maxsize=200
        broadcaster.emit({
            "event": "triage.chunk.completed",
            "level": "info",
            "chunk_id": f"c{i:03d}",
        })
    assert q.qsize() == 200
    first = q.get_nowait()
    # First retained event is c005 (c000-c004 dropped)
    assert first["ctx"]["chunk_id"] == "c005"
