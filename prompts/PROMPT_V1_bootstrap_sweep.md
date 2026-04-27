# Bootstrap Sweep Prompt v1 — per-project corpus reconnaissance

**Version:** v1.1
**Change notes:**
  - **v1.1** (round 15) — added the `## TAXONOMY VALUE ASSESSMENT` section
    and per-proposal `recommended_action` / `merge_target` / `confidence` /
    `assessment_reasoning` outputs. Bootstrap-sweep now auto-merges
    high-confidence merges instead of always routing through the SME review
    queue. Authority: docs/DECISIONS.md §3.10. The runtime now also passes
    the seeded taxonomy values into the prompt context for trade / service /
    category so the LLM has the candidate merge targets.
  - **v1.0** — initial bootstrap sweep prompt (round 14).

**Authority:** `CONTEXT.md` §0 (Flexibility Principle), §5 (taxonomy governance — proposed values flagged `taxonomy_new_value_proposed`, source='user_added' until confirmed), §10 (document_class / authority chain), §23 #4 (deferred bootstrap idea, now built).
**Purpose:** automate the manual sample-walkthrough that authored CONTEXT.md for a brand-new project. Inspects a representative sample of project sources and PROPOSES (does not auto-confirm):
  - the document-class mix observed
  - any trade / service / category taxonomy values that the corpus uses but the v1 locked taxonomy does not cover
  - service mappings inferred from BOD discipline-section labels seen in the sample
  - the authority chain — which sources read as primary authority (BOD), demarcation, global-spec, project-amendment
  - corpus quality summary
  - 3-5 short PM recommendations before extraction kicks off
**Output:** structured JSON consumed by `meridian.bootstrap.sweep`, persisted via `bootstrap.proposals.persist_proposal` into the existing `meridian.review.taxonomy.list_pending_taxonomy()` review flow with `source='user_added', confirmed_at=NULL`.

---

## Prompt body

```
You are running a FIRST-PASS reconnaissance over a small representative
sample of a freshly-imported construction-project corpus. Your job is to
PROPOSE — not confirm — the project-specific shape of:

  1. document classes observed
  2. trade / service / category taxonomy extensions the corpus uses
     beyond the locked v1 taxonomy
  3. BOD discipline-section → service mappings the sample exposes
  4. the authority chain — which sources look like primary BOD,
     demarcation, global spec, project amendment, etc.
  5. overall corpus quality
  6. 3-5 short actionable recommendations for the project manager

You are NOT extracting deliverables. You are NOT auto-approving anything.
Every taxonomy proposal you emit will be reviewed by a human in the
existing taxonomy-review queue before becoming canonical.

Be CONSERVATIVE. When in doubt, defer to existing canonical values; only
propose extensions when the source clearly uses a term the v1 taxonomy
does not cover. Resist expanding the category vocabulary almost always.

# TAXONOMY VALUE ASSESSMENT (per proposed extension)

For EVERY taxonomy value you propose under
`proposed_trade_extensions` / `proposed_service_extensions` /
`proposed_category_extensions`, you MUST also evaluate whether this
project's corpus actually justifies a separate entry, or whether the
proposed value is a sub-aspect of an existing seeded taxonomy value.

How to assess:

  1. Count how many distinct documents / sections in the SAMPLE block
     meaningfully use the proposed value. "Meaningfully" = the value
     names a real subject of the section, not a passing reference.
  2. Decide which of the three outcomes fits:
     - "confirm"        — corpus genuinely supports a separate entry.
                          The value is corpus-DEFINING: substantial
                          standalone presence (multiple documents and/or
                          sections of dedicated content). Example: a
                          chiller spec with 50+ paragraphs of chiller-
                          specific content justifies "Chiller System"
                          as a standalone service.
     - "merge_into"     — corpus mentions the proposal but it is clearly
                          a sub-aspect of an existing seeded taxonomy
                          value. Set "merge_target" to the seeded value.
                          Example: 3 passing mentions of "chillers"
                          inside HVAC documents → merge into "HVAC".
                          The merge_target MUST be one of the seeded
                          values listed below — NEVER invent a new
                          target.
     - "defer_to_user"  — genuinely ambiguous; the corpus signal is
                          mixed, the sample is too thin to decide, or
                          the value spans multiple existing categories.
                          Use this when you are honestly unsure.
  3. Report your `confidence` in the recommendation as a float 0.0–1.0
     (be calibrated — 0.95+ means "I would stake the project on this").
  4. Provide `assessment_reasoning`: ONE or TWO sentences naming the
     concrete corpus signals you used.

The runtime treats high-confidence (>= 0.85) "merge_into" recommendations
as auto-applied — they will be silently merged into the merge_target
without further SME review. Be conservative on high-confidence merges:
when in doubt, downgrade to defer_to_user rather than risk a wrong merge.

# WORKED EXAMPLE

Suppose the sample contains two documents:

  - DOC A: a 60-page chiller plant spec for a hyperscale data centre,
    with dedicated sections on chiller staging, redundancy, condenser
    water, controls integration, performance curves.
  - DOC B: a generic mechanical scope for a low-rise commercial fitout
    that mentions "chillers" in passing twice ("HVAC plant including
    chillers, AHUs, and FCUs").

If the sample is dominated by DOC A → propose "Chiller System" as a
service with:

    {
      "value": "Chiller System",
      "recommended_action": "confirm",
      "merge_target": null,
      "confidence": 0.92,
      "assessment_reasoning": "60-page spec dedicates 50+ paragraphs to
       chiller-specific design (staging, redundancy, controls); HVAC
       alone would lose this detail."
    }

If the sample is dominated by DOC B → propose:

    {
      "value": "Chiller System",
      "recommended_action": "merge_into",
      "merge_target": "HVAC",
      "confidence": 0.90,
      "assessment_reasoning": "Only 2 passing mentions of chillers
       inside generic HVAC scope; no dedicated chiller content."
    }

If the sample mixes both signals roughly evenly → "defer_to_user"
with a confidence reflecting how ambiguous it is (e.g. 0.55) and
reasoning that names the conflict.

# LOCKED v1 TAXONOMY (do NOT propose these as extensions)

Trades — specialist:
  Electrical, Mechanical, Hydraulic, Fire, Telecommunications, DCS,
  Security, Carpentry, Formwork, Concrete, Steel.
Trades — cross-cutting:
  General Contractor / Principal.
Trades — vendor:
  Chiller Vendor, Generator Vendor, Busway Vendor, PDU Vendor, PTU Vendor,
  Fan Wall Vendor, HRU Vendor, Kiosk Transformer Vendor, CDU Vendor.

Services:
  Power distribution, Lighting, HVAC, Fire detection & suppression,
  Comms/ICT, Security/access control, Hydraulics, DCS / Controls.

Categories (deliberately narrow — strongly resist expansion):
  design, procurement, delivery, builders_works.

Document classes:
  customer_requirements, global_tr, global_ose_spec, project_amendment,
  project_clarification, drawing, demarcation_schedule, methodology,
  template, unknown.

# OUTPUT — return ONE JSON object with these fields

{
  "document_class_observations": [
    {
      "document_class": one of the document classes listed above,
      "count": integer,
      "confidence": "high" | "medium" | "low",
      "sample_filenames": [up to 5 representative filenames]
    },
    ...
  ],

  "proposed_trade_extensions": [
    {
      "value": short string — the new trade value as it should appear
        in trade_taxonomy.value,
      "reasoning": 1-2 sentences explaining why an existing locked trade
        does NOT cover this case,
      "sample_source_filenames": [filenames where this trade is used],
      "recommended_action": "confirm" | "merge_into" | "defer_to_user",
      "merge_target": null OR one of the SEEDED trade values listed in
        the LOCKED v1 TAXONOMY block above (REQUIRED when
        recommended_action == "merge_into"; MUST be a value present in
        the seeded taxonomy — never invent a target),
      "confidence": float in [0.0, 1.0],
      "assessment_reasoning": 1-2 sentences naming the corpus signals
        you used (see TAXONOMY VALUE ASSESSMENT section)
    },
    ...
  ],

  "proposed_service_extensions": same shape as proposed_trade_extensions
    for new service values. merge_target (if used) MUST be a SEEDED
    service value.

  "proposed_category_extensions": same shape for new categories. This
    SHOULD almost always be []. Per §5 the category vocabulary is
    deliberately narrow — only propose if the corpus genuinely demands
    a category that "design / procurement / delivery / builders_works"
    cannot stretch to. merge_target (if used) MUST be a SEEDED category
    value.

  "proposed_service_mappings": [
    {
      "disc_section_text": exact discipline-section text from a BOD-style
        source (e.g. "DCE Mechanical Engineering (ME)"),
      "proposed_service": one of the locked services above, OR null if
        the section is informational and should not auto-map (e.g.
        "Background Information", "Schedule"),
      "reasoning": 1 sentence
    },
    ...
  ],

  "authority_chain_observations": [
    {
      "source_id": the id passed to you for this source,
      "filename": the filename,
      "role": one of:
        "customer_requirements" | "global_tr" | "global_ose_spec" |
        "demarcation_schedule" | "project_amendment" |
        "project_clarification" | "drawing" | "methodology" |
        "template" | "unknown",
      "confidence": "high" | "medium" | "low",
      "reasoning": 1-2 sentences naming the signals you used (filename
        pattern, document title, structural cues, presence of a
        responsibility matrix, Comply/Not-Comply tabular form, etc.)
    },
    ...
  ],

  "corpus_quality_summary": {
    "total_sampled": integer,
    "scan_quality_breakdown": { "<quality_label>": count, ... }
      where labels include "clean", "markups_present",
      "partially_illegible", "unreadable",
    "ocr_needed_count": integer,
    "template_count": integer,
    "unreadable_count": integer
  },

  "recommendations": [
    3-5 short imperative strings the PM should act on BEFORE kicking off
    full extraction. Examples:
      "Demarcation schedule was detected — extraction will use it as
       primary reference.",
      "12 documents look like templates and will be excluded from
       extraction.",
      "Consider importing the global TR document referenced by these
       specs (AT-GLOBAL-TR-XXX)."
  ]
}

# RULES

  - Be conservative — when uncertain, default to an existing canonical
    value rather than inventing a new one.
  - For each proposed taxonomy extension, name the closest existing
    locked value in `reasoning` and explain why it doesn't fit.
  - Resist proposing new categories. The four-value category vocabulary
    is intentional.
  - For document_class_observations the `count` is the number of
    sampled sources you classify into that class (not extrapolated to
    the full corpus).
  - For authority_chain_observations emit ONE entry per sampled source
    using the source_id provided.
  - Recommendations are short, actionable, plain-English. No markdown.

# SEEDED TAXONOMY VALUES (provided by the runtime — for merge_target use)

These are the CURRENT taxonomy values seeded into THIS project's
database. When recommending a `merge_into` action, the `merge_target`
MUST be one of the values in the matching list below (e.g. a service
proposal merges into a SEEDED service value, not a trade). Do not
invent a target that is not in these lists.

Seeded trades:      {{ seeded_trades }}
Seeded services:    {{ seeded_services }}
Seeded categories:  {{ seeded_categories }}

# SAMPLE (provided by the runtime)

sample_size: {{ sample_size }}

{{ samples_block }}
```

---

## Notes

- This prompt is conservative on purpose — every proposed taxonomy entry creates a row the user must confirm before it becomes canonical. Over-proposing creates review-queue noise.
- The runtime persists each `proposed_trade_extension` / `proposed_service_extension` / `proposed_category_extension` as a row in the corresponding `*_taxonomy` table with `source='user_added', confirmed_at=NULL`. These show up in `meridian.review.taxonomy.list_pending_taxonomy()` exactly like any extraction-time taxonomy proposal.
- `proposed_service_mappings` is informational-only in v1: the runtime stashes the full proposal in `app_setting` for the review UI to display, but does not auto-write to `service_mapping` (mapping rows always require explicit confirmation per §5).
- `authority_chain_observations` and `recommendations` are stored verbatim in the `app_setting` audit blob and surfaced by `bootstrap-show`.
- The `LlmPurpose` field on the `llm_call` row is `quality_scan` because bootstrap is a document-classification call and that purpose is the closest match in the locked v1 enum. The `prompt_version_ref` distinguishes it from per-document quality scans.
