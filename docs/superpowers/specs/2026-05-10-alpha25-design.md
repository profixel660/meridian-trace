# Alpha-25 — Auto-trigger pipeline + dashboard progress tile (keystone) + small wins

> Scope: closes punch-list item #3 (auto-trigger bootstrap+extract from the wizard) — the SME's "buttonless dashboard" wall — plus three additive items that share dashboard or export surfaces. Quarantine taxonomy work, `--isolated` IPC, Ctrl+C cancel, and CLI/wizard data-dir consolidation are explicitly out of scope.

## 1. Problem

Alpha-24 ships the SME cleanly through the wizard onto a project dashboard for the first time. The dashboard then gives her **no affordance to advance the pipeline**: 4 sources imported, 0 extracted, no button. She has 4 of the dashboard's KPI tiles + an amber `BaselineBanner` saying "needs review" with a 0/0 blocker list, and the only path forward (CLI `meridian extract`) is invisible to her workflow.

Two related grievances surface at the same time:

- The master-register Excel emit produces a `flags` column of opaque tokens like `conflicts_with_source_<uuid>` even though the conflict-pass LLM (alpha-7/11) already wrote a high-quality plain-English `most_onerous_reasoning` paragraph that's denormalised onto the deliverable's `flag_context` JSON. PMs read the export; the explanation is sitting unread in the column next door.
- Header navigation: the top "Project" button in the dashboard chrome restarts the setup wizard instead of opening the project dashboard (02/05 punch-list carryover).

## 2. Goal

After alpha-25 ships:

- The SME imports a folder, presses Continue once, and the dashboard polls a real extraction tile to completion without further input. No invisible CLI step.
- The Excel master register surfaces conflict reasoning verbatim in its own column.
- The "needs review" banner stops firing on sources-only projects.
- The header "Project" button opens the dashboard.

## 3. Scope

**In scope:**

1. **Keystone (§4–6):** new server-orchestrated pipeline (bootstrap → extract) with a job-poll endpoint; wizard handoff that fires it; dashboard progress tile that surfaces phase + per-source progress + the current source filename verbatim.
2. **BaselineBanner suppression (§7):** redefine `is_data_present` to require deliverables or LLM-call activity, not just imported sources.
3. **Header "Project" button fix (§8):** route to `/projects/<slug>` instead of `/setup`.
4. **Master Excel `conflict_summary` column (§9):** newline-joined `[<conflict.kind>] <most_onerous_reasoning>` per row, pending+superseded conflicts only, verbatim.

**Out of scope:**

- Quarantine taxonomy add-new flow (strict vs permissive — design question deferred).
- Cancel button / Ctrl+C cancel for in-flight extract.
- `--isolated` extract child-process IPC.
- CLI/wizard data-dir consolidation.
- Backend-restart resume of in-flight pipeline jobs (best-effort: in-memory registry dies; existing `last_extraction_at`/coverage signals carry the user through).
- Frontend test infra (vitest etc.) — alpha-10 deferral stands.
- Schema migration. None expected. If we find ourselves needing one, stop and reconsider.

## 4. Architecture overview

```
                    setup/ready                  /projects/<slug>
   wizard ──────► (POST /pipeline) ──────► dashboard ──┐
                       │                                │ poll 1500 ms
                       ▼                                │
                   _pipeline_jobs ◄──── GET /pipeline/{id}
                       │
                ┌──────┴──────┐
                ▼             ▼
            bootstrap       extract (run_job_over_sources)
            (advisory)      (canonical, holds project_lock)
```

Two new HTTP endpoints (§5), one new daemon-thread worker (§5), one new frontend tile component (§6), one frontend trigger point on the wizard's ready page (§6), one in-memory registry mirroring the alpha-24 wizard `_jobs` shape.

**Why a server-orchestrated pipeline endpoint and not the frontend chaining `/bootstrap` then `/extract`:**

- Bootstrap soft-fail handling (§5.3) lives next to the bootstrap call, not in two places.
- The frontend has one job to poll, not two; `phase` advances atomically.
- `acquire_project_lock` already wraps `run_job_over_sources`; orchestration layer can centralise the 409 surface.

**Why a new endpoint vs. extending `/extract`:**

- `POST /api/projects/{name}/extract` today is **synchronous** (no thread spawn — calling it blocks until every source finishes). Repurposing it would change the contract for the existing CLI consumer (`meridian extract`) and the alpha-12 e2e tests. Adding a sibling `/pipeline` endpoint preserves the synchronous extract for headless/CLI use and gives the GUI an honest async surface.

## 5. Backend

### 5.1 Endpoints (in `meridian.api.main` `_projects_api`)

```python
class PipelineRequest(BaseModel):
    sample_size: int = 15            # forwarded to bootstrap
    provider: str | None = None
    model: str | None = None

class PipelineResponse(BaseModel):
    job_id: str

class PipelineStatusResponse(BaseModel):
    job_id: str
    phase: Literal["bootstrap", "extract", "done", "failed"]
    bootstrap_status: Literal["pending", "running", "succeeded", "failed", "skipped"]
    extract_total: int               # 0 until extract phase begins
    extract_completed: int
    current_source_filename: str | None
    started_at: str                  # ISO-8601 UTC
    finished_at: str | None
    error_message: str | None        # populated when phase == "failed"
    holder_pid: int | None           # populated on 409→retry surface
```

```
POST  /api/projects/{name}/pipeline       → PipelineResponse  (200 / 404 / 409 / 400)
GET   /api/projects/{name}/pipeline/{id}  → PipelineStatusResponse  (200 / 404)
```

POST accepts an optional `Idempotency-Key` UUIDv4 header using the same registry pattern alpha-24 just landed for `/setup/import-folder` (§5.4).

### 5.2 Worker module

New module `src/meridian/workers/pipeline_worker.py`:

```python
@dataclass
class _PipelineJob:
    id: str
    db_path: Path
    phase: str = "pending"
    bootstrap_status: str = "pending"
    extract_total: int = 0
    extract_completed: int = 0
    current_source_filename: str | None = None
    started_at: str = field(default_factory=_iso_now)
    finished_at: str | None = None
    error_message: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

_pipeline_jobs: dict[str, _PipelineJob] = {}
_pipeline_jobs_lock = threading.Lock()
```

Worker body (single thread per job, daemon=True):

```python
def _run_pipeline(job: _PipelineJob, *, sample_size, provider, model):
    conn = connect(job.db_path, busy_timeout_ms=30000)
    try:
        # Phase 1 — bootstrap (advisory, soft-fail)
        with job._lock:
            job.phase = "bootstrap"
            job.bootstrap_status = "running"
        try:
            run_bootstrap_sweep(conn, sample_size=sample_size,
                                provider=provider, model=model)
            with job._lock:
                job.bootstrap_status = "succeeded"
        except Exception as exc:
            _log.warning("pipeline.bootstrap_soft_failed", job_id=job.id, error=str(exc))
            with job._lock:
                job.bootstrap_status = "failed"
            # fall through — do NOT abort the pipeline

        # Phase 2 — extract
        with job._lock:
            job.phase = "extract"
        source_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM source_document ORDER BY imported_at"
        ).fetchall()]
        with job._lock:
            job.extract_total = len(source_ids)
        # Per-source progress: run_job_over_sources iterates internally; we
        # use a callback hook (see §5.5) to bump extract_completed +
        # current_source_filename as each finishes. If hook isn't trivial to
        # add, fallback: read extraction_job_source rows on each GET.
        run_job_over_sources(
            conn, source_ids=source_ids, provider=provider, model=model,
            on_source_complete=lambda src: _bump(job, src),
        )
        with job._lock:
            job.phase = "done"
            job.finished_at = _iso_now()
            job.current_source_filename = None
    except ProjectBusy:
        # Caller already handled the 409 path on the POST (§5.4); a busy that
        # appears mid-run here is unexpected — surface as failed.
        raise
    except BaseException as exc:
        with job._lock:
            job.phase = "failed"
            job.finished_at = _iso_now()
            job.error_message = f"{type(exc).__name__}: {exc}"
        _log.exception("pipeline.failed", job_id=job.id)
    finally:
        conn.close()
```

### 5.3 Bootstrap soft-fail

Bootstrap proposals are advisory — extraction runs without them (the per-deliverable taxonomy proposal pass during extract is the canonical seeding). A bootstrap failure (LLM timeout, malformed JSON, no sources to sample, etc.) is logged, marked `bootstrap_status=failed`, and the worker proceeds to the extract phase. The dashboard tile surfaces "Vocabulary classification skipped — proceeding to extraction" without alarm.

### 5.4 POST handler — idempotency + `ProjectBusy`

```python
@_projects_api.post("/projects/{name}/pipeline", response_model=PipelineResponse)
def projects_pipeline_run(
    name: str, req: PipelineRequest, request: Request
) -> PipelineResponse:
    db_path = _ensure_project(name)
    idem_key = _validate_idempotency_key(request.headers.get("Idempotency-Key"))

    if idem_key is not None:
        existing = _idempotency_lookup(idem_key)   # shared registry shape
        if existing is not None:
            return PipelineResponse(job_id=existing[0])

    candidate_id = str(uuid.uuid4())
    if idem_key is not None:
        winner = _idempotency_claim(idem_key, candidate_id)
        if winner != candidate_id:
            return PipelineResponse(job_id=winner)

    # 409 surface for an in-flight extract on this project. We do this
    # opportunistically by inspecting the project_lock table directly
    # rather than racing into run_job_over_sources from the main thread —
    # the latter would still raise ProjectBusy but only after the worker
    # had been spawned.
    if _project_locked(db_path):
        raise HTTPException(status_code=409, detail={"error": "project_busy", ...})

    job = _PipelineJob(id=candidate_id, db_path=db_path)
    with _pipeline_jobs_lock:
        _pipeline_jobs[job.id] = job
    threading.Thread(
        target=_run_pipeline,
        kwargs={"job": job, "sample_size": req.sample_size,
                "provider": req.provider, "model": req.model},
        daemon=True,
        name=f"pipeline-{job.id[:8]}",
    ).start()
    return PipelineResponse(job_id=job.id)
```

The idempotency-key validator + lookup + claim functions are reused from `meridian.wizard.api` — refactor those into a small shared module `meridian.api.idempotency` so they're not duplicated. Same UUIDv4 regex, same TTL (15 min), same lazy GC.

### 5.5 Per-source completion hook

`run_job_over_sources` in `meridian.extract.orchestrator` already iterates sources internally. Add an optional `on_source_complete: Callable[[SourceResult], None] | None = None` parameter; call after each source's row is committed. The pipeline worker passes a closure that bumps `job.extract_completed` and updates `current_source_filename` to the next pending source. Existing CLI/test callers pass `None` and behaviour is unchanged.

If adding the hook is non-trivial (the orchestrator is more layered than expected), fall back to **reading `extraction_job_source` table rows on each GET**: count where `finished_at IS NOT NULL` for the job's `extraction_job_id` (stamped on the `_PipelineJob` once extract begins). Costs one extra small query per poll; acceptable.

## 6. Frontend

### 6.1 Trigger point — wizard ready page

`apps/web/src/app/setup/ready/page.tsx`: after the existing `POST /setup/complete` lands, before navigating to `/projects/<slug>`:

```ts
const idempotencyKey = crypto.randomUUID();
try {
  const { job_id } = await api.startPipeline(slug, { idempotencyKey });
  window.sessionStorage.setItem("meridian.setup.pipeline_job_id", job_id);
} catch (err) {
  // Silent on 409/network — dashboard polling will fall back to the
  // last_extraction_at signal. Do NOT block the navigation; the SME
  // already finished the wizard.
  console.warn("[setup/ready] pipeline kick-off failed", err);
}
router.push(`/projects/${slug}`);
```

Trigger-on-page-load (not on a button press) is consistent with the existing wizard pattern (e.g. `/setup/complete` already fires from `useEffect`). The user already opted in by completing the wizard.

### 6.2 New `setupClient.startPipeline` + `pipelineStatus`

Two thin client wrappers in `apps/web/src/lib/setupClient.ts` over `apiFetch`. Type definitions mirror §5.1.

### 6.3 Dashboard progress tile

New component `apps/web/src/components/dashboard/PipelineProgressTile.tsx`. Mounted in `apps/web/src/app/projects/[name]/page.tsx`'s `DashboardBody` directly above `BaselineBanner`. Visible only while `phase !== "done"`.

Job-id pickup priority:

1. `sessionStorage.getItem("meridian.setup.pipeline_job_id")` — happy path right after the wizard.
2. Fallback when sessionStorage is absent (page refresh, app restart): if `coverage.last_extraction_at == null && coverage.sources_imported > 0`, query a thin **GET `/api/projects/{name}/pipeline`** index endpoint (returns the most-recent unfinished job for this project, or 404). Cheap; gates off the same registry.

Polling cadence: 1500 ms; matches `/setup/import-folder` poll. Stops on terminal phase or component unmount.

Render states (matching §5.1 phase enum):

```
phase=bootstrap                 "Classifying your project's vocabulary…" — single-line indeterminate
phase=extract                   "Extracting deliverables — N of M"
                                progress bar (extract_completed / extract_total)
                                current_source_filename verbatim, font-mono, truncate
phase=failed                    red panel; error_message verbatim; "Try again" → re-POST /pipeline
phase=done                      tile self-removes; refetches /coverage; brief 3 s green toast
                                "Extraction complete — N deliverables on the master register"
                                (N comes from the refreshed coverage payload)
```

While `phase !== "done"`, the four KPI tiles render with reduced opacity (0.5) and the welcome panel (the `!is_data_present` branch) does NOT render — the tile is the only signal the user needs.

Per project memory feedback ("surface LLM reasoning verbatim"): the `error_message` and `current_source_filename` strings render unchanged. No paraphrase, no truncation beyond CSS.

### 6.4 Idempotency-Key plumbing on the new endpoint

`startPipeline(slug, { idempotencyKey })` adds the header in `apiFetch` exactly the way `setupApi.importFolder` does today (alpha-24). No new client machinery.

## 7. BaselineBanner suppression

`src/meridian/coverage/dashboard.py:398-403`:

Today: `total_data_signals = sources_imported + status.total + cost.total_calls`.

Change: drop `sources_imported`. New: `total_data_signals = status.total + cost.total_calls`.

Rationale: a project with sources but no deliverables and no LLM calls is genuinely "no opinion yet" from the coverage layer's point of view. The dashboard's `!is_data_present` branch already renders the welcome panel; pre-extract projects fall to that branch and the keystone tile (§6.3) takes precedence visually.

Tests: update `tests/coverage/test_dashboard.py` (or equivalent) — the existing case "1 source, 0 deliverables → is_data_present=True" flips to False; a new case "1 source, 1 deliverable → is_data_present=True" guards against accidental over-suppression.

## 8. Top "Project" button restart fix

Locate during implementation: most likely `apps/web/src/components/review/ReviewLayout.tsx` or a header child. Currently `href="/setup"` (or equivalent route). Change to `href={\`/projects/${projectSlug}\`}`. Single-line change once located.

## 9. Master Excel `conflict_summary` column

`src/meridian/export/excel.py`:

- Extend `_MASTER_COLUMNS`: insert `"conflict_summary"` between `"flags"` and `"deliverables_summary"`.
- Extend `_select_master`: add `d.flag_context` to the SELECT.
- New helper:

```python
def _format_conflict_summary(
    conn: sqlite3.Connection, flag_context_raw: str | None
) -> str:
    if not flag_context_raw:
        return ""
    try:
        ctx = json.loads(flag_context_raw)
    except json.JSONDecodeError:
        return ""
    conflict_ids = [
        v["conflict_id"] for v in ctx.values()
        if isinstance(v, dict) and v.get("conflict_id")
    ]
    if not conflict_ids:
        return ""
    rows = conn.execute(
        "SELECT kind, status, most_onerous_reasoning FROM conflict "
        f"WHERE id IN ({','.join('?'*len(conflict_ids))}) "
        "AND status IN ('pending','superseded')",
        conflict_ids,
    ).fetchall()
    return "\n".join(
        f"[{r['kind']}] {r['most_onerous_reasoning']}"
        for r in rows
    )
```

- `_write_master_sheet`: pass the new value into the row, set the cell's `Alignment(wrap_text=True)`, set column width 60.
- Verbatim posture: `most_onerous_reasoning` lands unchanged; only the `[<kind>]` prefix is added as structural context (per the LLM-text-verbatim feedback memory).

If `meridian.tender.builder._humanise_flag` already produces the same shape, reuse it instead of duplicating; if shapes diverge, keep the helper emitter-local (the tender renderer's audience is different).

Empirical: 16/323 rows on syd2-shell-cd have ≥1 conflict, max 4 per row → newline-joined single cell is sufficient. No schema change.

## 10. Tests

### 10.1 Backend (pytest, FastAPI TestClient + `mock_llm_client` fixture)

`tests/e2e/test_pipeline_e2e.py` (new):

- Happy path: 2-source folder import → `/pipeline` → poll until `phase=done` → assert `coverage.deliverable_status.total > 0` + `is_data_present=True`.
- Bootstrap soft-fail: monkeypatch `run_bootstrap_sweep` to raise; assert pipeline still ends `phase=done`, `bootstrap_status=failed`, deliverables present.
- Idempotency: parallel POSTs with same `Idempotency-Key` → exactly one job_id returned across both responses; only one worker thread observed (count `_pipeline_jobs` entries).
- 409 on busy: start one pipeline, attempt second pipeline while first mid-extract → second returns 409 with `holder_pid` populated.
- Pipeline status 404: GET on a stale/unknown id returns 404 with the standard "Job not found" detail.

`tests/coverage/test_dashboard.py` (extend):

- `is_data_present=False` for sources-only project (regression).
- `is_data_present=True` when 1 source + 1 deliverable.

`tests/export/test_excel.py` (extend):

- Project with 3 deliverables: 1 with no flag_context, 1 with a pending conflict, 1 with a resolved conflict. Assert `conflict_summary` column has empty / verbatim-pending / empty (resolved hidden) — and pending row's text equals `most_onerous_reasoning` byte-for-byte.

### 10.2 Frontend

No new infra. Manual gauntlet step 7j replaces it (§10.3).

### 10.3 Gauntlet

`scripts/release_gauntlet.py` — new step 7j:

1. Run installer.
2. Open Tauri.
3. Drive wizard end-to-end with a 2-PDF fixture folder.
4. Land on dashboard.
5. Wait until pipeline tile reports `phase=done` (poll `last_extraction_at` via `/api/projects/<slug>/coverage`; cap timeout 4 min).
6. Assert `coverage.deliverable_status.total > 0`.
7. Open `/api/projects/<slug>/export.xlsx`, assert the workbook has a `conflict_summary` column header.
8. Tear down.

## 11. Risks

- **Hook into `run_job_over_sources`** — if adding `on_source_complete` is more layered than expected, the §5.5 fallback (read `extraction_job_source` rows on each GET) is the no-API-change path. Low risk; either path lands.
- **Backend death mid-extract** — in-memory `_pipeline_jobs` is gone on next start. Tile falls back to coverage signals; user sees `last_extraction_at` populated and no banner. Acceptable for alpha-25; durable resume is a separate concern.
- **Two browser tabs** — academic per project memory. The idempotency-key path means duplicate POSTs from two tabs return the same job_id; both polls converge.
- **Bootstrap soft-fail noise** — if bootstrap consistently fails for a real reason (key invalid, model unavailable), the user sees `bootstrap_status=failed` repeatedly without context. Mitigation: log the underlying exception with full detail at backend.log; the tile's failure copy points users to the operator-visible log.

## 12. Open questions / explicitly deferred

- Cancel: Ctrl+C / "Stop extraction" button. Punch-list item; defer.
- Quarantine taxonomy add-new (strict vs permissive): defer; locking now would presume Quarantine work in alpha-26+, which itself isn't sequenced.
- `--isolated` extract IPC: defer.
- CLI/wizard data-dir consolidation: defer.

## 13. Convention compliance

- Commits: `[scoped] alpha-25 <stream>: <subject>` with `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer.
- Skill order: brainstorming → writing-plans → subagent-driven-development → finishing-a-development-branch. Serial dispatch only — no parallel implementers.
- LLM-text fields render verbatim wherever they reach a user surface (`most_onerous_reasoning`, `current_source_filename`, `error_message`).
- No new test infra; pytest + TestClient + `mock_llm_client` is the gate for backend; gauntlet step 7j is the gate for frontend e2e.
