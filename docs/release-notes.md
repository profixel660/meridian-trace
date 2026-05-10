# Release notes

Round-by-round delta in plain English. Round numbers map to alpha versions for the v0.1.x line (round 7 → alpha-7, round 12 → alpha-12). From v0.2.0-alpha onwards, releases are tagged `v0.2.0-alpha.N` and bundle multiple rounds (the Tauri rebuild rolls 13 → 17.5 into one release).

When you upgrade, skim the relevant version's notes — anything marked **breaking** needs a manual step (typically `meridian db-migrate <project>`).

## What's new in v0.2.0-alpha.26

Two interconnected feature streams sharing plumbing:

1. **Live process monitor** — a sticky-bottom panel on every project page streaming real-time progress + log events. Heartbeat dot that shifts cyan→green→amber→red on event-age thresholds, gradient progress bar, current activity description, expandable tail of the last few events. Answers "is it hung?" at a glance and doubles as a debug surface.
2. **Conflicts as a first-class platform feature** — restored queue navigation strip (Quarantine, Conflicts, Audit, Questions, Taxonomy with pending counts), a prominent ConflictsTile on the dashboard with counts-as-CTA self-prioritisation, a peer Conflict register page + xlsx export, and an auto-inferred Source-of-truth Hierarchy view with Sankey ⇄ Ranked-list toggle.

No schema change. Existing projects upgrade with `pip install --upgrade` and no `db-migrate` step.

### The live monitor — SSE end-to-end

Six new endpoints, four new frontend components.

**Backend** (`src/meridian/events/broadcaster.py`): a process-local broadcaster taps the structlog processor chain and fans allow-listed events out to per-subscriber `asyncio.Queue` consumers. Subscriber cap is `Settings.events_max_subscribers` (default 5, override via `MERIDIAN_EVENTS_MAX_SUBSCRIBERS=<n>`). Allow-list covers the load-bearing observability events: `extraction.source.*`, `triage.chunk.completed`, `llm_call.completed`, `pipeline.*` (including a new `pipeline.done` emit added to `pipeline_worker._run_pipeline` on the success transition).

`GET /api/projects/{n}/events` streams `text/event-stream` frames. Heartbeat every 5s on idle. 503 with `subscriber_limit` body when the cap is reached. `/setup/runtime` gains an `events` section reporting `active_subscribers / max_subscribers / broadcaster_enabled` for stuck-subscriber triage without a backend restart.

**Frontend** (`apps/web/src/components/dashboard/LiveMonitor.tsx`): mounted in `ReviewLayout` so it appears on every `/projects/<slug>/*` page. Three render states (collapsed-idle / active-collapsed / expanded) with localStorage-persisted collapse state and one-shot auto-expand on first activity per session. Heartbeat dot uses `box-shadow` glow shifts: cyan (events <2s ago), green (steady), amber (>30s no event), red (>90s). `requestAnimationFrame`-driven last-event-age timer mutates `textContent` directly without React re-renders.

The frontend hook (`apps/web/src/lib/eventStream.ts`) uses `fetch + ReadableStream + manual SSE-frame parser` rather than the browser `EventSource` API — `EventSource` cannot expose HTTP status codes, which makes detecting the 503 subscriber-cap response impossible. The hand-rolled parser is ~30 lines.

### Conflicts as a first-class feature

**Restored navigation:** `ReviewLayout`'s queue strip extends from a single `[Quarantine]` entry (alpha-16 prune) back to all five queues with their pending-count badges. The right-cluster artifacts strip gains a "Conflict register" link alongside Sources + Master register.

**Dashboard:**
- New `ConflictsTile` component, three render shapes: pending>0 (amber, prominent CTAs + optional Start-here highlight), resolved>0 (green-checkmark "all resolved"), neither (muted informational).
- New `HierarchyView` section with a Sankey ⇄ Ranked-list toggle persisted to localStorage. Sankey is hand-rolled SVG (no `d3-sankey` dependency); ribbon thickness = win count, hover tooltips deeplink to sample conflicts via the new `?focus=` query param. Ranked list shows precedence order with win-rate per source class.
- Dashboard restores all four queue cards (Quarantine / Audit / Questions / Taxonomy — Conflicts is the prominent tile above the grid).
- **Counts-as-CTA self-prioritisation:** the highest-priority pending queue gets a Start-here highlight. Priority order: Conflicts → Quarantine → Audit → Questions → Taxonomy. This replaces a separate "what to do now" widget with a built-in mechanism.

**Conflict register page** (`/projects/<slug>/conflict-register`): peer to the master register, NOT a sub-page of review. Filter chips for All / Pending / Resolved / Superseded with counts. Download xlsx CTA in the layout actions slot. Table renders 10 columns (Source A/B, Value A/B, Kind, Most-onerous reasoning verbatim, Status badge, Resolution, Resolved at). Pending rows expose a `Resolve →` deeplink that navigates to `/conflicts?focus=<id>` and the resolution queue scrolls to the conflict.

**Excel export:** `<slug>-conflicts.xlsx` via a new `meridian.export.conflict_register` module. Single sheet, 9 columns, wrap-text on the reasoning + value columns, freeze pane at A2. Reuses the alpha-24 `BackgroundTask` cleanup pattern for the temp file.

### Source-of-truth hierarchy — auto-inferred

`GET /api/projects/{n}/hierarchy` aggregates resolved-conflict patterns into:

- **Edges:** directed `(winner_class → loser_class)` with `wins`, `losses`, `win_rate`, three sample conflict IDs.
- **Ranked:** per-source-class precedence ordering with rank + win/loss totals.
- **`same_class_conflicts`:** OSE-vs-OSE etc. counted separately, NOT in edges.
- **`resolved_count`:** all four resolved variants (accept_a, accept_b, hybrid, reject_both); only accept_a/b shape edges.
- NULL `document_class` coerced to literal `"Unclassified"` so the inference is honest about what's being counted.

Refresh triggers: component mount, `pipeline.done` events from the live monitor's event hook, `localStorage["meridian.conflicts.last_resolved_at"]` signal written by `ConflictsQueue` after every resolve.

### Tests + gauntlet

- ~25 new backend e2e tests across SSE wire-format, conflict register filters + xlsx round-trip, hierarchy aggregation (basic, hybrid-skip, self-class, NULL-coercion).
- Full backend e2e: **222 passing / 1 skipped** (alpha-25.1 was 199 → +23 alpha-26 tests).
- Release gauntlet **18 steps green** including new step 7k: `_step_alpha26_sse_and_conflicts` validates the full SSE stream + conflict register + hierarchy + subscriber cap on a fresh-install backend.
- One fixup landed during the gauntlet shakedown: step 7k's subscriber-cap test was assuming zero existing subscribers when the SSE-consumer thread (used during the pipeline-run smoke earlier in the same step) was still holding a slot. Refactored to probe `/setup/runtime` for `active_subscribers` first, then fill `cap - active` slots and assert +1 returns 503.

### Carry-overs to alpha-27+

- Manual hierarchy override (admin UI + stored override edges + override-vs-inferred badge).
- Quarantine taxonomy combobox + add-new affordance (open since alpha-24 punch list).
- Pipeline cancel / Ctrl+C support.
- `--isolated` extract child-process IPC.
- CLI / wizard data-dir consolidation.
- Onboarding three small fixes (Tour copy, Step 2 Ollama hyperlink, Step 3 missing Projects link).
- `?` keyboard-shortcut binding fix.
- SSE replay-on-reconnect (event-history endpoint).
- Server-side conflict register pagination (kicks in past 500 conflicts/project).
- Per-document hierarchy (filename × filename precedence).
- The `superseded` conflict status (currently schema-deferred — endpoints + counts handle it as a stub).

## What's new in v0.2.0-alpha.25.1

Hotfix on the alpha-25 line. The keystone pipeline shipped without the conflict-pass step, so the master register's `flags` column landed empty across all rows and the freshly-built `conflict_summary` column had nothing to render. Caught on the SME's first review.

No schema change. Re-run `Reset-Meridian.ps1` and re-install for a clean run, or run `meridian conflicts <project>` from PowerShell against an already-extracted alpha-25 project to backfill conflicts without resetting.

### What broke

Meridian's CLI workflow has always been **two commands**: `meridian extract` then `meridian conflicts`. The alpha-25 keystone pipeline_worker only wired the first — `run_bootstrap_sweep` then `run_job_over_sources`. The conflict-pass (`run_conflict_pass`, which writes `conflicts_with_source_<id>` tokens into each affected deliverable's `flags` and stamps `flag_context.<token>.conflict_id`) was never called from the GUI auto-trigger path.

The regression hid because:
- Alpha-25 backend e2e tests use `mock_llm_client`. The mock produced deliverables but not conflicts; tests passed.
- Gauntlet step 7j only checked the `conflict_summary` *column header* was present, not that any row was populated.
- The synthetic two-source DOCX corpus the gauntlet uses wouldn't produce cross-document conflicts even with conflict-pass running.

### The fix

`pipeline_worker._run_pipeline` now runs three serial phases instead of two:

1. **bootstrap** (advisory, soft-fail — unchanged).
2. **extract** (canonical, holds project lock — unchanged).
3. **conflicts** (NEW — soft-fail like bootstrap). Calls `run_conflict_pass(conn, provider, model)`. Three terminal states:
   - `succeeded` — pass ran, conflicts (if any) persisted with their reasoning into `flag_context`.
   - `skipped` — extract produced zero deliverables / audit rows; conflict-pass would have raised `RuntimeError("No deliverables or audit rows present...")`. Logged at INFO; pipeline still ends `phase=done`.
   - `failed` — pass ran but raised something else (LLM auth, malformed JSON, etc.). Logged at WARNING; pipeline still ends `phase=done` so deliverables already on the master register aren't lost.

The dashboard `PipelineProgressTile` gains a third intermediate render: `phase="conflicts"` shows "Detecting cross-source conflicts…" with the same dimmed-KPIs / hidden-`BaselineBanner` posture as bootstrap and extract. On `phase=done` the tile self-removes; the master register's Excel export now has populated `flags` + `conflict_summary` columns for any deliverable referenced in a pending conflict.

`PipelineStatusResponse` (HTTP) gains `conflict_pass_status: Literal["pending"|"running"|"succeeded"|"failed"|"skipped"]`. The frontend `PipelineStatus` interface mirrors it. No client breakage — the field is additive.

### Tests + gauntlet

Two new backend e2e tests pin the regression so it can't return:

- `test_pipeline_runs_conflict_pass_after_extract` — pipeline run produces at least one `llm_call` row with `purpose='conflict_pass'`. The empty-flags-column failure mode would leave that count at zero.
- `test_pipeline_conflict_pass_soft_fails_when_extract_produced_nothing` — when extract is no-op'd, conflict-pass marks `skipped` and the pipeline still reaches `phase=done`.

Gauntlet step 7j extends to assert `conflict_pass_status in {"succeeded", "skipped"}` after `phase=done` — `failed` or `pending` here means the pass either errored or never ran.

Backend e2e: **199 passing / 1 skipped** (alpha-25 was 197 → +2 new conflict-pass tests). Gauntlet 17 steps green at the new sub-assertion.

### Backward-compatibility note

The CLI's standalone `meridian conflicts <project>` command is unchanged — calling it on an already-conflict-pass'd project is idempotent at the LLM-call level (records a fresh `llm_call` row) but `_persist_conflicts` upserts on `conflict.id` so a second run on identical input is benign. The GUI's keystone auto-trigger now calls `run_conflict_pass` once per pipeline run; CLI users who prefer the two-command flow can keep doing it.

### Carry-overs to alpha-26 (unchanged from alpha-25)

- Cancel / Ctrl+C on a wedged extract.
- Quarantine taxonomy add-new flow (typeahead + "+ Add" affordance — confirmed in 2026-05-10 SME-review point #2).
- Dashboard "what now" guidance for PMs landing on the dashboard for the first time (2026-05-10 SME-review point #1).
- `--isolated` extract child-process IPC.
- CLI / wizard data-dir consolidation.

## What's new in v0.2.0-alpha.25

The keystone: closes punch list item #3 from the SME's 2026-05-02 alpha-22 round — the dashboard "0 extracted, N pending" wall where the SME imported a folder, walked through setup, landed on a buttonless dashboard with no path to advance the pipeline. Plus three additive wins on shared dashboard / export surfaces.

No schema change. Existing projects upgrade with `pip install --upgrade` and no `db-migrate` step.

### The keystone — auto-trigger bootstrap+extract from the wizard

After the wizard's `/setup/complete` lands, the frontend now POSTs `/api/projects/<slug>/pipeline` with a fresh `Idempotency-Key`, stashes the returned `job_id` in `sessionStorage`, and navigates to the dashboard. A new `PipelineProgressTile` mounts above `BaselineBanner`, picks up the stashed job_id (or falls back to `GET /api/projects/<slug>/pipeline` for the latest job), polls every 1.5 s, and renders three states:

- **bootstrap** — "Classifying your project's vocabulary…" indeterminate spinner. One LLM call seeds trade / service / category taxonomies before extraction.
- **extract** — progress bar (`extract_completed / extract_total`) plus the **current source filename rendered verbatim** per the project's surface-LLM-text-as-written posture.
- **failed** — red panel with the worker's `error_message` rendered verbatim (no paraphrase) plus a "Try again" button that re-POSTs `/pipeline` with a fresh idempotency key.

While `phase !== "done"` the dashboard hides the (now-redundant) amber `BaselineBanner` and dims the KPI grid to 50% opacity. On `phase=done` the tile self-removes, the dashboard refetches `/coverage`, and the KPIs return to full opacity with real numbers.

### The pipeline endpoint — three new routes under `/api/projects/{name}`

- `POST /pipeline` — kicks off a daemon-thread worker that runs `run_bootstrap_sweep` then `run_job_over_sources` serially. Returns `{job_id}` immediately (~50 ms). Accepts an optional `Idempotency-Key` header using the alpha-24 registry pattern, now extracted into `meridian.api.idempotency` for shared use. Surfaces `409 project_busy` when another extract / backup is in flight on the same project (checked via the new `is_project_lock_held(slug)` helper that reads the `.lock` file directly — no thread spawned just to discover the lock is held).
- `GET /pipeline/{job_id}` — polled by the dashboard tile. Returns `{phase, bootstrap_status, extract_total, extract_completed, current_source_filename, started_at, finished_at, error_message, holder_pid}`.
- `GET /pipeline` — same shape, returns the most-recent job for this project's `db_path` (insertion-ordered). Used by the dashboard tile when sessionStorage is empty (page refresh, new device) and by gauntlet step 7j.

The orchestrator's `run_job_over_sources` gained an optional `on_source_complete: Callable[[str, str], None]` callback that fires `(source_id, filename)` once per source after its `extraction_job_source` row is committed. The pipeline worker uses it to bump per-source progress on the in-memory `_PipelineJob` registry without polling the DB on every tile-tick. Callback failures are swallowed with a warning — observers don't get to break the worker.

### Bootstrap soft-fail

Bootstrap proposals are advisory — extraction runs without them (the per-deliverable taxonomy proposal pass during extract is the canonical seeding). A bootstrap failure (LLM timeout, malformed JSON, no sources to sample) is logged at WARNING, marked `bootstrap_status="failed"`, and the worker proceeds to the extract phase. The dashboard tile renders the bootstrap step's outcome inline.

### Three small wins on shared surfaces

1. **`BaselineBanner` suppression on sources-only projects.** `is_data_present` now requires `deliverable_status.total + cost.total_calls > 0`, dropping `sources_imported` from the signal. A project with sources but no deliverables / LLM calls is genuinely "no opinion yet"; the welcome panel + the keystone tile cover those states. Pre-keystone, the SME would land on `is_data_present=true` with `0/0` blockers and an amber "NEEDS REVIEW" banner that was actively misleading.

2. **Header `Projects` button gate-softening.** The homepage at `/` previously bounced any user with `setup.complete=false` straight back through the wizard, even when their project DBs were already on disk. The gate now requires `setup.complete=false` AND no projects on disk before redirecting — closes the SME's "the Project button at top still restarts setup" complaint without changing the gate's first-install role.

3. **Master-register Excel `conflict_summary` column.** New column between `flags` and `deliverables_summary` rendering the conflict-pass LLM's `most_onerous_reasoning` paragraph **verbatim** (with a `[<conflict.kind>]` prefix) for every pending conflict referenced in the deliverable's `flag_context` JSON. `wrap_text=True`, column width 60. Resolved conflicts are excluded — they already shaped the surviving deliverable's summary. No schema change; reads existing `flag_context` and the `conflict` table.

### Tests + gauntlet

- 11 new backend e2e tests across pipeline worker, pipeline endpoints, pipeline e2e (happy path + busy 409), `is_data_present` regression and forward case, master Excel `conflict_summary` (verbatim and resolved-hidden).
- One pre-existing test updated (`test_alpha22_coverage_empty_state.py`) to add an LLM call so its assertion still holds after the `is_data_present` redefinition.
- One regression caught by the full e2e run (`test_alpha24_log_volume.py:45` was reaching `wizard_api._idempotency_lock` directly; the alpha-25 idempotency-helper extraction made that attribute disappear) — fixup landed before tag.
- Full backend e2e: **197 passing / 1 skipped** (alpha-24 was 179 → +18 alpha-25 tests).
- Release gauntlet **17 steps green** including the new step 7j (`_step_pipeline_e2e_and_workbook_smoke`): generates a synthetic `.docx`, ingests via `/setup/import-folder`, kicks `/pipeline`, polls until `phase=done` (4-min cap), asserts deliverables produced, downloads `export.xlsx`, asserts `conflict_summary` column header. Catches future wiring drift on the pipeline path at the wheel level.

### What's NOT fixed yet (alpha-25 carry-overs)

The alpha-24 punch list still has work after the keystone landed. Notable open items:

- **Cancel / Ctrl+C** on a wedged extract. Pipeline failure + retry works; deliberate cancel does not.
- **Quarantine taxonomy add-new** flow (strict vs permissive — design question deferred).
- **`--isolated` extract child-process IPC** (CLI surface, not GUI).
- **CLI / wizard data-dir consolidation** — CLI uses `settings.data_dir`, wizard uses `_meridian_home()` chain.
- **Onboarding three small fixes** (Tour button copy, Step 2 Ollama hyperlink routing, Step 3 missing Projects link).
- **`?` keyboard shortcut binding** — hint shown, binding doesn't fire.

Backend-honest item still open: log-level on silent-failure paths (alpha-22 punch #2). And the OSE-Requisition-Form 0-deliverables investigation noted in the 09/05 SME round.

### Edge cases worth knowing about

- **Backend restart mid-extract.** The in-memory `_pipeline_jobs` dict dies; a refreshed dashboard tile's `pipelineApi.latest()` returns 404 and the tile renders nothing. The user sees `last_extraction_at` populated in `/coverage` and no banner. They can re-trigger by completing the wizard again or via the CLI — durable resume is a separate concern.
- **Two browser tabs.** Each `setup/ready` mount generates its own UUID; the second tab's POST returns the first tab's `job_id` via the idempotency registry's race-loser path — both tiles converge on the same job. Out-of-band manual POSTs from a refresh-after-close behave identically.
- **Bootstrap failure + extract success.** If bootstrap fails (e.g., LLM auth blip on the first call) but extract recovers, the pipeline still ends `phase=done` with `bootstrap_status=failed` exposed in the GET response. Tile renders the extract progress as normal; the `failed` bootstrap is a warning-level log entry, not user-visible.

### Backward-compatibility note

The synchronous `POST /api/projects/{name}/extract` endpoint is unchanged — CLI consumers (`meridian extract`) and the alpha-12 e2e tests still drive it directly. The new `/pipeline` endpoint is additive.

### Carry-overs to alpha-26

- The full alpha-24 punch list minus the four items closed in alpha-25.
- Pipeline cancel / Ctrl+C support.
- Tauri `.msi` (round 18) still requires Rust + MSVC + WiX.
- Crash endpoint URL still awaits Cloudflare Worker deployment.
- License public key still awaits keypair generation.
- T-Bionic TLD still TBD.

## What's new in v0.2.0-alpha.24

One fix: closes punch list item #4 from the SME's 2026-05-02 alpha-22 testing round — the frontend double-submission of the folder-import POST that produced 694 `import_job.file_done` events for a 347-file folder (each file kicked off twice). Alpha-23's race-safety in `ingest_file` neutered the user-visible failure mode; alpha-24 closes the source so log volume + LLM cost stop doubling and the operator's mental model ("each file uploaded once") is honest.

No schema change. Existing projects upgrade with `pip install --upgrade` and no `db-migrate` step.

### The fix — three layers, defence-in-depth

The double-submit can fire from at least three triggers (confirm-dialog double-click, browser auto-resubmit on focus regain, page remount race). Rather than guess which trigger the SME hit, alpha-24 closes the structural opening at three independent layers, all shipped together:

1. **L1 — phase-machine guard** (`apps/web/src/app/setup/first-documents/page.tsx`). New `submitting` Phase variant flipped synchronously BEFORE `triggerImport`'s `await setupApi.importFolder(...)`. Re-entrant calls to `triggerImport` hit the existing `if (phase.kind !== "scanned") return` guard and bail. Failed POSTs revert to `scanned` (not `failed`) so deliberate retries get a fresh idempotency key without re-picking the folder; `pickerError` is hoisted out of the `idle/scan_*` branches so the user sees the error during `scanned` too.

2. **L2 — disabled `ConfirmDialog` confirm button**. The existing `ConfirmDialog.busy` prop is now plumbed from `first-documents/page.tsx` (`busy={phase.kind === "submitting"}`); the disabled button additionally surfaces `title="Working — please wait"` for hover-discoverability. Two complementary signals (visible "Working…" label + native tooltip) stop the second click that L1 alone can't catch in React's batched-event window.

3. **L3 — server-side `Idempotency-Key` dedupe** (`src/meridian/wizard/api.py`). New in-process `_idempotency` registry alongside the existing `_jobs` board. Frontend generates `crypto.randomUUID()` per click; backend validates the UUIDv4 shape (regex anchored on the `4` version + `[89ab]` variant nibble — malformed values 400 with `error: "invalid_idempotency_key"`); the atomic `_idempotency_claim()` helper does a check-and-set under `_idempotency_lock` BEFORE any side-effects (path validation, project creation, folder walk, thread spawn). Same-token replays return the original `job_id` with `wizard.import_folder.idempotent_replay` structured-log; race-losers return the winner's `job_id` with `wizard.import_folder.idempotent_race_loser`. TTL: 15 minutes with lazy GC (no background thread). Backwards-compat: header-less POSTs keep current behaviour.

The L3 atomic claim was a real catch — building the release-gauntlet step exposed a TOCTOU race in the original two-stage `lookup-then-record` design where two parallel POSTs both passed the read-only lookup, both ran `create_project`, hit a SQLite write lock, and produced two distinct `job_id`s. The atomic claim closes that window cleanly.

### Tests + gauntlet

10 new e2e tests across three files:

* `tests/e2e/test_alpha24_import_idempotency.py` (8 tests) — replay returns same `job_id`, first-post records, distinct tokens, no-header backwards-compat, malformed 400, TTL expiry, reaped-job replay (documents the limit), in-process concurrent-race dedupe via the atomic claim.
* `tests/e2e/test_wizard_api.py` (1 new test) — `wizard.import_folder.idempotent_replay` event fires once per replay with the recorded `job_id` + `idempotency_token` + non-negative `age_seconds`.
* `tests/e2e/test_alpha24_log_volume.py` (1 test) — symptom-level regression: N files imported under simulated double-submit pressure (two concurrent POSTs with same `Idempotency-Key`) emit exactly N `import_job.file_done` (or `file_failed`) events, not 2N. The original alpha-22 SME-round shape, locked.

Full e2e: 179 passing / 1 skipped. Release gauntlet 16 steps green including the new 7i (`step_7i_idempotency_dedupes_parallel_posts`) which spawns two threads against `/setup/import-folder` on a wheel-installed backend with the same idempotency token and asserts identical `job_id`. Catches future wiring drift on the dedupe path at the wheel level — same posture as step 2b's static check on installer URL constants for the alpha-5 IPv6 regression class.

### What's NOT fixed yet

Items #2 (log-level on silent-failure paths) and #3 (dashboard "0 extracted, 329 pending" → auto-trigger bootstrap+extract from the wizard, the keystone) from the SME's 2026-05-02 round remain open. The 09/05 SME round added a stack of further UX/wiring findings — full alpha-24 punch list (~17 items) is sequenced for subsequent revisions starting with the keystone (#3, the heaviest single item; unblocks ~6 downstream dashboard items).

### Edge cases worth knowing about

- **Process restart between claim and registry write:** the in-process `_idempotency` registry is empty after a backend bounce. A retry whose token survived a backend bounce is treated as a fresh request and creates a new job. Same posture as alpha-22 noted for `_jobs`. Persisting tokens to disk would couple this fix to the broader storage cleanup the keystone touches; out of scope here.
- **Two parallel browser tabs:** each tab generates its own UUID. Server creates two jobs. Per project memory, "academic, single-user single-tab" — not addressed.
- **Reaped-job replay:** if the recorded `job_id` has been removed from `_jobs` (manual reaping or a future cleanup pass), the replay returns the recorded `job_id` and the client's first poll resolves to 404 → existing "Lost contact with the import job" failed-phase UX. Documented limit, not a regression. Locked by `test_replay_after_job_reaped_returns_token_record_with_dead_job_id`.

### Backward-compatibility note

Header-less `POST /api/setup/import-folder` keeps the current behaviour (creates a fresh job per request). The `Idempotency-Key` header is additive — older clients and scripts pinned to alpha-23 will not break.

### Carry-overs to alpha-25

- The full alpha-24 punch list (~17 items) — keystone (#3) sequenced first.
- The same in-process `_idempotency` registry posture (no persistence across backend bounces).
- Tauri `.msi` (round 18) still requires Rust + MSVC + WiX.
- Crash endpoint URL still awaits Cloudflare Worker deployment.
- License public key still awaits keypair generation.
- T-Bionic TLD still TBD.

## What's new in v0.2.0-alpha.23

One fix: closes punch list item #1 from the SME's 2026-05-02 alpha-22 testing round — the post-import banner that read "127 files failed for an unclassified reason" when the SME imported a 347-file project folder.

Root cause was a race in `ingest_file`: SELECT-then-INSERT on `content_hash` was not atomic. Under the alpha-22 double-submission pattern (two concurrent worker jobs processing the same path list — punch list item #4, still open), the slower worker hit the `source_document.content_hash` UNIQUE constraint, raised `sqlite3.IntegrityError`, and `_classify_ingest_error` had no branch for it — so 127 successfully-ingested files were labelled `unknown` and surfaced as "unclassified failed."

### The fix

`ingest_file` now wraps the INSERT block in `try/except sqlite3.IntegrityError`. On a content_hash UNIQUE conflict the loser re-reads the row the sibling worker committed and returns `IngestResult(deduped=True, race_recovered=True)` — semantically "this file is already in the project" (which it is — the sibling just put it there). Other UNIQUE-constraint paths propagate normally so genuine schema violations are not swallowed. A `_build_dedupe_result` helper unifies the existing SELECT-found-existing branch and the new IntegrityError-recovery branch so the two dedupe paths cannot drift.

### Tests + gauntlet

One new e2e test (`tests/e2e/test_alpha22_ingest_race.py`) — two threads synchronise at a barrier post-hash, then race the transaction; pre-fix raised `IntegrityError`, post-fix returns one `deduped=False` + one `deduped=True` with the same `source_id` and exactly one row in the DB. Full e2e suite: 175 passing / 2 skipped. Gauntlet green on the 0.2.0a23 wheel.

### What's NOT fixed yet

Items #2 (log-level on silent-failure paths), #3 (dashboard "0 extracted, 329 pending"), and #4 (frontend double-submission) from the SME's 2026-05-02 round remain open. Item #1's race-safety means even if #4 keeps happening, the user no longer sees phantom failures — the loser dedupes cleanly. #4 is still worth fixing for log volume + UX clarity (the user expects each file to be uploaded once).

## What's new in v0.2.0-alpha.22

Closes the bod-2 zero-sources bug end-to-end. Alpha-21 shipped a wizard where the user could import 4 PDFs successfully, walk through the rest of setup, and land on a dashboard reading "0 sources". Root cause: wizard state file path was coupled to `settings.projects_dir`, which the projects-creation handler mutates mid-process — orphaning every prior step's progress at the old location AND minting a fresh empty SQLite at the user-chosen location instead of adopting the staging DB.

No schema change. Existing projects upgrade with `pip install --upgrade` and no `db-migrate` step. **One-shot legacy state migration on first wizard run after upgrade** — explained below.

### What broke

Walking the wizard with a non-default `projects_dir` choice ended with: 4 imported PDFs stranded at `C:\Meridian\data\projects\<slug>.sqlite`, a fresh empty `<chosen_dir>\<slug>.sqlite`, `/api/setup/complete` returning 400, the frontend swallowing the 400, and a project page showing "0 sources" with a nonsense "NEEDS REVIEW: 0 deliverables missing full provenance (0.0% complete)" amber banner.

Two on-disk `onboarding_state.json` files were the smoking gun — one with the API key and import counters, one with just the new project slug. The wizard had been writing to two different state files without realising it.

### The eight fixes (one comprehensive arc)

1. **`state_path()` decoupled from `projects_dir`.** Wizard JSON state now lives at `%USERPROFILE%\.meridian\onboarding_state.json` (or `MERIDIAN_WIZARD_STATE_DIR` for tests). Survives any mid-process mutation of `settings.data_dir`. **Legacy migration:** on first wizard run after upgrading, an alpha-21 state file at `<projects_dir>/_meridian/onboarding_state.json` is read once, written to the new path, and unlinked. Subsequent loads see only the new path. Defensive try/except handles corrupt legacy JSON.

2. **`adopt_project(old_db_path, new_db_path, new_name)` helper.** New low-level primitive in `meridian.projects` that performs WAL checkpoint + journal_mode=DELETE on the source (so no orphaned `-wal`/`-shm` sidecars), `shutil.move`s the file, then rewrites the in-DB `project.name` row inside a `contextlib.closing` block. Best-effort rollback if the post-move UPDATE fails (logs `projects.adopt.rollback_failed` if rollback itself fails). Refuses to overwrite a pre-existing target file.

3. **Wizard `/api/setup/projects` adopts the staging DB.** Previously minted a fresh empty SQLite at the user's chosen `projects_dir`, stranding any imported documents at the staging location. Now dispatches three branches: no staging → original `create_project` fresh; staging at the same path AND same slug → in-place name UPDATE; otherwise → `adopt_project` (file move + name rewrite). New 409 error code `import_in_progress` blocks the projects step if a folder-import job is still writing to the staging DB. New 409 error code `staging_db_locked` for adopt_project failures that the user can recover from.

4. **`/api/setup/projects/suggest-name` excludes the wizard's own staging.** Previously bumped "bod" → "bod-2" because the staging file at `bod.sqlite` existed, silently changing the user's typed name. Now the staging file is excluded from the collision check (via the `_jobs` registry). Real pre-existing projects (created out-of-band) still cause the suffix bump.

5. **Coverage endpoint distinguishes "no data" from "untrustworthy".** New `is_data_present: bool` field on `/api/projects/<slug>/coverage`. `is_baseline_trustworthy` widened from `bool` to `bool | None` — `None` when `is_data_present` is false (no opinion yet). `baseline_trust_blockers` is `[]` on empty projects. CLI renderer adds `[EMPTY] NO DATA YET:` branch.

6. **Ready page surfaces `/api/setup/complete` 400.** Previously called `setupApi.complete().catch(() => {})` — fire-and-forget. The user saw "Setup complete ✓" and an active "Open project" button even when gates failed. Now: 400 responses with `error: "setup_incomplete"` render a red error panel with the backend's `next_step` hint and a "Continue: <step>" CTA. The Open Project button is disabled. Generic non-2xx responses (network error, 500) render a "Try again" button that retries `complete()` via a nonce-bumped `useEffect`. `aria-describedby` plumbed for screen readers.

7. **Dashboard suppresses `BaselineBanner` on empty projects + adds an empty-state CTA.** The amber banner previously fired on zero-data projects. Now early-returns `null` when `is_data_present === false`. A new welcome panel renders an "Add documents" CTA jumping straight to `/setup/first-documents` and a "What does Meridian do?" link to the glossary. The sources page replaces the bare "No sources imported" string with a styled empty-state via the existing `EmptyState` component (extended with optional `ctaHref` / `ctaLabel` props).

8. **First-project page surfaces the bumped-suffix hint.** When `suggest-name` returns `is_available: false`, an inline amber `<p role="status">` appears beneath the name input: `A project with the name "X" already exists. We suggested "X-2" — feel free to change it.` User can edit the suggestion or accept it knowingly. Now narrowly applicable (Task 4 above means the wizard's own staging no longer triggers it), but if there IS a real prior project at the same slug the user sees what changed.

### Tests + gauntlet

172 e2e passing (was 158 at start of cycle). 14 new alpha-22-specific tests across 5 new test files (`test_alpha22_*`), plus extensions to `test_wizard_api.py`. Frontend `npm run build` clean. Gauntlet 14 steps green on the 0.2.0a22 wheel.

### Edge cases worth knowing about

- **Process restart between import and projects-POST:** the in-process `_jobs` registry is empty on the new process, so the staging-detection bypass for the early-409 doesn't fire. The user gets `slug_exists` (409) for their imported staging. Recovery: delete `<old_projects_dir>/<staging_slug>.sqlite` and re-import. Low-probability path; not addressed in alpha-22.
- **Two parallel projects-POSTs:** not guarded. Wizard UI is single-user single-tab so this is academic.
- **Reentrant `setup_import_folder` against a pre-existing real project DB:** silently clobbers `project.name` via the case-2 in-place UPDATE. Requires deliberately driving the wizard at an unrelated existing slug. No SME workflow does this. Hardening tracked for alpha-23.

### Backward-compatibility note

Coverage endpoint's `is_baseline_trustworthy` is now `bool | null` (was `bool`). Scripts using identity comparisons against `False` (`body["is_baseline_trustworthy"] is False`) will silently see different behaviour on empty projects — switch to truthy checks (`if body["is_baseline_trustworthy"]:`) or test the new `is_data_present` flag.

### Carry-overs to alpha-23

- Frontend `_jobs` registry has no TTL/cap (grows for the backend's process lifetime — small in practice but unbounded).
- T2 `adopt_project` rollback path has no automated test (covered manually).
- Welcome-panel "Add documents" CTA routes back into the wizard (`/setup/first-documents`) rather than a project-scoped sources flow — works (wizard is idempotent + content-hash-deduped) but worth a project-scoped picker eventually.
- Tauri `.msi` (round 18) still requires Rust + MSVC + WiX.
- Crash endpoint URL still awaits Cloudflare Worker deployment.
- License public key still awaits keypair generation.
- T-Bionic TLD still TBD.

## What's new in v0.2.0-alpha.10

First release shipped under the new pre-ship scrutiny grid. Five real findings from grid-walking alpha-9 (none surfaced by the gauntlet alone): missing backend test, lying TS contract, untestable inline path construction, broken UX for Windows-copied paths, silent observability gap. All five fixed.

No schema change. Existing projects upgrade with `pip install --upgrade` and no `db-migrate` step.

### Findings + fixes

1. **Backend `/setup/defaults` had zero test coverage** for the new `home_dir` field added in alpha-9 — a regression could ship invisible. Alpha-10 adds `test_setup_defaults_returns_real_paths` asserting both fields are present, non-empty, and contain no placeholder tokens (catches the alpha-5 422 class).

2. **TS contract was lying about runtime.** `SetupDefaults.home_dir: string` declared the field as required, but the frontend code accessed it via `d?.home_dir` (optional). An alpha-9 frontend talking to an alpha-8 backend would see the field absent and the type would be wrong. Alpha-10 marks the field optional in the TS interface — type contract now matches reality.

3. **`buildPrefilledPath` and `looksAbsolute` extracted to `lib/setupPaths.ts`** as pure functions. Inline construction in `handlePickFolder` couldn't be unit-tested and had subtle edge cases (trailing separator on `homeDir` doubled the slash, empty `folderName` produced `<home>\Documents\` which then passed `looksAbsolute` and triggered a folder-not-found scan of Documents itself). Pure functions let us cover those edge cases when frontend test infra lands. `buildPrefilledPath` now also trims trailing separators from `homeDir`.

4. **Surrounding double-quotes are now stripped before path validation.** Windows 11's `Win+Shift+C` ("Copy as path") wraps the copied path in `"…"`. Alpha-9 users who pasted that into the typed-path input got "doesn't look like a full path" because position 0 was `"` not a letter. Alpha-10 strips outer quotes in `submitManualPath` before calling `looksAbsolute`. Mid-path quoted segments (rare but valid in NTFS) survive.

5. **`setupApi.defaults()` failure now logs to the console.** Alpha-9 swallowed the error silently (`.catch(() => {})`), leaving the operator no way to know why the smart pre-fill wasn't appearing. Alpha-10 calls `console.warn` with the underlying error so DevTools surfaces it. User-facing behaviour unchanged (still falls back to no-pre-fill).

### Process change shipped alongside

A pre-ship scrutiny grid is now applied to every commit going forward (saved as user-feedback memory). For each change: enumerate failure modes per concrete input/dependency, audit cross-stack contract, check backward-compat, define UX failure mode, verify observability, and check test rigour with the "could this test pass while production is broken?" question. Tier the rigor by reversibility (Reversible / Scoped / Systemic). Filled grid goes in the commit message body so the audit trail is durable.

The grid surfaced the five alpha-10 findings in 15 minutes — none of which would have been caught by "tests pass + gauntlet green". Worth doing BEFORE shipping each alpha going forward.

### Tests + gauntlet

105 e2e passing in ~13s (was 104 + 1 new). Release gauntlet 9 steps — `[ ALL ] PASSED`.

### Deferred

* **Frontend unit test infra (vitest).** The pure helpers in `lib/setupPaths.ts` are now extractable and ready to test, but adding vitest is a systemic change that warrants its own grid pass. Tracked as alpha-11 candidate.

### Carry-overs from alpha-9

- Tauri `.msi` (round 18) still requires Rust + MSVC + WiX — and is the proper fix for the entire browser-path-restriction class.
- Crash endpoint URL still awaits Cloudflare Worker deployment.
- License public key still awaits keypair generation.
- T-Bionic TLD still TBD.

## What's new in v0.2.0-alpha.9

The folder-pick browser-fallback wasted users' time: pre-fill was just the folder name, no warning that the path needed extending, and the wizard sent the malformed payload to the backend which 400'd. Alpha-9 makes the typed-path step actually usable.

No schema change. Existing projects upgrade with `pip install --upgrade` and no `db-migrate` step.

### The bug

Browsers (Chrome/Edge/Firefox) hide absolute folder paths from JavaScript by design (security restriction since ~2015). The wizard's `webkitdirectory` picker only learns the folder NAME — never the parent path. The wizard's typed-path prompt was supposed to bridge this gap, but:

1. It pre-filled the input with only the folder name — looked complete to the user.
2. There was no visible explanation of why a typing step existed at all.
3. Submitting the form with just the folder name sent `{"folder_path":"Syd02 document repository"}` to the backend — no drive letter, no parent path. Backend correctly returned 400 `folder_not_found`. Until alpha-8 the error was misclassified as "transient hiccup"; alpha-8 fixed the message; alpha-9 stops the malformed submit at the source.

### The fix

- **Backend `/setup/defaults` extended** with a new `home_dir` field (`str(Path.home())`). The frontend uses it to construct a smart pre-fill.
- **Smart pre-fill**: when the picker returns `folderName="Syd02 document repository"`, the wizard pre-fills the input with `<home_dir>\Documents\<folderName>` — correct for the ~90% case (project folders under Documents). User just presses Enter; one edit if their project lives elsewhere.
- **Visible amber callout** above the input explains *why* the typing step exists ("Browser security: we only got the folder NAME from your pick. Browsers hide the full filesystem path from web pages by design.") and previews the upcoming Tauri MSI fix that removes the step entirely.
- **Client-side path-shape validation** refuses to submit if the path doesn't match an absolute-path pattern (Windows drive letter `C:\...`, UNC `\\server\...`, or POSIX `/foo/...`). Inline red error message: "That doesn't look like a full path. Add the drive letter (e.g. `C:\Users\...`)." Prevents another doomed `folder_not_found` round-trip.

### Carry-overs from alpha-8

- Tauri `.msi` (round 18) still requires Rust + MSVC + WiX — and is the proper fix for the entire browser-path-restriction class.
- Crash endpoint URL still awaits Cloudflare Worker deployment.
- License public key still awaits keypair generation.
- T-Bionic TLD still TBD.
- Install-time UX polish deferred until install flow stabilises.
- `Tooltip`-clobbering-onClick footgun in the component itself.

## What's new in v0.2.0-alpha.8

The wizard's folder-pick error message was misleading: any 400 from `/setup/import-folder/scan` showed an amber "We couldn't reach Meridian to scan that folder. Most often this is a transient network hiccup" — even when the actual cause was "you typed a folder path that doesn't exist". Alpha-8 surfaces the backend's specific error code so the user sees exactly what was wrong with the path they entered.

No schema change. Existing projects upgrade with `pip install --upgrade` and no `db-migrate` step.

### The bug

`classifyScanError` in the wizard's first-documents page only inspected `err.message`, which is the generic "Meridian API 400 Bad Request for /setup/import-folder/scan" string. The backend's structured `{"detail": {"error": "folder_not_found", "message": "Folder does not exist: ..."}}` body is on `err.body`, but the classifier ignored it. Result: every 400 routed to the amber "transient hiccup" panel — the wrong direction entirely. Confirmed live by the user typing a path with a typo and getting the misleading error.

### The fix

- **`classifyScanError` parses `err.body`** for `MeridianApiError` 400 responses, extracts the backend's structured `error` code (`folder_not_found` / `folder_not_a_directory` / `folder_access_denied`) and `message`. All three known codes route to the **red "invalid path" panel** (user-fixable) rather than the amber "transient hiccup" panel (network-flaky).
- **The error message displayed is the backend's specific message** ("Folder does not exist: `<path you typed>`"), not the generic "Meridian API 400". The user can see exactly what was rejected without retyping or guessing.
- **Legacy substring fallback retained** — the previous heuristic (`err.message` contains "does not exist", "ENOENT", etc.) still runs as a fallback for non-structured 400 responses.

### Carry-overs from alpha-7

- Tauri `.msi` (round 18) still requires Rust + MSVC + WiX.
- Crash endpoint URL still awaits Cloudflare Worker deployment.
- License public key still awaits keypair generation.
- T-Bionic TLD still TBD.
- Install-time UX polish deferred until install flow stabilises.
- `Tooltip`-clobbering-onClick footgun in the component itself (worked around for the folder-pick button).
- Firefox/Safari users who cancel the picker wait up to 60s before the wizard resets.
- Browser-fallback users still have to type the full Windows path themselves (browser security hides absolute paths from JS); Tauri MSI eliminates this.

## What's new in v0.2.0-alpha.7

The folder picker silently dropped successful picks on large project folders. Alpha-7 fixes the race condition and adds visible progress so a slow scan no longer looks identical to a hang.

No schema change. Existing projects upgrade with `pip install --upgrade` and no `db-migrate` step.

### The bug

The browser-fallback `pickFolderWithFallback` had a 500ms focus-return watchdog: when the user dismissed the picker, focus returned to the window, and a `setTimeout(500ms)` fired — if no `change` event had arrived in that window, the promise settled as `cancelled`. On large project folders (thousands of files), the browser's enumeration of the picked directory took longer than 500ms — so a successful pick fired the watchdog FIRST, the promise settled as `cancelled`, and the wizard stayed on the idle "Choose project folder" screen even though the user had nominated a folder. Pure silent failure with zero feedback. Reproduced live.

### The fix

- **Removed the 500ms focus watchdog entirely.** The new flow: `input.onchange` fires on a successful pick (universal); `input.oncancel` fires on cancel (Chromium 113+); a 60-second leak watchdog releases the promise if neither fires (Firefox/Safari cancel — they don't fire `oncancel` on file inputs as of 2026-04). 60s is well past any plausible enumeration time, so a real pick of even a 100k-file corpus settles via `change` first. Tradeoff: Firefox/Safari users who cancel see the wizard wait up to 60s before resetting; an explicit Cancel button on the page is alpha-8 polish.
- **Visible scanning progress.** The "Scanning folder…" panel now shows an animated pulse dot, animated trailing dots, and an elapsed-second counter ("Scanning folder.. (12s)"). After 15 seconds in scanning state, an amber callout surfaces with "Still scanning — large folders with thousands of files can take a minute or two on a spinning disk".
- **Import-phase progress bar verified.** Already present from Stream B (alpha-2): `|████░░░░░░| 47/120 — currently importing AT-GLOBAL-OR-000103.pdf` with `role="progressbar"` and a live current-file display. No change needed; surfacing here so the alpha-7 trail is complete.

### Tests + gauntlet

104 e2e passing in 12.6s (no test changes — the bug was browser-only behaviour). Release gauntlet still 9 steps, all green.

### Carry-overs from alpha-6

- Tauri `.msi` (round 18) still requires Rust + MSVC + WiX.
- Crash endpoint URL still awaits Cloudflare Worker deployment.
- License public key still awaits keypair generation.
- T-Bionic TLD still TBD.
- Install-time UX polish deferred until install flow stabilises.
- `Tooltip`-clobbering-onClick latent footgun (alpha-6 worked around it for the folder-pick button; future buttons inside `Tooltip` will silently lose their onClick the same way).
- `sessionStorage` stale-key issue on bookmarked first-project URL (cosmetic).

## What's new in v0.2.0-alpha.6

Four bugs from the user's first end-to-end walkthrough of the alpha-5 GUI wizard. The installer flow itself now reaches the wizard reliably (alpha-5 closed that); alpha-6 fixes the wizard's first real round of UX gaps.

No schema change. Existing projects upgrade with `pip install --upgrade` and no `db-migrate` step.

### Bug fixes

- **Wizard re-asked for the API key after the CLI installer already saved it** (cosmetic). The CLI installer writes the Anthropic key to `C:\Meridian\.env` AND Windows Credential Manager (via `keyring.set_password("meridian.api_key", "anthropic", key)`). The GUI wizard's `/setup/state` only checked the JSON state file, missed both other sources, and re-prompted. Alpha-6 broadens `WizardState.api_key_set` to check (1) the JSON state flag, (2) `ANTHROPIC_API_KEY` env var, (3) keyring at the canonical service/account. Single-source-of-truth constants in `meridian/wizard/state.py` mean the read path can never drift from the write path again.

- **"Couldn't reach Anthropic" warning fired even with valid keys** (cosmetic). The wizard's validator imported the `anthropic` SDK directly. The SDK isn't a hard meridian dependency (only litellm is) so `import anthropic` raised `ModuleNotFoundError` → caught → "unable_to_verify". Documented in `project_v013_deferred.md` since alpha-1; finally fixed. Alpha-6 wraps the SDK import in `try/except ImportError`; on miss, falls back to a one-shot `litellm.completion(model="anthropic/claude-haiku-4-5-20251001", messages=[{"role":"user","content":"ping"}], max_tokens=1, api_key=...)` call. AuthenticationError → `invalid`; PermissionDeniedError → `invalid`; everything else → `unable_to_verify` (conservative — better to surface "we couldn't check" than to misclassify a transient 5xx as `invalid`).

- **`/setup/projects` returned 422 because the form's projects_dir contained literal `<you>`** (blocker). The first-project page's "Projects folder" input had the default value `C:\Users\<you>\Meridian\projects`. The user clicked Create without editing, the frontend POSTed that string, the backend's path validator rejected the `<` and `>` (Windows-reserved chars), 422. Two-part fix: (1) new backend endpoint `GET /setup/defaults` returns a server-resolved `projects_dir` from the same `_meridian_home()` chain everything else uses (no placeholders); (2) frontend's `useEffect` calls it on mount and pre-fills the input. Falls back to a navigator-platform-derived guess on 404 so the frontend works against older backends too.

- **"Choose project folder" button did nothing on click** (blocker). Root cause: the `Tooltip` component clobbered the button's `onClick` via `React.cloneElement` — Tooltip injects its own click handler to toggle visibility, **overwriting** the wrapped child's `onClick`. The button only opened the tooltip; `handlePickFolder` was never called. Pure silent failure. Alpha-6 moves the tooltip onto a separate "What goes in the folder?" affordance so the button keeps its native onClick. Hardening: browser fallback now uses a window-focus watchdog to detect picker-cancel reliably (most browsers don't fire `oncancel` on `webkitdirectory` pickers); inline `pickerError` panel surfaces any future silent failure visibly.

### Tests + gauntlet

104 e2e passing in 12.6s (was 99 + 5 new — keyring round-trip, env-var read, litellm-fallback-valid, litellm-fallback-auth-invalid, conservative-classification). Release gauntlet still 9 steps, all green. The new `GET /setup/defaults` endpoint adds nothing to the gauntlet — the existing `/setup/state` probe already exercises the same import chain.

### Known limitations

- The `Tooltip`-clobbering-onClick footgun is a latent bug in the component itself. Alpha-6 worked around it for the folder-pick button; future buttons placed inside a `Tooltip` will silently lose their onClick the same way. Worth a 5-line follow-up to make `Tooltip` compose the child's onClick rather than overwrite it.
- The first-project page's auto-name `useEffect` reads `sessionStorage["meridian.setup.folder_path"]`. If a PM bookmarks first-project directly with a stale value, they'll see a "we suggested this from your folder" hint without ever picking a folder this run. Cosmetic; not in alpha-6 scope.

### Carry-overs from alpha-5

- Tauri `.msi` (round 18) still requires Rust + MSVC + WiX.
- Crash endpoint URL still awaits Cloudflare Worker deployment.
- License public key still awaits keypair generation.
- T-Bionic TLD still TBD.
- Install-time UX polish deferred until install flow stabilises.

## What's new in v0.2.0-alpha.5

A targeted IPv6/IPv4 fix for an installer hang the user hit on alpha-4. The release gauntlet missed this one because the gauntlet probed `127.0.0.1` directly while the installer probed `localhost`; alpha-5 closes the gap with a new gauntlet step that statically checks installer URL constants.

No schema change. Existing projects upgrade with `pip install --upgrade` and no `db-migrate` step.

### The bug

On Windows, the name `localhost` resolves to `::1` (IPv6 loopback) before falling back to `127.0.0.1` (IPv4). Uvicorn binds to `127.0.0.1` only by default. PowerShell's `HttpWebRequest` with a 1s timeout doesn't fall back from IPv6 to IPv4 fast enough — the probe targets an empty IPv6 socket, times out, and the installer hangs forever at "Waiting for the backend to come up" even though the backend is fully healthy on `127.0.0.1:8000`.

### The fix

- **Installer:** `$MERIDIAN_HEALTH_URL` and `$MERIDIAN_WIZARD_URL` now use `http://127.0.0.1:8000` instead of `http://localhost:8000`. Backend binds 127.0.0.1, probe targets 127.0.0.1, no DNS fallback timing involved.
- **CLI `meridian start`:** same swap — `base_url = f"http://127.0.0.1:{port}"`. The browser-open URL is also 127.0.0.1 (browsers handle IPv6→IPv4 fallback well, but consistency keeps the codebase predictable).
- **Release gauntlet step 2b (new):** static check that no `Install-Meridian.ps1` line contains `http://localhost:` outside comments. The exact bug class is now caught at the gauntlet level — any future re-introduction of `localhost` in installer URL constants fails the gauntlet before a build is cut.

### Tests + gauntlet

99 e2e passing in 12.6s. Gauntlet now has 9 ordered steps (added 2b: installer URL constants). Required-green before any future cut.

### Carry-overs from alpha-4

- Tauri `.msi` (round 18) still requires Rust + MSVC + WiX.
- Crash endpoint URL still awaits Cloudflare Worker deployment.
- License public key still awaits keypair generation.
- T-Bionic TLD still TBD.
- Install-time UX polish deferred until install flow stabilises.

## What's new in v0.2.0-alpha.4

The first release that passes a real **release gauntlet** before shipping. Every alpha through alpha-3 broke on the user's box in a way pytest didn't catch; alpha-4 introduces `scripts/release_gauntlet.py` — a one-command end-to-end check that builds the wheel, installs it into a fresh venv, spawns the backend from a tmp cwd with `MERIDIAN_HOME` pointing elsewhere, and asserts /health, /setup/, version, and CLI surface all behave correctly. The gauntlet is required-green before alpha-4 cuts.

No schema change. Existing projects upgrade with `pip install --upgrade` and no `db-migrate` step.

### Bug fixes

- **`_project_root()` now always substitutes `_meridian_home()` for installed wheels.** Alpha-3 only triggered the substitution when cwd was a known Windows system path (System32 / SysWOW64 / Program Files / ProgramData). The release gauntlet caught the gap: a wheel run from any "ordinary" cwd (Downloads, a tmp dir, the user's home) silently routed logs and project state into that cwd. Alpha-4 drops the cwd-as-fallback entirely — the dev tree is the only branch where cwd-equivalent behaviour ever made sense, and that branch is gated by the presence of `pyproject.toml` (which an installed wheel never has).
- **Installer `cmd /c "redirect-trick"` replaced by a generated `runtime/launch_backend.cmd` helper.** Alpha-3's `Start-Process cmd.exe /c "<python> -m meridian.api.main >> backend.log 2>&1"` was eaten by cmd.exe's quote-stripping rule — the recorded PID was alive, but Python never actually ran and `backend.log` was never created. Alpha-4 writes `runtime/launch_backend.cmd` at install time and `Start-Process`es the .cmd file; cmd.exe handles its own redirects natively, no quote pathology.
- **Wizard URL was wrong.** Alpha-3 pointed the installer + CLI at `/setup/welcome`; the Next.js static export bundles the welcome page at `/setup/index.html`. Result: 404 on first browser load. Alpha-4 swaps to `/setup/` everywhere (installer, CLI, docs, troubleshooting).
- **Locked-venv auto-recovery.** The installer now detects any `python.exe` / `pythonw.exe` whose `MainModule.FileName` is under `C:\Meridian\venv` BEFORE attempting to recreate the venv, kills them via `Stop-Process -Force`, re-checks, and only proceeds if all are gone. Stops the alpha-2 leftover-process scenario where the user had to reboot.
- **Frontend API client now uses relative URLs.** `apps/web/src/lib/api.ts` and the master-page Excel-download `<a href>` were defaulting to `http://localhost:8000` — a different origin from `127.0.0.1:8000` per browser security, so opening the wizard at `127.0.0.1` triggered a CORS-style fail and the wizard rendered "Couldn't reach the Meridian API yet — running in offline preview mode". Alpha-4 defaults `API_BASE` to empty string, making every fetch same-origin relative — works regardless of how the user landed on the page.
- **Brand string updated to "Meridian - Trace"** in the wizard welcome card, layout title, top-nav, and the CLI's `meridian init` panel.

### Release gauntlet

`scripts/release_gauntlet.py` runs 8 ordered steps and exits 0 only on full pass:

1. **PowerShell parser check** — every `.ps1` / `.psm1` in `installer/` round-trips through `[Parser]::ParseFile()` with zero errors. Catches the alpha-3 em-dash-encoded-as-cp1252 bug class.
2. **ASCII-only check** — every `.ps1` / `.psm1` / `.bat` / `.cmd` contains only codepoints 0x00–0x7F. Belt-and-braces against future encoding regressions.
3. **Wheel build** — `uv build` produces a wheel that contains `meridian/_web/setup/index.html` (the bundled Next.js wizard).
4. **Fresh-venv install** — wheel pip-installs cleanly into an isolated venv.
5. **Cwd-System32 simulation (the headline)** — spawns `python -m meridian.api.main` from a tmp dir with `MERIDIAN_HOME` set to a separate tmp dir, polls `/health` until 200, asserts log files appear under `MERIDIAN_HOME` and NOT under spawn cwd. The exact alpha-2 failure scenario — caught alpha-3's incomplete fix.
6. **`/setup/` probe** — GET returns 200 with "Meridian" in the body. Catches the alpha-3 `/setup/welcome` 404.
7. **Version assertion** — `/health` JSON `version` field matches `pyproject.toml`'s `version`. Catches the alpha-1 stale-`0.1.0` class.
8. **CLI help-renders** — `meridian --help`, `meridian start --help`, `meridian init --help` all exit 0.

Wrapped in `tests/release/test_release_gauntlet.py` with `@pytest.mark.slow` (deselected from the default test run). Runs in ~40-60s. **`[ ALL ] release gauntlet PASSED` is now a hard gate before any future alpha cuts.**

### Tests

99 e2e passing in 12.6s. New regression test `test_project_root_substitutes_meridian_home_when_no_pyproject_reachable` proves the alpha-4 fix — cwd is a tmp dir (NOT a system path), no `pyproject.toml` reachable, `_project_root()` must return `_meridian_home()`.

### Carry-overs unchanged from alpha-3

- Tauri `.msi` still requires Rust + MSVC + WiX (round 18). Alpha-4 is still the Python wheel + PowerShell installer + browser GUI.
- Crash endpoint URL still awaits Cloudflare Worker deployment.
- License public key still awaits keypair generation.
- T-Bionic TLD still TBD.
- Next.js 15.1.6 CVE-2025-66478 — still pending the version bump.
- Install-time UX polish (WPF splash / Inno Setup / Tauri MSI) deferred until install flow is stable. Alpha-4 is "make the existing installer reliable enough that the bundled-installer work isn't being layered on top of a broken foundation".

## What's new in v0.2.0-alpha.3

A targeted fix for the elevated-Admin-cwd bug that prevented alpha-2 from launching the GUI wizard. The PowerShell installer runs as Administrator, so the spawned Python backend inherited `C:\Windows\System32` as its working directory, which then tried to write logs and project DBs there → `PermissionError: [Errno 13]`. The class of bug was previously documented in deferred-installer-fixes notes but the lesson was not applied during alpha-2.

No schema change. Existing projects upgrade with `pip install --upgrade` and no `db-migrate` step.

### The fix (3 layers, defence-in-depth)

- **`config.py` — defensive `_project_root()`.** New `_is_unsafe_cwd()` recognises Windows system paths (System32 / SysWOW64 / Program Files / ProgramData). New `_meridian_home()` resolves to `MERIDIAN_HOME` env var, OR `C:\Meridian` if it exists on Windows, OR `~/Meridian` cross-platform. When the running Python's cwd lands in an unsafe place AND no `pyproject.toml` is reachable from the package source path, `_project_root()` substitutes `_meridian_home()` instead of returning the unsafe cwd. The bug becomes silent-impossible at the source.
- **`config.py` — multi-path `.env` discovery.** The bootstrap dotenv loader now searches `<project_root>/.env` then `<MERIDIAN_HOME>/.env`. The installer writes the Anthropic API key to `C:\Meridian\.env`; alpha-2 didn't load it because cwd was System32 (no `pyproject.toml` above it). alpha-3 picks it up regardless.
- **Installer — explicit env vars + `-WorkingDirectory`.** `Start-Process` for the backend now passes `-WorkingDirectory $MERIDIAN_ROOT`, sets `$env:MERIDIAN_HOME` and `$env:MERIDIAN_PROJECTS_DIR` in the parent shell so the child inherits them. The bug becomes silent-impossible from the launch side too.

### Debug-phase install visibility

Per the deferred install-polish design note, the installer's debug-phase posture is "visibility over polish":

- **Backend window is visible during install.** Replaces alpha-2's `-WindowStyle Hidden`. The user (debugging the install flow) can see import errors, port conflicts, etc. as they happen. A polished hidden launch lands once the install flow stabilises.
- **`backend.log` tee.** Backend stdout+stderr are also redirected to `C:\Meridian\runtime\backend.log` via `cmd /c "<python> -m meridian.api.main >> backend.log 2>&1"`. Forensic trail survives even if the window closes.
- **On `/health` poll timeout, the installer prints the last 30 lines of `backend.log` inline** before falling back to the legacy CLI wizard. No more "silent backend death".

### CLI start banner

`meridian start` now prints the resolved `Meridian home` and `Projects dir` up front — so a config-resolution surprise is visible at a glance instead of failing inside `configure_logging`.

### Tests

98 passing in 12.9s. Adds `tests/e2e/test_install_path_safety.py` covering:
- `_is_unsafe_cwd` happy/sad path table
- `_meridian_home` resolution order (env override / Windows canonical / cross-platform fallback)
- `_project_root` substitutes when cwd is unsafe
- `.env` discovered in MERIDIAN_HOME when project root has none
- Smoke: importing `meridian.api.main` from an unsafe cwd does not raise

The slow concurrency suite (`test_concurrency.py`) remains excluded from the release gate — same posture as alpha-2.

### Carry-overs unchanged from alpha-2

- Tauri `.msi` still requires Rust + MSVC + WiX (round 18).
- Crash endpoint URL still awaits Cloudflare Worker deployment.
- License public key still awaits keypair generation.
- T-Bionic TLD still TBD.
- Next.js 15.1.6 CVE-2025-66478 — still pending the version bump.

## What's new in v0.2.0-alpha.2

A UX-focused follow-up to alpha-1. The headline is that the setup experience is now a **GUI wizard in your browser** with **folder-pick** for documents — built for non-technical construction PMs. alpha-1 dropped users into a `cmd.exe` prompt asking for "a source document" and the SME had no idea what to do; alpha-2 fixes that.

No schema change. Existing projects upgrade with `pip install --upgrade` and no `db-migrate` step.

### The simplification

- **GUI wizard auto-launches in your browser.** The PowerShell installer now starts the FastAPI backend in the background and opens your default browser at `http://localhost:8000/setup/` after install. The CLI `meridian init` flow stays as a fallback only if the backend doesn't come up.
- **Folder-pick for first documents.** The wizard now asks **"Where are your project documents?"** with one button: **"📁 Choose project folder"**. It walks the folder recursively (`os.walk`, full tree, smart pruning of `.git`/`node_modules`/etc.), shows a manifest preview ("Found 47 PDFs, 12 docx, 3 xlsx in `<folder name>` — import them?"), and ingests everything supported in one go. Native folder picker via Tauri when running the desktop build, browser-fallback typed-path input otherwise.
- **Project name auto-derived from the folder.** When you pick a folder, the project name pre-fills from the folder's basename. Pick `Shell-C-D` and the project becomes `shell-c-d` — change it if you'd like, otherwise just press Enter.
- **Step order swapped.** New flow: welcome → api-key → first-documents (pick folder) → first-project (confirm/rename) → ready. The `/setup/import-folder` endpoint creates the project on the fly the first time it's called, so by the time you reach first-project it's a confirm step, not a create step.
- **PM-vernacular prose pass.** "Source document" → "document" or "file"; "ingest" → "import"; "API key" → "Claude AI key (from Anthropic)" with a glossary tooltip. Throughout all 5 wizard pages.
- **`meridian start` command.** Launches the backend (or attaches to one already running) and opens the wizard in the browser. The desktop shortcut now uses this on first run instead of the CLI wizard. Flags: `--no-browser`, `--port`.

### Bug fixes (from the alpha-1 SME install)

- **`app_version: "0.1.0"` in logs** — fixed. `__version__` and `app_version` now resolve from `importlib.metadata.version("meridian")` so they always track pyproject.
- **`Errno 13 Permission denied` when typing a folder path** — fixed. The CLI fallback wizard now `is_dir()`-checks first and offers to walk the folder via `walk_directory`, mirroring the GUI flow.
- **"Wizard aborted but installer reports success"** misleading banner — gone. The installer now ends with "Meridian is starting up. Setup will open in your browser."

### Backend additions

- `POST /setup/import-folder/scan` — returns a manifest of detected ingestable files grouped by kind, plus a list of skipped files with reasons.
- `POST /setup/import-folder` — walks + ingests in one job; auto-creates the project if it doesn't yet exist.
- `GET /setup/import-folder/{job_id}` — poll progress (`{imported, deduped, failed, total, current_file}`).
- `POST /setup/projects/suggest-name` — returns the slugified folder-basename and bumps `-2`, `-3` on collision.
- **FastAPI StaticFiles mount** — serves the bundled Next.js export at `/`, with API routes registered first so `/setup/state` (GET, JSON) and `/setup/` (GET, HTML) coexist correctly.
- **`meridian.ingest.dispatcher.walk_directory`** — reusable directory-walk helper. `os.walk(followlinks=False)`, prunes `.git`/`node_modules`/`__pycache__`/`_meridian`, skips Windows hidden/system files, captures access-denied per-file rather than aborting.

### Wheel-bundling change (build pre-step required)

The wheel now bundles `apps/web/out/` (the Next.js static export) under `src/meridian/_web/`. **Building from source requires `cd apps/web && npm run build` BEFORE `uv build`** — otherwise hatch errors `Forced include not found: apps/web/out`. The PowerShell installer doesn't need this; it pip-installs the published wheel which already has the GUI baked in.

### Tests

- 80 passing in 12.5s (alpha-1 baseline 65 + 19 from Stream A's wizard/dispatcher coverage + 3 from Stream C's `meridian start` smoke − 7 noise from re-numbering / merging). The slow concurrency test class (`test_concurrency.py`) was excluded from the alpha-2 release gate due to a hang on the new project location; the underlying `ProjectLock` code is unchanged from alpha-1.

### Carry-overs unchanged from alpha-1

- Tauri `.msi` still requires Rust + MSVC + WiX — alpha-2 ships as the Python wheel + PowerShell installer + browser GUI. The `.msi` is round 18, blocked on the Rust install.
- Crash endpoint URL still awaits Cloudflare Worker deployment.
- License public key still awaits keypair generation.
- T-Bionic TLD still TBD.
- Next.js 15.1.6 CVE-2025-66478 — still pending the version bump.

## What's new in v0.2.0-alpha.1

First SME-testable build of the v0.2 line. Bundles seven rounds of work on top of alpha-12: the v0.1.x finishers (rounds 13–15), v0.1.x polish (round 14), the Tauri/Next-export refactor (round 16), the setup wizard + FastAPI sidecar (round 17), the company rebrand, and the §3.6/§3.8 deployment prep (round 17.5).

**Heads up before you upgrade:** schema v5 → v6 (one `meridian db-migrate <project>` step). The Tauri `.msi` is **not** in this release — installation is still the PowerShell installer or `pip install meridian`. The .msi lands in alpha-2 once Rust + MSVC + WiX are installed on the build machine.

### Major user-facing additions

- **Onboarding wizard.** `meridian init` walks a six-step setup flow (API key → TOTP → first project → first document → bootstrap LLM sweep → next-steps agenda). State persists between steps so partial completion resumes cleanly.
- **Backup/restore.** `meridian backup create|restore|verify|list` — bundles `<slug>.sqlite` plus all sibling artefact dirs into one zip with SHA-256 manifest. Online backup, safe mid-extraction.
- **Smart taxonomy auto-assessment.** The bootstrap LLM sweep now self-assesses each proposed taxonomy value (`confirm` / `merge_into` / `defer_to_user`) with a confidence score; high-confidence merges auto-apply, lower-confidence routes to the standard review queue with the LLM's recommendation visible. `meridian review walk-taxonomy` renders the recommendation per row and offers `[A]ccept LLM recommendation` as the default keystroke.
- **End-user documentation suite.** Eight docs files (~12,500 words): README index, getting-started, concepts, full CLI reference, troubleshooting, security, architecture, release-notes — all PM-readable, no jargon without first-use definition.
- **Multi-user concurrency safety.** Project locks (`acquire_project_lock` + `ProjectLock` context manager) wrap every extraction job and write-heavy API endpoint. Atomic file create + three-outcome liveness check (alive / dead / unknown). CLI prints friendly holder info on conflict; API returns 409 with holder details. SQLite `busy_timeout` PRAGMA hardened (5s default, 30s on write-heavy paths).
- **Routing-preset operator aliases.** `cloud-default` / `hybrid` / `air-gapped` resolve to the technical preset names. Both forms work at the CLI; existing project DBs unaffected.

### Tauri rebuild (foundation only — no .msi yet)

- **Tauri 2.x scaffold.** `src-tauri/` crate root with the three Tauri plugins wired (dialog, shell, fs), `tauri.conf.json` (1280×800 window, msi bundler, identifier `com.tbionic.meridian`), capabilities tightened to scoped sidecar spawn + dialog open + fs default.
- **Next.js static-export refactor.** All 14 dynamic project pages converted from server components to client components with `useEffect` + `apiFetch` data fetching, three-state UX (loading skeleton / error panel / data render). `output: "export"` enables Tauri to bundle the static `out/` directory as the frontend.
- **Setup wizard (5 pages).** `welcome → api-key → first-project → first-documents → ready` at `/setup/*`. WHY-before-HOW prose in PM language, three-outcome validation per step (valid / invalid / unable_to_verify with skip-with-warning), native Tauri file pickers with browser fallback, full keyboard nav, `?` shortcut sheet on every page.
- **FastAPI sidecar wiring.** Round 17 wires Tauri to spawn the bundled PyInstaller binary (round-18 drop-in) with a `python -m uvicorn` dev fallback, TCP health-gate before window display, idempotent kill on close. Won't actually compile until Rust + MSVC are installed (round 18).
- **§3.6 crash Worker scaffold.** Cloudflare Worker code at `infra/cloudflare/crash-worker/` ready to deploy. Local crash-send refuses to POST to a placeholder endpoint until configured.
- **§3.8 license keypair script.** `scripts/gen_license_keypair.py` generates the Ed25519 signing keypair; private key written to user-supplied path, public key printed as hex for embedding in `meridian.licensing.verify`.

### Rebrand: Undivided Systems → T-Bionic

Company-name change across 11 files (Tauri identifier `com.undivided.meridian` → `com.tbionic.meridian`, pyproject author, brand strings in apps/web, licensing CLI strings, docs). Every previously-`support@undivided.systems` string is now phrased "T-Bionic support" with no specific email — the company TLD is being registered separately and the wrong email shipped in binaries is hard to roll back.

### Defects fixed since alpha-12

- Cross-reference sweep noise reduced 86% (98 borderline → 13) via tightened equipment-tag regex + false-positive blocklist + multi-line-capture cleanup + four-outcome classification (`confirmed` / `borderline` / `external_reference` / `rejected`).
- Tender flag pills now resolve `conflicts_with_source_<uuid>` to filename(s) via the conflict → conflict_party → deliverable → source_document chain.
- Chunk-level resume: interrupted extractions now restart at the chunk boundary (not the source boundary). Per-chunk state machine + transactional source-completion.
- Standards-extraction prompt strengthened (v1.1) with region-grouped recognition cues (AU/NZ, UK/EU/intl, US codes + industry).
- Bootstrap auto-trigger on first import (interactive default-Yes; silent-skip when stdin isn't a TTY).

### Test + schema state

- **65/65 e2e tests passing** in ~11s (16 baseline + 14 in round 14 + 7 in round 15 + 13 in round 17 + 15 from rounds 10/11).
- **Schema v6** — adds the LLM auto-assessment columns to the three taxonomy tables. `meridian db-migrate <project>` is idempotent; safe to re-run.
- **Ruff clean** across `src/meridian/` and `tests/`.

### Known carry-overs

- **Tauri `.msi` requires Rust + MSVC + WiX** on the build machine. Until installed, this release ships as the Python wheel + PowerShell installer.
- **Three Tauri 2 API uncertainties** in the round-17 sidecar wiring (capability JSON shape for scoped `shell:allow-spawn`, `CommandChild::kill()` ownership signature, `RunEvent::WindowEvent` field name) need verification post-Rust-install. Round-18 first task: `cargo build` and fix anything that doesn't compile.
- **Next.js 15.1.6 has CVE-2025-66478** — bump to a patched 15.x before any external-facing release.
- **Crash endpoint URL** awaits Cloudflare Worker deployment.
- **License public key** awaits keypair generation.
- **T-Bionic TLD** still TBD; support strings are placeholder-phrased pending domain registration.

## What's new in alpha-12

Production-readiness scaffolds plus the first automated test suite. Three of four planned streams landed; the web build verification is blocked on a Node install.

- **Local clients for license, update, and crash handling.** All three are wired locally end-to-end and waiting on a deployment decision (signing keys, manifest URL, crash endpoint URL respectively). When those decisions land, wire-up is hours not days. The `crash send` command refuses to POST to a placeholder endpoint — explicit configuration is required before any real send.
- **License (Ed25519 verify).** `meridian license install`, `meridian license status`, `meridian license verify`. Pure-stdlib payload parsing; the `cryptography` library is an optional extra (`pip install meridian[license]`); the module loads without it (returns "needs review" with an install hint). Three-outcome discipline: malformed licenses are routed to "needs review", not silently failed.
- **Updates.** `meridian updates check`, `meridian updates skip`, `meridian updates show-skipped`. Stdlib `urllib`, defensive against URL errors and JSON decode errors, never crashes the host. Manual `--check` only — actual download and install awaits the installer technology decision.
- **Crash reporting (opt-in, preview-before-send).** `meridian crash list`, `meridian crash preview`, `meridian crash send`, `meridian crash opt-in`. Defensive secret redaction runs on every payload. Off by default; opt-in flag persists per machine.
- **API-side TOTP login.** New endpoints: `POST /auth/login`, `POST /auth/logout`, `GET /auth/whoami`, `GET /auth/status`. `whoami` is the only protected one (uses `Depends(require_session)`). Sliding-window rate limit (10 attempts / 5 min per source IP). Constant-time error response — never reveals format-invalid vs wrong-value to the caller. TOTP and recovery codes are never logged.
- **First automated test suite.** `pytest tests/e2e/` — 16 tests, 3.5 s wall time, all passing. Covers project lifecycle, extraction (with transactional EJS rollback + three-outcome classification + chunk-resume + zero-real-LLM regression guard), API smoke (auth/login + tender + glossary), evidence pack round-trip (build + verify + secret-redaction sanity). All offline; the `mock_llm_client` fixture monkeypatches the LLM call function with deterministic stubs.
- **Status: scaffolded — pending decisions.** Code paths are implemented; deployment specifics (signing keys §3.8, update manifest URL §3.5, crash endpoint URL §3.6, installer tech §3.7, code-signing certs §3.4) are still open. See [troubleshooting.md](troubleshooting.md) under "License or update commands say not configured".

## What's new in alpha-11

Defect fixes from the alpha-10 test pass plus a major pipeline-resilience upgrade.

- **Cross-reference sweep classification overhaul.** The alpha-10 sweep was producing 98 findings, all routed to "borderline" (i.e. 98 noise rows about to ambush the SME's review queue). Alpha-11 fixes:
  - **Tightened equipment-tag regex** to require letter-prefix + separator + at least one digit. Words like `GENERAL`, `RFP`, `RFI`, `CHANGE`, `CHW` no longer match.
  - **False-positive blocklist** of common construction abbreviations (BMS, HVAC, UPS without number, DCS, AHJ, IFC, IBC, EPD, BOD, OSE, TR, DR, SOP, TMP, FWK, POL, REF, SPC, SCH, ACC).
  - **Multi-line capture cleanup** — collapses `ISO\n14025`-style cross-line matches.
  - **Four-outcome classification** instead of one: `confirmed` (auto when target anchor is in another ingested doc), `borderline` (truly ambiguous — only these enter review queue), `external_reference` (citation to a doc not in this project's corpus — useful intel for the SME, not noise), `rejected` (blocklist hit, dropped silently).
  - Live result on the test corpus: 125 findings → 0 confirmed / 13 borderline (was 98) / 80 external_reference / 32 rejected. **86% reduction in queue pollution.**
  - **Breaking — schema v3 → v4.** Run `meridian db-migrate <project>` on existing projects.
- **Tender flag-pill UUID resolution.** Alpha-10 tender packages showed `conflicts_with_source_<uuid>` — opaque to a PM. Alpha-11 resolves the UUID to filename(s) via the conflict → conflict_party → deliverable → source_document chain. Defensive fallback to raw string on lookup miss. Per-build cache eliminates N+1. Cover-page flag-summary table re-deduped + sorted on humanised labels. Flags now read e.g. `conflicts with: AT-GLOBAL-OR-000103_SPC-ACC-01[11].pdf, AT-GLOBAL-OR-000303_SCH-ACC-01_Chiller_Demarcation.pdf`.
- **Next.js UI surfaces for tender, evidence, and xref.** Three new pages mirror the alpha-8 patterns: trade list with build button (Tender), build / verify list with confirm dialog (Evidence), four-outcome breakdown with persist confirm (Xref). Project dashboard updated with three new "Hand-off & integrity" tiles. Glossary expanded with three new entries (`tender-package`, `legal-evidence-pack`, `cross-reference-sweep`). Keyboard shortcuts: `?` for cheat sheet, `B` for build, `S` / `P` / `L` for sweep / persist / LLM-assist.
- **Chunk-level resume + transactional source completion.** Before alpha-11, an interrupted job restarted partially-extracted sources from chunk 0; long sources lost prior work. Now:
  - **Per-chunk state machine** on `source_document_chunk`: new columns `extraction_status` (`pending|in_progress|extracted|skipped`), `extraction_started_at`, `extraction_finished_at`, `extraction_job_id`. Triage marks `in_progress` before LLM call; success → `extracted`; rejection → `skipped`. Resume detects orphaned `in_progress` chunks (previous job died mid-call), logs them, resets to `pending`, re-runs.
  - **Transactional source-completion**: deliverable persist + audit persist + EJS state flip from `extracting` → `completed` are now wrapped in one transaction. Crash mid-finalisation rolls back all of it.
  - **Breaking — schema v4 → v5.** Run `meridian db-migrate <project>`. Backfill on existing rows: `triage_marked_for_extraction=1 → extracted`; `=0 → skipped`; NULL → `pending`.
- **CLI display polish.** Xref sweep summary now shows the `external_reference` line in console output alongside confirmed / borderline / rejected.

## What's new in alpha-10

Tier-2 / Tier-3 product modules plus the cross-reference sweep, prompt strengthening, and bootstrap-on-first-import.

- **Tender Package Builder.** New `meridian tender` subapp. Read-only export pipeline: filters the master register to one trade, joins source provenance and taxonomy, groups by service then category, emits xlsx (mirrors the master-register styling) or markdown to `<projects-dir>/<slug>.tenders/`. Cover sheet covers project, trade, timestamp, deliverable count, source-doc list, applicable-standards summary, flag summary, and "review before issue" rows for missing service / category mappings (three-outcome surfacing). Zero DB writes.
- **Legal Evidence Pack.** New `meridian evidence` subapp. Assembles a defensible audit-trail bundle: `MANIFEST.json` (with SHA-256 of every contained file + tool version), `deliverables.csv`, `audit_trail.csv`, `llm_calls.csv` + `llm_calls_full.jsonl` (with defensive secret redaction for `sk-` / `Bearer` prefixes), embedded copies of every prompt referenced, `sources.csv`, `cover.md` (plain-English what-this-pack-proves boilerplate), `chain_of_custody.md` (auto-narrated). `meridian evidence verify <pack.zip>` re-hashes every file vs MANIFEST.
- **Cross-reference exhaustive sweep.** New `meridian xref` subapp. Post-extraction deterministic regex pass over every deliverable looking for explicit textual cross-references in all OTHER source docs (sections, clauses, drawings, specs, MasterFormat, standards, equipment tags, vendor names). Three-outcome classification (confirmed / borderline / rejected — alpha-11 added external_reference). Optional `--llm-assist` flag (defaults off — deterministic pass is useful on its own at zero LLM cost). CSV + Markdown reports emitted to `<projects-dir>/<slug>.reports/xref/`.
- **Standards-extraction prompt strengthened (v1.1).** New `APPLICABLE_STANDARDS — DETECTION` section in the text-spec extraction prompt with a region-grouped recognition-cue prefix list (AU/NZ AS/AS-NZS/NCC/BCA, UK/EU/intl BS/EN/ISO/IEC, US codes IBC/IFC/IMC/NEC/NFPA, US industry ASTM/ANSI/ASHRAE/UL/IEEE/etc.); explicit format-variant tolerance for compound / dated / amended forms; a structured detection step before deliverable extraction; three worked examples covering attach / drop-doc-wide-foreword / preserve-as-written. The strict-citation rule (no document-wide inheritance) is unchanged and reinforced.
- **Bootstrap auto-trigger on first import.** When a project has zero source documents before `meridian import-doc` AND any are imported by it, the command now offers (interactively, default-Yes) to run the bootstrap LLM sweep inline. Silent-skip when stdin isn't a TTY. Two new flags: `--no-auto-bootstrap` and `--bootstrap-sample-size`.
- **Schema migration command.** New `meridian db-migrate <project>` — idempotent opt-in upgrade for existing project DBs. New projects always get the current latest version. **Breaking — schema v2 → v3** to add the cross-reference sweep tables.

## What's new in alpha-9

TOTP authentication scaffold (single-user, self-enrolled).

- **Pure-stdlib TOTP** (RFC 6238). All six SHA-1 test vectors pass. Constant-time comparison via `hmac.compare_digest`. ±30 s clock-skew tolerance.
- **Recovery codes.** Generated formatted (`XXXX-XXXX-XXXX`), hashed at rest, one-time-use enforced (re-use blocked).
- **Sessions.** HMAC-SHA256 signed bearer tokens, 8-hour default expiry, on-disk revocation list.
- **Storage abstraction.** `SecretStore` Protocol with `EncryptedFileStore` default + `KeyringStore` stub for future OS-keychain upgrade.
- **QR code.** Minimal stdlib encoder for the enrolment UI (ASCII + SVG output).
- **CLI.** `meridian auth enroll` / `status` / `verify` / `logout` / `reset`.
- **FastAPI dependency.** `require_session` is defined but not yet applied to existing routes — that decision is held until the API-side login endpoint ships (alpha-12 closed this).

## What's new in alpha-8

Next.js review UI.

- **Eight new pages.** Dashboard, quarantine, audit, questions, conflicts, taxonomy, master, sources — under `apps/web/src/app/projects/[name]/...`. Plus a permanent `/glossary` page.
- **Twelve new review components.** `ReviewLayout`, `Tooltip`, `FlagPill` (with full flag-vocabulary explanation map), `StatusBadge`, `ConfirmDialog`, `EmptyState` (tutorials not bare empty), `FirstUseCallout` (route-keyed localStorage dismissal), `KeyboardShortcutSheet` (`?` opens it), `ToastHost`, `RowDetailDrawer`, `ApiErrorPanel` (errors with next-step guidance), `flagExplanations.ts`.
- **UX discoverability checklist all green.** Every flag pill has a tooltip; every queue has an explanatory empty state; every destructive action wraps in `ConfirmDialog`; every async action shows loading + error-with-retry; `?` opens the shortcut sheet on every queue; dark-theme tokens throughout.
- **Clean hand-off to Python.** Typed API client wraps every endpoint; queue actions hit existing `/projects/{name}/...` POSTs.

## What's new in alpha-7

Observability foundation.

- **Local structured logging.** structlog with rotating JSONL files at `<projects-dir>/<slug>.logs/meridian-YYYYMMDD.log` (10 MB rotation, keep 5). Every CLI invocation, every LLM call, every extraction step, every API request emits a structured event. Log files auto-route to the bound project's directory.
- **LLM-assisted error explanation.** New `meridian explain-last-error <project>` reads the last error from the JSONL log, redacts secrets, and asks the LLM for a plain-English diagnosis + suggested next steps. Crash-report scaffold writes a local JSON dossier ready to send when the endpoint is decided (alpha-12 closed this).
- **Per-project bootstrap LLM sweep.** New `meridian bootstrap` command — first-pass LLM recon over a representative sample of a new project's corpus. Proposes document classes, taxonomy extensions, BOD service mappings, and an authority-chain reading. Proposals land in the existing taxonomy review flow.

## Migration cheatsheet

If you're upgrading from an older alpha, these are the manual steps in order. All migrations are idempotent — safe to re-run.

| From | To | Manual step |
|---|---|---|
| alpha-9 or earlier (schema v2) | alpha-10 (schema v3) | `meridian db-migrate <project>` |
| alpha-10 (schema v3) | alpha-11 (schema v4) | `meridian db-migrate <project>` |
| alpha-11 (schema v4) | alpha-11 (schema v5, same release) | `meridian db-migrate <project>` |
| alpha-11 (schema v5) | alpha-12 | No DB change. New CLI subapps appear automatically. |

After any migration, run `meridian status <project>` and `meridian review-status <project>` to confirm the project is healthy.

## Known carry-overs

- **Optional LLM-assist mode for the cross-reference sweep.** The deterministic pass (with the alpha-11 four-outcome classification) is useful at zero cost; an LLM second-pass for borderline rows is stubbed but disabled by default.
- **Auto-confirm signal strengthening for xref.** Current `confirmed: 0` on small corpora reflects that genuine inside-corpus cross-references are rare with only a few sources. Expect this to climb as the corpus grows.
- **Web build verification.** Blocked at the time of writing on a Node install. Three new alpha-11 pages (tender, evidence, xref) have not yet had a first build through `npm run build`. First-build TypeScript errors may surface; nothing in production scope.
- **Taxonomy auto-quarantine on case-sensitive value mismatch.** Surfaced by the e2e tests. Any LLM-proposed taxonomy value that doesn't case-match the seeded vocabulary auto-quarantines and never reaches the master register until confirmed via `meridian review walk-taxonomy`. Worth a UX nudge ("LLM proposed N new taxonomy values — review them before tendering") on the dashboard or after extraction.
