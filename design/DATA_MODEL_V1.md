# Data model — v1 sketch

**Version:** v1.0-draft (sketch — not implementation-ready DDL)
**Authority:** `CONTEXT.md` §0 (flexibility), §4 (output schema), §5 (taxonomy), §7 (source traceability), §8 (flags), §9 (HITL), §10 (state/class), §11 (granularity), §13 (cost controls), §14 (reproducibility), §15 (Excel role).
**Storage model:** one SQLite file per project (CONTEXT.md §6).
**Goal of this document:** make every persistence decision explicit before code is written. If a field is not listed here, the build chat should pause and ask before adding it.

---

## 0. Cross-cutting conventions

- **Primary keys** are stable UUIDs (TEXT, 36 chars, lower-case, hyphenated). Generated at insert time. Never reused. Survive Excel round-trips (CONTEXT.md §4, §15).
- **Foreign keys** are enforced (`PRAGMA foreign_keys = ON;` per connection).
- **Timestamps** are ISO 8601 UTC TEXT (`YYYY-MM-DDTHH:MM:SSZ`). Stored as TEXT for human readability; SQLite has no native datetime.
- **JSON columns** use SQLite JSON1 (TEXT under the hood, validated via `json_valid()` CHECK constraints). Used for: structured `source_ref`, `applicable_standards` array, `flag_context` object, `column_mapping` blobs, taxonomy synonym lists.
- **Soft enums**: status / classification fields are TEXT with CHECK constraints listing the permitted values. Easier to evolve than separate enum tables.
- **No cross-project FKs.** Per CONTEXT.md §6 each project is its own SQLite file. Cross-project queries are out of scope.
- **All schema migrations** are versioned in a `schema_migrations` table (single `version INTEGER PRIMARY KEY`, `applied_at TEXT`). v1 ships as `version = 1`.
- **Per-project tables only in this file.** License log lives in Peter's OneDrive (CONTEXT.md §17), TOTP secret lives in the OS keychain — neither belongs in the per-project SQLite.

---

## 1. Project + auth

### `project`
Single row per file. Holds project-level metadata.

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | UUID. The project's own identity. |
| `name` | TEXT NOT NULL | User-supplied. |
| `created_at` | TEXT NOT NULL | ISO 8601 UTC. |
| `schema_version` | INTEGER NOT NULL | Mirrors `schema_migrations.version`. |
| `app_version` | TEXT NOT NULL | App version that created the file. For forward/back compat triage. |
| `tool_disclaimer_version` | TEXT NOT NULL | Versioned disclaimer text (CONTEXT.md §20) accepted at create time. |
| `default_provider` | TEXT NOT NULL | `anthropic` / `openai`. CHECK constraint. |
| `default_model` | TEXT NOT NULL | e.g. `claude-sonnet-4-6`. |
| `auto_approval_threshold` | TEXT NOT NULL DEFAULT `'high_no_flags'` | Fixed value in v1; column reserved for v1.x per-project tuning (CONTEXT.md §23 #6). |
| `notes` | TEXT | Free-text user notes. |

### `app_setting`
Project-scoped key/value for things that don't warrant their own column.

| Column | Type | Notes |
|---|---|---|
| `key` | TEXT PK | e.g. `last_export_path`. |
| `value` | TEXT |  |
| `updated_at` | TEXT NOT NULL |  |

---

## 2. Taxonomy (extensible per project — CONTEXT.md §5 governance)

One table per axis. Unified shape: a canonical value, optional synonyms, provenance (default-shipped vs user-confirmed), an `is_active` flag for soft delete.

### `trade_taxonomy`

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | UUID. |
| `value` | TEXT NOT NULL UNIQUE | Canonical name, e.g. `Mechanical`, `Chiller Vendor`. |
| `category_hint` | TEXT NOT NULL | `specialist` / `cross_cutting` / `vendor`. CHECK constraint. |
| `synonyms` | TEXT | JSON array of strings. Populated as the user merges near-matches. |
| `source` | TEXT NOT NULL | `default` (shipped with v1) / `user_added` / `user_merged`. CHECK constraint. |
| `confirmed_at` | TEXT | NULL until user explicitly confirms (governance — CONTEXT.md §5). |
| `is_active` | INTEGER NOT NULL DEFAULT 1 | 1/0. Soft delete. |
| `created_at` | TEXT NOT NULL |  |
| `notes` | TEXT |  |

Default rows seeded at project create from the locked taxonomy in CONTEXT.md §5.

### `service_taxonomy`
Same shape as `trade_taxonomy` minus `category_hint` (services have no sub-classification in v1).

### `category_taxonomy`
Same shape as `service_taxonomy`. Defaults: `design`, `procurement`, `delivery`, `builders_works`. Resist expansion (CONTEXT.md §5 — narrow vocabulary).

### `service_mapping`
BOD discipline-section → service mapping (CONTEXT.md §5 BOD section). Per-project canonical, persisted on first user confirmation per the active-feedback prompt model.

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | UUID. |
| `disc_section_text` | TEXT NOT NULL UNIQUE | Verbatim BOD column value, e.g. `DCE Mechanical Engineering (ME)`. |
| `service_id` | TEXT | FK → `service_taxonomy.id`. NULL if mapping = "informational, no service". |
| `confirmed_at` | TEXT NOT NULL | Set when the user resolves the HITL prompt. |
| `confirmed_by_question_id` | TEXT | FK → `question.id` for audit. |
| `notes` | TEXT |  |

---

## 3. Sources (documents)

The source-doc table is **separate** from the deliverables table per the kickoff brief. Per-document quality scan output lives here, not denormalised onto every deliverable row.

### `source_document`

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | UUID. |
| `filename` | TEXT NOT NULL | Original filename as imported. |
| `relative_path` | TEXT NOT NULL | Path within the project's source-folder mirror. |
| `content_hash` | TEXT NOT NULL UNIQUE | SHA-256 of raw file bytes. Used for content-hash dedup (CONTEXT.md §13). UNIQUE means re-importing the same file content does NOT create a new source row. |
| `mime_type` | TEXT NOT NULL | `application/pdf`, `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, etc. |
| `size_bytes` | INTEGER NOT NULL |  |
| `imported_at` | TEXT NOT NULL |  |
| `document_class` | TEXT | One of `customer_requirements`, `global_tr`, `global_ose_spec`, `project_amendment`, `project_clarification`, `drawing`, `demarcation_schedule`, `methodology`, `template`, `unknown` (CONTEXT.md §10.3). NULL until quality scan completes. CHECK constraint. |
| `document_state` | TEXT | One of `concept`, `30%`, `50%`, `90%`, `100%`, `IFC`, `as-built`, or NULL. NULL is correct for `customer_requirements` (BOD), `global_*`, `methodology`, `template` — they are revisioned, not maturity-graded. |
| `revision` | TEXT | e.g. `rev1`, `rev2`, `latest`, `Rev 11`, `SYD29EX2`. Free text. The meaningful version axis (CONTEXT.md §10.1). |
| `revision_detected_via` | TEXT | `filename_pattern` / `embedded_metadata` / `content_scan` / `user_pinned`. CHECK. Audit trail for §10.1 default behaviour. |
| `is_authoritative_revision` | INTEGER NOT NULL DEFAULT 1 | 1/0. Set 0 when a newer revision supersedes this one. User can override-pin per §10.1. |
| `superseded_by_id` | TEXT | FK → `source_document.id`. Set when a newer revision supersedes. |
| `is_template` | INTEGER NOT NULL DEFAULT 0 | 1 → auto-excluded from extraction (CONTEXT.md §8). |
| `is_demarcation_schedule` | INTEGER NOT NULL DEFAULT 0 | 1 → triggers special handling (CONTEXT.md §5 — primary reference for trade allocation). |
| `quality_scan_id` | TEXT | FK → `document_quality_scan.id`. |
| `extraction_path` | TEXT NOT NULL | `text_spec` / `bod_import` / `drawing` / `excluded`. CHECK. Decided by quality scan. |
| `notes` | TEXT |  |

**Indexes:**
- `idx_source_document_content_hash` on `content_hash` (UNIQUE — already from constraint).
- `idx_source_document_class` on `document_class`.
- `idx_source_document_filename` on `filename`.
- `idx_source_document_authoritative` on `(filename, is_authoritative_revision)` for fast latest-rev lookup.

### `source_document_text`
Raw extracted text. Kept separate so the source row stays small and the text can be rebuilt on re-extraction without rewriting metadata.

| Column | Type | Notes |
|---|---|---|
| `source_id` | TEXT PK | FK → `source_document.id`. One row per source. |
| `extraction_method` | TEXT NOT NULL | `pdf_text` / `pdf_ocr` / `xlsx_parsed` / `docx_parsed` / `dwg_converted` / `eml_parsed`. CHECK. |
| `extracted_at` | TEXT NOT NULL |  |
| `text` | TEXT | Full extracted text. May be large; SQLite handles multi-MB cells fine. |
| `metadata` | TEXT | JSON. Method-specific (page count, sheet names + cell ranges, layer list, etc.). |

### `source_document_chunk`
Normalised section/clause/sheet-row chunks. Used by the Haiku triage pass (CONTEXT.md §13) and as the unit of reference inside `source_ref`.

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | UUID. |
| `source_id` | TEXT NOT NULL | FK → `source_document.id`. |
| `chunk_kind` | TEXT NOT NULL | `pdf_page` / `pdf_section` / `xlsx_row` / `xlsx_cell_range` / `drawing_region` / `email_message`. CHECK. |
| `locator` | TEXT NOT NULL | JSON. Method-specific structured locator (page, section, sheet+row, bbox, etc.). |
| `text` | TEXT | Chunk text content (where applicable). |
| `triage_marked_for_extraction` | INTEGER | NULL until triage runs. 1 = send to Sonnet. 0 = Haiku flagged as no-deliverables-likely. |
| `triage_reason` | TEXT | Brief Haiku reasoning (audit). |

**Indexes:**
- `idx_chunk_source` on `source_id`.
- `idx_chunk_triage` on `(source_id, triage_marked_for_extraction)`.

### `document_quality_scan`
One row per source. Per-doc LLM summary at ingestion (CONTEXT.md §8).

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | UUID. |
| `source_id` | TEXT NOT NULL UNIQUE | FK → `source_document.id`. UNIQUE = one scan per source (re-scan replaces). |
| `llm_call_id` | TEXT NOT NULL | FK → `llm_call.id`. Reproducibility trail (CONTEXT.md §14). |
| `scan_quality` | TEXT NOT NULL | `clean` / `markups_present` / `partially_illegible` / `unreadable`. CHECK. |
| `markups_present` | INTEGER NOT NULL DEFAULT 0 | 1/0. |
| `illegible_regions` | TEXT | JSON array of locators where text could not be extracted. |
| `mismatched_references` | TEXT | JSON array of cross-references the scan could not resolve. |
| `template_detected` | INTEGER NOT NULL DEFAULT 0 | 1 → mirrors `source_document.is_template`. |
| `proposed_class` | TEXT | LLM's proposal — copied to `source_document.document_class` after user confirms (or auto-confirmed if high confidence). |
| `proposed_state` | TEXT | Similar — may be NULL. |
| `proposed_revision` | TEXT | Similar. |
| `summary` | TEXT NOT NULL | Plain-English summary, surfaced in the import UI. |
| `scanned_at` | TEXT NOT NULL |  |

---

## 4. Extraction jobs + checkpoints

### `extraction_job`
One row per "press the run button". A job runs over a set of sources; each source is processed in its own subprocess worker (CONTEXT.md §6).

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | UUID. |
| `started_at` | TEXT NOT NULL |  |
| `finished_at` | TEXT |  |
| `status` | TEXT NOT NULL | `running` / `paused` / `completed` / `failed` / `cancelled`. CHECK. |
| `provider` | TEXT NOT NULL | At-job-start snapshot (may differ from project default). |
| `model` | TEXT NOT NULL |  |
| `prompt_text_spec_version` | TEXT NOT NULL | e.g. `v1.0`. Reproducibility (CONTEXT.md §14). |
| `prompt_bod_version` | TEXT NOT NULL |  |
| `cost_estimate_cents` | INTEGER | Pre-run estimate shown in the cost-preview UI (CONTEXT.md §13). |
| `cost_actual_cents` | INTEGER | Filled in as the job progresses. |
| `triggered_by` | TEXT NOT NULL | `user_initiated` / `resume`. CHECK. |

### `extraction_job_source`
Per-source checkpoint within a job. Enables pause/resume after laptop close, network drop, or app crash (CONTEXT.md §6).

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | UUID. |
| `job_id` | TEXT NOT NULL | FK → `extraction_job.id`. |
| `source_id` | TEXT NOT NULL | FK → `source_document.id`. |
| `status` | TEXT NOT NULL | `pending` / `triaging` / `extracting` / `completed` / `failed` / `skipped_unchanged`. CHECK. |
| `last_completed_chunk_id` | TEXT | FK → `source_document_chunk.id`. The resume point. |
| `started_at` | TEXT |  |
| `finished_at` | TEXT |  |
| `error_message` | TEXT | Populated when status = `failed`. |
| `worker_pid` | INTEGER | For diagnostics. |

**Indexes:**
- `idx_ejs_job_status` on `(job_id, status)` for resume-time lookup.
- `idx_ejs_source` on `source_id`.

UNIQUE constraint on `(job_id, source_id)` — a source appears at most once per job.

---

## 5. LLM call records (reproducibility — CONTEXT.md §14)

Every LLM call is logged with the full inference context so an old project re-runs deterministically.

### `llm_call`

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | UUID. |
| `job_id` | TEXT | FK → `extraction_job.id`. NULL for ad-hoc calls (e.g. quality scan triggered outside a job). |
| `purpose` | TEXT NOT NULL | `quality_scan` / `triage` / `extract_text_spec` / `extract_bod` / `conflict_pass` / `error_explain`. CHECK. |
| `provider` | TEXT NOT NULL |  |
| `model` | TEXT NOT NULL |  |
| `provider_api_version` | TEXT | e.g. `2024-10-22` for Anthropic; NULL where the SDK doesn't expose. |
| `system_prompt` | TEXT NOT NULL |  |
| `user_prompt` | TEXT NOT NULL | Resolved final prompt (after template substitution). |
| `prompt_version_ref` | TEXT | e.g. `text_spec_v1.0` — points to the template used, for diffing later. |
| `temperature` | REAL |  |
| `top_p` | REAL |  |
| `max_tokens` | INTEGER |  |
| `input_hash` | TEXT NOT NULL | SHA-256 of `(system_prompt + user_prompt + relevant inputs)`. Lets us detect if source content drifted between runs. |
| `response_text` | TEXT NOT NULL | Raw model response (pre-parse). |
| `parsed_response` | TEXT | JSON. Populated when the response was a structured extraction. |
| `prompt_tokens` | INTEGER |  |
| `completion_tokens` | INTEGER |  |
| `cache_read_tokens` | INTEGER | For Anthropic prompt caching (CONTEXT.md §13). |
| `cache_write_tokens` | INTEGER |  |
| `cost_cents` | INTEGER |  |
| `started_at` | TEXT NOT NULL |  |
| `finished_at` | TEXT |  |
| `error` | TEXT | NULL on success. |

**Indexes:**
- `idx_llm_call_job` on `job_id`.
- `idx_llm_call_purpose` on `purpose`.
- `idx_llm_call_input_hash` on `input_hash` for "have we seen this exact input before?" lookups.

---

## 6. Deliverables (the master register + quarantine + rejected, all in one table with status)

This is the central table. Per the multi-tag rule (CONTEXT.md §4), one row per `(deliverable × trade × service)` combination — not comma-separated.

### `deliverable`

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | UUID. **Stable across re-runs** — the schema-baked identity column for round-trip support (CONTEXT.md §15). |
| `extraction_group_id` | TEXT NOT NULL | UUID grouping rows that came from the same extraction-event candidate. Lets the multi-row split (single deliverable × N trades) be rebuilt. |
| `source_id` | TEXT NOT NULL | FK → `source_document.id`. |
| `chunk_id` | TEXT | FK → `source_document_chunk.id`. NULL if no chunk-level granularity (e.g. pure-PDF extraction without sectioning). |
| `extraction_job_id` | TEXT NOT NULL | FK → `extraction_job.id`. The job that produced this row. |
| `llm_call_id` | TEXT NOT NULL | FK → `llm_call.id`. The specific call. |
| `source_ref` | TEXT NOT NULL | JSON. Structured locator object — see §6.1 below. Designed for future click-to-open hyperlinking. |
| `trade_id` | TEXT | FK → `trade_taxonomy.id`. NULL allowed. |
| `service_id` | TEXT | FK → `service_taxonomy.id`. NULL allowed. |
| `category_id` | TEXT | FK → `category_taxonomy.id`. NULL allowed. |
| `applicable_standards` | TEXT NOT NULL DEFAULT `'[]'` | JSON array of strings. Strictly source-cited per the prompt rules. |
| `confidence` | TEXT NOT NULL | `high` / `medium` / `low`. CHECK. |
| `flags` | TEXT NOT NULL DEFAULT `'[]'` | JSON array from the controlled vocabulary (CONTEXT.md §8). |
| `flag_context` | TEXT NOT NULL DEFAULT `'{}'` | JSON object keyed by flag → payload. e.g. `{"negotiated_response": "...verbatim Landlord Comment..."}`. |
| `deliverables_summary` | TEXT NOT NULL | Terse present-tense noun phrase. ` ⚠` marker appended at render time when `negotiated_response` is in flags (NOT stored in this column — applied by the export layer). |
| `gate_outcome` | TEXT NOT NULL | `inside` / `borderline`. CHECK. (`outside` rows live in `audit_record` — see §7.) |
| `status` | TEXT NOT NULL | See state machine below. CHECK constraint. |
| `auto_route_decision` | TEXT NOT NULL | `auto_approved` / `quarantined`. CHECK. Snapshot of the §9 auto-route rule outcome. |
| `created_at` | TEXT NOT NULL |  |
| `last_user_action_at` | TEXT |  |
| `last_user_action_by_question_id` | TEXT | FK → `question.id` for audit when status changed via a HITL prompt. |
| `parent_deliverable_id` | TEXT | FK → `deliverable.id`. Self-referential; used when a row was edited (the original is kept; the new row points back). NULL for never-edited rows. |
| `notes` | TEXT |  |

**Status state machine (`status` column):**

```
quarantined        ─┬─► user_accepted ─────► (in master register)
                    ├─► user_edited   ─────► (in master register, with new row + parent_deliverable_id)
                    └─► user_rejected ─────► (excluded from master, kept for audit)

auto_approved ──────► (in master register, no user action)

borderline (quarantined) ─► user_promoted ─► (in master register, gate_outcome stays `borderline`)
```

**The "master register"** is a SQL view, not a table:

```sql
CREATE VIEW v_master_register AS
SELECT * FROM deliverable
WHERE status IN ('auto_approved', 'user_accepted', 'user_edited', 'user_promoted')
  AND id NOT IN (SELECT parent_deliverable_id FROM deliverable WHERE parent_deliverable_id IS NOT NULL);
-- The second clause excludes the pre-edit row when a user_edited successor exists.
```

**Indexes:**
- `idx_deliverable_source` on `source_id`.
- `idx_deliverable_job` on `extraction_job_id`.
- `idx_deliverable_status` on `status`.
- `idx_deliverable_trade` on `trade_id`.
- `idx_deliverable_service` on `service_id`.
- `idx_deliverable_extraction_group` on `extraction_group_id`.
- `idx_deliverable_gate_outcome` on `gate_outcome` (for the BORDERLINE review queue).

### 6.1. Structured `source_ref` JSON shape

Schema varies by source type. Examples — illustrative, not exhaustive (CONTEXT.md §7 — must accommodate future source types):

```jsonc
// PDF text spec
{
  "kind": "pdf_text",
  "filename": "AT-GLOBAL-OR-000103_SPC-ACC-01[11].pdf",
  "page": 18,
  "section": "2.5.2",
  "rendered": "p.18 §2.5.2"
}

// XLSX (BOD)
{
  "kind": "xlsx_row",
  "filename": "SYD2CD_(SYD29EX2)_-_AirTrunk.xlsx",
  "sheet": "Exhibit A2 - Technical Req",
  "row": 9,
  "req_id": "2.1.8.1",
  "rendered": "Sheet 'Exhibit A2 - Technical Req' Row 9 (Req Id 2.1.8.1)"
}

// PDF drawing
{
  "kind": "pdf_drawing",
  "filename": "M-401_Mech_GA_L01.pdf",
  "page": 1,
  "sheet_label": "M-401",
  "bbox": [120, 340, 480, 720],
  "annotation_id": null,
  "rendered": "Sheet M-401 (region [120,340,480,720])"
}

// DWG
{
  "kind": "dwg",
  "filename": "ELE-401.dwg",
  "layer": "ELE-CABLE-TRAY",
  "view": "Model",
  "extents": [0, 0, 20000, 12000],
  "rendered": "Layer 'ELE-CABLE-TRAY' (Model view)"
}

// Email
{
  "kind": "email",
  "filename": "thread-2025-08-12-RFI-clarification.eml",
  "message_index": 3,
  "paragraph": 2,
  "rendered": "Message 3 ¶ 2 of thread-2025-08-12-RFI-clarification.eml"
}
```

The `rendered` field is what the Excel cell shows; the rest is what powers future click-to-open hyperlinking. Keep the rendering convention consistent across kinds so PMs see a uniform style.

---

## 7. Audit (OUTSIDE rows — logged not lost)

Per CONTEXT.md §3 — outside rows are NOT silently dropped. They live here, visible in a "Below Threshold" review queue.

### `audit_record`

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | UUID. |
| `source_id` | TEXT NOT NULL | FK → `source_document.id`. |
| `chunk_id` | TEXT | FK → `source_document_chunk.id`. |
| `extraction_job_id` | TEXT NOT NULL | FK → `extraction_job.id`. |
| `llm_call_id` | TEXT NOT NULL | FK → `llm_call.id`. |
| `source_ref` | TEXT NOT NULL | JSON, same shape as `deliverable.source_ref`. |
| `candidate_text` | TEXT NOT NULL | Verbatim or paraphrase of what was rejected. |
| `rejection_reason` | TEXT NOT NULL | One sentence; which rule fired. |
| `landlord_response` | TEXT | Where the rejection came via the BOD disposition path. |
| `created_at` | TEXT NOT NULL |  |
| `user_promoted_to_deliverable_id` | TEXT | FK → `deliverable.id`. NULL until a reviewer rescues it from audit. |

**Indexes:**
- `idx_audit_source` on `source_id`.
- `idx_audit_job` on `extraction_job_id`.

A reviewer scrolling the "Below Threshold" queue can promote any audit row to a deliverable; the row stays in audit with `user_promoted_to_deliverable_id` set, so the audit trail is preserved.

---

## 8. HITL questions (batched ambiguity prompts — CONTEXT.md §9)

The LLM logs questions during a run; the user resolves them in batch.

### `question`

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | UUID. |
| `extraction_job_id` | TEXT NOT NULL | FK → `extraction_job.id`. |
| `llm_call_id` | TEXT NOT NULL | FK → `llm_call.id`. |
| `kind` | TEXT NOT NULL | `service_mapping` / `taxonomy_new_value` / `disposition_unclear` / `borderline_decision` / `conflict_resolution` / `other`. CHECK. |
| `context` | TEXT NOT NULL | Why the question arose. |
| `question_text` | TEXT NOT NULL | The actual question to the user. |
| `candidate_source_refs` | TEXT NOT NULL DEFAULT `'[]'` | JSON array of `source_ref` objects pointing to the relevant content. |
| `proposed_resolution` | TEXT | LLM's suggestion, where it has one. |
| `status` | TEXT NOT NULL DEFAULT `'pending'` | `pending` / `resolved` / `dismissed`. CHECK. |
| `resolved_at` | TEXT |  |
| `resolution_payload` | TEXT | JSON. Whatever data the user's answer produced (e.g. confirmed service mapping, accepted new taxonomy value). |
| `created_at` | TEXT NOT NULL |  |

**Index:** `idx_question_job_status` on `(extraction_job_id, status)`.

---

## 9. Conflicts (cross-source disagreements — CONTEXT.md §9, §10)

Conflicts are detected by a second-pass LLM run AFTER initial extraction. Each conflict references two-or-more deliverables (or audit rows) that disagree.

### `conflict`

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | UUID. |
| `extraction_job_id` | TEXT NOT NULL | FK → `extraction_job.id`. The conflict-pass job that surfaced it. |
| `llm_call_id` | TEXT NOT NULL | FK → `llm_call.id`. |
| `kind` | TEXT NOT NULL | `cross_source_content` / `responsibility` / `revision` / `document_class` / `scope_demarcation`. CHECK. |
| `most_onerous_party_id` | TEXT | FK → `deliverable.id` or `audit_record.id`. NULL when LLM judged "not directly comparable" (CONTEXT.md §9). |
| `most_onerous_reasoning` | TEXT NOT NULL | The LLM's stated reasoning. Always populated — even when the LLM ranked NULL ("not directly comparable", with explanation). |
| `status` | TEXT NOT NULL DEFAULT `'pending'` | `pending` / `resolved_accept_a` / `resolved_accept_b` / `resolved_hybrid` / `resolved_reject_both`. CHECK. |
| `resolved_at` | TEXT |  |
| `resolved_into_deliverable_id` | TEXT | FK → `deliverable.id`. Where the resolution landed (if a hybrid was created). |
| `created_at` | TEXT NOT NULL |  |

### `conflict_party`
Many-to-many between `conflict` and the rows in conflict.

| Column | Type | Notes |
|---|---|---|
| `conflict_id` | TEXT NOT NULL | FK → `conflict.id`. |
| `party_kind` | TEXT NOT NULL | `deliverable` / `audit`. CHECK. |
| `party_id` | TEXT NOT NULL | FK → either `deliverable.id` or `audit_record.id` (no SQLite-level FK to two tables; enforced in app code). |
| `party_position` | TEXT | One-sentence summary of what this party requires/asserts. |

PRIMARY KEY (`conflict_id`, `party_id`).

When a deliverable is in conflict, each affected `deliverable` row carries `conflicts_with_source_<id>` in its `flags`, where `<id>` is the conflict ID.

---

## 10. Optional / deferred — schema seams reserved for v1.x

These tables are **not created in v1** but are listed here so the build chat doesn't accidentally implement them in a way that locks v1.x out.

- **`deliverable_dependency`** — modelling explicit deliverable-to-deliverable dependencies (Mech ductwork → GC penetration). Deferred per CONTEXT.md §5 builders works section.
- **`analysis_run`** — top-level for v1.x analyses (Compliance Traceability / OSE Procurement / Trade Overlap / Quantity Reconciliation / Dependency Dangling References). Deferred per BUILD_KICKOFF and CONTEXT.md §23 #9.
- **`bootstrap_proposal`** — per-project bootstrap LLM sweep output (CONTEXT.md §23 #4). Deferred.
- **`round_trip_import`** — Excel-edit re-import staging. Deferred per CONTEXT.md §15. The stable `id` column on `deliverable` is the seam.

---

## 11. Open items for build phase

These are the schema-shape decisions I'd like to confirm before writing DDL.

1. **`deliverable.source_ref` as JSON column vs separate `source_ref` table.** v1 sketch uses JSON inline. JSON keeps the row self-contained (good for audit, good for export) at the cost of being non-queryable as structured. The structured queries we'd want (find all deliverables on PDF page X) are not v1 use cases. **Recommendation: keep inline JSON for v1**, introduce a `source_ref_index` derived table in v1.x if needed for analyses.

2. **Foreign key to two tables on `conflict_party.party_id`.** SQLite cannot enforce this; app code must. Alternative: split into `conflict_party_deliverable` and `conflict_party_audit`. Slightly tidier but doubles the number of tables. **Recommendation: single table with app-enforced integrity for v1.**

3. **Status timestamps on `deliverable`.** I have `created_at` and `last_user_action_at`. Should every status transition write its own row in a `deliverable_history` audit table? The kickoff / CONTEXT.md don't require this. **Recommendation: skip in v1**, add via migration if a real audit need surfaces. The `parent_deliverable_id` chain already preserves edit history.

4. **`flag_context` as JSON object vs a separate `deliverable_flag` table.** JSON keeps the deliverable row self-contained. A separate table would let us query "show me all rows with `negotiated_response` flag context length > 200 chars". That is not a v1 query. **Recommendation: JSON for v1.**

5. **`extraction_job_source.last_completed_chunk_id` vs progress percent.** Chunk-id checkpointing is precise but assumes the chunk list is stable across resume. If the user re-imports a doc (different content_hash → different source_id) the resume point is meaningless anyway. **Recommendation: keep chunk-id checkpointing**, document that re-imported sources start fresh.

6. **`document_quality_scan` is scoped one-per-source via UNIQUE constraint.** That means a re-scan replaces the previous scan and we lose the prior scan's content. Acceptable? Or should we keep history? **Recommendation: keep latest only for v1**; add an `is_current` flag if history becomes valuable.

7. **`license` table.** Not in this schema — license state lives outside the per-project file (in the app's local config). Confirm: is that the right home? **Recommendation: yes — license is per-install, not per-project.**

8. **No native `varchar(N)` constraints in SQLite.** All TEXT columns are unbounded. Where field length matters (e.g. `confidence` is 4-6 chars), the CHECK constraint enumerates the values. Don't add app-level length validation until something breaks.

---

## 12. Decisions I made without asking (flagging for transparency)

- **Per-axis taxonomy tables, not one polymorphic table.** Trade / service / category each have their own table because they have different governance vocabularies (specialist/cross-cutting/vendor for trade; nothing for service; narrow vocabulary for category). One polymorphic table would obscure those differences.
- **Single `deliverable` table with `status` column rather than separate `deliverable` / `quarantine` / `rejected` tables.** Keeps the schema simpler; the master register is a view filter. The state machine documents the transitions.
- **Audit (OUTSIDE) gets its own table, not the same table as deliverables.** Different shape (`candidate_text`, `rejection_reason`), different lifecycle (audit rows are immutable except for `user_promoted_to_deliverable_id`).
- **`extraction_group_id` introduced** to group multi-row outputs from a single candidate. Not an explicit CONTEXT.md requirement — surfaced while drafting; I judged it necessary for the multi-tag rule to round-trip correctly.
- **Schema migrations table** baked in from v1 even though there are no migrations yet. Introducing it later requires a migration to introduce the migrations table, which is awkward. Cheap to ship now.

---

*Sketch complete. Awaiting confirmation / redirection before moving to step 5 (UX flows).*
