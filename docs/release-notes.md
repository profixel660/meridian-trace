# Release notes

Round-by-round delta in plain English. Round numbers map to alpha versions: round 7 → alpha-7, round 8 → alpha-8, and so on. The current release is alpha-12.

When you upgrade, skim the relevant version's notes — anything marked **breaking** needs a manual step (typically `meridian db-migrate <project>`).

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
