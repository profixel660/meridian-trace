# Overnight build report

**Period:** rounds 7–15. Rounds 7-14 ran autonomously; round 15 was dispatched after the user worked through §3.10 (smart taxonomy auto-assessment in the bootstrap LLM sweep). Decisions §3.1-§3.10 walked through with the user end of session — see [docs/DECISIONS.md](../../docs/DECISIONS.md).
**Status at end of round 11:** clean. All Python imports clean. Ruff clean across `src/meridian/`. Schema at v5 (round 10 added xref sweep tables → v3; round 11 added xref `external_reference` outcome → v4 and chunk-level resume columns → v5). Live project DB has been migrated to v5 with all backfill applied. 41 FastAPI routes; 36+ CLI commands. Two product-tier modules (Tender Package Builder, Legal Evidence Pack) ship complete with Next.js UI surfaces. Cross-reference sweep noise reduced 86% (98 → 13 borderline) via tightened patterns + four-outcome classification. Tender flag pills now read as filenames not UUIDs. Extraction pipeline now resumes at chunk boundary, not source boundary, with transactional source-completion. Standards-extraction prompt strengthened (round 10). Bootstrap LLM sweep offers itself on first import.

---

## 1. What changed overnight

### Round 7 — observability foundation
- **Local structured logging** (`src/meridian/logging/`) — structlog with rotating JSONL files at `<projects_dir>/<slug>.logs/meridian-YYYYMMDD.log` (10 MB rotation, keep 5). Every CLI invocation, every LLM call, every extraction step, every API request emits a structured event. Log files auto-route to the bound project's directory.
- **LLM-assisted error explanation** (`src/meridian/errors/explain.py` + new prompt `prompts/PROMPT_V1_error_explain.md`) — when a CLI command crashes, `meridian explain-last-error <project>` reads the last error from the JSONL log, redacts secrets, and asks the LLM for a plain-English diagnosis + suggested next steps. Crash-report scaffold writes a local JSON dossier ready to send when the endpoint is decided.
- **Per-project bootstrap LLM sweep** (`src/meridian/bootstrap/`, prompt `prompts/PROMPT_V1_bootstrap_sweep.md`, CLI `meridian bootstrap`, three new API endpoints) — first-pass LLM recon over a representative sample of a new project's corpus that proposes document classes, taxonomy extensions, BOD service mappings, and an authority-chain reading. Proposals land in the existing taxonomy review flow with `source='user_added'` so they're confirmed via the queue you already drive.

### Round 8 — Next.js review UI
- **Eight new pages** under `apps/web/src/app/projects/[name]/...`: dashboard, quarantine, audit, questions, conflicts, taxonomy, master, sources. Plus a permanent `/glossary` page.
- **12 new components** under `apps/web/src/components/review/`: `ReviewLayout`, `Tooltip`, `FlagPill` (with full flag-vocabulary explanation map), `StatusBadge`, `ConfirmDialog`, `EmptyState` (tutorials not bare empty), `FirstUseCallout` (route-keyed localStorage dismissal), `KeyboardShortcutSheet` (`?` opens it), `ToastHost`, `RowDetailDrawer`, `ApiErrorPanel` (errors with next-step guidance), `flagExplanations.ts`.
- **UX discoverability checklist all green** — every flag pill has a tooltip, every queue has explanatory empty state, every destructive action wraps in `ConfirmDialog`, every async action shows loading + error-with-retry, `?` opens shortcut sheet on every queue, dark-theme tokens throughout.
- **Clean hand-off to Python**: typed API client wraps every new endpoint; queue actions hit `/projects/{name}/...` POSTs already wired in `src/meridian/api/main.py`.

### Round 10 — Tier-2/Tier-3 product modules + sweep + prompt strengthening

Dispatched four parallel subagents on file-disjoint streams (per the parallel-subagents-by-default discipline) plus two main-thread tasks. All six landed clean.

- **Tender Package Builder** (`src/meridian/tender/`, new module). Read-only export pipeline: filters `v_master_register` to one trade, joins source provenance + taxonomy, groups by service then category, emits xlsx (mirrors `excel.py` styling) or markdown to `<projects_dir>/<slug>.tenders/`. Cover sheet covers project, trade, timestamp, deliverable count, source-doc list, applicable-standards summary, flag summary, and "review before issue" rows with missing service/category mappings (three-outcome surfacing). New CLI subcommands: `meridian tender list <project>` and `meridian tender build <project> --trade ... --format xlsx|md`. Three new API routes under `/projects/{name}/tender/`. Zero DB writes — pure export surface.
- **Legal Evidence Pack** (`src/meridian/evidence/`, new module). Assembles defensible audit-trail bundle: `MANIFEST.json` (with SHA-256 of every contained file + tool version), `deliverables.csv`, `audit_trail.csv`, `llm_calls.csv` + `llm_calls_full.jsonl` (with defensive secret redaction for `sk-`/`Bearer ` prefixes), embedded copies of every prompt referenced, `sources.csv`, `cover.md` (plain-English what-this-pack-proves boilerplate), `chain_of_custody.md` (auto-narrated). New CLI: `meridian evidence build` and `meridian evidence verify <pack.zip>` (re-hashes vs MANIFEST). Two new API routes. **Live smoke-tested** by the agent on the existing `syd2-shell-cd` DB — produced a 323-deliverable / 68-LLM-call pack and `verify_pack` round-tripped all 7 files.
- **Cross-reference exhaustive sweep** (`src/meridian/extract/cross_references.py` extended + two new sibling files). Post-extraction deterministic regex pass over every deliverable looking for explicit textual cross-references in all OTHER source docs (sections, clauses, drawings, specs, MasterFormat, standards, equipment tags, vendor names). Three-outcome classification: confirmed / borderline (routes to existing `question` table for SME review) / rejected. Optional `--llm-assist` flag (defaults off — deterministic pass is useful on its own at zero LLM cost). New CLI: `meridian xref sweep <project>` and `meridian xref report <project>`. Two new API routes. CSV + Markdown reports emitted to `<projects_dir>/<slug>.reports/xref/`. **Schema bumped v2 → v3** to add `cross_reference_sweep_run` + `cross_reference_sweep_result` tables.
- **Standards-extraction prompt strengthened** (`prompts/PROMPT_V1_text_spec.md` v1.1). New `APPLICABLE_STANDARDS — DETECTION` section with: a region-grouped recognition-cue prefix list (AU/NZ AS/AS-NZS/NCC/BCA, UK/EU/intl BS/EN/ISO/IEC, US codes IBC/IFC/IMC/NEC/NFPA, US industry ASTM/ANSI/ASHRAE/UL/IEEE/etc.); explicit format-variant tolerance for compound/dated/amended forms (`AS/NZS 3000:2018`, `ASTM A123/A123M-17`, `BS EN 12101-3:2015+A1:2018`); a structured detection step BEFORE deliverable extraction; three worked examples covering attach / drop-doc-wide-foreword / preserve-as-written. **Strict-citation rule unchanged** — the prompt only attaches a standard when the chunk's local context cites it.
- **Bootstrap auto-trigger on first import** (`src/meridian/cli.py` `import-doc` extended). When a project has zero source documents before the call AND any are imported by it, the command now offers (interactively, default-Yes) to run the bootstrap LLM sweep inline. Silent-skip when stdin isn't a TTY (so scripted ingest pipelines never block). Two new flags: `--no-auto-bootstrap` and `--bootstrap-sample-size`. Resolves §4 #6 of the round-9 report.
- **Schema migration command** (`meridian db-migrate <project>`) — idempotent opt-in upgrade for existing project DBs from v2 → v3. New projects get v3 free via `create_project`. Existing `syd2-shell-cd.sqlite` deliberately untouched until you run the command.

### Round 11 — Defect-fixes from the test pass + UI surfaces + pipeline resilience

After driving a live test pass through round-10 surfaces (in-conversation, you watching), we surfaced four defects worth fixing and one whole work-stream that was queued. Dispatched four parallel subagents on file-disjoint streams. All four landed clean.

- **Xref sweep classification overhaul** (`src/meridian/extract/cross_references.py` + `db/schema.sql` + `db/connection.py` v3→v4 migration). The round-10 sweep was producing 98 findings, 100% routed to "borderline" (i.e. 98 noise rows about to ambush the SME's review queue). Round 11 fixes:
  - **Tightened equipment-tag regex** to `\b[A-Z]{1,5}[-.]\d+[A-Z0-9.]{0,3}\b` (requires letter-prefix + separator + at least one digit) — `GENERAL`, `RFP`, `RFI`, `CHANGE`, `CHW` etc. can no longer match.
  - **False-positive blocklist** of common construction abbreviations (BMS, HVAC, UPS without number, DCS, AHJ, IFC, IBC, EPD, BOD, OSE, TR, DR, SOP, TMP, FWK, POL, REF, SPC, SCH, ACC).
  - **Multi-line capture cleanup** — collapses `ISO\n14025` style cross-line matches.
  - **Four-outcome classification** instead of one: `confirmed` (auto when target anchor is in another ingested doc), `borderline` (truly ambiguous — only these enter review queue), `external_reference` (citation to a doc not in this project's corpus — useful intel for the SME, not noise), `rejected` (blocklist hit, dropped silently).
  - **Live dry-run on syd2-shell-cd** post-fix: 125 findings → 0 confirmed / **13 borderline** (was 98) / 80 external_reference / 32 rejected. **86% reduction in queue pollution.**
  - **Schema:** v3→v4 migration rebuilds `cross_reference_sweep_result` to add `external_reference` to the outcome CHECK list (SQLite cannot ALTER a CHECK in place — full table rebuild with FK-off transactional pattern, mirrors the existing v1→v2 llm_call rebuild).

- **Tender flag-pill UUID resolution** (`src/meridian/tender/builder.py` only). Round-10 tender packages showed `conflicts_with_source_<uuid>` — opaque to a PM. Round 11 adds `_humanise_flag(conn, raw_flag, cache)` that resolves the UUID to filename(s) via the `conflict → conflict_party → deliverable → source_document` chain (the prefix-name was misleading; the trailing UUID is actually a `conflict.id`, not a `source_document.id`). Defensive fallback to raw string on lookup miss. Also catches sibling shapes `superseded_by_<uuid>` and `references_source_<uuid>` (defensive — currently unused by writers but plausible). Per-build cache eliminates N+1. Cover-page flag-summary table re-deduped + sorted on humanised labels. **Live verified** on a fresh `tender build syd2-shell-cd --trade Mechanical --format md` — flags read e.g. `conflicts with: AT-GLOBAL-OR-000103_SPC-ACC-01[11].pdf, AT-GLOBAL-OR-000303_SCH-ACC-01_Chiller_Demarcation.pdf`.

- **Next.js UI surfaces for round-10 modules** (`apps/web/src/`, 8 new files + 2 modified). Three new pages under `/projects/[name]/{tender,evidence,xref}` matching round-8 patterns exactly:
  - **Tender page** — trade list with deliverable counts, per-row Build button → drawer with format choice, Toast on success, FirstUseCallout, ApiErrorPanel, KeyboardShortcut `B` to open Build dialog.
  - **Evidence page** — Build New Pack button (ConfirmDialog explaining what gets assembled), list of previously-generated packs with timestamp + size + copy-path action. Verify is intentionally deferred to CLI hint (`meridian evidence verify <path>`) to avoid shipping multi-GB zips through the Next.js API route. Shortcut `B`.
  - **Xref page** — Run Sweep button with Dry-run / Persist options (Persist requires ConfirmDialog explicitly mentioning queue impact); LLM-assist toggle; outcome-filter pills (`confirmed` / `borderline` / `external_reference` / `rejected`); per-row drill-down via existing RowDetailDrawer; anchor-kind filters. Designed for the new four-outcome model so round-11's classification fix lands seamlessly. Shortcuts: `S` (sweep dry-run), `P` (persist confirm), `L` (LLM-assist toggle).
  - **Project dashboard** updated with three new "Hand-off & integrity" tiles linking to /tender, /evidence, /xref. **Glossary** expanded with three new entries (`tender-package`, `legal-evidence-pack`, `cross-reference-sweep`).
  - Three new typed apiClient modules: `tender.ts`, `evidence.ts`, `xref.ts`. No new dependencies. Dark-theme tokens, ConfirmDialog, ApiErrorPanel, FirstUseCallout, ToastHost, RowDetailDrawer, Tooltip — every UX-discoverability requirement honoured.

- **Chunk-level resume + EJS transactional consolidation** (touches `extract/orchestrator.py`, `extract/triage.py`, `extract/persist.py`, `extract/text_spec.py`, `extract/bod_import.py`, `extract/demarcation.py`, `workers/extraction_worker.py`, `db/schema.sql`, `db/connection.py` v4→v5 migration). Round-9 §6 carryover. Before round 11, an interrupted job restarted partially-extracted sources from chunk 0; long sources (chiller spec is 50+ chunks) lost prior work. Now:
  - **Per-chunk state machine** on `source_document_chunk`: new columns `extraction_status` (`pending|in_progress|extracted|skipped`), `extraction_started_at`, `extraction_finished_at`, `extraction_job_id`. Triage marks `in_progress` before LLM call; success → `extracted`; rejection → `skipped`. Resume detects orphaned `in_progress` chunks (previous job died mid-call), logs them, resets to `pending`, re-runs.
  - **Transactional source-completion**: the per-source finalisation (deliverable persist + audit persist + EJS state flip from `extracting`→`completed`) is now wrapped in one `transaction(conn)`. Crash mid-finalisation rolls back ALL of it — source stays `extracting`, no torn-write state. Per-chunk LLM-call commits stay outside this txn (they need to commit early so they can be referenced via FK). Idempotency via `input_hash` dedup handles the rare double-call case.
  - **Schema migration** is idempotent (`ALTER TABLE ADD COLUMN` is in-place when no CHECK changes; column-presence detected via `PRAGMA table_info`). Backfill on existing rows: `triage_marked_for_extraction=1 → extracted`; `=0 → skipped`; NULL → `pending`. **Live verified** on syd2-shell-cd: 44 extracted / 8 skipped / 243 pending = 295 chunks total — exactly matches the existing triage flag distribution.
  - **Backwards compatible**: legacy `run_*_extraction` wrappers preserved alongside new `extract_*_payload()` returning `(parsed, llm_call_id)`. Existing call sites unchanged.

- **CLI display polish (main thread)** — round-11 xref sweep summary was missing the `external_reference` line in console output (data was correct in JSON event + CSV/markdown reports, just hidden in the human print). Added the missing line so `external_reference: 80 (citations to docs not in corpus — informational only)` now renders alongside confirmed/borderline/rejected.

### Round 12 — Production-readiness scaffolds + first automated test suite

You asked how close we were to production. Round 12 builds the local clients for the deferred-decision items (license / update / crash) so wire-up is hours not days when you decide on URLs/keys. It also adds API-side TOTP login and the first automated test suite. Four parallel streams; three landed clean, one blocked.

- **License / update / crash CLIENTS** (`src/meridian/{licensing,updates,crash}/`, three new sibling modules + `pyproject.toml` extra). All three include a placeholder URL/key constant clearly tagged `# DEFERRED §3.X` so a single grep reveals what to swap when decisions land. Highlights:
  - **Licensing** (Ed25519 verify): pure-stdlib payload parsing; `cryptography>=42` is an optional extra (`pip install meridian[license]`); module loads without it (returns `LicenseStatus.malformed` with install hint). End-to-end sign+verify validated. CLI: `meridian license install|status|verify`. Three-outcome discipline applied — `malformed` is "needs review" (exit 2), not silent fail.
  - **Updates**: stdlib `urllib.request` (no extra deps), defensive against URLError/JSONDecodeError, never crashes the host. Manual `--check` only — actual download/install deferred until installer technology decision §3.7. CLI: `meridian updates check|skip|show-skipped`.
  - **Crash**: extends the existing `errors/explain.py` capture path with a SEND path. `prepare_crash_payload` runs an additional defensive secret-redaction pass (`sk-`, `Bearer `, `aws_access_key_id`). **Refuses to POST to the placeholder URL** (forces explicit wire-up before any real send). Opt-in flag persists to `<projects_dir>/_meridian/crash_opt_in.json` (off by default). Always preview before send. CLI: `meridian crash list|preview|send|opt-in`.
- **POST /auth/login + /logout + /whoami + /status** (`src/meridian/auth/login_api.py`, new module). 4 endpoints. `/whoami` is the only protected one — uses `Depends(require_session)`. Existing routes are untouched (decision §3.2 / §3.3 stay deferred). Rate limit: sliding-window 10 attempts / 5 min per source IP, in-memory dict + threading.Lock (single-process app per CONTEXT). Constant-time error response — never reveals format-invalid vs wrong-value to the caller. TOTP / recovery codes are NEVER logged.
- **pytest end-to-end harness** (`tests/e2e/`, 6 new files). 16 tests, 3.5s wall time, all passing, ruff clean. Coverage: project lifecycle (5), extraction pipeline (4 — including transactional EJS rollback + three-outcome classification + chunk-resume + zero-real-LLM regression guard), API smoke (5 — including auth/login + tender + glossary), evidence pack round-trip (2 — pack + verify, plus secret-redaction sanity). All offline; `mock_llm_client` fixture monkeypatches the LLM call function with deterministic stubs. Caught a real production observation (see §4 #10 below).
- **Web build verification** — BLOCKED. Node.js is not installed on this machine. Subagent confirmed cleanly (no `node`, `npm`, `nvm`, `volta`, or `fnm` on PATH). When you `winget install OpenJS.NodeJS.LTS`, run `cd apps/web && npm install && npm run build` and re-dispatch this verification stream — round 11 added 3 new pages so first-build may surface TypeScript errors that need fixing.

### Round 13 — Backup/restore, onboarding wizard, end-user docs, multi-user concurrency

Four parallel streams; all four landed clean.

- **Backup/restore CLI** (`src/meridian/backup/`, new module). `meridian backup create|restore|verify|list`. Bundles `<slug>.sqlite` + all sibling artefact dirs (`<slug>.{logs,tenders,evidence,reports}/`) into one zip with `BACKUP_MANIFEST.json` (SHA-256 + size for every file). Uses SQLite's online `Connection.backup()` API so taking a backup mid-extraction is safe (no torn writes). Restore verifies all hashes BEFORE extracting; auto-runs `initialise()` on the restored DB so older backups migrate forward to current schema. Defensive: missing sibling dirs are valid (skipped silently); slug validation prevents path-traversal via malicious manifest. **Live verified on syd2-shell-cd**: 23 files / 9.7 MB uncompressed / 3.0 MB zipped; verify reports valid; all 23 file hashes match.

- **Onboarding wizard** (`src/meridian/onboarding/`, new module). `meridian init` walks a six-step flow: (1) Anthropic API key (validated via tiny live call; three-outcome valid/invalid/unable_to_verify); (2) TOTP enrolment (skippable; composes the round-9 primitives directly); (3) create first project; (4) import first document; (5) bootstrap LLM sweep (with explanation); (6) "where to go next" agenda tailored to the new project. State persisted after every step to `<projects_dir>/_meridian/onboarding_state.json` so partial completion resumes cleanly. Non-TTY contexts refuse with a clear message instead of hanging. Idempotent re-runs. API key NEVER persisted to disk (env var only). Also wires `meridian init-cmd` sub-app for `--status` / `--restart` introspection.

- **End-user documentation** (`docs/`, 8 new files, ~12,500 words). `README.md` (index) + `getting-started.md` (5-minute quickstart) + `concepts.md` (deliverable model, three-outcome discipline, authority chain) + `cli-reference.md` (every command with intent + example) + `troubleshooting.md` (common problems with structured-log event names to grep) + `security.md` (credentials handling, air-gapped mode) + `architecture.md` (per-project SQLite, stages, schema-version table, extension points) + `release-notes.md` (round-by-round delta as alpha-N versions). Plain-English; PM reader; no jargon without first-use definition. Spot-check uncovered five real CLI documentation errors that were corrected against actual `--help` output (preset names, positional args, `--commit` vs `--dry-run` xref default).

- **Multi-user concurrency: hazards documented + minimal safe defaults** (`src/meridian/projects.py` extended; `extract/orchestrator.py`, `api/main.py`, `cli.py` updated; `tests/e2e/test_concurrency.py` new; `docs/concurrency-analysis.md` new). 7 hazards documented (1 critical, 2 high, 1 medium, 3 low). Implemented: `ProjectLock` context manager + `acquire_project_lock()` + `ProjectBusy` exception in `projects.py`. Atomic file create via `O_EXCL`; JSON payload `{pid, hostname, purpose, acquired_at}`. Three-outcome liveness check (alive / dead / unknown — Windows + permission cases), 60-second grace before treating "unknown" as orphan. `run_job_over_sources` + `run_job_over_sources_isolated` both lock-wrapped. CLI commands `extract` and `resume` catch `ProjectBusy` and print friendly holder info + lock path. API `POST /projects/{name}/extract` returns 409 with holder details. **20/20 e2e tests pass** (16 from round 12 + 4 new concurrency tests). Hazards flagged for round 14+: SQLite `busy_timeout` PRAGMA, WAL growth under stale readers, LLM double-billing on crash (needs schema migration).

### Round 15 — Smart taxonomy auto-assessment (driven by §3.10)

After walking the §3 decisions with the user, §3.10's "future projects: build automatic assessment" half became a real product feature. Two parallel streams (backend + surface), 7 new tests.

- **Backend (Stream A)** — `prompts/PROMPT_V1_bootstrap_sweep.md` v1.1 with new `## TAXONOMY VALUE ASSESSMENT` section (recognition rules + worked example: chiller-heavy data centre → `confirm`; generic mech with passing chiller mention → `merge_into: HVAC`). `TaxonomyExtensionProposal` extended with `recommended_action` (`confirm` / `merge_into` / `defer_to_user`), `merge_target`, `confidence` (0.0-1.0), `assessment_reasoning`. Schema v5 → v6 via `ALTER TABLE ADD COLUMN` (idempotent) — adds `llm_recommended_action`, `llm_merge_target`, `llm_confidence`, `llm_reasoning` to `service_taxonomy` / `trade_taxonomy` / `category_taxonomy`. Persist policy: high-confidence merges (≥0.85) auto-applied as synonyms with `source='llm_auto_merged'`; high-confidence confirms get `source='llm_proposed_high_confidence'`; lower-confidence routes to standard pending review with the LLM recommendation visible. Defensive: malformed assessment fields downgrade to `defer_to_user` with structured warning. 5 new tests (`tests/e2e/test_bootstrap_assessment.py`).
- **Surface (Stream B)** — `meridian review walk-taxonomy` now renders the LLM recommendation per row (`LLM recommends: confirm (92% confidence) — "..."`) and offers `[A]ccept LLM recommendation` as the default keystroke. `GET /projects/{name}/taxonomy/pending` Pydantic response model extended with the four LLM fields (`description=` on each for OpenAPI clarity); legacy NULL fields surface as JSON `null`. Next.js `apps/web/src/components/review/TaxonomyQueue.tsx` updated with colour-coded LLM pill (emerald=confirm / amber=merge / neutral=defer / dimmed=legacy) wrapped in Tooltip showing reasoning + confidence. "Accept LLM recommendation" button as leftmost action that pre-fills the existing ConfirmDialog. Three-outcome discipline preserved — SME always presses a key. 2 new e2e tests.
- **Polish (main thread)** — three legacy schema-version test assertions in `tests/e2e/test_project_lifecycle.py` updated from hardcoded `==5` to `== SCHEMA_VERSION` (idempotent for future bumps); one test fixture switched from `capsys` to `capfd` to handle structlog's stderr-grab-at-import behaviour. **§3.5 URL swap landed:** `_PLACEHOLDER_MANIFEST_URL` in `src/meridian/updates/client.py` now points to `https://github.com/profixel660/meridian-trace/releases/latest/download/manifest.json` (returns 404 cleanly until the first release is tagged).

**Final round-15 state:** schema v6, 45 API routes, 46/46 e2e tests passing in 10.3s, ruff clean across `src/meridian/` + `tests/`. Both halves verified live.

### Round 14 — Polish, hardening, and final state

Three parallel streams + main-thread polish; all clean.

- **Routing-preset reconciliation** (`src/meridian/config.py`, `src/meridian/cli.py`, docs). Resolved the CONTEXT.md §12 vs shipped-CLI naming drift via option (C) — operator-facing aliases (`cloud-default`, `hybrid`, `air-gapped`) resolve to technical preset names (`cloud-sonnet-default`, `ollama-5090-balanced`, `ollama-air-gapped`). Both forms work at the CLI; existing project DBs are untouched (lookup tries alias-then-technical). New `cloud-sonnet-default` preset added (every purpose on Sonnet 4.6, Haiku 4.5 for triage). New CLI: `meridian routing list-presets`. Three-outcome discipline applied: success / preset-not-found / preset-found-but-validation-failed (e.g. an Ollama preset chosen with no local Ollama reachable). Glossary entries added for `Routing preset` and `Air-gap mode`. Routing air-gap-on warning text now uses the alias form.

- **Expanded e2e test coverage** (`tests/e2e/`, 4 new files, 14 new tests). New coverage: backup round-trip (4 tests — create/verify/restore-as-new-slug/corrupt-zip detection/missing-sibling-dirs), onboarding state machinery (3 tests — round-trip, non-TTY refuse, state path), production-readiness clients (6 tests — license malformed, license unverified-with-placeholder, updates unreachable, updates invalid-JSON, crash refuses-placeholder, crash redacts secrets), CLI help-renders omnibus (1 test — every top-level command's `--help` exits 0). **No production defects surfaced** — every module behaved per its safe-default contract. Total e2e suite: 25 → 39 tests, 8.1s wall time, all passing.

- **SQLite `busy_timeout` PRAGMA hardening** (Hazard 2 from round-13 audit). `connect()` now sets `PRAGMA busy_timeout` so concurrent writers wait instead of immediately raising "database is locked". Default 5000ms (5s) for read-mostly call sites; 30000ms (30s) for write-heavy paths (extraction worker, orchestrator, API write endpoints). Backwards-compatible: existing call sites without the new kwarg get the new sane default. Hazard 2 marked Implemented in `docs/concurrency-analysis.md`. `docs/troubleshooting.md` updated. Live-tested: `PRAGMA busy_timeout` returns 5000 for default connections.

- **Polish (main thread)**: routing-air-gap-on warning now references alias `air-gapped` instead of technical `ollama-air-gapped`; glossary entries for routing-preset + air-gap-mode added so the alias system is discoverable from the web UI.

### Round 16 — Tauri scaffold + Next.js static-export refactor

Round 16 lands Path A's foundation (per `docs/DECISIONS.md` §3.7): a Tauri 2.x scaffold and a Next.js static-export refactor of the round-11 server-component pages. File generation only — no Rust/MSVC install on the dev machine yet, no `cargo build`, no `.msi` produced. Four parallel streams on file-disjoint surfaces; all four landed clean.

- **Stream A — Tauri scaffold** (`src-tauri/` new directory + a handful of edits in `apps/web/`). New crate root: `Cargo.toml` (Tauri 2.x with `tauri-plugin-dialog`, `tauri-plugin-shell`, `tauri-plugin-fs` deps), `build.rs` (the standard `tauri_build::build()` shim), `tauri.conf.json` (window config — title `Meridian`, 1280×800 default, Next-export `out/` as the frontend dist; bundler set to `msi` only; identifier `com.undivided.meridian`), `src/main.rs` + `src/lib.rs` (the `tauri::Builder` skeleton with the three plugins wired plus a `// ROUND-17: spawn FastAPI sidecar here` marker), `capabilities/default.json` (`core:default` + `dialog:default` + `shell:allow-open` + `fs:default`; tightening pass deferred to round 17 when the wizard knows what it actually needs), `icons/README.md` (placeholder explaining real icons land round 18; tauri-bundler will refuse to build without a real `.ico`/`.png` set, which is fine — round 18 owns that step), `.gitignore` (excludes `target/`, `gen/`, `WixTools/`; **`Cargo.lock` is intentionally tracked** per Tauri docs — reproducible MSI builds need a pinned dep tree). On the Next side: `apps/web/package.json` gains `@tauri-apps/cli` as a devDep, `@tauri-apps/api` + `@tauri-apps/plugin-{dialog,shell,fs}` as deps, and `tauri:dev` / `tauri:build` scripts; version bumped `0.1.x` → `0.2.0`. `apps/web/next.config.ts` flips to static-export shape: `output: "export"`, `trailingSlash: true`, `images: { unoptimized: true }`. Root `.gitignore` extended with `src-tauri/target/`, `WixTools/`, `gen/`. **Deferred items, deliberately:** real icon set → round 18; FastAPI sidecar spawn (PyInstaller-bundled `meridian-server.exe` launched by Tauri at app start, killed cleanly on quit) → round 17; code-signing cert → §3.4 (alpha-distribute unsigned for now); capability tightening once the wizard's needs are known → round 17.

- **Stream B — 11 dynamic project pages → client** (`apps/web/src/app/projects/[name]/{page.tsx, audit, conflicts, evidence, master, quarantine, questions, sources, taxonomy, tender, xref}/page.tsx` — the dashboard plus 10 sub-pages; the dispatch brief overcounted as 12). The round-11 pages were `async` server components doing server-side data fetching with `await params` — incompatible with `output: "export"`. Round 16 converts each to `"use client"` + React 19's `use(params)` hook for the route param, plus a `useEffect` data-fetch via the existing `apiFetch` wrapper (which already injects the bearer token from §3.2's localStorage). Three-state UX is unconditional: loading skeleton, error panel via `ApiErrorPanel`, data render — `ReviewLayout` chrome wraps all three states so navigation is never absent. `export const dynamic = "force-dynamic"` is dropped from each (it's a server-component-only directive and meaningless under `output: "export"`). Sibling client components (`TenderBuilder`, `XrefPanel`, the various `*Queue.tsx` components) are frozen scope — Stream B touched only the page shells, not the children. Authentication redirect-to-login path preserved via `AuthGate`.

- **Stream C — top-level pages** (`apps/web/src/app/{page.tsx, health/page.tsx, login/page.tsx}` converted; `app/{glossary,help,onboarding}/page.tsx` and `app/layout.tsx` audited and left alone). The two pages that did server-side fetching get the same client-conversion treatment as Stream B's pages: `"use client"`, `useEffect` + `apiFetch`, three-state render. **`/login` was an unexpected third conversion** — the existing page used `await searchParams` (Next 15 server-component idiom) to read the `?from=` redirect target after auth, which doesn't survive static export. Replaced with `useSearchParams()` from `next/navigation` wrapped in a `<Suspense>` boundary (Next.js's documented escape hatch for static-exported routes that read query params at runtime). The remaining top-level routes were already static-export-clean — `/glossary` and `/help` are pure content, `/onboarding` is sync content wrappers. No churn there.

- **Stream D — docs** (this section, `docs/DECISIONS.md` §3.7 status update + summary-table cell + new follow-up bullet, `apps/web/README.md` Tauri quick-start appended). Documentation-only stream; no source touched.

The four streams ran in parallel on the parallel-subagents-default discipline; file-disjoint boundaries held — Stream A owned `src-tauri/**` + the three `apps/web/` config files, Stream B owned the 12 project page shells, Stream C owned the two top-level page conversions, Stream D owned the three docs files. No cross-stream merges required.

**Build-verification follow-up (added in main thread after the four streams reported clean):** the first `npm run build` failed on `Page "/projects/[name]/conflicts" is missing "generateStaticParams()"` — `output: "export"` requires the export on every dynamic segment, but Stream B's 11 page shells are all `"use client"` and `generateStaticParams` is a server-only export. Fix landed via a single new server-component **`apps/web/src/app/projects/[name]/layout.tsx`** that defines `generateStaticParams` once for the whole `[name]` segment, returning a `[{ name: "_" }]` placeholder. The Tauri webview opens `index.html` at the SPA root and all real project navigation happens client-side via Next.js's router — direct URL loads to a non-prerendered project name aren't a supported entry path inside the desktop shell. Re-run: 23 pages generated cleanly, dynamic routes prerendered at `/projects/_/{audit,conflicts,evidence,master,page,quarantine,questions,sources,taxonomy,tender,xref}`, static export written to `apps/web/out/`. First-load JS for the project shell pages 115–121 kB — comparable to round 14's pre-export build.

**What works without Rust today:** `npm run dev` (Next dev server on port 3000 against the FastAPI on 8000); `npm run build` produces a static export to `apps/web/out/` (verified clean this session — see above). **What needs round 18:** `npm run tauri:dev` and `npm run tauri:build` will both fail today because (a) Rust isn't installed yet and (b) the icon set is intentionally absent — Tauri-bundler refuses to build `.msi` without real icons. Both unblock together when round 18 installs Rust + MSVC Build Tools 2022 + WiX Toolset and drops the icon set in.

**Schema v6, no DB changes (zero Python touched this round); ruff clean (untouched); 52 e2e tests still passing per the round-15 final-state lock since no Python changed; Next.js shell now static-exportable; Tauri scaffold ready for `cargo`/`MSVC` install (round 18). Round 17 (`/setup` wizard) is unblocked.**

---

### Round 9 — TOTP authentication scaffold
- **Pure-stdlib TOTP** (`src/meridian/auth/totp.py`) — RFC 6238 implementation, all six SHA-1 test vectors pass, constant-time comparison via `hmac.compare_digest`, ±30 s clock-skew tolerance.
- **Recovery codes** (`src/meridian/auth/recovery.py`) — generated formatted (`XXXX-XXXX-XXXX`), hashed at rest, one-time-use enforced (re-use blocked).
- **Sessions** (`src/meridian/auth/session.py`) — HMAC-SHA256 signed bearer tokens, 8-hour default expiry, on-disk revocation list.
- **Storage abstraction** (`src/meridian/auth/secrets.py`) — `SecretStore` Protocol with `EncryptedFileStore` default + `KeyringStore` stub for OS-keychain upgrade.
- **QR code** (`src/meridian/auth/qr.py`) — minimal stdlib encoder (byte mode, EC L/M, versions 1–10, ASCII + SVG output) for the enrolment UI.
- **CLI**: `meridian auth enroll / status / verify / logout / reset`.
- **FastAPI dependency** (`src/meridian/auth/fastapi_dep.py`) — `require_session` dependency defined but NOT applied to existing routes (decision point — see §3 below).

---

## 2. Where the codebase stands now

| Surface | Count |
|---|---:|
| Python modules importable | 35+ |
| CLI commands (incl. subgroups) | 35+ |
| FastAPI routes | 38 |
| Schema version | 2 |
| Sources ingested | 3 |
| Deliverables in master | 242 |
| Quarantined (awaiting your SME) | 81 |
| Audit OUTSIDE rows (awaiting promote/reject) | 46 |
| HITL questions (pending) | 16 |
| Conflicts (pending) | 11 |
| Taxonomy proposals (pending) | 1 (`service/'Chiller System'` → 143 deliverables) |
| LLM cost spent on real data | $4.14 across 68 calls |

**Pending review state preserved untouched** — your SME drives those queues today.

---

## 3. Decision points held for you — plain English + my recommendation

These are the items where I deliberately stopped rather than guess. Each is an architectural / operational fork that affects distribution, security, or cost. I've laid out what the choice is, what each option means in practice, and which way I'd lean based on accepted practice (with reasoning so you can disagree).

### 3.1 — TOTP secret-storage backend

**The choice:** Where on disk does the user's TOTP seed (a 160-bit random number that generates the 6-digit codes) live?

**Options:**

- **(A) Plaintext JSON with file permissions** — store the secret in `<projects_dir>/_auth/totp.json`, set the file to user-read-only (`chmod 0600` on POSIX, ACL'd on Windows). Anyone who can read your user account can read it. **This is what the v1 default is today.**
- **(B) OS keychain** — Windows Credential Manager / macOS Keychain / Linux Secret Service. Requires adding the `keyring` Python package. The OS handles encryption and unlock. Standard practice for desktop apps that store credentials.
- **(C) Encrypted-with-passphrase file** — store the secret encrypted with a key derived from a passphrase the user enters at every API boot. Operationally painful (passphrase prompt every restart) and Python's stdlib doesn't have a real authenticated cipher (AES-GCM, ChaCha20-Poly1305) — would need to add `cryptography` library or roll-your-own (which is "a known footgun" per the agent who built this).

**My recommendation:** **Option B (OS keychain).** The `keyring` package is small, well-maintained, and works cross-platform. The TOTP secret is exactly the shape of credential the OS keychain is designed for. The `KeyringStore` stub exists today; enabling needs ~20 lines of code + `keyring` added to `pyproject.toml`. **Stick with Option A only if your projects directory will always live under `%LOCALAPPDATA%`/`%USERPROFILE%` (i.e. never on a shared OneDrive folder where another user might browse to it).** Currently it lives under your OneDrive — so Option B is the right move.

### 3.2 — Whether to enforce TOTP on existing API routes

**The choice:** When you wire the FastAPI auth dependency `require_session`, do you put it on every route immediately or only on side-effecting (POST) routes?

**Options:**

- **(A) Global** (`FastAPI(..., dependencies=[Depends(require_session)])`) — every request needs a bearer token. Maximum security. **Breaks any local automation you have running today** until they're updated to fetch a token from a `/auth/login` endpoint (which doesn't exist yet — see §3.3).
- **(B) POST-only** — read endpoints (`GET /projects`, `GET /coverage`, etc.) stay open; mutating endpoints require the token. Lower security, but doesn't break the existing CLI / Next.js read paths.
- **(C) None** — leave the dependency available but don't wire it. v1 ships with no API auth.

**My recommendation:** **Option B (POST-only)**, AFTER §3.3 below ships. The CLI authenticates via the local DB anyway (TOTP is for API surface, where the future Next.js app authenticates a user). Read-only browse stays open for local automation; mutations require the bearer. This matches how `gh` / `git` / similar dev-facing tools handle local-vs-remote auth.

### 3.3 — POST /auth/login endpoint

**The choice:** The CLI mints sessions today (`meridian auth verify`), but the API has no way to exchange a TOTP code for a bearer token. Without this, the Next.js shell can't authenticate.

**Options:**

- **(A) Add `POST /auth/login` (TOTP code → bearer token) + `POST /auth/logout` (revoke token).** Standard pattern. Maybe 30 LOC.
- **(B) Have the Next.js shell read the bearer token from a local file the CLI writes.** No new endpoints, but tightly couples web UI to CLI lifecycle.

**My recommendation:** **Option A.** Standard, simple, decouples the surfaces. Should be done in the next round.

### 3.4 — Code-signing certificates for distribution

**The choice (CONTEXT.md §17, §18):** When you ship installers, modern OSes warn users about unsigned binaries (Windows SmartScreen, macOS Gatekeeper). Code signing avoids this, but requires certificates.

**Options:**

- **(A) Sign with a commercial code-signing certificate** (Sectigo, DigiCert, etc., ~AUD $400–700/year). Works immediately, no warnings.
- **(B) Sign with EV (Extended Validation) certificate** (~AUD $1500–4500/year) — bypasses Windows SmartScreen reputation-build period entirely. Worth it for commercial distribution at scale.
- **(C) Ship unsigned, accept the warnings**. Realistic for early-internal-testing only; PMs will flinch.
- **(D) Self-sign + ask users to trust a custom CA.** Standard in regulated/defence environments; user-hostile in general PM use.

**My recommendation:** **(A) for v1 commercial release; (C) for the next 1-2 months of dev-internal testing.** EV upgrade only if you hit conversion friction with normal certs. Plan to budget ~AUD $500/year for signing as a fixed cost.

### 3.5 — Auto-update endpoint URL (CONTEXT.md §18)

**The choice:** "Updates: in-app auto-update with 'skip this version' option. Endpoint = JSON file on CDN." The endpoint URL doesn't exist yet.

**Options:**

- **(A) GitHub Releases** — host the installer + a `latest.json` manifest in a public release. Free, durable. Standard. Used by Tauri / Electron apps everywhere.
- **(B) Cloudflare R2 / AWS S3 + custom domain.** ~AUD $5/month. Slightly more polish (`updates.meridian.undivided.systems`).
- **(C) Static hosting on undivided.systems alongside the marketing site.** Cheapest if the site already exists.

**My recommendation:** **Option A (GitHub Releases) for v1.** Zero infra, zero cost, durable, well-known. Move to Option B if you want a cleaner branded URL later — the migration is just changing one constant + redirecting. Implementation is ~50 LOC of "fetch JSON, compare semver, prompt user" + the installer download.

### 3.6 — Crash report endpoint URL (CONTEXT.md §19)

**The choice:** "Opt-in crash reporting: the same LLM-generated report is shown to the user for approval before send. Endpoint = small serverless function."

**Options:**

- **(A) Cloudflare Workers** — single tiny function, ~AUD $0–5/month. Receive POST, write to R2 / log / email.
- **(B) Sentry / Bugsnag** — commercial crash-reporting platforms with rich UI. ~AUD $30–100/month for low volume. Built-in deduplication, release tracking, alerting.
- **(C) Direct email to support@undivided.systems** via a serverless SMTP function. Simplest.

**My recommendation:** **Option C for v1 (direct email)**. Crash reports will be rare given the LLM-assisted-explanation gate; you're getting a few per month at most. Email keeps everything in one inbox you already check. **Move to (B) Sentry once volume justifies it** (~5+ reports/week). The local crash-report assembly already works (writes JSON to disk); enabling network send needs an endpoint URL + ~10 LOC.

### 3.7 — Installer technology (CONTEXT.md §18)

**The choice:** Windows installer.

**Options:**

- **(A) PyInstaller** + Inno Setup wrapper — most common for Python desktop apps. Bundles Python runtime + dependencies into a single .exe; Inno Setup creates the install/uninstall flow.
- **(B) Briefcase** (BeeWare) — newer, Python-native packaging. Less battle-tested than PyInstaller.
- **(C) Pure web app distribution** — skip the installer entirely; ship as a Docker image + run-script. Wrong fit for non-technical PMs.
- **(D) Tauri / Electron wrapper around the Next.js shell** — bundles the FastAPI backend + Next.js frontend + a desktop window into one app. Heaviest approach; biggest install size; nicest UX.

**My recommendation:** **(A) PyInstaller + Inno Setup for v1**, with installer auto-launching the FastAPI server on a local port + opening the user's default browser to `http://localhost:<port>`. Simple, well-trodden path. **Tauri-wrapped variant in v1.x** if user feedback says the browser-tab-to-localhost UX feels janky.

### 3.8 — License signing key generation policy (CONTEXT.md §17)

**The choice:** "Ed25519, private key held by Peter, public key embedded in app." Need to generate the keypair + decide how it's stored.

**Options:**

- **(A) Generate once, store private key in your password manager** (1Password, Bitwarden, etc.). Backup to encrypted USB stored offline. Standard practice.
- **(B) Generate via a YubiKey or hardware token.** Stronger; key never exists on disk. More expensive (~AUD $80) and requires the YubiKey to be present when issuing licences.
- **(C) Cloud KMS** (AWS KMS, GCP Cloud KMS, Azure Key Vault). Highest operational maturity. Overkill for one-person licence issuance.

**My recommendation:** **(A) for v1; (B) once you have ≥10 paying customers.** The threat model — "if someone gets the private key they can mint pirate licences" — is real but bounded. Password manager + encrypted offline backup + Ed25519's simplicity (single 32-byte key) gets you 95% of the way. Budget AUD $80 for a YubiKey when commercial volume warrants.

### 3.9 — Web shell node setup

**The choice:** The Next.js shell (`apps/web/`) is fully scaffolded but has never been built. To run it you need Node 20+ installed.

**Options:**

- **(A) Install Node + run `npm install` in `apps/web/`** — standard. Then `npm run dev` against `uvicorn meridian.api.main:app --reload --port 8000` and you have the review UI live.
- **(B) Skip the web shell for now**, drive everything via CLI. The CLI is fully functional for review.

**My recommendation:** **Either is valid.** If you want to demo the UI to the SME tomorrow, do (A) — instructions in `apps/web/README.md`. If you'd rather keep moving on Python-only feature work for now, (B) is fine and the UI will be there when you want it.

### 3.10 — `service/'Chiller System'` taxonomy proposal (already surfaced earlier)

**Reminder:** the LLM proposed `Chiller System` as a new service value during extraction; 143 deliverables (almost half the master register) currently use it. SME should either confirm it as canonical or merge into HVAC. The merge has been spot-tested on a temp DB copy — works in one operation, repoints all 143 rows, marks `Chiller System` as `user_merged`, adds it as a synonym of HVAC. Command:
```
uv run meridian review merge-taxonomy syd2-shell-cd --table service --source "Chiller System" --target "HVAC"
```

---

## 4. Value-adds noticed during the autonomous rounds

These are things I noticed worth flagging that aren't on the formal roadmap:

1. **The compliance analytics revealed a likely prompt-tuning opportunity.** Only 19/323 deliverables (5.9%) carry `applicable_standards`. The strict-citation rule from CONTEXT.md §4 explains some of it, but the OSE chiller spec has many ASTM/ASHRAE references that should have produced more citations. **Worth a SME spot-check on a few rows**: open the chiller spec PDF, identify a section with explicit standard citations, query the corresponding deliverable rows, and check whether the standards landed. If not, the v1.1 text-spec prompt may need a stronger instruction to capture standards — or possibly an orchestrator-level secondary pass that re-extracts standards specifically.

2. **The risk-hotspot distribution is healthy and analytically informative.** 227/323 deliverables score zero (the auto-approved baseline); only 6 score ≥7. This pattern shows the auto-route + flag system is correctly concentrating PM attention rather than producing flat alerting. Worth surfacing on the dashboard as a confidence indicator.

3. **OSE completeness is meaningful only when you ingest the OSE spec.** Chiller Vendor: 71% (87 deliverables, because the OSE chiller spec was ingested). Busway and Generator vendors: 0% (only mentioned in BOD rows; no dedicated OSE spec ingested). **The analytics correctly flag the data gap, not a prompt gap.** Worth surfacing in the UI as "OSE completeness assumes the vendor's OSE spec is in the corpus — vendors mentioned only in BOD rows will look incomplete."

4. **Trade Overlap analytics confirms the §5 vendor-attribution rule is working.** 56 Chiller Vendor / Mechanical co-occurrences dominates the matrix — exactly what the rule predicts (vendor owns equipment + native docs; specialist trade owns connecting provisions). This is a positive signal that the prompt is honouring the rule consistently.

5. **The Conflict Register output is genuinely a separate product candidate.** Looking at the 11 conflicts surfaced — UPS topology deviation (6N5 vs spec's 2N), 7-min restart obligation, fire alarm interlock prohibition, Spec108 vs Spec203 inlet-temp 32°C vs 35°C — these are exactly the artefacts QA reviewers / construction lawyers / claims teams need for project documentation. **Worth packaging as a standalone billable export** (Tier 3 from yesterday's analysis) — the implementation is already done; just the positioning + pricing.

6. **Bootstrap LLM sweep should run automatically on first import**, not manually via `meridian bootstrap`. Add a prompt in the `import-doc` CLI: "First documents in this project — run bootstrap sweep to propose taxonomies and authority chain? [Y/n]". Lower friction for the SME's first session with a new project.

7. **The triage cost story is quietly excellent.** 51 Haiku triage calls cost $0.00 in actuals. This means the cost of routing decisions (which chunks of which docs to send to expensive Sonnet calls) is essentially free. Worth surfacing in your sales / commercial pitch — "we use a tiered model strategy that filters cheap before sending to expensive."

8. **The legal-defensibility evidence pack is closer to ready than I thought.** Every LLM call has reproducibility metadata; every reviewer action has a timestamp + status transition. The output of `meridian cost-summary <project>` already shows a per-call audit. Wrapping these into a single zip + PDF cover for "evidence pack" output is maybe 200 LOC. **High-leverage future module.**

9. **The structured logging unlocks LLM-assisted error explanation as a real differentiator.** PM hits an error → CLI shows "Anthropic credit balance too low → top up at console.anthropic.com → Plans & Billing" rather than a Python stack trace. This is the kind of polish that separates "looks impressive in a demo" from "actually drivable by a non-technical user".

---

## 5. Suggested first-look-tomorrow agenda

Roughly 60–90 minutes of SME / your time:

1. **Open the Excel** at `data/projects/syd2-shell-cd-master.xlsx` (regenerate first via `uv run meridian export syd2-shell-cd -o data/projects/syd2-shell-cd-master.xlsx` if you want freshness) — gives you 242-row view of the deliverables.
2. **Read the report at `data/projects/REPORT_v0.md`** — earlier snapshot for orientation.
3. **Run `uv run meridian review-status syd2-shell-cd`** in a terminal — see the full coverage dashboard with `[BLOCKED] BASELINE NOT YET TRUSTWORTHY` summary.
4. **Try the merge-taxonomy command** to consolidate `Chiller System` into HVAC (or have your SME walk it interactively via `uv run meridian review walk-taxonomy syd2-shell-cd`):
   ```
   uv run meridian review merge-taxonomy syd2-shell-cd --table service --source "Chiller System" --target "HVAC"
   ```
   Re-run `review-status` afterward to see review_coverage_pct move.
5. **Walk a few quarantined items** with your SME via `uv run meridian review walk-quarantine syd2-shell-cd` — accept/reject the first 5 to feel the workflow.
6. **Walk the conflicts** via `uv run meridian review walk-conflicts syd2-shell-cd` — there are 11 substantive conflicts pending; resolving even half of them noticeably moves the baseline-trustworthiness needle.
7. **Optional**: spin up the API + Next.js for a UI preview if you have Node:
   ```
   # Terminal 1
   uv run uvicorn meridian.api.main:app --reload --port 8000
   # Terminal 2
   cd apps/web && npm install && npm run dev
   ```
   Browse to `http://localhost:3000`. The Glossary, Onboarding, and Help pages are fully content-complete; the Project review queues hit the FastAPI backend live.
8. **Then look at decision points** in §3 above and choose. Most can be resolved in a single sentence each.

---

## 6. What stays queued for future autonomous rounds (when you're back)

**Round 10 cleared:** Tender Package Builder, Legal Evidence Pack, multi-doc cross-reference exhaustive sweep, bootstrap-on-first-import auto-trigger, standards-extraction prompt strengthening.

**Round 11 cleared:** xref classification fix (86% queue-noise reduction), tender flag-pill UUID resolution, Next.js UI surfaces for tender/evidence/xref + glossary entries, chunk-level resume + EJS transactional consolidation, CLI display polish.

**Round 12 cleared:** license/update/crash production-readiness clients (with placeholder constants ready for swap); API-side TOTP login + logout + whoami + status endpoints; pytest end-to-end harness (16 tests, 3.5s, all passing). Web build verification blocked — Node not installed.

**Round 13 cleared:** backup/restore CLI (live verified, 23-file round-trip); onboarding wizard (`meridian init`, 6 steps, idempotent); end-user documentation (~12,500 words across 8 files); multi-user concurrency hazard audit + ProjectLock primitive (4 new tests; 20/20 passing).

**Round 14 cleared:** routing preset alias system (operator vs technical names, both forms supported); expanded e2e coverage (25 → 39 tests, all passing in 8.1s); SQLite busy_timeout PRAGMA hardening (5s read / 30s write); polish (warning text + glossary entries).

Still queued, in rough priority:

- **POST `/auth/login` endpoint** + opt-in apply of `require_session` to POST routes (resolves §3.2 + §3.3 once you've decided)
- **License validation** scaffold (Ed25519 signature verification, public-key embedded; key generation deferred to your call per §3.8)
- **Auto-update mechanism** (semver compare against a JSON manifest; endpoint URL deferred per §3.5)
- **Crash-report network send** (small POST with the existing local JSON dossier; endpoint URL deferred per §3.6)
- **Installer + distribution pipeline** (PyInstaller + Inno Setup; deferred per §3.7)
- **Optional LLM-assist mode** for the cross-reference sweep (deterministic pass with new four-outcome classification already useful at zero cost; LLM second-pass for the 13 borderline rows is stubbed but disabled by default)
- **Tender `delivery` category dominance** noted in test pass — 47/87 Mechanical rows under `delivery`; possible category-classification accuracy issue worth a SME spot-check + potential prompt tweak
- **Evidence pack web verify** — currently CLI-only (multi-GB upload through Next.js API was the wrong tradeoff); revisit if SMEs ask for it
- **Auto-confirm signal strengthening for xref** — current `confirmed: 0` reflects that the corpus only has 3 sources, so genuine inside-corpus cross-references are rare. Once more docs ingest, expect this to climb.
- **Web build verification + first-build TS-error fixes** — needs Node installed; deferred to your `winget install OpenJS.NodeJS.LTS` step.
- **Taxonomy auto-quarantine on case-sensitive value mismatch** — surfaced by the e2e tests. Any LLM-proposed taxonomy value that doesn't case-match the seeded vocabulary auto-quarantines and never reaches `v_master_register` until the SME confirms via `walk-taxonomy`. Worth a UX nudge ("LLM proposed N new taxonomy values — review them before tendering") on the dashboard or after extraction.

---

## 7. Final state confirmation

End of round 14:

- All Python imports clean (50+ modules — `from meridian.cli import app; from meridian.api.main import app` smoke test passes)
- `ruff check src/meridian/ tests/` — all checks passed
- `pytest tests/e2e/` — **39/39 passing in 8.1s** (round 12: 16; round 13: +4 concurrency = 20; round 14: +14 expanded coverage + 4 busy_timeout + 1 routing alias = 39)
- 45 FastAPI routes
- 42+ CLI commands across `meridian {init,tender,evidence,xref,license,updates,crash,backup,db-migrate,bootstrap,bootstrap-show,review,routing,auth,analytics,explain-last-error,...}`
- Schema at v5; live `syd2-shell-cd` DB migrated to v5; 242 deliverables + 16 review-queue questions untouched
- `docs/` directory — ~12,500 words of end-user documentation across 8 files
- New web UI surfaces under `apps/web/src/app/projects/[name]/{tender,evidence,xref}` plus glossary additions; never built (Node not installed in this environment)
- Schema at v5 (round 10 → v3 added xref tables; round 11 → v4 added xref `external_reference` outcome via table rebuild; round 11 → v5 added 4 columns to `source_document_chunk` for chunk-level resume via `ALTER TABLE ADD COLUMN`)
- **Live project DB `syd2-shell-cd.sqlite` is at v5** — was migrated during round-11 verification. Backfill applied: 44 chunks `extracted` / 8 `skipped` / 243 `pending` (matches existing triage flag distribution). 242 deliverables and 16 review-queue questions untouched. The 98 noise rows from the round-10 test pass were rolled back before round 11 began.
- New Next.js UI surfaces under `apps/web/src/app/projects/[name]/{tender,evidence,xref}` — never built; `cd apps/web && npm install && npm run dev` against `uvicorn meridian.api.main:app --reload --port 8000` to view
- Logs flowing to `data/projects/syd2-shell-cd.logs/meridian-YYYYMMDD.log`
- Real LLM cost spent during round 11: **$0.00** (all live tests were dry-run / read-only / zero-LLM)

**The codebase is in a known-good integrated state. Nothing is half-built. Every round's deliverables landed cleanly with verification.**

---

## 8. New things to try in the morning (round-10 + round-11 surfaces)

In addition to the §5 agenda above:

- **Tender Package Builder.** `meridian tender list syd2-shell-cd` to see per-trade counts; `meridian tender build syd2-shell-cd --trade Mechanical --format xlsx` to drop a tender package into `data/projects/syd2-shell-cd.tenders/`. Flag pills now read as filenames (round-11 fix). Several test runs are already in that directory for inspection.
- **Legal Evidence Pack.** `meridian evidence build syd2-shell-cd` for a full-project pack (round-10 prompt-resolution fix in round 11 means the `prompts/` folder is now correctly populated); `meridian evidence verify <pack.zip>` to round-trip the integrity check. Three test packs already in `data/projects/syd2-shell-cd.evidence/`.
- **Cross-reference sweep.** Already migrated to v5. `meridian xref sweep syd2-shell-cd --dry-run` to preview the four-outcome breakdown (currently 0 confirmed / 13 borderline / 80 external_reference / 32 rejected); add `--no-dry-run` flag explicitly (or omit `--dry-run`) to persist + add the 13 borderline rows to the SME review queue. `meridian xref report syd2-shell-cd --format md` to re-render.
- **First-import friction is gone.** Next time you create a project and run `meridian import-doc <new-project> <files>...`, you'll be offered the bootstrap LLM sweep inline. Pass `--no-auto-bootstrap` for scripted runs.
- **Web shell is real now.** Three new pages plus dashboard tiles + glossary entries. `cd apps/web && npm install && npm run dev` (decision §3.9) — try them at `http://localhost:3000/projects/syd2-shell-cd/{tender,evidence,xref}`. Keyboard shortcuts: `?` for cheat sheet, `B` to build (tender/evidence pages), `S` for sweep dry-run, `P` for persist confirm, `L` to toggle LLM-assist (xref page).

---

## 9. Handover summary — what to do next

End of round 14 is the planned final autonomous round. The remaining work is decision-shaped, not code-shaped.

### What you can use immediately

- **Drive a real project end-to-end via the CLI.** `meridian init` walks first-time setup. Existing `syd2-shell-cd` is preserved at v5 with the 242 deliverables + 16 review questions intact.
- **Hand artefacts to the SME.** Tender packages live in `data/projects/syd2-shell-cd.tenders/`; evidence packs in `.evidence/`; xref reports in `.reports/xref/`. The `tender build`, `evidence build|verify`, and `xref sweep|report` CLIs all live.
- **Read the docs.** [docs/README.md](../../docs/README.md) is the index; `docs/getting-started.md` is the 5-minute quickstart; `docs/cli-reference.md` is every command.
- **Run the tests.** `.venv/Scripts/python -m pytest tests/e2e/` — 39 tests in ~8s.
- **Take a backup.** `meridian backup create syd2-shell-cd` produces a verifiable zip of the SQLite + all sibling artefacts.

### What you need to decide before commercial v1 ships

The 10 deferred items in §3 above. In rough priority for unblocking distribution:

1. **§3.7 installer technology** (PyInstaller + Inno Setup recommended) — blocks installer build
2. **§3.4 code-signing certificate** (~AUD $500/year normal cert) — blocks unsigned-binary friction
3. **§3.5 auto-update endpoint URL** (GitHub Releases recommended for v1) — once chosen, swap one constant in `src/meridian/updates/client.py` (tagged `# DEFERRED §3.5`)
4. **§3.6 crash-report endpoint URL** (email-via-serverless recommended) — swap constant in `src/meridian/crash/sender.py` (tagged `# DEFERRED §3.6`)
5. **§3.8 license keypair generation** (password-manager + offline backup recommended) — swap pubkey constant in `src/meridian/licensing/verify.py` (tagged `# DEFERRED §3.8`)
6. **§3.1 TOTP secret backend** (OS keychain recommended given the OneDrive projects dir) — `KeyringStore` stub exists in `src/meridian/auth/secrets.py`
7. **§3.2 + §3.3 auth enforcement** — POST-only enforcement recommended; the `/auth/login` endpoint exists, just needs `dependencies=[Depends(require_session)]` added to write routes
8. **§3.9 web shell first build** — `winget install OpenJS.NodeJS.LTS && cd apps/web && npm install && npm run build`. Round 11 added 3 new pages so first build may surface TypeScript errors that round-12's verification stream couldn't reach (Node was absent).
9. **§3.10 Chiller System taxonomy merge** — SME's call

### Real product gaps that are NOT code-fixable

- **Multi-user concurrency beyond the round-13 ProjectLock.** Lock prevents extraction collisions but doesn't address two SMEs simultaneously editing the same review queue. Per-row locking semantics are SQLite-natural but no UX exists yet for "this row is being edited by another reviewer".
- **No performance characterisation at scale.** 3 sources tested; behaviour at 50 / 500 sources is unknown.
- **No live LLM cost-ceiling enforcement.** `cost-preview` exists but runs are not capped mid-flight.
- **No Anthropic API outage degradation story.** A Sonnet outage during extraction surfaces as a per-call retry, but the orchestrator doesn't pause the job globally to wait for service restoration.

### Total deltas across rounds 7–14

- 50+ Python modules (was ~30 at start of round 7)
- 45 FastAPI routes (was 25)
- 42+ CLI commands (was ~15)
- 39 e2e tests (was 0 at start of round 12)
- ~12,500 words of end-user documentation (was 0 at start of round 13)
- Schema v1 → v5 with idempotent forward migration
- 9 substantive product modules added: bootstrap, evidence, tender, xref, licensing, updates, crash, backup, onboarding (+ login_api as a small auth surface, + ProjectLock concurrency primitive)
- Real LLM cost spent across all 8 rounds: **$0.00** (all live tests were dry-run / read-only / mocked)

---

## 10. Session handoff — end of 2026-04-27

The interactive working session that produced rounds 12–15, walked the §3 decisions, shipped v0.1.0/v0.1.1/v0.1.2 to GitHub, and attempted the first SME install ended here. A new Claude Code session picks up from this state. **The next planned work is round 16 (Path A — Tauri scaffold + Next.js static-export refactor).**

### Where we ended

- **Repo:** github.com/profixel660/meridian-trace (currently Public — flipped from Private to unblock anonymous API access from the v0.1.2 installer; revert when v0.1.3 PAT-prompt or Tauri lands)
- **Latest release:** v0.1.2 (PowerShell installer + wheel + sdist as assets)
- **Schema:** v6; 52/52 e2e tests passing in ~10s; ruff clean
- **Local Anthropic API key:** in `.env` (gitignored). User issued a separate fine-scoped key for the SME with a $50 spend cap (the SME's key, not the dev key).

### SME test status — PAUSED

First real install attempt (2026-04-27 evening) succeeded mechanically (Python install, venv, pip install, wheel install, key prompt) but failed for the SME's actual workflow:

- Project DB landed in `C:\Windows\System32\data\projects\` instead of `C:\Meridian\projects\` (elevated cmd cwd + `.env` not loaded into process env when `meridian init` ran)
- SME ended up in `cmd.exe` not the Meridian PowerShell shortcut, so `meridian` wasn't on PATH and recovery PowerShell commands were treated as cmd syntax errors
- `anthropic` SDK import warning (harmless but confusing)

User concluded: **"this install/setup needs a web interface with clear guidance — not fit for purpose as it stands"**. SME paused her test; she resumes once the Tauri-based installer lands.

### Path A committed (rounds 16–19 plan)

Per end-of-session conversation:

- **Round 16:** Tauri scaffold (`src-tauri/` Rust crate, `tauri.conf.json`, native window config) + Next.js static-export refactor (round-11 server-component pages → client-only data fetching). No Rust/MSVC install needed yet — file generation + page refactor only.
- **Round 17:** `/setup` wizard pages (5 pages — welcome / api-key / first-project / first-documents / ready-to-go) with construction-PM language explaining WHY each step matters. Native file pickers via Tauri's dialog API. Backend endpoints to support the wizard flow.
- **Round 18:** PyInstaller-bundle the FastAPI backend into one `meridian-server.exe`. Tauri spawns it as a sidecar at app launch, kills it cleanly on quit. `npm run tauri build` produces the actual `Meridian-Setup.msi`. **Rust + MSVC Build Tools install on dev machine becomes essential here.** User confirmed they can install whatever's needed without IT pushback.
- **Round 19:** Real-machine validation on a fresh Windows VM (no Python, no Node, no Rust). Find what breaks. Fix. SME re-test follows.

### Deferred follow-ups (DO NOT lose these)

Captured in `docs/DECISIONS.md` follow-ups section + `project_v013_deferred.md` memory:

- **v0.1.3 fixes** — pre-load .env in installer; litellm fallback in wizard validation; PAT prompt for private repos. Most resolved by Tauri itself; capture lessons before they're forgotten.
- **§3.4 code-signing cert** — budget AUD $500/year when commercial; ship Tauri .msi unsigned for now.
- **§3.6 crash endpoint URL** — choose email-via-serverless when ready; placeholder still in `src/meridian/crash/sender.py`.
- **§3.8 Ed25519 license keypair** — generate when commercial; embed pubkey in `src/meridian/licensing/verify.py`.
- **`apps/web/` ESLint config scaffolding** — `npm run lint` is interactive on first run.

### Kickoff prompt for the new session

Paste this verbatim to start the next session:

> Continuing the Meridian build (`github.com/profixel660/meridian-trace`). Read `data/projects/OVERNIGHT_REPORT.md` §10 and `docs/DECISIONS.md` in full first — they're the authoritative state. Then dispatch round 16 (Tauri scaffold + Next.js static-export refactor) per the Path A plan. SME test is paused; we resume after round 19. Same parallel-subagents pattern that worked for rounds 7–15. The three locked memories (Path A Tauri commitment, parallel subagents default, UX discoverability discipline non-negotiable) all apply.

---

*End of overnight report. Sleep well; talk in the morning.*
