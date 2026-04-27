-- Meridian v1 schema. Authority: design/DATA_MODEL_V1.md.
-- One SQLite file per project (CONTEXT.md §6).

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ─── 0. Schema migrations ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

-- ─── 1. Project + auth ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS project (
    id                       TEXT PRIMARY KEY,
    name                     TEXT NOT NULL,
    created_at               TEXT NOT NULL,
    schema_version           INTEGER NOT NULL,
    app_version              TEXT NOT NULL,
    tool_disclaimer_version  TEXT NOT NULL,
    default_provider         TEXT NOT NULL CHECK (default_provider IN ('anthropic','openai')),
    default_model            TEXT NOT NULL,
    auto_approval_threshold  TEXT NOT NULL DEFAULT 'high_no_flags',
    notes                    TEXT
);

CREATE TABLE IF NOT EXISTS app_setting (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT NOT NULL
);

-- ─── 2. Taxonomy ───────────────────────────────────────────────────────────
-- v6 (round 15): the bootstrap LLM sweep emits a recommended_action / merge_target /
-- confidence / reasoning per proposed taxonomy value, and may auto-apply high-
-- confidence merges (source='llm_auto_merged'). See docs/DECISIONS.md §3.10.
CREATE TABLE IF NOT EXISTS trade_taxonomy (
    id                       TEXT PRIMARY KEY,
    value                    TEXT NOT NULL UNIQUE,
    category_hint            TEXT NOT NULL CHECK (category_hint IN ('specialist','cross_cutting','vendor')),
    synonyms                 TEXT CHECK (synonyms IS NULL OR json_valid(synonyms)),
    source                   TEXT NOT NULL CHECK (source IN (
        'default','user_added','user_merged',
        'llm_proposed_high_confidence','llm_auto_merged'
    )),
    confirmed_at             TEXT,
    is_active                INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at               TEXT NOT NULL,
    notes                    TEXT,
    llm_recommended_action   TEXT CHECK (
        llm_recommended_action IS NULL OR llm_recommended_action IN (
            'confirm','merge_into','defer_to_user'
        )
    ),
    llm_merge_target         TEXT,
    llm_confidence           REAL CHECK (
        llm_confidence IS NULL OR (llm_confidence BETWEEN 0 AND 1)
    ),
    llm_reasoning            TEXT
);

CREATE TABLE IF NOT EXISTS service_taxonomy (
    id                       TEXT PRIMARY KEY,
    value                    TEXT NOT NULL UNIQUE,
    synonyms                 TEXT CHECK (synonyms IS NULL OR json_valid(synonyms)),
    source                   TEXT NOT NULL CHECK (source IN (
        'default','user_added','user_merged',
        'llm_proposed_high_confidence','llm_auto_merged'
    )),
    confirmed_at             TEXT,
    is_active                INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at               TEXT NOT NULL,
    notes                    TEXT,
    llm_recommended_action   TEXT CHECK (
        llm_recommended_action IS NULL OR llm_recommended_action IN (
            'confirm','merge_into','defer_to_user'
        )
    ),
    llm_merge_target         TEXT,
    llm_confidence           REAL CHECK (
        llm_confidence IS NULL OR (llm_confidence BETWEEN 0 AND 1)
    ),
    llm_reasoning            TEXT
);

CREATE TABLE IF NOT EXISTS category_taxonomy (
    id                       TEXT PRIMARY KEY,
    value                    TEXT NOT NULL UNIQUE,
    synonyms                 TEXT CHECK (synonyms IS NULL OR json_valid(synonyms)),
    source                   TEXT NOT NULL CHECK (source IN (
        'default','user_added','user_merged',
        'llm_proposed_high_confidence','llm_auto_merged'
    )),
    confirmed_at             TEXT,
    is_active                INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at               TEXT NOT NULL,
    notes                    TEXT,
    llm_recommended_action   TEXT CHECK (
        llm_recommended_action IS NULL OR llm_recommended_action IN (
            'confirm','merge_into','defer_to_user'
        )
    ),
    llm_merge_target         TEXT,
    llm_confidence           REAL CHECK (
        llm_confidence IS NULL OR (llm_confidence BETWEEN 0 AND 1)
    ),
    llm_reasoning            TEXT
);

CREATE TABLE IF NOT EXISTS service_mapping (
    id                          TEXT PRIMARY KEY,
    disc_section_text           TEXT NOT NULL UNIQUE,
    service_id                  TEXT REFERENCES service_taxonomy(id),
    confirmed_at                TEXT NOT NULL,
    confirmed_by_question_id    TEXT,  -- soft FK; question table created below
    notes                       TEXT
);

-- ─── 3. Sources ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS source_document (
    id                          TEXT PRIMARY KEY,
    filename                    TEXT NOT NULL,
    relative_path               TEXT NOT NULL,
    content_hash                TEXT NOT NULL UNIQUE,
    mime_type                   TEXT NOT NULL,
    size_bytes                  INTEGER NOT NULL,
    imported_at                 TEXT NOT NULL,
    document_class              TEXT CHECK (
        document_class IS NULL OR document_class IN (
            'customer_requirements','global_tr','global_ose_spec',
            'project_amendment','project_clarification','drawing',
            'demarcation_schedule','methodology','template','unknown'
        )
    ),
    document_state              TEXT CHECK (
        document_state IS NULL OR document_state IN (
            'concept','30%','50%','90%','100%','IFC','as-built'
        )
    ),
    revision                    TEXT,
    revision_detected_via       TEXT CHECK (
        revision_detected_via IS NULL OR revision_detected_via IN (
            'filename_pattern','embedded_metadata','content_scan','user_pinned'
        )
    ),
    is_authoritative_revision   INTEGER NOT NULL DEFAULT 1 CHECK (is_authoritative_revision IN (0,1)),
    superseded_by_id            TEXT REFERENCES source_document(id),
    is_template                 INTEGER NOT NULL DEFAULT 0 CHECK (is_template IN (0,1)),
    is_demarcation_schedule     INTEGER NOT NULL DEFAULT 0 CHECK (is_demarcation_schedule IN (0,1)),
    quality_scan_id             TEXT,  -- soft FK; document_quality_scan created below
    extraction_path             TEXT NOT NULL CHECK (extraction_path IN (
        'text_spec','bod_import','drawing','demarcation','excluded','pending'
    )) DEFAULT 'pending',
    notes                       TEXT
);

CREATE INDEX IF NOT EXISTS idx_source_document_class ON source_document(document_class);
CREATE INDEX IF NOT EXISTS idx_source_document_filename ON source_document(filename);
CREATE INDEX IF NOT EXISTS idx_source_document_authoritative
    ON source_document(filename, is_authoritative_revision);

CREATE TABLE IF NOT EXISTS source_document_text (
    source_id          TEXT PRIMARY KEY REFERENCES source_document(id) ON DELETE CASCADE,
    extraction_method  TEXT NOT NULL CHECK (extraction_method IN (
        'pdf_text','pdf_ocr','xlsx_parsed','docx_parsed','dwg_converted','eml_parsed'
    )),
    extracted_at       TEXT NOT NULL,
    text               TEXT,
    metadata           TEXT CHECK (metadata IS NULL OR json_valid(metadata))
);

CREATE TABLE IF NOT EXISTS source_document_chunk (
    id                              TEXT PRIMARY KEY,
    source_id                       TEXT NOT NULL REFERENCES source_document(id) ON DELETE CASCADE,
    chunk_kind                      TEXT NOT NULL CHECK (chunk_kind IN (
        'pdf_page','pdf_section','xlsx_row','xlsx_cell_range','drawing_region','email_message'
    )),
    locator                         TEXT NOT NULL CHECK (json_valid(locator)),
    text                            TEXT,
    triage_marked_for_extraction    INTEGER CHECK (
        triage_marked_for_extraction IS NULL OR triage_marked_for_extraction IN (0,1)
    ),
    triage_reason                   TEXT,
    -- Chunk-level resume support (schema v5). The state machine is:
    --   pending    → never seen by the per-chunk extractor (triage)
    --   in_progress→ a worker started processing this chunk; if observed on
    --                resume it is an orphaned mid-write and is re-processed
    --   extracted  → the chunk was processed and produced output (triage kept
    --                it OR a downstream extractor consumed it)
    --   skipped    → the chunk was processed and rejected (triage keep=false)
    -- Resume logic (workers/extraction_worker, extract/triage): chunks in
    -- {extracted, skipped} are not re-processed for the same source.
    extraction_status               TEXT NOT NULL DEFAULT 'pending' CHECK (
        extraction_status IN ('pending','in_progress','extracted','skipped')
    ),
    extraction_started_at           TEXT,
    extraction_finished_at          TEXT,
    extraction_job_id               TEXT REFERENCES extraction_job(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_chunk_source ON source_document_chunk(source_id);
CREATE INDEX IF NOT EXISTS idx_chunk_triage
    ON source_document_chunk(source_id, triage_marked_for_extraction);
CREATE INDEX IF NOT EXISTS idx_chunk_extraction_status
    ON source_document_chunk(source_id, extraction_status);

CREATE TABLE IF NOT EXISTS document_quality_scan (
    id                      TEXT PRIMARY KEY,
    source_id               TEXT NOT NULL UNIQUE REFERENCES source_document(id) ON DELETE CASCADE,
    llm_call_id             TEXT NOT NULL,  -- soft FK; llm_call created below
    scan_quality            TEXT NOT NULL CHECK (scan_quality IN (
        'clean','markups_present','partially_illegible','unreadable'
    )),
    markups_present         INTEGER NOT NULL DEFAULT 0 CHECK (markups_present IN (0,1)),
    illegible_regions       TEXT CHECK (illegible_regions IS NULL OR json_valid(illegible_regions)),
    mismatched_references   TEXT CHECK (mismatched_references IS NULL OR json_valid(mismatched_references)),
    template_detected       INTEGER NOT NULL DEFAULT 0 CHECK (template_detected IN (0,1)),
    proposed_class          TEXT,
    proposed_state          TEXT,
    proposed_revision       TEXT,
    summary                 TEXT NOT NULL,
    scanned_at              TEXT NOT NULL
);

-- ─── 4. Extraction jobs + checkpoints ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS extraction_job (
    id                          TEXT PRIMARY KEY,
    started_at                  TEXT NOT NULL,
    finished_at                 TEXT,
    status                      TEXT NOT NULL CHECK (status IN (
        'running','paused','completed','failed','cancelled'
    )),
    provider                    TEXT NOT NULL,
    model                       TEXT NOT NULL,
    prompt_text_spec_version    TEXT NOT NULL,
    prompt_bod_version          TEXT NOT NULL,
    cost_estimate_cents         INTEGER,
    cost_actual_cents           INTEGER,
    triggered_by                TEXT NOT NULL CHECK (triggered_by IN ('user_initiated','resume'))
);

CREATE TABLE IF NOT EXISTS extraction_job_source (
    id                       TEXT PRIMARY KEY,
    job_id                   TEXT NOT NULL REFERENCES extraction_job(id) ON DELETE CASCADE,
    source_id                TEXT NOT NULL REFERENCES source_document(id),
    status                   TEXT NOT NULL CHECK (status IN (
        'pending','triaging','extracting','completed','failed','skipped_unchanged'
    )),
    last_completed_chunk_id  TEXT REFERENCES source_document_chunk(id),
    started_at               TEXT,
    finished_at              TEXT,
    error_message            TEXT,
    worker_pid               INTEGER,
    UNIQUE(job_id, source_id)
);

CREATE INDEX IF NOT EXISTS idx_ejs_job_status ON extraction_job_source(job_id, status);
CREATE INDEX IF NOT EXISTS idx_ejs_source ON extraction_job_source(source_id);

-- ─── 5. LLM call records (reproducibility — CONTEXT.md §14) ───────────────
CREATE TABLE IF NOT EXISTS llm_call (
    id                      TEXT PRIMARY KEY,
    job_id                  TEXT REFERENCES extraction_job(id) ON DELETE SET NULL,
    purpose                 TEXT NOT NULL CHECK (purpose IN (
        'quality_scan','triage','extract_text_spec','extract_demarcation',
        'extract_bod','conflict_pass','error_explain'
    )),
    provider                TEXT NOT NULL,
    model                   TEXT NOT NULL,
    provider_api_version    TEXT,
    system_prompt           TEXT NOT NULL,
    user_prompt             TEXT NOT NULL,
    prompt_version_ref      TEXT,
    temperature             REAL,
    top_p                   REAL,
    max_tokens              INTEGER,
    input_hash              TEXT NOT NULL,
    response_text           TEXT NOT NULL,
    parsed_response         TEXT CHECK (parsed_response IS NULL OR json_valid(parsed_response)),
    prompt_tokens           INTEGER,
    completion_tokens       INTEGER,
    cache_read_tokens       INTEGER,
    cache_write_tokens      INTEGER,
    cost_cents              INTEGER,
    started_at              TEXT NOT NULL,
    finished_at             TEXT,
    error                   TEXT
);

CREATE INDEX IF NOT EXISTS idx_llm_call_job ON llm_call(job_id);
CREATE INDEX IF NOT EXISTS idx_llm_call_purpose ON llm_call(purpose);
CREATE INDEX IF NOT EXISTS idx_llm_call_input_hash ON llm_call(input_hash);

-- ─── 6. Deliverables (master / quarantine / rejected via status) ──────────
CREATE TABLE IF NOT EXISTS deliverable (
    id                              TEXT PRIMARY KEY,
    extraction_group_id             TEXT NOT NULL,
    source_id                       TEXT NOT NULL REFERENCES source_document(id),
    chunk_id                        TEXT REFERENCES source_document_chunk(id),
    extraction_job_id               TEXT NOT NULL REFERENCES extraction_job(id),
    llm_call_id                     TEXT NOT NULL REFERENCES llm_call(id),
    source_ref                      TEXT NOT NULL CHECK (json_valid(source_ref)),
    trade_id                        TEXT REFERENCES trade_taxonomy(id),
    service_id                      TEXT REFERENCES service_taxonomy(id),
    category_id                     TEXT REFERENCES category_taxonomy(id),
    applicable_standards            TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(applicable_standards)),
    confidence                      TEXT NOT NULL CHECK (confidence IN ('high','medium','low')),
    flags                           TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(flags)),
    flag_context                    TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(flag_context)),
    deliverables_summary            TEXT NOT NULL,
    gate_outcome                    TEXT NOT NULL CHECK (gate_outcome IN ('inside','borderline')),
    status                          TEXT NOT NULL CHECK (status IN (
        'auto_approved','quarantined','user_accepted','user_edited','user_rejected','user_promoted'
    )),
    auto_route_decision             TEXT NOT NULL CHECK (auto_route_decision IN ('auto_approved','quarantined')),
    created_at                      TEXT NOT NULL,
    last_user_action_at             TEXT,
    last_user_action_by_question_id TEXT,
    parent_deliverable_id           TEXT REFERENCES deliverable(id),
    notes                           TEXT
);

CREATE INDEX IF NOT EXISTS idx_deliverable_source ON deliverable(source_id);
CREATE INDEX IF NOT EXISTS idx_deliverable_job ON deliverable(extraction_job_id);
CREATE INDEX IF NOT EXISTS idx_deliverable_status ON deliverable(status);
CREATE INDEX IF NOT EXISTS idx_deliverable_trade ON deliverable(trade_id);
CREATE INDEX IF NOT EXISTS idx_deliverable_service ON deliverable(service_id);
CREATE INDEX IF NOT EXISTS idx_deliverable_extraction_group ON deliverable(extraction_group_id);
CREATE INDEX IF NOT EXISTS idx_deliverable_gate_outcome ON deliverable(gate_outcome);

-- Master register view (CONTEXT.md §4 + §15).
DROP VIEW IF EXISTS v_master_register;
CREATE VIEW v_master_register AS
SELECT *
FROM deliverable d
WHERE d.status IN ('auto_approved','user_accepted','user_edited','user_promoted')
  AND d.id NOT IN (
      SELECT parent_deliverable_id FROM deliverable WHERE parent_deliverable_id IS NOT NULL
  );

-- ─── 7. Audit (OUTSIDE rows — logged not lost, CONTEXT.md §3) ─────────────
CREATE TABLE IF NOT EXISTS audit_record (
    id                                  TEXT PRIMARY KEY,
    source_id                           TEXT NOT NULL REFERENCES source_document(id),
    chunk_id                            TEXT REFERENCES source_document_chunk(id),
    extraction_job_id                   TEXT NOT NULL REFERENCES extraction_job(id),
    llm_call_id                         TEXT NOT NULL REFERENCES llm_call(id),
    source_ref                          TEXT NOT NULL CHECK (json_valid(source_ref)),
    candidate_text                      TEXT NOT NULL,
    rejection_reason                    TEXT NOT NULL,
    landlord_response                   TEXT,
    created_at                          TEXT NOT NULL,
    user_promoted_to_deliverable_id     TEXT REFERENCES deliverable(id)
);

CREATE INDEX IF NOT EXISTS idx_audit_source ON audit_record(source_id);
CREATE INDEX IF NOT EXISTS idx_audit_job ON audit_record(extraction_job_id);

-- ─── 8. HITL questions (batched per CONTEXT.md §9) ────────────────────────
CREATE TABLE IF NOT EXISTS question (
    id                          TEXT PRIMARY KEY,
    extraction_job_id           TEXT NOT NULL REFERENCES extraction_job(id),
    llm_call_id                 TEXT NOT NULL REFERENCES llm_call(id),
    kind                        TEXT NOT NULL CHECK (kind IN (
        'service_mapping','taxonomy_new_value','disposition_unclear',
        'borderline_decision','conflict_resolution','other'
    )),
    context                     TEXT NOT NULL,
    question_text               TEXT NOT NULL,
    candidate_source_refs       TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(candidate_source_refs)),
    proposed_resolution         TEXT,
    status                      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','resolved','dismissed')),
    resolved_at                 TEXT,
    resolution_payload          TEXT CHECK (resolution_payload IS NULL OR json_valid(resolution_payload)),
    created_at                  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_question_job_status ON question(extraction_job_id, status);

-- ─── 9. Conflicts (cross-source disagreements per CONTEXT.md §9, §10) ─────
CREATE TABLE IF NOT EXISTS conflict (
    id                              TEXT PRIMARY KEY,
    extraction_job_id               TEXT NOT NULL REFERENCES extraction_job(id),
    llm_call_id                     TEXT NOT NULL REFERENCES llm_call(id),
    kind                            TEXT NOT NULL CHECK (kind IN (
        'cross_source_content','responsibility','revision','document_class','scope_demarcation'
    )),
    most_onerous_party_id           TEXT,
    most_onerous_reasoning          TEXT NOT NULL,
    status                          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
        'pending','resolved_accept_a','resolved_accept_b','resolved_hybrid','resolved_reject_both'
    )),
    resolved_at                     TEXT,
    resolved_into_deliverable_id    TEXT REFERENCES deliverable(id),
    created_at                      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conflict_party (
    conflict_id     TEXT NOT NULL REFERENCES conflict(id) ON DELETE CASCADE,
    party_kind      TEXT NOT NULL CHECK (party_kind IN ('deliverable','audit')),
    party_id        TEXT NOT NULL,
    party_position  TEXT,
    PRIMARY KEY (conflict_id, party_id)
);

-- ─── 10. Cross-reference exhaustive sweep (schema v3) ─────────────────────
-- Append-only block. Created by post-extraction sweep that scans every
-- ingested doc for explicit textual references to other project docs.
-- See src/meridian/extract/cross_references.py :: run_exhaustive_sweep.

CREATE TABLE IF NOT EXISTS cross_reference_sweep_run (
    id                          TEXT PRIMARY KEY,
    started_at                  TEXT NOT NULL,
    finished_at                 TEXT,
    status                      TEXT NOT NULL CHECK (status IN (
        'running','completed','failed'
    )),
    llm_assist                  INTEGER NOT NULL DEFAULT 0 CHECK (llm_assist IN (0,1)),
    dry_run                     INTEGER NOT NULL DEFAULT 0 CHECK (dry_run IN (0,1)),
    sources_scanned             INTEGER NOT NULL DEFAULT 0,
    deliverables_scanned        INTEGER NOT NULL DEFAULT 0,
    references_confirmed        INTEGER NOT NULL DEFAULT 0,
    references_borderline       INTEGER NOT NULL DEFAULT 0,
    references_rejected         INTEGER NOT NULL DEFAULT 0,
    report_csv_path             TEXT,
    report_md_path              TEXT,
    error_message               TEXT
);

CREATE TABLE IF NOT EXISTS cross_reference_sweep_result (
    id                          TEXT PRIMARY KEY,
    sweep_run_id                TEXT NOT NULL REFERENCES cross_reference_sweep_run(id) ON DELETE CASCADE,
    from_source_id              TEXT NOT NULL REFERENCES source_document(id),
    to_source_id                TEXT REFERENCES source_document(id),
    to_deliverable_id           TEXT REFERENCES deliverable(id),
    anchor_kind                 TEXT NOT NULL CHECK (anchor_kind IN (
        'section','clause','drawing','spec','standard','equipment_tag',
        'vendor','owner_id','short_spec','other'
    )),
    raw_match                   TEXT NOT NULL,
    normalised_match            TEXT NOT NULL,
    from_excerpt                TEXT,
    detection_method            TEXT NOT NULL CHECK (detection_method IN (
        'regex','string','llm'
    )),
    outcome                     TEXT NOT NULL CHECK (outcome IN (
        'confirmed','borderline','external_reference','rejected'
    )),
    confidence                  TEXT NOT NULL CHECK (confidence IN ('high','medium','low')),
    reason                      TEXT,
    review_question_id          TEXT REFERENCES question(id),
    created_at                  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_xref_sweep_run ON cross_reference_sweep_result(sweep_run_id);
CREATE INDEX IF NOT EXISTS idx_xref_sweep_from ON cross_reference_sweep_result(from_source_id);
CREATE INDEX IF NOT EXISTS idx_xref_sweep_to ON cross_reference_sweep_result(to_source_id);
CREATE INDEX IF NOT EXISTS idx_xref_sweep_outcome ON cross_reference_sweep_result(outcome);
