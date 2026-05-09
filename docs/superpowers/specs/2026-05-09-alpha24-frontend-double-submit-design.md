# Alpha-24 — Frontend double-submit elimination (item #4)

> Scope: contained warm-up before the alpha-24 keystone (auto-trigger bootstrap+extract from the wizard, item #3). This spec covers item #4 only.

## 1. Problem

The 2026-05-02 SME testing round produced 694 `ingest.start` events for 347 distinct files — i.e. every file's ingest path was kicked off twice. Alpha-23's `IntegrityError` race-recovery in `ingest_file` neutered the user-visible failure mode (the loser dedupes cleanly instead of surfacing as "127 files failed for an unclassified reason"), but log volume + LLM cost still doubled and the operator's mental model ("each file uploaded once") is still wrong.

Symptom: two concurrent `POST /api/setup/import-folder` requests with the same path list, producing two worker jobs that both walk the same files. The trigger is intermittent and not deterministically reproducible — most plausibly a confirm-dialog double-click before the POST returns, but browser-layer retries and remount races are also live possibilities.

## 2. Goal

After alpha-24 ships, a folder import of N files produces ≤N `ingest.start` events. The single-click → single-job invariant holds regardless of which trigger fires (double-click, browser auto-resubmit on focus regain, network-layer retry, StrictMode-style remount). The fix closes the class, not just the most plausible trigger.

## 3. Architecture — three independent layers, all shipped together

| Layer | Where | What it stops |
|---|---|---|
| L1 — Phase-machine guard | `apps/web/src/app/setup/first-documents/page.tsx` `triggerImport` | Re-entry from any source within the same page instance |
| L2 — Disabled confirm button | `apps/web/src/components/review/ConfirmDialog.tsx` `busy` prop, plumbed from `page.tsx` | The specific double-click trigger (visible UX feedback: "Working…") |
| L3 — Idempotency token | New `Idempotency-Key` header on the POST + server-side dedupe in the existing wizard `_jobs` registry | Replays the page state machine couldn't see (browser retry, page remount, network retry) |

Layering rationale: L1 closes the structural opening at the source — once `triggerImport` is in flight, it cannot be re-entered. L2 makes that state visible so the user doesn't keep clicking. L3 is the backstop: even if L1+L2 are bypassed, the server returns the original `job_id` for the same idempotency key.

Out of scope:
- Changes to alpha-23's `IntegrityError` race-recovery in `ingest_file` (stays as the row-level last line of defence).
- ConfirmDialog *refactors* (component already exposes `busy`; this design adds a one-line `title` to the disabled confirm button — see §4.2 — but does not restructure the component).
- Persistent idempotency storage (in-memory registry is fine; alpha-22 documented the same posture for `_jobs`).
- Two-tab races (per project memory: "academic, single-user single-tab").
- Other wizard POSTs (`/projects`, `/projects/suggest-name`); alpha-22 already documented those as out of scope and that posture stands.

## 4. Frontend (L1 + L2)

### 4.1 Phase-machine guard

`Phase` union (page.tsx:66–77) gains a new variant:

```ts
| { kind: "submitting"; folderPath: string; projectName: string; manifest: FolderScanResponse }
```

`manifest` is preserved on the variant so a failed POST can transition cleanly back to `scanned` with the original manifest intact.

`triggerImport` body becomes:

```ts
if (phase.kind !== "scanned") return;
const folderPath = phase.manifest.folder_path;
const projectName = phase.manifest.folder_name;
const manifest = phase.manifest;
const idempotencyKey = crypto.randomUUID();
setPhase({ kind: "submitting", folderPath, projectName, manifest });
setConfirmImport(false);
try {
  const res = await setupApi.importFolder(folderPath, projectName, idempotencyKey);
  // sessionStorage write unchanged
  setPhase({ kind: "importing", jobId: res.job_id, status: null });
  // existing polling setup unchanged
} catch (err) {
  setPhase({ kind: "scanned", manifest });
  // surface error via existing failed-phase pattern (or new toast)
}
```

`isBusy` (page.tsx:494) extends to include `submitting`. The continue button in `SetupShell` stays disabled across the whole submit→import arc.

Idempotency key is generated inside `triggerImport` (not on dialog open), so a deliberate retry after a failed submit produces a fresh UUID and the server treats it as a new request — correct behaviour for an explicit user retry.

### 4.2 ConfirmDialog `busy` plumbing

`ConfirmDialog` already supports `busy?: boolean` (component file lines 21, 91–94: disables both buttons, swaps the confirm label to "Working…"). Single-prop edit at page.tsx:1018:

```tsx
<ConfirmDialog
  open={confirmImport}
  busy={phase.kind === "submitting"}
  // ... other props unchanged
/>
```

Add `title="Import is starting — please wait"` to the disabled confirm button inside `ConfirmDialog.tsx` (one-line change; applies to all `busy={true}` callers — defensible because `busy` already implies async-in-flight everywhere). If a tighter scope is preferred, add a new `busyHint?: string` prop instead. Default to the broader change.

### 4.3 Client API plumbing

`setupApi.importFolder` in `apps/web/src/lib/setupClient.ts` gains a third parameter `idempotencyKey: string` and adds `Idempotency-Key: <key>` to the request headers. No other call sites today; signature change is local.

## 5. Backend (L3)

### 5.1 Endpoint behaviour

`POST /api/setup/import-folder` in `src/meridian/wizard/api.py`. Before the existing path-validation + job-creation logic, read the `Idempotency-Key` header:

| Token state | Server behaviour |
|---|---|
| Header absent | Current behaviour unchanged — create new job. (Backwards compatible for any non-bundled client.) |
| Header present, no record | Create the job, record `(token → job_id, created_at)` in the registry, return as normal. |
| Header present, record exists | Skip job creation; return the original `JobCreatedResponse` shape with the original `job_id`. |
| Header present, malformed | 400 with `error: "invalid_idempotency_key"`. Validation regex: UUIDv4 shape `^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`. |

The frontend's polling loop is unchanged — it polls the returned `job_id` regardless of whether the server created a new job or replayed an existing one.

### 5.2 Storage

Extend the wizard's existing `_jobs: dict[str, JobState]` registry with a sibling `_idempotency: dict[str, IdempotencyRecord]` mapping token → `(job_id, created_at_monotonic)`. Same module, same lifetime, same `threading.Lock` semantics.

TTL: 15 minutes. Cleanup is lazy — every lookup also opportunistically deletes entries older than TTL. No background thread.

The idempotency record outlives the job: even after the job has been reaped from `_jobs`, the token stays for TTL so a late retry deduplicates correctly. If a token hits and the recorded `job_id` is no longer in `_jobs`, the response includes the recorded `job_id` and the client's first poll resolves to 404 → existing "Lost contact with the import job" failed-phase UX. Acceptable trade-off; rehydrating completed-job terminal status is more code for an edge case the SME has never hit.

### 5.3 Process restart

In-memory registry is empty after a process restart. A retry whose token survived a backend bounce is treated as a fresh request and creates a new job. Same posture as alpha-22 noted for the `_jobs` staging-detection bypass; documented limit of in-memory storage.

### 5.4 Observability

New structured log events:
- `wizard.import_folder.idempotent_replay` — fields: `idempotency_token`, `job_id`, `age_seconds`. Quantifies how often L3 actually fires (informs whether L1+L2 are sufficient in practice).
- `wizard.import_folder.idempotency_token_rejected` — fields: `reason` (`"invalid_format"`). Wiring-bug detector.

No new metrics; existing structlog JSONL surfaces these via grep and `meridian explain-last-error`.

## 6. Error handling — full table

| Failure | Where caught | User-visible behaviour |
|---|---|---|
| Network error during POST | L1 catch | Phase reverts to `scanned`; existing failed-phase UI fires. Re-click → fresh idempotency key. |
| Backend 5xx on POST | L1 catch | Same as network error. |
| Replay returns existing in-flight job | L1 success | Transition to `importing` with original `job_id`; polling sees normal progress. |
| Replay returns `job_id` already reaped | L1 success → first poll 404 | Existing polling-error path (page.tsx:447–460) fires: clears poll handle, transitions to `failed` phase with "Lost contact with the import job." Same UX as today's process-restart-mid-poll. |
| Malformed `Idempotency-Key` | Backend 400 | Surfaces in existing scan/import error classifier; should never happen from our own client. Wiring-bug indicator. |

## 7. Edge cases — re-entry table

| Scenario | Behaviour |
|---|---|
| Double-click confirm button | First click flips phase to `submitting` and disables button via `busy`. Second click hits L1 guard, returns. Even if both reach the network, both carry the same idempotency key → server returns same `job_id`. |
| Click → POST fails → click again | Catch branch returned phase to `scanned`. Re-click generates fresh UUID; server treats as new (intended). |
| Click → navigate away → return | Page remount loses state. New `triggerImport` generates fresh UUID. Server creates new job. Acceptable — covered by content-hash dedup at the row level (alpha-23). |
| Browser auto-resubmit on focus regain | Carries original `Idempotency-Key`. Server returns original `job_id`. **This is the case L3 specifically catches that L1+L2 cannot.** |
| Two tabs, click each | Each tab generates its own UUID. Server creates two jobs. Out of scope (per project memory). |

## 8. Testing

### 8.1 Backend e2e — `tests/e2e/test_alpha24_import_idempotency.py` (new)

| Test | Asserts |
|---|---|
| `test_first_post_creates_job_and_records_token` | POST with `Idempotency-Key` returns 202 with a `job_id`; `_idempotency` registry contains the token mapped to that `job_id`. |
| `test_replay_with_same_token_returns_original_job_id` | Two sequential POSTs with same token + body → identical `job_id`. Only one job exists in `_jobs`. Only one folder-scan was performed (monkeypatch `meridian.ingest.dispatcher.walk_directory` with a counting spy; assert call count == 1). |
| `test_different_tokens_create_different_jobs` | Two POSTs, different tokens, same path → two distinct `job_id`s. Confirms tokens are the dedupe key, not the path. |
| `test_no_token_header_creates_new_job_each_time` | Backwards-compat: two header-less POSTs → two jobs. |
| `test_malformed_token_returns_400_invalid_idempotency_key` | Header value `"not-a-uuid"` → 400 with `error: "invalid_idempotency_key"`. |
| `test_token_ttl_expires_after_15_minutes` | Monkeypatch `time.monotonic`; advance past TTL; replay creates a new job. |
| `test_replay_after_job_reaped_returns_token_record_with_dead_job_id` | Replay returns recorded `job_id` even when job is no longer in `_jobs`; subsequent `GET /import-folder/{job_id}` returns 404. (Documents the documented limit, not a regression.) |

### 8.2 Backend unit — extension to `tests/e2e/test_wizard_api.py`

- `test_idempotent_replay_emits_structured_log` — `wizard.import_folder.idempotent_replay` event fires once with `age_seconds >= 0`.

### 8.3 Frontend

No frontend test infra in repo as of alpha-23 (alpha-10 explicitly deferred vitest; not landed). For alpha-24:

- **Decision: skip frontend unit tests.** Adding vitest is a systemic change that should not piggyback on a contained warm-up. The backend tests prove the dedup contract; the frontend layers are observable via manual rapid-double-click verification and the log-volume regression check below.

### 8.4 Release gauntlet

New step in `scripts/release_gauntlet.py`: spawn the wheel-installed backend, hit `/import-folder` twice in parallel with the same token (`concurrent.futures.ThreadPoolExecutor`), assert one job created. Same posture as alpha-5's static check on installer URL constants — catches wiring at the wheel level.

### 8.5 Log-volume regression check

Extend an existing alpha-22 e2e test (or add a sibling): after a folder-import e2e walk imports 10 files, count `ingest.start` events in the JSONL log; assert exactly 10, not 20. Regression test for the symptom of #4, not just the structural fix. Cheap, self-cleaning.

### 8.6 Not tested here

- Two-tab race (out of scope).
- Process-restart-during-window (design explicitly accepts this).
- Alpha-23 row-level race recovery — covered by `test_alpha22_ingest_race.py`; not modified.

## 9. Acceptance criteria

- E2e suite green at 183+ passing on the alpha-24 wheel (alpha-23 baseline 175 + 7 new tests in `test_alpha24_import_idempotency.py` + 1 new test extending `test_wizard_api.py` = 183 minimum; adjust upward if log-volume regression check in §8.5 lands as a separate test rather than an extension).
- Release gauntlet green including the new parallel-POST step (§8.4).
- Log-volume regression check (§8.5) confirms one `ingest.start` per file in the post-fix corpus.
- Manual verification: SME-style folder import with rapid double-click on the confirm button produces a single job; the JSONL log shows ≤N `ingest.start` events for N files.

## 10. Carry-overs into later alpha-24 items

None. Item #4 is structurally independent of the rest of the alpha-24 list. The keystone (item #3 — auto-trigger bootstrap+extract) has separate touch points (wizard completion → bootstrap kickoff → extract job lifecycle). The idempotency primitive introduced here may be reused if/when item #3 needs server-side dedup on its own kickoff endpoint, but no commitment is made now.
