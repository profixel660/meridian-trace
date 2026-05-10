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


def test_broadcast_processor_fans_real_structlog_events(monkeypatch):
    """A real structlog log call lands on a subscriber's queue when the
    event is in the allow-list."""
    from meridian.events import broadcaster
    from meridian.logging import configure_logging, get_logger

    broadcaster._reset_for_tests()
    configure_logging(console=False)
    _t, q = broadcaster.subscribe("*")
    log = get_logger("test")
    log.info("triage.chunk.completed", chunk_id="c-real", keep=True)

    payload = q.get_nowait()
    assert payload["event"] == "triage.chunk.completed"
    assert payload["ctx"]["chunk_id"] == "c-real"
    assert payload["ctx"]["keep"] is True


def test_pipeline_done_emit_reaches_subscribers(
    project_with_two_sources, mock_llm_client,
):
    """Alpha-26: pipeline_worker emits pipeline.done on success transition."""
    from pathlib import Path
    from meridian.events import broadcaster
    from meridian.workers.pipeline_worker import _PipelineJob, _run_pipeline

    broadcaster._reset_for_tests()
    _t, q = broadcaster.subscribe("*")

    conn, _source_ids, _ = project_with_two_sources
    db_path = Path(conn.execute("PRAGMA database_list").fetchone()["file"])
    job = _PipelineJob(id="job-pipeline-done-1", db_path=db_path)
    _run_pipeline(job, sample_size=2, provider=None, model=None)

    assert job.phase == "done"
    # Drain queue and find a pipeline.done frame.
    seen_events = []
    while not q.empty():
        seen_events.append(q.get_nowait()["event"])
    assert "pipeline.done" in seen_events, (
        f"expected pipeline.done in events; got {seen_events}"
    )
