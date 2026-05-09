"""Alpha-24 item #4 — server-side idempotency for /setup/import-folder.

The wizard's confirm button can fire twice in a single user click (browser
auto-resubmit on focus regain, dialog double-click, page remount race).
Alpha-23 added row-level race-recovery in ingest_file so the user-visible
failure mode is gone, but log volume + LLM cost still doubled. This module
locks the contract: a client that sends the same Idempotency-Key header
twice gets back the same job_id and only one folder-scan was performed.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# Canonical fixtures — short readable strings instead of random uuids so
# failure messages tell you which token came back.
TOKEN_A = "11111111-1111-4111-8111-111111111111"
TOKEN_B = "22222222-2222-4222-8222-222222222222"


@pytest.fixture(autouse=True)
def _clear_idempotency_registry() -> None:
    """Each test starts with an empty idempotency registry.

    Module-level state in meridian.wizard.api persists across tests inside
    the same pytest run; without this fixture, a token recorded in test N
    would leak into test N+1 and falsely trigger the replay branch.
    """
    from meridian.api import idempotency as _idem
    from meridian.wizard import api as wizard_api

    _idem._reset_for_tests()
    # Also clear the _jobs registry so two tests scanning the same folder
    # don't trip the staging-detection logic in suggest-name (irrelevant
    # to this file's contract but defensive).
    with wizard_api._jobs_lock:
        wizard_api._jobs.clear()
    yield


def _seed_folder(tmp_path: Path) -> Path:
    """Create a one-PDF folder; cheap and deterministic."""
    folder = tmp_path / "src_docs"
    folder.mkdir()
    pdf = folder / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 alpha24 idempotency fixture\n%%EOF\n")
    return folder


def test_replay_with_same_token_returns_original_job_id(
    fastapi_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two POSTs with the same token + body → identical job_id, one scan."""
    folder = _seed_folder(tmp_path)

    # Spy on walk_directory so we can assert the dedupe path skips the
    # re-walk. The spy preserves real behaviour but counts calls.
    from meridian.ingest import dispatcher

    real_walk = dispatcher.walk_directory
    call_count = {"n": 0}

    def _counting_walk(*args, **kwargs):
        call_count["n"] += 1
        return real_walk(*args, **kwargs)

    monkeypatch.setattr(
        "meridian.wizard.api.walk_directory", _counting_walk
    )

    body = {"folder_path": str(folder), "project_name": "alpha24-replay"}
    res1 = fastapi_client.post(
        "/setup/import-folder",
        json=body,
        headers={"Idempotency-Key": TOKEN_A},
    )
    assert res1.status_code == 200, res1.text
    job_id_first = res1.json()["job_id"]

    res2 = fastapi_client.post(
        "/setup/import-folder",
        json=body,
        headers={"Idempotency-Key": TOKEN_A},
    )
    assert res2.status_code == 200, res2.text
    job_id_second = res2.json()["job_id"]

    assert job_id_first == job_id_second, (
        f"Replay with same Idempotency-Key returned a different job_id: "
        f"{job_id_first!r} vs {job_id_second!r}. The dedupe path must "
        "return the original job_id, not create a new job."
    )

    assert call_count["n"] == 1, (
        f"walk_directory called {call_count['n']} times — replay path "
        "must short-circuit before re-walking the folder."
    )

    # Exactly one job in the registry.
    from meridian.wizard import api as wizard_api

    with wizard_api._jobs_lock:
        assert len(wizard_api._jobs) == 1, (
            f"Expected 1 job in registry; got {len(wizard_api._jobs)}."
        )


def test_first_post_creates_job_and_records_token(
    fastapi_client: TestClient,
    tmp_path: Path,
) -> None:
    """A token's first appearance creates a job and records the token."""
    folder = _seed_folder(tmp_path)

    res = fastapi_client.post(
        "/setup/import-folder",
        json={"folder_path": str(folder), "project_name": "alpha24-first"},
        headers={"Idempotency-Key": TOKEN_A},
    )
    assert res.status_code == 200, res.text
    job_id = res.json()["job_id"]

    from meridian.api import idempotency as _idem

    registry = _idem._get_registry_for_tests()
    assert TOKEN_A in registry, (
        "Idempotency registry should contain the token after a first POST."
    )
    recorded_job_id, recorded_at = registry[TOKEN_A]
    assert recorded_job_id == job_id


def test_different_tokens_create_different_jobs(
    fastapi_client: TestClient,
    tmp_path: Path,
) -> None:
    """Two POSTs with distinct tokens → two distinct job_ids (same body)."""
    folder = _seed_folder(tmp_path)
    body = {"folder_path": str(folder), "project_name": "alpha24-distinct"}

    res1 = fastapi_client.post(
        "/setup/import-folder",
        json=body,
        headers={"Idempotency-Key": TOKEN_A},
    )
    res2 = fastapi_client.post(
        "/setup/import-folder",
        json=body,
        headers={"Idempotency-Key": TOKEN_B},
    )
    assert res1.status_code == 200 and res2.status_code == 200
    assert res1.json()["job_id"] != res2.json()["job_id"], (
        "Distinct tokens should produce distinct job_ids — the dedupe key "
        "is the token, not the request body."
    )


def test_no_token_header_creates_new_job_each_time(
    fastapi_client: TestClient,
    tmp_path: Path,
) -> None:
    """Backwards-compat: header-less POSTs always create new jobs."""
    folder = _seed_folder(tmp_path)
    body = {"folder_path": str(folder), "project_name": "alpha24-nohdr"}

    res1 = fastapi_client.post("/setup/import-folder", json=body)
    res2 = fastapi_client.post("/setup/import-folder", json=body)
    assert res1.status_code == 200 and res2.status_code == 200
    assert res1.json()["job_id"] != res2.json()["job_id"], (
        "Without the Idempotency-Key header, the endpoint must keep its "
        "current behaviour of creating a fresh job per POST."
    )


def test_token_ttl_expires_after_15_minutes(
    fastapi_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A record older than _IDEMPOTENCY_TTL_SECONDS is treated as absent."""
    folder = _seed_folder(tmp_path)
    body = {"folder_path": str(folder), "project_name": "alpha24-ttl"}

    # Pin time so we can fast-forward predictably. Patch the symbol the
    # idempotency module reaches for, not time.monotonic globally — there are
    # other monotonic readers in the request path (uvicorn, FastAPI, etc.)
    # that would behave oddly under a global patch.
    import time as time_module
    from meridian.api import idempotency as _idem

    fake_now = {"t": 100.0}

    def _fake_monotonic() -> float:
        return fake_now["t"]

    monkeypatch.setattr(time_module, "monotonic", _fake_monotonic)

    res1 = fastapi_client.post(
        "/setup/import-folder",
        json=body,
        headers={"Idempotency-Key": TOKEN_A},
    )
    assert res1.status_code == 200, res1.text
    job_id_first = res1.json()["job_id"]

    # Fast-forward past TTL.
    fake_now["t"] = 100.0 + _idem.IDEMPOTENCY_TTL_SECONDS + 1.0

    res2 = fastapi_client.post(
        "/setup/import-folder",
        json=body,
        headers={"Idempotency-Key": TOKEN_A},
    )
    assert res2.status_code == 200, res2.text
    assert res2.json()["job_id"] != job_id_first, (
        "After TTL elapsed, replay must create a fresh job."
    )

    # Lazy GC: the expired entry should have been pruned during the lookup.
    from meridian.api import idempotency as _idem

    registry = _idem._get_registry_for_tests()
    # The new POST recorded a fresh entry — assert it's the new job_id,
    # not the stale one.
    recorded_job_id, _ = registry[TOKEN_A]
    assert recorded_job_id == res2.json()["job_id"]


def test_malformed_token_returns_400_invalid_idempotency_key(
    fastapi_client: TestClient,
    tmp_path: Path,
) -> None:
    """Bad header value → 400 with structured error code."""
    folder = _seed_folder(tmp_path)
    body = {"folder_path": str(folder), "project_name": "alpha24-bad-token"}

    for bad in ("", "not-a-uuid", "11111111111141118111111111111111", "x"):
        res = fastapi_client.post(
            "/setup/import-folder",
            json=body,
            headers={"Idempotency-Key": bad},
        )
        assert res.status_code == 400, (
            f"Idempotency-Key={bad!r} should produce 400; got {res.status_code}"
        )
        detail = res.json()["detail"]
        assert detail["error"] == "invalid_idempotency_key", detail


def test_replay_after_job_reaped_returns_token_record_with_dead_job_id(
    fastapi_client: TestClient,
    tmp_path: Path,
) -> None:
    """A token record outlives the job it points at.

    Documents the limit, not a regression: if the job was reaped from
    _jobs but the token record is still within TTL, the replay returns
    the recorded job_id — the next GET /import-folder/{job_id} returns
    404 and the frontend's existing 'Lost contact' phase fires.
    """
    folder = _seed_folder(tmp_path)
    body = {"folder_path": str(folder), "project_name": "alpha24-reaped"}

    res1 = fastapi_client.post(
        "/setup/import-folder",
        json=body,
        headers={"Idempotency-Key": TOKEN_A},
    )
    assert res1.status_code == 200
    job_id_first = res1.json()["job_id"]

    # Simulate post-completion reaping by clearing the job from _jobs
    # without touching _idempotency. This is the future-cleanup scenario
    # alpha-24 explicitly accepts: the token record outlives the job.
    from meridian.wizard import api as wizard_api

    with wizard_api._jobs_lock:
        wizard_api._jobs.pop(job_id_first, None)

    res2 = fastapi_client.post(
        "/setup/import-folder",
        json=body,
        headers={"Idempotency-Key": TOKEN_A},
    )
    assert res2.status_code == 200
    assert res2.json()["job_id"] == job_id_first, (
        "Replay must return the originally-recorded job_id even when "
        "the job has been reaped from the live registry."
    )

    # Subsequent GET /import-folder/{job_id} must 404 — confirms the
    # frontend will route to the existing 'Lost contact' path.
    poll = fastapi_client.get(f"/setup/import-folder/{job_id_first}")
    assert poll.status_code == 404


def test_concurrent_posts_with_same_token_dedupe_via_claim(
    fastapi_client: TestClient,
    tmp_path: Path,
) -> None:
    """In-process companion to gauntlet step 7i.

    Two concurrent POSTs with the same Idempotency-Key must produce
    exactly one job and identical job_ids. Pre-fix (before
    `_idempotency_claim`), both requests passed the read-only lookup
    simultaneously and both ran `create_project`, hitting a SQLite
    lock. The atomic claim path closes the TOCTOU window.
    """
    import concurrent.futures

    folder = _seed_folder(tmp_path)
    body = {"folder_path": str(folder), "project_name": "alpha24-race"}

    def _post() -> tuple[int, str | None]:
        res = fastapi_client.post(
            "/setup/import-folder",
            json=body,
            headers={"Idempotency-Key": TOKEN_A},
        )
        if res.status_code != 200:
            return res.status_code, None
        return res.status_code, res.json()["job_id"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(_post)
        f2 = ex.submit(_post)
        status_1, job_id_1 = f1.result(timeout=10)
        status_2, job_id_2 = f2.result(timeout=10)

    assert status_1 == 200 and status_2 == 200, (
        f"Both POSTs must succeed; got status_1={status_1}, status_2={status_2}. "
        "Pre-fix the race winner could 500 with a SQLite lock."
    )
    assert job_id_1 == job_id_2, (
        f"Concurrent POSTs with the same token must dedupe via _idempotency_claim. "
        f"Got distinct job_ids: {job_id_1!r} vs {job_id_2!r}."
    )

    # Exactly one job in the registry.
    from meridian.wizard import api as wizard_api

    with wizard_api._jobs_lock:
        assert len(wizard_api._jobs) == 1, (
            f"Expected exactly 1 job in registry; got {len(wizard_api._jobs)}. "
            "Race-loser must NOT have constructed a second _ImportJob."
        )


# Direct unit tests for the shared idempotency module
def test_idempotency_module_validate_uuid4() -> None:
    from meridian.api import idempotency

    assert idempotency.validate(None) is None
    assert idempotency.validate("11111111-1111-4111-8111-111111111111") == \
        "11111111-1111-4111-8111-111111111111"


def test_idempotency_module_validate_rejects_v3() -> None:
    from fastapi import HTTPException
    from meridian.api import idempotency

    try:
        idempotency.validate("11111111-1111-3111-8111-111111111111")
    except HTTPException as exc:
        assert exc.status_code == 400
        assert exc.detail["error"] == "invalid_idempotency_key"
        return
    raise AssertionError("expected HTTPException")


def test_idempotency_module_claim_idempotent() -> None:
    from meridian.api import idempotency

    idempotency._reset_for_tests()
    key = "22222222-2222-4222-8222-222222222222"
    assert idempotency.claim(key, "job-A") == "job-A"
    # second claim returns the original winner
    assert idempotency.claim(key, "job-B") == "job-A"
