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
    from meridian.wizard import api as wizard_api

    with wizard_api._idempotency_lock:
        wizard_api._idempotency.clear()
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

    from meridian.wizard import api as wizard_api

    with wizard_api._idempotency_lock:
        assert TOKEN_A in wizard_api._idempotency, (
            "Idempotency registry should contain the token after a first POST."
        )
        recorded_job_id, recorded_at = wizard_api._idempotency[TOKEN_A]
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
