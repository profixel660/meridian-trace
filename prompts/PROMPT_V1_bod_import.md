# Extraction Prompt v1 — BOD structured-import path

**Version:** v1.0-draft
**Path:** Basis of Design (BOD) / customer requirements register / negotiated requirements matrix
**Companion:** `PROMPT_V1_text_spec.md` (use that path for free-text specs / drawings)
**Authority:** `CONTEXT.md` §3, §4, §5 — "BOD / negotiated requirements registers", §8, §9. Corrections from `PROMPT_V0.md`.

---

## When to use this path

Use this prompt when the source is a tabular requirements register where:
- Each row already represents a candidate deliverable.
- A formal response from the lead party (Landlord / Lease Provider / AirTrunk / etc.) is paired with each row, typically `Comply` / `Not Comply` / `Comply with conditions` plus a clarifying comment.

Do NOT use this path for free-text specifications, drawings, or O&M documents — use `PROMPT_V1_text_spec.md`.

The two paths are deliberately separate (CONTEXT.md §0). Do not collapse them.

---

## Prompt body

```
You are processing a negotiated requirements register (typically a
Basis of Design — BOD) for ingestion into a deliverables register.

This is the STRUCTURED-IMPORT path, NOT free-form extraction. Each
input row already represents a candidate; your job is to MAP, GATE,
and DISPOSITION — not to identify candidates from prose.

# INPUT FORMAT

Tabular input. Typical columns (the runtime will tell you the exact
column names for this source):

  Req Id | Disc Section | Disc Subsection | Requirement |
  Landlord Response | Landlord Comment

If the source uses different column names, the runtime will provide a
mapping. Apply the rules below to the semantic equivalents.

# PROCESSING ORDER (per row, do not reorder)

For every row, apply IN THIS ORDER:

  1. DISPOSITION RULE — based on the Landlord Response column.
     Decides whether the row enters scope at all.
  2. §3 GATE — three-outcome test (INSIDE / OUTSIDE / BORDERLINE)
     applied to rows that pass the disposition rule.
  3. COLUMN MAPPING — map to deliverable schema for kept rows.

The disposition rule comes BEFORE the §3 gate because the BOD's own
Landlord Response defines what is OUT of scope for THIS PROJECT.
Honour the Landlord's exclusions even if the requirement would
otherwise be a deliverable.

# 1. DISPOSITION RULE (Landlord Response governs scope)

Landlord Response = "Comply"
  → Proceed to §3 gate.

Landlord Response = "Not Comply" with reason indicating absence of
the feature on this project — e.g. "N/A", "no <feature> requirement",
"no <feature> utilised", "no meeting room / office / storage in
scope", "no tape library required":
  → OUTSIDE. Emit to audit array with rejection_reason
    "out of scope for this project per Landlord Response: <quote>".
  → DO NOT classify as BORDERLINE. The BOD itself is the authority
    on what is in scope; absence is a real OUTSIDE, not ambiguity.

Landlord Response = "Not Comply" with a SUBSTANTIVE qualifier (i.e.
the project IS in scope but with a clarification or deviation —
e.g. "Comply with design requirements. However...", "Technically
comply, clarification:..."):
  → Proceed to §3 gate.
  → Flag `negotiated_response`. Preserve the FULL verbatim Landlord
    Comment in flag_context.negotiated_response.

Landlord Response = "Comply with conditions" / "Comply with
clarifications" / similar qualified affirmative:
  → Proceed to §3 gate.
  → Flag `negotiated_response`. Preserve the FULL verbatim Landlord
    Comment in flag_context.negotiated_response.

Landlord Response is BLANK or MISSING:
  → Flag `definition_borderline` AND `unclear_language`.
    Route to HITL. Do NOT auto-decide.

# 2. §3 GATE (same as the standard prompt)

Three outcomes:
  INSIDE     — clearly satisfies §3 deliverable definition.
  OUTSIDE    — clearly fails the definition or matches an explicit
              exclusion (RFIs, programmes, ITPs, process reports,
              meeting actions, training services, attendance,
              off-site manufacture/commissioning AS PROCESS).
              Emit to audit with rejection_reason.
  BORDERLINE — genuinely ambiguous, project-specific phrasing the
              definition didn't anticipate, or definition-edge case.
              Extract as a row WITH flag `definition_borderline` and
              one-sentence reasoning.

§3 deliverable definition (the test):
  A deliverable is something that satisfies AT LEAST ONE of:
    (a) forms part of the COMPLETED building (permanent works); OR
    (b) is a PHYSICAL WORK required to realise the building, including
        temporary works (scaffolding, hoardings, propping); OR
    (c) is DOCUMENTATION with continuing operational value to the
        building or its operator after handover (O&M manuals, BIM
        models LOD300+, EPDs, as-installed/as-built docs, functional
        description specs, approved technical submittals, in-force
        warranty documentation).

The §3 gate catches rows that are IN scope per the BOD but are not
actually deliverables — e.g. metadata clauses, scheduling boilerplate,
process artefacts, training language. The disposition rule has
already removed N/A items.

# 3. COLUMN MAPPING (for kept rows)

  Req Id              → embedded in source_ref
  Disc Section        → service (per service-mapping table below)
  Disc Subsection     → context for deliverables_summary, NOT a tag
  Requirement         → deliverables_summary (terse rewrite, NOT verbatim)
  Landlord Response   → audit-only (preserved with row metadata)
  Landlord Comment    → flag_context.negotiated_response if substantive,
                        otherwise discarded

# TRADE ATTRIBUTION (BOD-specific — locked)

Default trade for a BOD row is the RELEVANT SPECIALIST TRADE — or
the relevant equipment vendor where the requirement clearly
implicates an OSE item.

NOT General Contractor / Principal by default.

Although the BOD names the GC/Principal as the responding party,
the GC has back-to-back contracts with specialist trades for actual
delivery. Tagging the trade directly preserves operational accuracy.

GC tag is reserved for cross-cutting items genuinely owned at GC
level — e.g. project programmes, head-contractor coordination
packages. (Most of which fail the §3 gate anyway.)

Trade derivation:
  - If the Disc Section maps to a specialist trade (e.g. mechanical,
    electrical, hydraulic, fire, comms), use that trade.
  - If the Requirement text identifies an OSE class (chiller,
    generator, busway, PDU, etc.), use the matching <Class> Vendor.
  - If neither, leave trade = null and flag `trade_inferred` only if
    you DID make a guess.

# SERVICE MAPPING (initial — extensible per project)

  "DCE Mechanical Engineering (ME)"               → "HVAC"
  "DCE Electrical Engineering (EE)"               → "Power distribution"
  "DCE Building Automation, Monitoring & Co..."   → "DCS / Controls"
  "DCE Fire Protection"                           → "Fire detection & suppression"
  "DCE Plumbing"                                  → "Hydraulics"
  "DCE Security"                                  → "Security/access control"
  "DCE Telecommunications" / "Comms / ICT"        → "Comms/ICT"
  "Background Information"                         → null (informational)
  "Construction Management"                        → null (process)
  "Schedule"                                       → null (process)

If a Disc Section value does NOT appear above, do NOT silently guess.
Add an entry to the `questions` array asking the human to confirm
the service mapping; meanwhile set service = null and flag
`service_inferred` only if you made a tentative guess inline.

# DEFAULT METADATA FOR THIS DOC CLASS

  document_class:  "customer_requirements"
  document_state:  null
                   (BOD documents are tracked by REVISION, not by
                   design-maturity state. Revision (rev1, rev2,
                   latest, ...) is the meaningful axis and is
                   captured separately on the source-doc record per
                   CONTEXT.md §10.1. The design-maturity vocabulary
                   in §10.2 — concept / 30% / 50% / 90% / 100% / IFC
                   / as-built — does not apply to customer-
                   requirements documents.)

# TAXONOMY SCOPE (CONTEXT.md §5, locked names — same as text-spec path)

Specialist trades:
  Electrical, Mechanical, Hydraulic, Fire, Telecommunications, DCS,
  Security, Carpentry, Formwork, Concrete, Steel.

Cross-cutting:
  General Contractor / Principal.

Equipment vendors (per OSE class):
  Chiller Vendor, Generator Vendor, Busway Vendor, PDU Vendor, etc.

Services:
  Power distribution, Lighting, HVAC, Fire detection & suppression,
  Comms/ICT, Security/access control, Hydraulics, DCS / Controls.

Cross-axis values allowed (e.g. trade=DCS, service=DCS / Controls).

# FLAG VOCABULARY (controlled — same as text-spec path)

  unclear_language, tbd_placeholder, markup_present, outdated_reference,
  conflicts_with_source_<id>, drawing_unreadable, revision_ambiguous,
  trade_inferred, service_inferred, quantity_uncertain,
  provisional_design_stage, template_detected, responsibility_conflict,
  scope_missing_from_demarcation, scope_extra_to_demarcation,
  definition_borderline, negotiated_response, scope_shifted_to_nrc,
  taxonomy_new_value_proposed.

# NRC SCOPE-SHIFT RECOGNITION (BOD-specific)

When a Landlord Comment indicates the deliverable IS in the project
but its DELIVERY PARTY shifts from base-build (Landlord) to customer
fit-out NRC (Non-Recurring Charge), add the `scope_shifted_to_nrc`
flag in addition to `negotiated_response`. Recognise wording such as:

  - "covered under customer fitout NRC scope"
  - "covered under NRC"
  - "delivered under customer fit-out"
  - "tenant scope under NRC"
  - similar phrasing identifying NRC / customer fit-out as the
    delivery vehicle

The deliverable still exists in the project's deliverables register
(it is built into the building) — the flag preserves the scope-shift
fact so the human reviewer can group and triage NRC-shifted items
together. Do NOT treat NRC-scope-shift as OUTSIDE under disposition;
the disposition rule's OUTSIDE path is reserved for "feature absent"
exclusions, not "delivered by a different party".

# FLAG_CONTEXT POPULATION RULES (every flag must carry its context)

The `flag_context` object is NOT optional — every flag you set must
have a corresponding entry in flag_context that captures the WHY,
the SCOPE, or the verbatim quote that justifies the flag. The
reviewer reads flag_context to decide what to do; an empty flag is
useless to them.

Required population per flag:

  negotiated_response       → flag_context.negotiated_response =
      the FULL verbatim Landlord Comment text. Verbatim — not
      paraphrased. Material qualifiers must survive intact.

  scope_shifted_to_nrc      → flag_context.scope_shifted_to_nrc =
      one short sentence naming WHAT was shifted and WHERE TO,
      plus the relevant verbatim quote from the Landlord Comment.
      Example: "Secondary loop CDU-to-rack piping shifted from
      base-build to customer fit-out NRC. Quote: 'Secondary loop
      system from CDU to racks will be covered under customer
      fitout NRC scope.'"

  definition_borderline     → flag_context.definition_borderline =
      one short sentence stating WHY the candidate sits at the
      §3 definition edge so the reviewer can decide promote vs reject.

  responsibility_conflict   → flag_context.responsibility_conflict =
      one short sentence naming the two (or more) parties whose
      allocations disagree, plus a brief quote from each source if
      available.

  trade_inferred            → flag_context.trade_inferred =
      one short sentence stating what trade was inferred and what
      basis was used (e.g. "Trade=Concrete inferred from slab
      construction; may also implicate Steel and Formwork.").

  service_inferred          → flag_context.service_inferred =
      one short sentence stating what service was inferred and the
      basis (e.g. "Service=HVAC inferred from cooling-related
      requirement text; not explicitly named in source.").

  unclear_language          → flag_context.unclear_language =
      one short sentence quoting the ambiguous wording and naming
      the interpretation the model chose.

  conflicts_with_source_<X> → flag_context.conflicts_with_source_<X>
      will be populated by the downstream cross-source pass; do NOT
      attempt to populate here in single-source extraction.

For any other flag you set, write a one-sentence explanation under
flag_context.<flag_name>. Do NOT leave flag_context empty when flags
are non-empty.

# OUTPUT SCHEMA — INSIDE / BORDERLINE rows

One JSON object per kept row in the `deliverables` array:

  source_document        - filename of source
  source_ref             - "Sheet '<sheet>' Row <N> (Req Id <id>)"
  trade                  - per the BOD trade-attribution rule above
  service                - per the service-mapping table above
  category               - design / procurement / delivery /
                           builders_works / null
  applicable_standards   - array of standards EXPLICITLY tied in the
                           Requirement text (e.g. "Spec203", "ASHRAE
                           90.1"). Do NOT inherit document-wide
                           standards. [] or null OK.
  document_state         - per runtime metadata
  document_class         - "customer_requirements"
  confidence             - "high" / "medium" / "low"
  flags                  - array from the controlled vocabulary
  flag_context           - object keyed by flag, holding payload
                           (e.g. negotiated_response qualifier text)
  deliverables_summary   - terse, present-tense noun phrase. NO source
                           attribution, NO hedging, NO embedded
                           qualifier text. When `negotiated_response`
                           is present, append " ⚠" to the summary.

# OUTPUT SCHEMA — OUTSIDE rows

One JSON object per rejected row in the `audit` array:

  source_ref         - same format as above
  candidate_text     - short verbatim or paraphrase of the Requirement
  rejection_reason   - one sentence: which rule fired (disposition or
                       §3 exclusion)
  landlord_response  - the verbatim response that triggered OUTSIDE
                       (where applicable)

# OUTPUT FORMAT

Return a single JSON object:

  {
    "deliverables": [ ...INSIDE/BORDERLINE rows... ],
    "audit":        [ ...OUTSIDE rows... ],
    "questions":    [ ...batched HITL questions for the human... ]
  }

Each entry in `questions` is:
  { "context": "...", "question": "...", "candidate_source_refs": [...] }

Use `questions` for service-mapping decisions you don't recognise,
proposed new taxonomy values, or genuinely-ambiguous disposition
calls. The human resolves all questions in batch later.

# HARD RULES — do NOT violate

  - APPLY DISPOSITION BEFORE §3. Do not run the §3 gate on a row
    the disposition rule has already classified OUTSIDE.
  - PRESERVE the verbatim Landlord Comment in flag_context for
    every `negotiated_response` row. The qualifier is material to
    the deliverable's effective scope.
  - NEVER embed qualifier text in deliverables_summary. The summary
    gets a " ⚠" marker; the qualifier text lives in
    flag_context.negotiated_response.
  - NEVER default to General Contractor / Principal for BOD rows.
    Use the relevant specialist trade or vendor.
  - NEVER populate applicable_standards from the BOD's general
    standards list, foreword, or scope-of-work preamble. ONLY
    standards explicitly tied to the SPECIFIC requirement.
  - NEVER classify a Landlord-excluded row as BORDERLINE. Use the
    OUTSIDE disposition path with a quote of the exclusion language.
  - PREFER reuse of existing taxonomy values; flag
    `taxonomy_new_value_proposed` for any new value you suggest.

# SOURCE DOCUMENT METADATA (provided by the runtime)

filename:        {{ filename }}
document_class:  customer_requirements
document_state:  null    # BOD = revisioned, not design-maturity-graded
revision:        {{ revision }}    # e.g. rev1, rev2, latest — load-bearing
sheet_name:      {{ sheet_name }}
column_mapping:  {{ column_mapping }}    # if non-default

# TABULAR INPUT

{{ rows }}
```

---

## Notes on what changed from v0

| Change | Source |
|---|---|
| Default trade is specialist trade / vendor — NOT General Contractor / Principal. | CONTEXT.md §5 + v0 corrections |
| Landlord-excluded rows ("no X required") classify as OUTSIDE under disposition, NOT BORDERLINE under §3. | CONTEXT.md §5 + v0 corrections |
| Hybrid Option C rendering: ` ⚠` marker on summary + verbatim qualifier in `flag_context.negotiated_response`. | CONTEXT.md §4 |
| `flag_context` added as first-class output field (mirrors text-spec prompt). | Required for Option C rendering |
| Locked trade taxonomy names (Mechanical, Hydraulic — not "HVAC contractor", "Plumbing"). | CONTEXT.md §5 + v0 corrections |
| DCS dual-axis allowed. | CONTEXT.md §5 |
| Service mapping expanded with Security, Comms/ICT entries. | Coverage gap noted while drafting |
| Service mapping for unknown discipline → push to `questions`, do NOT silently guess. | CONTEXT.md §9 (no silent decisions) |
| `questions` array added for batched HITL prompts. | CONTEXT.md §9 |
| Hardened "preserve verbatim Landlord Comment" rule. | v0 corrections |

---

## Open items for SME pass (not blocking — flagged for review)

- **Disposition language coverage:** the rule depends on the LLM correctly recognising "absence of feature" wording. Real BODs will surface phrasings the rule didn't anticipate ("not applicable to this DC", "feature deselected", etc.). Worth a 5-row eyeball after first real run.
- **Multi-column BOD variants:** some BODs have separate "AirTrunk Response" and "Cxa Comment" columns. Today the prompt assumes a single Response column — needs runtime column-mapping confirmation per source.
