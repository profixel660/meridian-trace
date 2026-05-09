"""Alpha-24 item #4 — log-volume regression.

The SME's 2026-05-02 round produced 694 ingest.start events for 347
distinct files (each file kicked off twice). This test pins the
post-fix invariant: a folder containing N files imported under the
double-click trigger pattern emits exactly N file-completion events,
not 2N.

The double-click is simulated by firing two concurrent POSTs with the
same Idempotency-Key. Pre-fix, this would create two jobs and 2N
worker logs; post-fix, the second POST is deduped and only one worker
runs.

Propagation note: ``meridian.wizard.import`` is configured with
``propagate=False`` (alpha-12 fix — keeps structlog JSON out of the
operator-visible stderr) which means pytest's ``caplog`` fixture (which
installs a handler on the root logger) cannot capture its records via
the normal propagation path. This test temporarily enables propagation
on ``_stdlog`` inside the capture context so ``caplog`` sees the
records, then restores the original flag. This is test-only wiring; it
does not affect the production behaviour described in alpha-12.
"""
from __future__ import annotations

import concurrent.futures
import logging
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


TOKEN = "11111111-1111-4111-8111-111111111111"


def test_double_submit_does_not_double_emit_file_done_events(
    fastapi_client: TestClient,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from meridian.wizard import api as wizard_api

    # Clear registries.
    with wizard_api._idempotency_lock:
        wizard_api._idempotency.clear()
    with wizard_api._jobs_lock:
        wizard_api._jobs.clear()

    # Seed a folder with 5 minimal PDFs.
    folder = tmp_path / "src"
    folder.mkdir()
    n_files = 5
    for i in range(n_files):
        (folder / f"doc-{i}.pdf").write_bytes(
            f"%PDF-1.4 alpha24 logvol {i}\n%%EOF\n".encode("utf-8")
        )

    body = {"folder_path": str(folder), "project_name": "alpha24-logvol"}

    # Capture INFO-level logs from the import worker (stdlib logger, not
    # structlog — see the _stdlog routing in wizard/api.py).
    #
    # Because _stdlog.propagate is False (alpha-12 isolation), caplog's
    # root-logger handler would not see the records under normal conditions.
    # We temporarily re-enable propagation for the duration of this test so
    # caplog can intercept the records. The original value (False) is restored
    # in the finally block so no other test is affected.
    _stdlog = wizard_api._stdlog
    _orig_propagate = _stdlog.propagate
    _stdlog.propagate = True
    try:
        with caplog.at_level(logging.INFO, logger="meridian.wizard.import"):
            # Fire two concurrent POSTs with the same token. Pre-fix: two
            # jobs, two workers, ~2N file_done events. Post-fix: one job,
            # one worker, ~N file_done events.
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
                f1 = ex.submit(
                    fastapi_client.post,
                    "/setup/import-folder",
                    json=body,
                    headers={"Idempotency-Key": TOKEN},
                )
                f2 = ex.submit(
                    fastapi_client.post,
                    "/setup/import-folder",
                    json=body,
                    headers={"Idempotency-Key": TOKEN},
                )
                res1 = f1.result()
                res2 = f2.result()

            assert res1.status_code == 200
            assert res2.status_code == 200
            assert res1.json()["job_id"] == res2.json()["job_id"], (
                "Both responses must carry the same job_id; otherwise the "
                "log-volume invariant cannot hold."
            )
            job_id = res1.json()["job_id"]

            # Wait for the worker to finish. The file-done event includes
            # the job id so we can scope the assertion.
            deadline_polls = 50
            for _ in range(deadline_polls):
                poll = fastapi_client.get(f"/setup/import-folder/{job_id}")
                assert poll.status_code == 200
                if poll.json()["status"] in {"succeeded", "failed"}:
                    break
                time.sleep(0.1)
            else:
                pytest.fail(
                    "import job did not reach a terminal state within deadline"
                )

    finally:
        _stdlog.propagate = _orig_propagate

    # Count per-file completion events. The worker emits exactly one of:
    #   • import_job.file_done  (INFO)  — for imported / deduped / missing files
    #   • import_job.file_failed (WARNING) — for files that raised an exception
    # Together they represent one event per file. The minimal PDF stubs used
    # by this test fail PDF parsing (code=unknown) so file_failed is expected;
    # the invariant being tested is that EITHER event appears exactly N times
    # (not 2N) regardless of outcome.
    per_file_events = [
        r for r in caplog.records
        if (
            ("import_job.file_done" in r.getMessage() or "import_job.file_failed" in r.getMessage())
            and job_id in r.getMessage()
        )
    ]
    assert len(per_file_events) == n_files, (
        f"Expected exactly {n_files} per-file log events (file_done or file_failed) "
        f"for job_id {job_id[:8]}...; got {len(per_file_events)}. "
        "Pre-fix the second POST would have created a second job and "
        "doubled the event count. This test pins the post-fix invariant."
    )
