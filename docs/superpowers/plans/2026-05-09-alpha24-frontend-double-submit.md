# Alpha-24 — Frontend double-submit elimination (item #4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a single user click → exactly one folder-import job, regardless of double-click, browser retry, page remount, or network replay. After this lands, an SME folder import of N files produces ≤N `ingest.start` events (today: up to 2N).

**Architecture:** Three independent layers shipped together. **L1** — phase-machine guard in the wizard's first-documents page (re-entry impossible once submit is in flight). **L2** — disable the `ConfirmDialog` confirm button while in flight (visible UX feedback). **L3** — server-side dedupe via an `Idempotency-Key` header, kept in a TTL'd in-process registry alongside the existing `_jobs` board. Each layer closes a different opening; together they close the class.

**Tech Stack:** Python 3.12 / FastAPI / Pydantic / SQLite (backend at `src/meridian/wizard/api.py`); Next.js 14 SPA + TypeScript + React 18 (frontend at `apps/web/src/`); pytest e2e via FastAPI `TestClient`.

**Spec:** `docs/superpowers/specs/2026-05-09-alpha24-frontend-double-submit-design.md` (committed `ddc24b5`).

---

## File structure

**Backend (Python):**
- Modify `src/meridian/wizard/api.py` — add `_idempotency` registry, `_IDEMPOTENCY_TTL_SECONDS`, header parsing helper, dedupe branch in `setup_import_folder`. ~80 net new lines clustered around the import-folder endpoint.
- Test new: `tests/e2e/test_alpha24_import_idempotency.py` — 7 tests covering create/replay/distinct/no-header/malformed/TTL/reaped-job behaviour.
- Test extend: `tests/e2e/test_wizard_api.py` — 1 new test asserting the structured replay log event fires.
- Test new: `tests/e2e/test_alpha24_log_volume.py` — 1 test asserting `ingest.start`-style events count == file count under simulated double-submit.

**Frontend (TypeScript / React):**
- Modify `apps/web/src/lib/setupClient.ts` — `importFolder(folderPath, projectName, idempotencyKey)` adds the third arg + `Idempotency-Key` header.
- Modify `apps/web/src/components/review/ConfirmDialog.tsx` — add `title` attribute on the disabled confirm button.
- Modify `apps/web/src/app/setup/first-documents/page.tsx` — new `submitting` phase, `crypto.randomUUID()` per click, `busy` plumbing on `ConfirmDialog`, `isBusy` extension.

**Release tooling:**
- Modify `scripts/release_gauntlet.py` — add step 7i: parallel-POST with same idempotency token must produce one job.

**Streams (parallel-safe except where noted):**
- **Stream A — Backend:** Tasks A1 → A5 (file-disjoint with Stream B; safe to run in parallel).
- **Stream B — Frontend:** Tasks B1 → B3 (file-disjoint with Stream A; safe to run in parallel).
- **Stream C — Release tooling + log volume:** Tasks C1 → C2 (depends on Stream A landing because the gauntlet step + log test exercise the backend endpoint).

---

## Conventions all tasks follow

- **Project root:** `C:\Users\PeterRoberts\OneDrive - Undivided Systems\Documents\Project_requirements_tester` — all paths below are repo-relative; quote the project root in shell calls.
- **Run tests** with the project venv's `python -m pytest <path> -v`. The repo uses `uv` for dep management; tests use plain `pytest`.
- **Frontend lint/typecheck:** `cd apps/web && npm run lint && npm run typecheck` (skipped if Node isn't installed locally — Stream A's tests are the gate).
- **Commit style** (matches `git log` head): `[scoped] alpha-24 <stream>: <subject>` — short imperative subject, body if helpful, trailer:

    ```
    Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
    ```

- **Idempotency token format:** UUIDv4 (regex `^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`). Frontend uses `crypto.randomUUID()`. Tests use the canonical fixed string `11111111-1111-4111-8111-111111111111` (and analogues `2…2`, `3…3`) so failures are easy to read.

---

## Stream A — Backend

### Task A1: Wire `Idempotency-Key` header read with bypass-when-absent

Goal: introduce the registry + header parser; behaviour stays identical when no header is sent (backwards compat). No tests should break.

**Files:**
- Modify: `src/meridian/wizard/api.py` (`setup_import_folder` near line 1209; add module-level state near `_jobs` near line 206)

- [ ] **Step 1: Add module-level state and helpers**

In `src/meridian/wizard/api.py`, find this block (around line 206):

```python
_jobs: dict[str, _ImportJob] = {}
_jobs_lock = Lock()
```

Add immediately after:

```python
# --------------------------------------------------------------------------
# Idempotency registry — alpha-24 item #4.
#
# Maps Idempotency-Key (UUIDv4 from the wizard) → (job_id, recorded_at_monotonic).
# Lifetime is process-local: a backend bounce forfeits the dedup window.
# Lookups also opportunistically delete entries older than the TTL — no
# background thread, no persistent storage.
# --------------------------------------------------------------------------

_IDEMPOTENCY_TTL_SECONDS: float = 15 * 60.0
_IDEMPOTENCY_KEY_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

_idempotency: dict[str, tuple[str, float]] = {}
_idempotency_lock = Lock()


def _idempotency_record(key: str, job_id: str) -> None:
    with _idempotency_lock:
        _idempotency[key] = (job_id, time.monotonic())


def _idempotency_lookup(key: str) -> tuple[str, float] | None:
    """Return (job_id, age_seconds) if the key is on file and unexpired.

    Side-effect: opportunistically purges any expired entries it sees.
    """
    now = time.monotonic()
    with _idempotency_lock:
        # Lazy GC of every expired entry — cheap (registry is bounded by
        # request rate × TTL, ~hundreds at most for a single-user wizard).
        expired = [
            k for k, (_jid, recorded_at) in _idempotency.items()
            if now - recorded_at > _IDEMPOTENCY_TTL_SECONDS
        ]
        for k in expired:
            del _idempotency[k]
        record = _idempotency.get(key)
        if record is None:
            return None
        job_id, recorded_at = record
        return job_id, now - recorded_at


def _validate_idempotency_key(value: str | None) -> str | None:
    """Return a normalised UUIDv4, or None if no header. Raises on malformed."""
    if value is None:
        return None
    if not _IDEMPOTENCY_KEY_PATTERN.match(value):
        _log.info("wizard.import_folder.idempotency_token_rejected", reason="invalid_format")
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
```

Also add `import re` at the top of the imports section if not already present (file already imports `re` indirectly via Pydantic but the module itself doesn't — verify). Specifically, locate the import block near line 18 and add `import re` if missing:

```python
import logging
import os
import re
import sys
```

- [ ] **Step 2: Run the existing test suite to confirm no regressions**

```bash
cd "C:\Users\PeterRoberts\OneDrive - Undivided Systems\Documents\Project_requirements_tester"
python -m pytest tests/e2e/test_wizard_api.py tests/e2e/test_alpha22_*.py -v
```

Expected: all existing tests pass (no behaviour change yet — the new symbols are dormant).

- [ ] **Step 3: Wire the header read into `setup_import_folder`**

In `src/meridian/wizard/api.py`, find the `setup_import_folder` function (around line 1209). Change its signature to accept the FastAPI `Request` (so we can read the `Idempotency-Key` header) and add the validation call at the top of the body. Find:

```python
@wizard_router.post(
    "/import-folder",
    response_model=ImportJobResponse,
    responses={
        400: {"description": "folder_path is missing, not a directory, or unreadable."},
    },
)
def setup_import_folder(req: FolderImportRequest) -> ImportJobResponse:
    """Walk ``folder_path`` and queue every supported file for ingestion.
    ...
    """
    folder = _validate_folder_path(req.folder_path)
```

Replace with:

```python
@wizard_router.post(
    "/import-folder",
    response_model=ImportJobResponse,
    responses={
        400: {
            "description": (
                "folder_path is missing, not a directory, or unreadable; "
                "OR Idempotency-Key header is malformed (alpha-24)."
            ),
        },
    },
)
def setup_import_folder(
    req: FolderImportRequest, request: Request
) -> ImportJobResponse:
    """Walk ``folder_path`` and queue every supported file for ingestion.
    ...
    """
    idempotency_key = _validate_idempotency_key(
        request.headers.get("Idempotency-Key")
    )
    folder = _validate_folder_path(req.folder_path)
```

(Leave the docstring text otherwise unchanged. The signature gains `request: Request`; the existing test `test_wizard_api.py` patterns pass `Request` automatically via TestClient.)

- [ ] **Step 4: Run the suite again**

```bash
python -m pytest tests/e2e/test_wizard_api.py tests/e2e/test_alpha22_*.py -v
```

Expected: still passing — the new arg is read but not yet acted on.

- [ ] **Step 5: Commit**

```bash
git add src/meridian/wizard/api.py
git commit -m "$(cat <<'EOF'
[scoped] alpha-24 backend: idempotency-key parser + registry skeleton

Adds the in-process _idempotency registry, UUIDv4 validator with structured
rejection log, and reads the Idempotency-Key header in setup_import_folder.
No behaviour change yet; a future commit adds the dedupe path.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task A2: Add the dedupe path (existing-record returns original `job_id`)

Goal: when an `Idempotency-Key` header is present and matches an unexpired record, return the original `job_id` without creating a new job.

**Files:**
- Create: `tests/e2e/test_alpha24_import_idempotency.py`
- Modify: `src/meridian/wizard/api.py` (`setup_import_folder`)

- [ ] **Step 1: Write the failing test file**

Create `tests/e2e/test_alpha24_import_idempotency.py`:

```python
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
```

- [ ] **Step 2: Run the test file — all four should fail**

```bash
python -m pytest tests/e2e/test_alpha24_import_idempotency.py -v
```

Expected: `test_replay_with_same_token_returns_original_job_id` FAILS (job_ids differ), `test_first_post_creates_job_and_records_token` FAILS (registry empty), `test_different_tokens_create_different_jobs` PASSES (distinct tokens always created distinct jobs even pre-fix), `test_no_token_header_creates_new_job_each_time` PASSES.

- [ ] **Step 3: Implement the dedupe branch in `setup_import_folder`**

In `src/meridian/wizard/api.py`, in `setup_import_folder` (which after Task A1 starts with `idempotency_key = _validate_idempotency_key(...)` then `folder = _validate_folder_path(...)`), insert the dedupe branch BEFORE the `walk_directory` + `_ImportJob` creation.

Find:

```python
    idempotency_key = _validate_idempotency_key(
        request.headers.get("Idempotency-Key")
    )
    folder = _validate_folder_path(req.folder_path)
    slug = _slugify(req.project_name)
```

Replace with:

```python
    idempotency_key = _validate_idempotency_key(
        request.headers.get("Idempotency-Key")
    )

    # Replay path — same token within TTL returns the original job_id.
    # Skip every side-effect (path validation, project creation, scan,
    # thread spawn): the original POST already did all of them.
    if idempotency_key is not None:
        existing = _idempotency_lookup(idempotency_key)
        if existing is not None:
            existing_job_id, age_seconds = existing
            _log.info(
                "wizard.import_folder.idempotent_replay",
                idempotency_token=idempotency_key,
                job_id=existing_job_id,
                age_seconds=round(age_seconds, 3),
            )
            return ImportJobResponse(job_id=existing_job_id)

    folder = _validate_folder_path(req.folder_path)
    slug = _slugify(req.project_name)
```

Then at the BOTTOM of the same function, just before `return ImportJobResponse(job_id=job.id)`, add the record step. Find:

```python
    thread = threading.Thread(
        target=_run_import_job,
        kwargs={"job": job, "db_path": db_path, "paths": paths},
        daemon=True,
        name=f"wizard-folder-import-{job.id[:8]}",
    )
    thread.start()
    return ImportJobResponse(job_id=job.id)
```

Replace with:

```python
    thread = threading.Thread(
        target=_run_import_job,
        kwargs={"job": job, "db_path": db_path, "paths": paths},
        daemon=True,
        name=f"wizard-folder-import-{job.id[:8]}",
    )
    thread.start()

    if idempotency_key is not None:
        _idempotency_record(idempotency_key, job.id)

    return ImportJobResponse(job_id=job.id)
```

- [ ] **Step 4: Run the test file — all four should pass**

```bash
python -m pytest tests/e2e/test_alpha24_import_idempotency.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Run the wider wizard suite to check for regressions**

```bash
python -m pytest tests/e2e/test_wizard_api.py tests/e2e/test_alpha22_*.py tests/e2e/test_alpha24_*.py -v
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/meridian/wizard/api.py tests/e2e/test_alpha24_import_idempotency.py
git commit -m "$(cat <<'EOF'
[scoped] alpha-24 backend: idempotent /import-folder dedupe path

Same-token replays now skip path validation + project creation + folder
walk + thread spawn; the original job_id is returned with a
'wizard.import_folder.idempotent_replay' structured log. Header-less
POSTs keep creating fresh jobs (backwards-compat). 4 new e2e tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task A3: TTL expiry

Goal: a record older than `_IDEMPOTENCY_TTL_SECONDS` (15 min) is treated as absent — the next POST with that token creates a new job.

**Files:**
- Modify: `tests/e2e/test_alpha24_import_idempotency.py` (append one test)
- (No `wizard/api.py` change — the lazy GC was wired in Task A1.)

- [ ] **Step 1: Add the failing test**

Append to `tests/e2e/test_alpha24_import_idempotency.py`:

```python
def test_token_ttl_expires_after_15_minutes(
    fastapi_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A record older than _IDEMPOTENCY_TTL_SECONDS is treated as absent."""
    folder = _seed_folder(tmp_path)
    body = {"folder_path": str(folder), "project_name": "alpha24-ttl"}

    # Pin time so we can fast-forward predictably. Patch the symbol the
    # wizard module reaches for, not time.monotonic globally — there are
    # other monotonic readers in the request path (uvicorn, FastAPI, etc.)
    # that would behave oddly under a global patch.
    from meridian.wizard import api as wizard_api

    fake_now = {"t": 100.0}

    def _fake_monotonic() -> float:
        return fake_now["t"]

    monkeypatch.setattr(wizard_api.time, "monotonic", _fake_monotonic)

    res1 = fastapi_client.post(
        "/setup/import-folder",
        json=body,
        headers={"Idempotency-Key": TOKEN_A},
    )
    assert res1.status_code == 200, res1.text
    job_id_first = res1.json()["job_id"]

    # Fast-forward past TTL.
    fake_now["t"] = 100.0 + wizard_api._IDEMPOTENCY_TTL_SECONDS + 1.0

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
    with wizard_api._idempotency_lock:
        # The new POST recorded a fresh entry — assert it's the new job_id,
        # not the stale one.
        recorded_job_id, _ = wizard_api._idempotency[TOKEN_A]
        assert recorded_job_id == res2.json()["job_id"]
```

- [ ] **Step 2: Run the new test — it should pass without further changes**

```bash
python -m pytest tests/e2e/test_alpha24_import_idempotency.py::test_token_ttl_expires_after_15_minutes -v
```

Expected: PASS. (The lazy GC was wired in Task A1; this test confirms it works.)

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_alpha24_import_idempotency.py
git commit -m "$(cat <<'EOF'
[scoped] alpha-24 backend: TTL test for idempotency registry

Pins the contract that records older than _IDEMPOTENCY_TTL_SECONDS are
treated as absent and pruned via the lazy GC pass during the next
lookup.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task A4: Malformed-token validation

Goal: the validation already raises (Task A1), but no test currently asserts it. Lock the 400 contract.

**Files:**
- Modify: `tests/e2e/test_alpha24_import_idempotency.py` (append one test)

- [ ] **Step 1: Add the test**

Append to `tests/e2e/test_alpha24_import_idempotency.py`:

```python
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
```

- [ ] **Step 2: Run it**

```bash
python -m pytest tests/e2e/test_alpha24_import_idempotency.py::test_malformed_token_returns_400_invalid_idempotency_key -v
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_alpha24_import_idempotency.py
git commit -m "$(cat <<'EOF'
[scoped] alpha-24 backend: lock 400 invalid_idempotency_key contract

Header-shape validator already raises; this test pins the contract
against the four most plausible misuse patterns (empty, plain string,
no-hyphens hex, too-short).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task A5: Reaped-job replay + structured-log assertion

Goal: cover the documented limit (replay returns recorded `job_id` even when the job has been reaped), and pin the structured-log event in `test_wizard_api.py`.

**Files:**
- Modify: `tests/e2e/test_alpha24_import_idempotency.py`
- Modify: `tests/e2e/test_wizard_api.py`

- [ ] **Step 1: Append the reaped-job test**

Append to `tests/e2e/test_alpha24_import_idempotency.py`:

```python
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
```

- [ ] **Step 2: Add the structured-log test to `test_wizard_api.py`**

Find the bottom of `tests/e2e/test_wizard_api.py` and append. The implementation monkeypatches the wizard module's structlog logger directly rather than going through `caplog` — structlog event surfacing through stdlib logging depends on the project's structlog config and we want this test to be robust to that:

```python
def test_idempotent_replay_emits_structured_log(
    fastapi_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The replay branch emits 'wizard.import_folder.idempotent_replay'
    once per replay, carrying the recorded job_id and a non-negative
    age_seconds field.
    """
    from meridian.wizard import api as wizard_api

    with wizard_api._idempotency_lock:
        wizard_api._idempotency.clear()
    with wizard_api._jobs_lock:
        wizard_api._jobs.clear()

    # Spy on the structlog logger directly. More robust than caplog —
    # we don't depend on whether structlog is configured to forward to
    # stdlib logging in the test environment.
    recorded: list[tuple[str, dict]] = []
    real_info = wizard_api._log.info

    def _spy_info(event: str, **kwargs):
        recorded.append((event, dict(kwargs)))
        return real_info(event, **kwargs)

    monkeypatch.setattr(wizard_api._log, "info", _spy_info)

    folder = tmp_path / "src"
    folder.mkdir()
    (folder / "doc.pdf").write_bytes(b"%PDF-1.4 test\n%%EOF\n")
    body = {"folder_path": str(folder), "project_name": "alpha24-log"}
    token = "11111111-1111-4111-8111-111111111111"

    res1 = fastapi_client.post(
        "/setup/import-folder",
        json=body,
        headers={"Idempotency-Key": token},
    )
    assert res1.status_code == 200
    expected_job_id = res1.json()["job_id"]

    # Reset the recorder so only events from the replay show up.
    recorded.clear()

    res2 = fastapi_client.post(
        "/setup/import-folder",
        json=body,
        headers={"Idempotency-Key": token},
    )
    assert res2.status_code == 200

    replay_events = [
        (event, kwargs)
        for event, kwargs in recorded
        if event == "wizard.import_folder.idempotent_replay"
    ]
    assert len(replay_events) == 1, (
        f"Expected exactly one replay event; got {len(replay_events)}. "
        f"All recorded events: {[e[0] for e in recorded]}"
    )
    _, kwargs = replay_events[0]
    assert kwargs["job_id"] == expected_job_id
    assert kwargs["idempotency_token"] == token
    assert kwargs["age_seconds"] >= 0
```

- [ ] **Step 3: Run both new tests**

```bash
python -m pytest tests/e2e/test_alpha24_import_idempotency.py::test_replay_after_job_reaped_returns_token_record_with_dead_job_id tests/e2e/test_wizard_api.py::test_idempotent_replay_emits_structured_log -v
```

Expected: 2 passed.

- [ ] **Step 4: Run the full backend e2e suite to confirm no regressions**

```bash
python -m pytest tests/e2e/ -v --ignore=tests/e2e/test_concurrency.py
```

Expected: 175 baseline + 7 new (test_alpha24_import_idempotency.py) + 1 new (test_wizard_api.py) = 183 passing minimum, 0 failed.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/test_alpha24_import_idempotency.py tests/e2e/test_wizard_api.py
git commit -m "$(cat <<'EOF'
[scoped] alpha-24 backend: reaped-job replay + structured-log assertion

Two new tests close out the backend coverage for alpha-24 item #4:
* token record outlives the job it points at (documented limit; replay
  returns recorded job_id and the subsequent poll 404s into the
  frontend's existing 'Lost contact' UX)
* structured-log event 'wizard.import_folder.idempotent_replay' fires
  exactly once on a replay path

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Stream B — Frontend

> **Parallelism note:** Stream B is file-disjoint from Stream A and can run in true parallel via subagent dispatch. The only ordering dependency is at integration time: Stream B sends an `Idempotency-Key` header, and Stream A must be able to honour it (Tasks A1 + A2). If running in parallel, hold integration test until both streams have landed.

### Task B1: `setupClient.ts` — `importFolder` accepts an idempotency key

Goal: signature change + header on the POST.

**Files:**
- Modify: `apps/web/src/lib/setupClient.ts` (`importFolder` near line 376)

- [ ] **Step 1: Update the function signature and body**

In `apps/web/src/lib/setupClient.ts`, find:

```typescript
  /**
   * Kick off a folder-import job. Returns a job id the wizard polls via
   * `folderImportStatus`. `project_name` becomes the project's display
   * name; the backend derives the slug.
   */
  importFolder(
    folderPath: string,
    projectName: string,
  ): Promise<ImportResponse> {
    return apiFetch<ImportResponse>("/setup/import-folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        folder_path: folderPath,
        project_name: projectName,
      }),
    });
  },
```

Replace with:

```typescript
  /**
   * Kick off a folder-import job. Returns a job id the wizard polls via
   * `folderImportStatus`. `project_name` becomes the project's display
   * name; the backend derives the slug.
   *
   * Alpha-24 item #4: pass a UUIDv4 `idempotencyKey` (one per user
   * click). Two POSTs with the same key within the server-side TTL
   * (15 minutes) return the same `job_id`. This closes the
   * double-submission class even when the page-level guards (L1+L2)
   * are bypassed by browser-layer retries.
   */
  importFolder(
    folderPath: string,
    projectName: string,
    idempotencyKey: string,
  ): Promise<ImportResponse> {
    return apiFetch<ImportResponse>("/setup/import-folder", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey,
      },
      body: JSON.stringify({
        folder_path: folderPath,
        project_name: projectName,
      }),
    });
  },
```

- [ ] **Step 2: Typecheck (if Node is installed)**

```bash
cd "apps/web"
npm run typecheck
```

Expected: typecheck FAILS at `app/setup/first-documents/page.tsx` because the existing call site `setupApi.importFolder(folderPath, projectName)` is now missing the third argument. This is expected and will be fixed in Task B3.

If Node isn't installed locally, this step is informational — Task B3 will fix the call site regardless.

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/lib/setupClient.ts
git commit -m "$(cat <<'EOF'
[scoped] alpha-24 frontend: setupApi.importFolder accepts Idempotency-Key

Adds a required idempotencyKey arg passed as the Idempotency-Key header.
The only call site (first-documents page) is updated in a sibling commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task B2: `ConfirmDialog` — title attribute on disabled confirm button

Goal: when `busy` is true, the confirm button surfaces a hover tooltip explaining why it's disabled. Applies to all `busy={true}` callers; no API change.

**Files:**
- Modify: `apps/web/src/components/review/ConfirmDialog.tsx` (button at line 88-95)

- [ ] **Step 1: Add the title attribute**

In `apps/web/src/components/review/ConfirmDialog.tsx`, find:

```tsx
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            className={`rounded-full px-4 py-2 text-sm font-medium ${confirmCls} disabled:opacity-50`}
          >
            {busy ? "Working…" : confirmLabel}
          </button>
```

Replace with:

```tsx
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            title={busy ? "Working — please wait" : undefined}
            className={`rounded-full px-4 py-2 text-sm font-medium ${confirmCls} disabled:opacity-50`}
          >
            {busy ? "Working…" : confirmLabel}
          </button>
```

- [ ] **Step 2: Typecheck (if Node is installed)**

```bash
cd "apps/web"
npm run typecheck
```

Expected: PASS. The `title` attribute is native to `<button>`; no signature change.

If Node isn't installed locally, this step is informational.

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/components/review/ConfirmDialog.tsx
git commit -m "$(cat <<'EOF'
[scoped] alpha-24 frontend: ConfirmDialog disabled-button hover tooltip

When busy=true the confirm button now surfaces a 'Working — please
wait' native tooltip via the title attribute. Augments the existing
visible 'Working…' label so the user gets two complementary signals
that the click is in-flight.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task B3: `first-documents/page.tsx` — `submitting` phase + UUID + busy plumbing

Goal: introduce a `submitting` phase between `scanned` and `importing`; generate a UUID per click and hand it to `importFolder`; pass `busy` to `ConfirmDialog`.

**Files:**
- Modify: `apps/web/src/app/setup/first-documents/page.tsx` (Phase union ~line 66; `triggerImport` ~line 380; `isBusy` derivation ~line 494; `ConfirmDialog` invocation ~line 1018)

- [ ] **Step 1: Extend the `Phase` union**

Find (around lines 66-77):

```typescript
type Phase =
  | { kind: "idle" }
  | { kind: "browser_path_prompt"; folderName: string | null }
  | { kind: "scanning"; folderPath: string }
  | { kind: "scanned"; manifest: FolderScanResponse }
  | { kind: "scan_invalid"; folderPath: string; message: string }
  | { kind: "scan_unable"; folderPath: string; message: string }
  | { kind: "importing"; jobId: string; status: FolderImportJobStatus | null }
  | { kind: "imported"; status: FolderImportJobStatus }
  | { kind: "partial"; status: FolderImportJobStatus }
  | { kind: "failed"; status: FolderImportJobStatus | null; message: string }
  | { kind: "skipped" };
```

Replace with:

```typescript
type Phase =
  | { kind: "idle" }
  | { kind: "browser_path_prompt"; folderName: string | null }
  | { kind: "scanning"; folderPath: string }
  | { kind: "scanned"; manifest: FolderScanResponse }
  | { kind: "scan_invalid"; folderPath: string; message: string }
  | { kind: "scan_unable"; folderPath: string; message: string }
  // Alpha-24 item #4: structural guard against double-submit. While
  // triggerImport's POST is in flight, phase is "submitting" — the L1
  // guard in triggerImport refuses re-entry, and the ConfirmDialog
  // disables its confirm button via the busy prop (L2). The manifest
  // is preserved so a failed POST can transition cleanly back to
  // "scanned".
  | { kind: "submitting"; manifest: FolderScanResponse }
  | { kind: "importing"; jobId: string; status: FolderImportJobStatus | null }
  | { kind: "imported"; status: FolderImportJobStatus }
  | { kind: "partial"; status: FolderImportJobStatus }
  | { kind: "failed"; status: FolderImportJobStatus | null; message: string }
  | { kind: "skipped" };
```

- [ ] **Step 2: Update `triggerImport` to enter `submitting` and pass an idempotency key**

Find (around lines 380-470):

```typescript
  const triggerImport = useCallback(async () => {
    if (phase.kind !== "scanned") return;
    setConfirmImport(false);
    const folderPath = phase.manifest.folder_path;
    // Project name comes from the folder name; the next page lets the
    // user edit it before /setup/projects is called. The folder-import
    // backend (Stream A) creates the project itself using this name.
    const projectName = phase.manifest.folder_name;
    try {
      const res = await setupApi.importFolder(folderPath, projectName);
```

Replace with:

```typescript
  const triggerImport = useCallback(async () => {
    if (phase.kind !== "scanned") return;
    const manifest = phase.manifest;
    const folderPath = manifest.folder_path;
    // Project name comes from the folder name; the next page lets the
    // user edit it before /setup/projects is called. The folder-import
    // backend (Stream A) creates the project itself using this name.
    const projectName = manifest.folder_name;

    // Alpha-24 item #4: flip phase BEFORE awaiting the POST so any
    // re-entrant call (dialog double-click, focus regain, browser
    // auto-resubmit) hits the L1 guard above and returns. The
    // idempotency key is generated at the moment of submission, not on
    // dialog open — a deliberate retry after a failed submit produces
    // a fresh UUID and the server treats it as a new request.
    const idempotencyKey = crypto.randomUUID();
    setPhase({ kind: "submitting", manifest });
    setConfirmImport(false);

    try {
      const res = await setupApi.importFolder(
        folderPath,
        projectName,
        idempotencyKey,
      );
```

Then find the catch block at the bottom of the same function (around line 462-469):

```typescript
    } catch (err) {
      setPhase({
        kind: "failed",
        status: null,
        message:
          err instanceof Error ? err.message : "Could not start the import.",
      });
    }
  }, [phase]);
```

Replace with:

```typescript
    } catch (err) {
      // L1 fallback: return to "scanned" so a deliberate retry gets a
      // fresh UUID and a fresh POST. (The "failed" phase is reserved
      // for terminal errors AFTER a job started — pre-job network /
      // 5xx errors should let the user re-submit without re-picking
      // the folder.)
      setPhase({ kind: "scanned", manifest });
      // Surface the error in a way the existing UI can render. The
      // simplest path: alert via the picker error panel.
      setPickerError(
        err instanceof Error
          ? `Could not start the import: ${err.message}`
          : "Could not start the import — please try again.",
      );
    }
  }, [phase]);
```

- [ ] **Step 3: Extend `isBusy`**

Find (around line 494):

```typescript
  const isBusy =
    phase.kind === "scanning" || phase.kind === "importing";
```

Replace with:

```typescript
  const isBusy =
    phase.kind === "scanning" ||
    phase.kind === "submitting" ||
    phase.kind === "importing";
```

- [ ] **Step 4: Plumb `busy` to the `ConfirmDialog`**

Find (around line 1017-1026):

```tsx
      <ConfirmDialog
        open={confirmImport}
        title={FIRST_DOCS_COPY.confirmImportDialog.title}
        body={FIRST_DOCS_COPY.confirmImportDialog.body}
        confirmLabel={FIRST_DOCS_COPY.confirmImportDialog.confirm}
        cancelLabel={FIRST_DOCS_COPY.confirmImportDialog.cancel}
        destructive={false}
        onConfirm={() => void triggerImport()}
        onCancel={() => setConfirmImport(false)}
      />
```

Replace with:

```tsx
      <ConfirmDialog
        open={confirmImport}
        busy={phase.kind === "submitting"}
        title={FIRST_DOCS_COPY.confirmImportDialog.title}
        body={FIRST_DOCS_COPY.confirmImportDialog.body}
        confirmLabel={FIRST_DOCS_COPY.confirmImportDialog.confirm}
        cancelLabel={FIRST_DOCS_COPY.confirmImportDialog.cancel}
        destructive={false}
        onConfirm={() => void triggerImport()}
        onCancel={() => setConfirmImport(false)}
      />
```

- [ ] **Step 5: Typecheck and lint (if Node is installed)**

```bash
cd "apps/web"
npm run typecheck
npm run lint
```

Expected: PASS. (The new `submitting` variant is handled in `triggerImport` and `isBusy`; no other branch of the switch-style rendering currently inspects `phase.kind` for `submitting` — the `idle/scan_*` rendering arms simply don't match, which is correct: the page renders nothing new during submit, the `ConfirmDialog`'s `busy` state covers the user-visible feedback.)

If Node isn't installed locally, skip this step — Stream A's tests + the gauntlet step in Stream C are the gate.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/app/setup/first-documents/page.tsx
git commit -m "$(cat <<'EOF'
[scoped] alpha-24 frontend: submitting phase + per-click idempotency UUID

Adds the L1 phase-machine guard to first-documents/page.tsx:
* New "submitting" Phase variant; triggerImport flips into it BEFORE
  awaiting the POST so re-entrant calls hit the existing
  `if (phase.kind !== "scanned") return` guard.
* crypto.randomUUID() generated per click and passed as the
  Idempotency-Key argument to setupApi.importFolder.
* ConfirmDialog now receives busy={phase.kind === "submitting"} (L2):
  the disabled confirm button shows "Working…" + a hover tooltip.
* Failed POSTs revert to "scanned" so deliberate retries work; the
  picker-error panel surfaces the underlying message.

Closes the structural openings for alpha-22 item #4 in concert with the
Stream A backend Idempotency-Key dedupe.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Stream C — Release tooling + log-volume regression

> **Ordering note:** Stream C depends on Stream A. Do not start until Tasks A1–A5 are committed and the e2e suite is green.

### Task C1: Release gauntlet step 7i — parallel-POST dedupe

Goal: a wheel-installed backend, hit twice in parallel with the same idempotency token, must produce one job. Catches wiring drift at the wheel level.

**Files:**
- Modify: `scripts/release_gauntlet.py`

- [ ] **Step 1: Locate the existing step 7h**

Open `scripts/release_gauntlet.py`. The pattern is: each step is a function `step_<N><letter>(...)` that returns nothing on success and calls `_fail()` + raises on failure. Find the existing `step_7h_*` function (search for `def step_7h`).

- [ ] **Step 2: Add step 7i**

Append a new function and call site mirroring the existing 7h pattern. Below the last `step_7*` function (and before `def step_8_*`), add:

```python
def step_7i_idempotency_dedupes_parallel_posts(base_url: str) -> None:
    """Step 7i — alpha-24 item #4: same Idempotency-Key + parallel POSTs == one job.

    Spawns two threads, each posting /setup/import-folder with the same
    UUIDv4 token to a tmp folder containing one .pdf. Both responses must
    carry the same job_id; the post-state /setup/runtime probe must
    report at most one folder-import job created during the window.

    Catches wiring drift at the wheel level (analogous to step 2b's
    static check on installer URL constants for the alpha-5 IPv6 bug).
    """
    import concurrent.futures
    import json as _json

    _info("step 7i — alpha-24 idempotency dedupe under parallel POST")

    with tempfile.TemporaryDirectory() as td:
        folder = Path(td) / "src"
        folder.mkdir()
        (folder / "doc.pdf").write_bytes(b"%PDF-1.4 gauntlet 7i\n%%EOF\n")

        token = "11111111-1111-4111-8111-111111111111"
        body = _json.dumps({
            "folder_path": str(folder),
            "project_name": "gauntlet-7i",
        }).encode("utf-8")

        def _post() -> str:
            req = urllib.request.Request(
                f"{base_url}/setup/import-folder",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Idempotency-Key": token,
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                payload = _json.loads(resp.read().decode("utf-8"))
                return payload["job_id"]

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            f1 = ex.submit(_post)
            f2 = ex.submit(_post)
            job_id_1 = f1.result()
            job_id_2 = f2.result()

        if job_id_1 != job_id_2:
            _fail(
                "7i",
                f"parallel POSTs with same Idempotency-Key returned different "
                f"job_ids: {job_id_1!r} vs {job_id_2!r}. Backend dedupe is "
                "not wired or the registry is leaking.",
            )
            raise SystemExit(1)
        _ok("7i", f"both POSTs returned job_id={job_id_1[:8]}…")
```

Then find the `main()` function (search for `def main`) and add a call to the new step in the right place — after `step_7h_*` and before `step_8_*`. The exact form of the call site mirrors the surrounding ones; e.g. if the existing pattern is:

```python
    step_7h_env_file_loader_contract(base_url=...)
    step_8_cli_help_smoke(...)
```

Insert:

```python
    step_7h_env_file_loader_contract(base_url=...)
    step_7i_idempotency_dedupes_parallel_posts(base_url=...)
    step_8_cli_help_smoke(...)
```

(If the existing call signature differs, match it exactly — the gauntlet's step calls thread `base_url` consistently.)

- [ ] **Step 3: Run the gauntlet locally to verify the new step**

```bash
uv run python scripts/release_gauntlet.py
```

Expected: every step including the new 7i prints `[ OK  ]`. Total runtime ~50-90s.

- [ ] **Step 4: Commit**

```bash
git add scripts/release_gauntlet.py
git commit -m "$(cat <<'EOF'
[scoped] alpha-24 release: gauntlet step 7i — parallel-POST dedupe

Spawns two threads against /setup/import-folder with the same
Idempotency-Key on the wheel-installed backend. Both responses must
carry the same job_id. Catches future wiring drift on alpha-24's
backend dedupe at the wheel level (same posture as step 2b's static
check for the alpha-5 IPv6 regression class).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task C2: Log-volume regression test

Goal: a folder-import e2e walk of N files emits exactly N `import_job.file_done` events under simulated double-submit pressure (the symptom of alpha-22 item #4 was 694/347).

**Files:**
- Create: `tests/e2e/test_alpha24_log_volume.py`

- [ ] **Step 1: Write the new test file**

Create `tests/e2e/test_alpha24_log_volume.py`:

```python
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
"""
from __future__ import annotations

import concurrent.futures
import logging
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
        else:
            pytest.fail(
                "import job did not reach a terminal state within deadline"
            )

    file_done_events = [
        r for r in caplog.records
        if "import_job.file_done" in r.getMessage() and job_id in r.getMessage()
    ]
    assert len(file_done_events) == n_files, (
        f"Expected exactly {n_files} import_job.file_done events for "
        f"job_id {job_id[:8]}…; got {len(file_done_events)}. "
        "Pre-fix the second POST would have created a second job and "
        "doubled the event count. This test pins the post-fix invariant."
    )
```

- [ ] **Step 2: Run the test**

```bash
python -m pytest tests/e2e/test_alpha24_log_volume.py -v
```

Expected: PASS. (5 import_job.file_done events for 5 files; the dedupe path means the second concurrent POST does not spawn a second worker.)

- [ ] **Step 3: Run the full e2e suite to confirm no regressions**

```bash
python -m pytest tests/e2e/ -v --ignore=tests/e2e/test_concurrency.py
```

Expected: 175 baseline + 7 from test_alpha24_import_idempotency.py + 1 from test_wizard_api.py + 1 from test_alpha24_log_volume.py = 184+ passing.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/test_alpha24_log_volume.py
git commit -m "$(cat <<'EOF'
[scoped] alpha-24 release: log-volume regression test

Pins the post-fix invariant: N files imported under simulated
double-submit pressure (two concurrent POSTs with the same
Idempotency-Key) emit exactly N import_job.file_done events, not 2N.
This is the symptom-level test for alpha-22 punch-list item #4 (the
SME's 2026-05-02 round saw 694/347).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Final integration

After all three streams have landed, run the full gauntlet + e2e from a clean tree to confirm:

- [ ] **Final step 1: Full e2e suite**

```bash
python -m pytest tests/e2e/ -v --ignore=tests/e2e/test_concurrency.py
```

Expected: 184+ passed, 2 skipped (the alpha-22 baseline skips), 0 failed.

- [ ] **Final step 2: Release gauntlet**

```bash
uv run python scripts/release_gauntlet.py
```

Expected: every step `[ OK  ]`, including 7i.

- [ ] **Final step 3: Manual verification (matches §9 of the spec)**

Spin up the wizard with the bundled wheel; pick a folder; double-click the import-confirm button as fast as possible. Confirm:
- Only one progress bar appears.
- `backend.log` shows exactly N (not 2N) `import_job.file_done` lines for the N files.
- The post-import success panel reads correctly.

- [ ] **Final step 4: Tag candidate**

If everything green:

```bash
git log --oneline main..HEAD
```

Expected: ~10 commits (5 backend + 3 frontend + 2 release-tooling). Ready for `v0.2.0-alpha.24` cut after the rest of the alpha-24 punch list lands.
