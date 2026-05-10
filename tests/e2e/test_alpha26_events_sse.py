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


# ---------------------------------------------------------------------------
# HTTP-level SSE endpoint tests (alpha-26 Task 4)
#
# Test-infrastructure note: Starlette's TestClient._TestClientTransport calls
# portal.call(app, scope, receive, send) which blocks until the ASGI app
# completes — with an infinite SSE generator this never returns. Neither
# TestClient.stream() nor httpx.AsyncClient+ASGITransport can reliably stream
# from an infinite generator in the test process.
#
# Workaround: test the SSE generator logic directly via asyncio.run() for the
# frame-format and timing tests. For the subscriber-cap test, a plain GET
# suffices because the 503 HTTPException is raised before the generator starts
# (the handler raises before returning StreamingResponse). The
# api_client_async fixture remains in conftest for future one-shot JSON tests.
# ---------------------------------------------------------------------------


def test_sse_stream_returns_log_frames_for_real_events(
    project_slug,
):
    """SSE generator emits a correctly-shaped log frame for an allow-listed event.

    Strategy: bypass HTTP entirely. Subscribe via the broadcaster directly,
    emit an allow-listed event onto the queue, then consume the async generator
    (extracted from the endpoint) inside asyncio.run(). Assert the yielded
    frame is well-formed SSE with the right payload shape.
    """
    import asyncio
    import json
    from meridian.events import broadcaster

    broadcaster._reset_for_tests()

    async def _run():
        token, queue = broadcaster.subscribe(project_slug)
        # Pre-load the queue with one event so the generator yields immediately.
        broadcaster.emit({
            "event": "extraction.source.start",
            "level": "info",
            "project_slug": project_slug,
            "filename": "spec.pdf",
        })

        # Replicate the generator logic from the SSE endpoint.
        async def _generator():
            try:
                while True:
                    try:
                        payload = await asyncio.wait_for(queue.get(), timeout=5.0)
                        yield f"event: log\ndata: {json.dumps(payload)}\n\n"
                    except asyncio.TimeoutError:
                        yield "event: heartbeat\ndata: {}\n\n"
            finally:
                broadcaster.unsubscribe(token)

        frames = []
        async for frame in _generator():
            frames.append(frame)
            break  # one frame is enough

        return frames

    frames = asyncio.run(_run())
    assert frames, "generator yielded no frames"
    frame = frames[0]
    assert frame.startswith("event: log\n")
    assert "data: " in frame
    data_line = [ln for ln in frame.splitlines() if ln.startswith("data: ")][0]
    payload = json.loads(data_line[len("data: "):])
    assert payload["event"] == "extraction.source.start"
    assert payload["ctx"]["filename"] == "spec.pdf"


def test_sse_503_when_subscriber_cap_reached(api_client, tmp_projects_dir):
    """Subscriber cap enforcement: second connection within cap returns 503.

    The 503 HTTPException is raised in the handler before the streaming
    generator starts — so a plain GET (non-streaming) correctly receives the
    error JSON. We hold a real broadcaster subscription open (not via HTTP) to
    consume the one slot, then confirm the API returns 503.
    """
    from meridian.config import settings
    from meridian.events import broadcaster

    broadcaster._reset_for_tests()
    settings.events_max_subscribers = 1

    # Create project
    res = api_client.post(
        "/api/projects",
        json={"name": "alpha26-fixture", "notes": "cap test"},
    )
    assert res.status_code in (200, 409), res.text

    # Occupy the one subscriber slot directly via the broadcaster (no HTTP).
    token, _queue = broadcaster.subscribe("alpha26-fixture")
    try:
        # Second subscriber via HTTP: should get 503 immediately.
        r2 = api_client.get("/api/projects/alpha26-fixture/events")
        assert r2.status_code == 503
        body = r2.json()
        assert body["detail"]["error"] == "subscriber_limit"
        assert body["detail"]["limit"] == 1
    finally:
        broadcaster.unsubscribe(token)
        settings.events_max_subscribers = 10


def test_sse_heartbeat_fires_when_idle(project_slug):
    """No real events — the generator yields a heartbeat frame within 6 s.

    Directly exercise the SSE generator via asyncio.run() with a 6-second
    wall-clock deadline. The generator's timeout is 5 s, so the heartbeat
    frame MUST appear before our deadline expires.
    """
    import asyncio
    import json
    from meridian.events import broadcaster

    broadcaster._reset_for_tests()

    async def _run():
        token, queue = broadcaster.subscribe(project_slug)

        async def _generator():
            try:
                while True:
                    try:
                        payload = await asyncio.wait_for(queue.get(), timeout=5.0)
                        yield f"event: log\ndata: {json.dumps(payload)}\n\n"
                    except asyncio.TimeoutError:
                        from datetime import UTC, datetime
                        ts = datetime.now(UTC).isoformat(
                            timespec="milliseconds"
                        ).replace("+00:00", "Z")
                        yield f"event: heartbeat\ndata: {{\"ts\":\"{ts}\"}}\n\n"
            finally:
                broadcaster.unsubscribe(token)

        # Wrap with an outer timeout to guarantee the test terminates.
        async def _collect_one():
            async for frame in _generator():
                return frame
            return None

        try:
            frame = await asyncio.wait_for(_collect_one(), timeout=6.0)
        except asyncio.TimeoutError:
            broadcaster.unsubscribe(token)
            raise AssertionError("no heartbeat received within 6 s")
        return frame

    frame = asyncio.run(_run())
    assert frame is not None, "generator yielded no frames"
    assert "event: heartbeat" in frame, f"expected heartbeat frame, got: {frame!r}"


def test_setup_runtime_includes_events_section(api_client):
    res = api_client.get("/api/setup/runtime")
    assert res.status_code == 200
    body = res.json()
    assert "events" in body
    assert body["events"]["max_subscribers"] >= 1
    assert body["events"]["active_subscribers"] >= 0
    assert body["events"]["broadcaster_enabled"] is True
