# Architecture

For the technically-curious project manager, or for an IT department evaluating Meridian. This page describes the per-project SQLite model, the pipeline stages, the schema-version history, and the extension points. It is not a code reference — for code-level detail, read the source under `src/meridian/`.

## Per-project SQLite model

Meridian stores everything about a project in a **single SQLite file** at `<projects-dir>/<slug>.sqlite`. The filename is the project's slug; one file per project. The reasons:

- **Clean isolation.** Projects do not share state. Deleting a project is one `rm` command.
- **Portable.** Email a SQLite file, hand it to someone, attach it to a ticket — it's self-contained. Add the matching `<slug>.logs/` if the recipient needs to investigate failures.
- **Easy backup.** Copy the file. There's no schema-coordinator service to coordinate with.
- **No cross-project queries on the roadmap.** The single-file design intentionally trades cross-project analytics for operational simplicity.

What lives in the SQLite (high level):

- `source_document` — every imported file, with hash, document class, document state, and revision metadata.
- `source_document_chunk` — the text chunks each source breaks into, with per-chunk extraction state for resume.
- `deliverable` — the candidate pool, with status (`auto_approved`, `quarantined`, `user_accepted`, `user_edited`, `user_rejected`, `user_promoted`), confidence, flags, and the structured source reference.
- `deliverable_audit` — the OUTSIDE log: what the gate ruled out and why.
- `question` — pending HITL ambiguities raised during extraction, plus borderline cross-reference findings when a sweep is persisted.
- `conflict`, `conflict_party` — pairs (or larger groups) of disagreements between sources, with most-onerous reasoning.
- `taxonomy_value`, `taxonomy_proposal` — per-project trade / service / category vocabulary, with the proposal flow.
- `extraction_job`, `extraction_job_source` — per-job state and per-source completion, used by `pause` / `resume`.
- `llm_call` — every LLM invocation with full reproducibility metadata: model, model version, provider API version, prompt + version, temperature, top_p, max_tokens, system prompt, input hash, token counts, cost.
- `cross_reference_sweep_run`, `cross_reference_sweep_result` — sweep state and per-finding outcome.
- `bootstrap_proposal` — the bootstrap sweep's output, awaiting user confirmation.
- `view v_master_register` — the read view that filters deliverables to those that have passed quarantine and live taxonomy.

The SQLite uses Write-Ahead Logging (WAL) mode for concurrency. Schema versions are tracked in a `schema_meta` table; migrations are forward-only and idempotent.

## Stages

A document moves through the pipeline in this order:

```
ingest -> triage -> extract -> persist -> review -> export
```

### Ingest

`meridian import-doc <project> <files>...` does:

1. Hash each file (SHA-256). Skip if already in `source_document` with the same hash.
2. Extract text via the appropriate path: PDF text extraction, OCR (tesseract) for image PDFs, `python-docx` for Word, `openpyxl` for Excel, plaintext otherwise. (DWG conversion via the ODA File Converter is in the design seam but not driven from `import-doc` directly yet.)
3. Chunk the text into reviewable units, stored in `source_document_chunk`.
4. Run a per-document quality scan (LLM): notes scan quality, revision detected, document state, document class, markups present, illegible regions, mismatched references, template detection (auto-excludes from extraction).
5. On the project's first import: offer to run the bootstrap LLM sweep over a sample of the corpus.

### Triage

For each chunk, a cheap-tier LLM (Haiku 4.5 by default) decides whether the chunk is *likely* to contain deliverables. This is the cost-control mechanism — the expensive Sonnet 4.6 call only runs on chunks the triage pass flagged. Triage results are stored in `source_document_chunk` (`triage_marked_for_extraction`).

### Extract

Each flagged chunk is sent to the relevant extraction prompt:

- `extract_text_spec` — the default path for free-text specs, drawing legends, clause-style documents.
- `extract_bod` — for BOD response registers (tabular requirements with `Comply` / `Not Comply` responses). See the disposition rules in [concepts.md](concepts.md).
- `extract_demarcation` — for Demarcation Schedules.

The extraction prompt enforces the §3 deliverable definition with three outcomes (INSIDE / OUTSIDE / BORDERLINE — see [concepts.md](concepts.md)). It returns structured JSON; parsing errors raise the chunk's status to `extraction_failed` rather than corrupting the candidate pool.

Each extraction call runs in a **subprocess worker** for crash isolation. A worker crashing doesn't take the orchestrator down; the orchestrator records the failure and moves on.

### Persist

Successful extractions land in `deliverable` (with status `auto_approved` or `quarantined` per the gate) or `deliverable_audit` (OUTSIDE rows). Per-source finalisation — deliverable persist, audit persist, EJS state flip from `extracting` → `completed` — runs in a single transaction. Crash mid-finalisation rolls back all of it; the source stays `extracting` and will be retried on resume.

Per-chunk LLM-call records commit early (outside the per-source transaction) because they are referenced via foreign key. Idempotency on the rare double-call case is handled by `input_hash` dedup.

### Review

The review queues (quarantine, audit, conflicts, questions, taxonomy) are user-driven via `meridian review walk-*` or via the web UI. Every reviewer action writes a row to a `*_review_action` table for the audit trail.

The cross-source conflict pass (`meridian conflicts <project>`) and cross-reference sweep (`meridian xref sweep <project>`) are post-extraction passes that produce queue items. They can be re-run at any time.

### Export

`meridian export <project> -o <path>.xlsx` regenerates the Excel workbook from `v_master_register`. The Excel is a render target, not a working store — edits to the Excel do not survive a re-export. Pivots (by trade, by service, by category) are read-only renders regenerated on every export.

Other render targets exist as separate exports:
- `meridian tender build` — per-trade tender packages (xlsx or md).
- `meridian evidence build` — Legal Evidence Pack zip (deliverables, audit trail, LLM calls, sources, prompts, cover, chain of custody).
- `meridian xref report` — cross-reference sweep CSV + Markdown.
- `meridian analytics *` — analytics CSVs + Markdown.

## Data flow

```
+-------------+        +---------+        +---------+
| Source docs | -----> | ingest  | -----> | triage  |
| (PDF, DOCX, |        | (hash + |        | (cheap  |
|  XLSX, ...) |        |  text)  |        |  LLM)   |
+-------------+        +---------+        +---------+
                                              |
                                              v
                                          +---------+        +---------+
                                          | extract | -----> | persist |
                                          | (LLM    |        | (txn,   |
                                          |  per    |        |  per    |
                                          |  chunk) |        |  source)|
                                          +---------+        +---------+
                                                                  |
                                                                  v
+-----------+   +---------+   +---------+   +---------+   +-------------+
|  conflict |<--| review  |<--| queues: |<--| SQLite  |   |   export    |
|  + xref   |   | walks / |   | quarantine,|   | (master |-->| (xlsx, md,  |
|  passes   |-->| web UI  |-->| audit,  |   | register|   |  zip,       |
|  (LLM,    |   |         |   | conflicts,|  | + audit |   |  reports)   |
|  optional)|   |         |   | questions, |  |  + queues)|  |             |
+-----------+   +---------+   | taxonomy)|   +---------+   +-------------+
                              +---------+
```

The `review` stage is the only stage with a human in the loop. The other stages run unattended — drop docs, run `extract`, walk away, come back to walk the queues.

## Schema versions

| Version | What it added | Introduced in | Migration path |
|---|---|---|---|
| **v1** | Initial schema: `source_document`, `source_document_chunk`, `deliverable`, `deliverable_audit`, `question`, `conflict`, `taxonomy_value`, `extraction_job`, `llm_call`. | Round 1 (alpha-1) | New projects only. |
| **v2** | `llm_call` table rebuild (added reproducibility columns: `provider_api_version`, `system_prompt`, `input_hash`). | Round 4 (alpha-4) | `meridian db-migrate` rebuilds the table with FK-off transactional pattern. |
| **v3** | `cross_reference_sweep_run`, `cross_reference_sweep_result` tables (alpha-10 cross-reference sweep). | Round 10 (alpha-10) | `meridian db-migrate` adds tables; idempotent. |
| **v4** | `cross_reference_sweep_result` rebuild to add `external_reference` to the outcome CHECK list (four-outcome classification — confirmed / borderline / external_reference / rejected). | Round 11 (alpha-11) | `meridian db-migrate` rebuilds the table; SQLite cannot ALTER a CHECK in place. |
| **v5** | Four columns on `source_document_chunk` for chunk-level resume: `extraction_status`, `extraction_started_at`, `extraction_finished_at`, `extraction_job_id`. | Round 11 (alpha-11) | `meridian db-migrate` uses `ALTER TABLE ADD COLUMN`; backfill applied: `triage_marked_for_extraction=1 → extracted`, `=0 → skipped`, NULL → `pending`. |

Schema migrations are **opt-in**: an older project keeps working at its existing version (with reduced functionality — for example, xref commands fail until v3) until you run `meridian db-migrate`. New projects always start at the current latest version.

There is no rollback path. If you need to keep data at the old structure, copy the SQLite file before migrating.

## Extension points

Meridian's flexibility principle (CONTEXT.md §0) is implemented through these extension points. Anything that should reasonably vary per project is **data, not code.**

### Prompts as data

Extraction, conflict-pass, error-explain, bootstrap-sweep, and quality-scan prompts live in `prompts/` as Markdown files with a `**Version:**` header. The prompt body is the part inside the fenced block. Updates to a prompt are versioned, and the version is logged on every LLM call so historical extractions stay reproducible.

To override a prompt for a specific project, add a project-scoped override file (planned — currently a global override). The LLM call records the resolved prompt version, so audit trails always show what was actually used.

### Taxonomies as data

Trade, service, category, document class, and document state vocabularies are stored in `taxonomy_value` per project. Defaults are seeded at `project-create` time. Extensions go through the `taxonomy_proposal` flow:

1. The LLM proposes a value not in the canonical list.
2. The proposal lands in `taxonomy_proposal` with `source='llm_proposed'` (or `'user_added'` for bootstrap-sweep proposals).
3. The user confirms (becomes canonical), merges (cascades existing rows that used the proposal to a target value), or rejects.
4. Confirmed values are canonical for that project. Subsequent extractions do not re-prompt for the same decision.

### Model and provider routing

Each LLM purpose (`quality_scan`, `triage`, `extract_text_spec`, `extract_bod`, `extract_demarcation`, `conflict_pass`, `error_explain`) is independently routable via `meridian routing set` or `meridian routing apply <preset>`. Three named recipes ship: `cloud-default` (Anthropic Sonnet 4.6 + Haiku 4.5 for triage), `hybrid` (triage on local Ollama, load-bearing calls on cloud), `air-gapped` (every purpose on local; cloud blocked at preflight).

The routing config is per-project, stored in the project SQLite. Defaults preserve cloud behaviour; users override per project, per environment, or per call.

### Output formats

The SQLite store is the single source of truth. Excel is one render target. Tender packages (xlsx + md), Legal Evidence Packs (zip), cross-reference reports (CSV + md), and analytics (CSV + md) are additional render targets. Future targets (CSV, JSON, API push to Procore or similar) drop in without schema changes.

## What is intentionally not in the architecture

- **No central server.** Meridian is a desktop tool; FastAPI runs locally for the web UI but is not a deployment.
- **No multi-user collaboration.** Single-user TOTP only. Multi-user is on the future considerations list (CONTEXT §24), not on the v1 roadmap.
- **No cross-project queries.** Per-project isolation was a deliberate design choice. Aggregation across projects is out of scope.
- **No background daemon.** Every Meridian invocation is a CLI process that exits when its work is done. The FastAPI server is started explicitly by the user.
- **No telemetry.** See [security.md](security.md).

## Where to read more

- The CONTEXT.md at the project root is the canonical product description and design discipline. Read it for the *why* behind every decision documented here.
- `src/meridian/` is laid out by stage: `bootstrap/`, `extract/`, `persist/`, `tender/`, `evidence/`, `xref/`, `review/`, `analytics/`, `auth/`, `licensing/`, `updates/`, `crash/`, `routing/`, `db/`, `logging/`, `errors/`, `workers/`, `api/`, `cli.py`. Each module has a README-equivalent docstring at the top.
- The pytest suite at `tests/e2e/` exercises every major code path end-to-end. Read these to understand the contract between stages.

For day-to-day operation, you do not need any of this. [getting-started.md](getting-started.md) and [cli-reference.md](cli-reference.md) are the working docs.
