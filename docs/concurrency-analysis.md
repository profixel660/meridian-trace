# Meridian — concurrency safety audit (round-14)

Scope: identify hazards arising from running multiple Meridian processes
against the same project SQLite file and sibling artefact tree
(`<projects_dir>/<slug>.sqlite`, `<slug>.logs/`, `<slug>.tenders/`,
`<slug>.evidence/`, `<slug>.reports/`, `<slug>.stop_signal`,
`<slug>.lock`). v1 architecture assumes a single user driving one process
at a time, but realistic deployment will see at minimum:

* a long-lived FastAPI server and an interactive CLI both pointed at the
  same project file;
* an operator who opens two terminals and types `meridian extract <slug>`
  in each;
* a backup running while extraction is in flight.

SQLite is in WAL mode (`PRAGMA journal_mode = WAL` set in
`meridian.db.connection.connect`), so multiple readers and one writer is
the supported configuration. The hazards below are not about SQLite
file-format corruption — WAL handles that — they are about **logical
races** between cooperating Meridian processes that share state outside
SQLite (artefact directories, signal files, stdout-coupled subprocesses,
LLM cost accounting).

Severity scale: **critical** (data loss / silent corruption / spend
explosion), **high** (visible incorrect behaviour, recoverable),
**medium** (noisy or confusing but eventually consistent), **low** (edge
case, easily diagnosed).

---

## Hazard 1 — Two extractions on the same project

* **Trigger**: Operator opens two terminals and runs
  `meridian extract syd2-shell-cd` in each, OR a CLI extraction starts
  while the FastAPI server's `POST /projects/{name}/extract` endpoint is
  invoked from a browser. Both processes call
  `run_job_over_sources_isolated`, which spawns per-source worker
  subprocesses against the same SQLite file.
* **Severity**: **critical**.
* **Current behaviour**: Each invocation creates its own
  `extraction_job` row (UUIDs collide-free) and inserts
  `extraction_job_source` rows referencing the same `source_document.id`.
  The chunk-level resume protocol in
  `meridian.extract.triage.run_triage_for_source` flips
  `source_document_chunk.extraction_status` from `pending` →
  `in_progress` → `extracted`/`skipped`. With two jobs racing, both will
  observe `pending` chunks, both will flip them to `in_progress`, and
  both will pay for the Haiku triage call. The downstream Sonnet
  text-spec call then consumes whichever chunk set the second writer
  finalised. EJS rows for both jobs end up in `completed` status pointing
  at overlapping `deliverable` rows — reviewer state is unaffected
  (rows are append-only with new `id`s) but the master register doubles
  up, cost is doubled, and the `worker_pid` column on each EJS row
  records the LAST writer's PID, breaking pause/kill semantics. **No
  process-level mutual exclusion exists today.**
* **Recommended mitigation**: process-level project lock at the
  `<projects_dir>/<slug>.lock` path, JSON payload identifying
  holder PID/host/purpose/timestamp, orphan detection via `os.kill(pid,
  0)` on POSIX and a 60-second grace window on Windows (where PID
  existence ≠ aliveness). Wraps both `run_job_over_sources` and
  `run_job_over_sources_isolated`.
* **Implemented?** **Yes** — see `meridian.projects.acquire_project_lock`
  (Part B of round-14).

## Hazard 2 — CLI and API running concurrently against the same DB

* **Trigger**: `uvicorn meridian.api.main:app` is running for the Next.js
  shell while the operator runs `meridian status syd2-shell-cd` in a
  terminal.
* **Severity**: **low** (read-only) → **high** (when the API endpoint is
  one of the writers: `/projects/{name}/extract`, `/projects/{name}/sources`
  POST, `/projects/{name}/deliverables/{id}/edit`, etc.).
* **Current behaviour**: SQLite WAL handles the concurrent reads
  trivially. Concurrent writes serialise at the SQLite-level via the
  `BEGIN IMMEDIATE` we issue in `transaction()`, so the second writer
  blocks (default 5-second `busy_timeout` is the SQLite default — note:
  we do NOT set a `PRAGMA busy_timeout`, so under contention the second
  writer raises `sqlite3.OperationalError: database is locked` rather
  than waiting). Reviewer endpoints are tiny single-statement
  transactions so contention is rare; an extraction job, however, holds
  many small transactions back-to-back over many minutes, and a
  concurrent reviewer write WILL occasionally see "database is locked".
* **Recommended mitigation**:
  1. Add `conn.execute("PRAGMA busy_timeout = 5000;")` to `connect()`
     so transient writer collisions wait instead of erroring. Pure
     SQLite-level fix, no API change.
  2. Document in CONTEXT.md that the project lock from Hazard 1 does
     NOT cover API endpoints — the API is intentionally lock-free for
     reviewer interactions, since they are short and the EJS job-source
     state machine already serialises the only long-running write.
* **Implemented?** **Yes** (round-15 hardening) — `connect()` now sets
  `PRAGMA busy_timeout` on every connection. Default 5000 ms covers the
  short reviewer/CLI writes; the extraction worker and long-running API
  write paths (extract, ingest, bootstrap, reviewer mutations) opt in to
  30000 ms. Read-only endpoints keep the 5 s default. See
  `tests/e2e/test_busy_timeout.py` for the regression guards.

## Hazard 3 — Writer starvation by long-running readers

* **Trigger**: A Python REPL or a stale `sqlite3` CLI session holds an
  open transaction (`BEGIN`) against a project DB while extraction
  runs. WAL allows the writer to proceed but the WAL file grows
  unboundedly until the reader closes (the `wal_checkpoint` cannot
  truncate while a reader's snapshot pins old frames).
* **Severity**: **medium**. Disk usage symptom; not a correctness issue.
* **Current behaviour**: We never explicitly `PRAGMA wal_checkpoint`.
  SQLite auto-checkpoints at ~1000 pages in the default config; pinned
  readers defer. A forgotten REPL session can grow `<slug>.sqlite-wal`
  to gigabytes.
* **Recommended mitigation**: run `PRAGMA wal_checkpoint(TRUNCATE)` at
  CLI exit (the orchestrator's `finally:` would be a natural site), or
  document the operator-visible disk symptom and the recovery (close
  any other connections; checkpoint runs on next writer).
* **Implemented?** **No** (low likelihood in real ops; documented).

## Hazard 4 — Backup-during-extraction safety

* **Trigger**: Operator runs `meridian backup syd2-shell-cd` while
  `meridian extract syd2-shell-cd` is in flight (per the round-13 plan;
  the backup CLI calls `sqlite3.Connection.backup`).
* **Severity**: **low**.
* **Current behaviour**: `meridian.backup.create_backup` does not yet
  exist as an importable callable in this tree — the
  `meridian/backup/__init__.py` re-exports from `meridian.backup.backup`
  but no such module is committed. Once it lands and uses
  `sqlite3.Connection.backup`, the SQLite online-backup API is
  page-by-page and acquires a shared lock per page; it is safe alongside
  WAL writes (the backup will see a consistent snapshot — possibly
  stale by the moment the zip closes, but never torn). The artefact-
  directory side (`<slug>.tenders/`, `<slug>.evidence/`) IS subject to
  read-while-write — a tender PDF being written by an in-flight job
  could be zipped half-complete.
* **Recommended mitigation**: when the backup module lands, wrap
  `create_backup` in the same project lock with `purpose="backup"`. The
  lock is NOT about SQLite (WAL covers that); it is about (a) preventing
  two concurrent backups colliding on the output zip path, and (b)
  preventing a backup from racing an extraction's artefact-directory
  writes.
* **Implemented?** **Out-of-scope-for-this-round** — `meridian.backup.backup`
  module does not exist in tree yet. Lock primitive is in place; wrap
  the call when the module lands.

## Hazard 5 — Stop-signal file races

* **Trigger**: Operator runs `meridian pause syd2-shell-cd` twice in
  quick succession, OR runs `pause` followed immediately by
  `extract` (the new extract command unconditionally `unlink()`s the
  stop-signal at start, but the CLI `pause` and the orchestrator's
  poll-then-unlink are not atomic).
* **Severity**: **low**.
* **Current behaviour**: `extract` clears the file at start; `pause`
  writes it; the orchestrator only `.exists()`-polls it between sources
  (`run_job_over_sources_isolated`). A `pause` issued during the small
  window after `extract` clears it but before the orchestrator first
  polls will be honoured at the NEXT source boundary. A `pause` issued
  AFTER the orchestrator's last poll (i.e. on the final source) is
  silently lost when the orchestrator deletes the file in `extract`'s
  next start. No corruption, no incorrect behaviour — just a "pause was
  ignored" surprise.
* **Recommended mitigation**: on `pause`, write a sidecar with the
  job_id; on `extract` start, only delete the stop-signal if its job_id
  matches a no-longer-running job. Or: rename the stop-signal to
  `<slug>.stop_signal.<job_id>` so the orchestrator's filter is precise.
* **Implemented?** **No** (low severity; pause UX is already "best
  effort, between sources").

## Hazard 6 — Session token concurrency (multiple browser tabs)

* **Trigger**: Operator logs in once via `POST /auth/login`, opens three
  browser tabs all hitting the same FastAPI process, each using the
  same session token from `meridian.auth.session`.
* **Severity**: **low**.
* **Current behaviour**: Tokens are looked up in the SQLite `auth_session`
  table on every request; reads are concurrency-safe (WAL). Logout
  deletes the row; if Tab A logs out while Tab B is mid-request, Tab B
  receives 401 on its next call — correct fail-closed behaviour. There
  is no shared mutable in-memory state per token.
* **Recommended mitigation**: none required. Document the fail-closed
  semantic in the auth API docs.
* **Implemented?** **Out-of-scope-for-this-round** (no defect to fix).

## Hazard 7 — LLM-call double-billing on process restart

* **Trigger**: Extraction worker subprocess crashes (or is killed) AFTER
  the LLM HTTP call returns 200 but BEFORE the response is persisted to
  the `llm_call` table. On resume, the orchestrator re-issues the same
  call; the provider charges twice.
* **Severity**: **high** (user-visible cost).
* **Current behaviour**: `meridian.llm.client.call_llm` writes the
  `llm_call` row in the same transaction as the response is observed;
  if the worker crashes mid-call, the LLM provider may have already
  billed. The `input_hash` dedup path described in the
  extraction_worker docstring (round-9 §6 Part B) prevents
  RE-CALLING the LLM on the next attempt for the same input, but it
  cannot recover the FIRST call's response, so the bill is paid twice
  (first call wasted, second call billed and persisted). For triage
  this is cents per source; for a 64k-token Sonnet text-spec call it is
  meaningful.
* **Recommended mitigation**: write a `pending` row to `llm_call`
  BEFORE the HTTP call (with `started_at`, `input_hash`, but
  `response_text=NULL`); on resume, query for orphaned `pending` rows
  for the same `input_hash` and treat them as confirming "we paid for
  this; do not re-call; mark this run as needing manual reconciliation
  with the provider's usage dashboard". Out of scope for this round
  but flagged for round-15+.
* **Implemented?** **No** (documented; non-trivial schema change —
  `llm_call` currently has `response_text NOT NULL`; would need a v6
  migration to allow pending-row pattern).

---

## Summary

| # | Hazard | Severity | Implemented this round |
|---|---|---|---|
| 1 | Concurrent extraction on same project | critical | yes |
| 2 | CLI + API concurrent writes (busy_timeout) | high | yes (round-15: 5000 ms default, 30000 ms for long writers) |
| 3 | Reader starvation grows WAL | medium | no |
| 4 | Backup-during-extraction artefact race | low | out of scope (module not yet committed) |
| 5 | Stop-signal file race | low | no |
| 6 | Session token concurrency | low | no defect |
| 7 | LLM double-billing on crash | high | no (schema change) |

Two hazards (1 and 7) materially affect cost or correctness; only 1 is
addressed this round because 7 needs a schema migration to do safely.
The added project lock closes the headline path for hazard 1 and
provides the primitive for hazards 4 and (when wired) 2 in future
rounds.

---

## Liveness check — three-outcome discipline

The PID-liveness check used by the project lock to detect orphaned
locks returns one of three states:

* **alive** — `os.kill(pid, 0)` succeeds (POSIX) or a process with
  matching PID is found in the OS process table. The existing lock is
  honoured; `ProjectBusy` is raised.
* **dead** — `os.kill(pid, 0)` raises `ProcessLookupError`. The lock is
  considered orphaned (process crashed without releasing); it is taken
  over with a structured warning `projects.lock.orphan_taken_over`.
* **unknown** — `os.kill(pid, 0)` raises `PermissionError` (POSIX, lock
  held by a different user) OR we are on Windows and have no `psutil`
  to consult the process table. **Default policy: respect the lock,
  but only within a 60-second grace window from `acquired_at`.** After
  the grace window the unknown state degrades to `dead` and the lock is
  taken over — this prevents a permanently-stale lock from a Windows
  hard-crash from blocking all future extractions on the project.

This matches the user's three-outcome evaluation default: never
silently treat ambiguity as one of the certain outcomes.
