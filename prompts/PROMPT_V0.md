# Extraction Prompt v0 — prototype work from discovery chat

Two prototype prompts drafted and run-through-by-hand against samples from the AirTrunk SYD2 corpus. Both produced reviewable output; the SME (collaborator) confirmed the approach and surfaced corrections that should land in v1.

This file preserves the v0 work + noted corrections so the build chat can iterate to v1 without redoing the prototype.

---

## v0 prompt 1 — Standard text-spec extraction path

Used against: `Samples/Contract-Documents/Approved-OSE-Specifications/Air-Cooled-Chiller/Air-Cooled-Chiller-Specifications/AT-GLOBAL-OR-000103_SPC-ACC-01[11].pdf` (sections 2.2 + 2.4.x).

```
You are extracting design deliverables from a construction project document into a structured register. Strict adherence to the rules below is more important than completeness — a small clean register beats a large noisy one.

# DELIVERABLE DEFINITION (the test)

A deliverable is something that satisfies AT LEAST ONE of:
  (a) forms part of the COMPLETED building (permanent works);
  (b) is a PHYSICAL WORK required to realise the building, including
      temporary works (scaffolding, hoardings, propping) that are
      removed at handover;
  (c) is DOCUMENTATION with continuing operational value to the
      building or its operator after handover (O&M manuals, BIM
      models LOD300+, EPDs, as-installed/as-built docs, function
      descriptions, approved technical submittals, in-force warranty
      documentation).

EXPLICITLY NOT DELIVERABLES (must be rejected):
  - RFIs
  - Submittals workflow artefacts (the request/review correspondence —
    the final approved submittal IS a deliverable per (c))
  - Programmes / construction schedules
  - ITPs (Inspection & Test Plans)
  - Process reports (RFI register, weekly status, meeting minutes,
    progress reports)
  - Meeting actions / minutes
  - General coordination tasks
  - Training services
  - Attendance at meetings
  - Off-site manufacture / commissioning as a process
    (the resulting equipment IS a deliverable; the act of
    commissioning is not)

# THREE-OUTCOME GATE (apply to every candidate)

For each candidate item, return EXACTLY ONE outcome:
  1. INSIDE  — clearly satisfies the definition. Extract as a
              deliverable row.
  2. OUTSIDE — clearly fails the definition or matches an explicit
              exclusion. Do NOT silently drop. Log to the audit
              with reasoning ("rejected because: [reason]").
  3. BORDERLINE — genuinely ambiguous, project-specific phrasing,
              or definition-edge case. Output as a deliverable row
              with flag `definition_borderline` and a one-sentence
              reasoning so the reviewer can decide.

# OUTPUT SCHEMA (per row)

Output each kept row (INSIDE or BORDERLINE) as JSON with these fields:
  source_document        - filename of source
  source_ref             - "p.{N} §{section}" or finest available locator
  trade                  - WHO does the work; null if no specific trade
  service                - WHAT building system; null if not tied to one
  category               - design / procurement / delivery / builders_works; null OK
  applicable_standards   - array of standards EXPLICITLY tied to this deliverable
                            in the source. Do NOT inherit document-wide standards.
                            null/[] OK.
  document_state         - source doc maturity (e.g. "100%", "IFC", "as-built")
  document_class         - source doc class (e.g. "global_ose_spec", "drawing")
  confidence             - "high" / "medium" / "low"
  flags                  - array from the controlled vocabulary
  deliverables_summary   - terse present-tense noun phrase, no source attribution,
                            no hedging language, no LLM commentary

Output OUTSIDE rows as JSON to a separate audit array:
  source_ref, candidate_text, rejection_reason

# GRANULARITY

When the source describes a collective item ("100 type-A fittings"),
emit ONE row per collective item (not one per instance), with the
quantity captured in deliverables_summary. Multiple rows ONLY when the
collective item spans multiple trades/services.

# HARD RULES — do not violate

  - NEVER extract an item if you cannot tie it to a specific source_ref.
  - NEVER infer trade or service if the source does not support it.
    Use null and flag `trade_inferred` / `service_inferred` only if
    you DID infer.
  - NEVER populate `applicable_standards` with standards from the
    document foreword, scope-of-work preamble, or general references
    section. Only standards the source EXPLICITLY cites against the
    specific deliverable in question.
  - NEVER include source attribution, hedging language ("appears to
    be", "likely"), or LLM commentary in `deliverables_summary`.
  - PREFER reuse of existing taxonomy values over creating new ones.
  - When a quantity, trade, service, or value is "TBD" / "TBC" /
    "to be advised", the deliverable still exists — extract it with
    flag `tbd_placeholder` and `quantity_uncertain` as appropriate.

# SOURCE DOCUMENT

filename: AT-GLOBAL-OR-000103_SPC-ACC-01[11].pdf
document_class: global_ose_spec
document_state: 100% (Revision 11, dated 09/04/25 — production OSE spec)

[source text follows]
```

---

## v0 prompt 2 — BOD structured-import path

Used against: `Samples/BOD/Shell-C-&-D/SYD2CD_(SYD29EX2)_-_AirTrunk.xlsx` (Exhibit A2 - Technical Req sheet, sample rows).

```
You are processing a negotiated requirements register (BOD —
Basis of Design) for ingestion into a deliverables register.
This is the STRUCTURED-IMPORT path, NOT free-form extraction.
Each input row already represents a candidate; your job is to
map, gate, and disposition — not to identify candidates from
prose.

# INPUT FORMAT

Tabular input with columns:
  Req Id | Disc Section | Disc Subsection | Requirement |
  Landlord Response | Landlord Comment

Each row is a customer-issued requirement that AirTrunk
(Landlord) has formally responded to.

# PROCESSING ORDER (per row)

For every row, apply in this order:

  1. DISPOSITION RULE — based on Landlord Response.
  2. §3 GATE — three outcomes (INSIDE / OUTSIDE / BORDERLINE)
     applied to rows that pass the disposition rule.
  3. COLUMN MAPPING — map to schema for kept rows.

# 1. DISPOSITION RULE

Landlord Response governs whether the row enters scope:

  - "Comply" → proceed to §3 gate.
  - "Not Comply" with reason "N/A" / "no <feature> requirement" / 
    "no <feature> utilised" → OUTSIDE; log to audit with reason
    "out of scope for this project per Landlord Response".
  - "Not Comply" with substantive qualifier (e.g. "Comply with
    design requirements. However...", "Technically comply,
    clarification:...") → proceed to §3 gate but flag
    `negotiated_response`. Preserve the comment in flag context.
  - "Comply with conditions" / similar → proceed to §3 gate,
    flag `negotiated_response`, preserve comment.
  - Blank / missing response → flag `definition_borderline` 
    AND `unclear_language`, route to HITL.

# 2. §3 GATE

Same three-outcome test as the standard prompt:
  INSIDE  / OUTSIDE (logged) / BORDERLINE (flagged + HITL).

The disposition rule has already removed N/A items; the gate
catches rows that ARE in scope but are not actually deliverables
(e.g. metadata, process artefacts, scheduling clauses).

# 3. COLUMN MAPPING

Input                  → Schema
Req Id                 → embedded in source_ref
Disc Section           → service (per service-mapping table below)
Disc Subsection        → context for deliverables_summary, not a tag
Requirement            → deliverables_summary (terse rewrite, NOT verbatim)
Landlord Response      → audit-only; preserved with row
Landlord Comment       → preserved as flag context if substantive

# SERVICE MAPPING (initial — extensible per project)

"DCE Mechanical Engineering (ME)"               → "HVAC"
"DCE Electrical Engineering (EE)"               → "Power distribution"
"DCE Building Automation, Monitoring & Co..."   → "DCS / Controls"
"DCE Fire Protection"                           → "Fire detection & suppression"
"DCE Plumbing"                                  → "Hydraulics"
"Background Information"                         → null (informational)
"Construction Management"                        → null (process)
"Schedule"                                       → null (process)

# DEFAULT TAGS FOR THIS DOC CLASS

document_class:  "customer_requirements"
document_state:  per BOD issue date / revision (here: per file metadata)

# HARD RULES

  - source_ref format: "Sheet '<sheet>' Row <N> (Req Id <id>)"
  - PRESERVE the verbatim Landlord Comment in flag context for any
    `negotiated_response` row — the qualifier is material to the
    deliverable's effective scope.
  - External-spec references in the Requirement text 
    (e.g. "Spec203", "Spec108", "ASHRAE n=20") populate
    applicable_standards ONLY if explicitly tied to the requirement.
  - deliverables_summary must be terse, present-tense, no hedging,
    no source attribution.
```

---

## Corrections that must land in v1 (locked decisions from the SME pass)

These are decisions captured into CONTEXT.md but not yet reflected in the v0 prompts above. The build chat must integrate them when authoring v1.

### Trade taxonomy (CONTEXT.md §5)

The trade defaults locked in CONTEXT.md are:
- Specialist: Electrical, Mechanical, Hydraulic, Fire, Telecommunications, DCS, Security, Carpentry, **Formwork**, Concrete, Steel.
- Cross-cutting: General Contractor / Principal.
- Equipment vendors: per-equipment-class entries (`Chiller Vendor`, `Generator Vendor`, etc.) — **not** a single generic vendor tag.

The v0 prompts referenced "Mechanical (HVAC) contractor" and "Plumbing"; v1 must use the locked names ("Mechanical", "Hydraulic", etc.).

### Vendor-supplied equipment attribution (CONTEXT.md §5 — "Attribution rule")

**v0 mistake:** documentation deliverables for the chiller (shop drawings, O&M, BIM, EPD, warranty) were tagged `trade = General Contractor / Principal`. **Correct attribution per locked rule:**

- Equipment + native documentation → `trade = <Equipment-class> Vendor` (e.g. `Chiller Vendor`).
- Connecting provisions (CHW pipes, power feeds, DCS interfaces) → `trade = the relevant specialist trade`.
- Each specialist trade owns its own shop drawings / O&M / BIM / EPD / warranty for the systems IT delivers — not the vendor's.
- Plinths and other builders-works supporting the equipment → `trade = Concrete` and/or `trade = Formwork` (multi-row), `category = builders_works`. Coordinated by GC; specialist trade provides weights/dims after validating with vendor — but does not deliver the plinth itself.

### OSE spec granularity (CONTEXT.md §5)

For global OSE specifications, components and sub-assemblies of the vendor's equipment **roll up into a single `[equipment-type] assembly` deliverable** rather than being extracted per-component. Sub-options (Option 1, 2, 3...) remain as separate borderline-flagged rows pending project-specific confirmation.

**v0 mistake:** evaporator, condenser, fans, compressors, refrigerant, water pump were extracted as separate component rows. v1 should produce one `Air-cooled chiller assembly` row tagged `Chiller Vendor`.

### BOD trade attribution (CONTEXT.md §5 — "BOD / negotiated requirements registers")

**v0 mistake:** every BOD row was tagged `trade = General Contractor / Principal` by default. **Correct rule:**

- BOD rows tag the **specialist trade directly** (or vendor where the requirement clearly implicates an OSE item).
- The GC has back-to-back contracts with specialist trades; tagging trade directly preserves operational accuracy.
- GC tag is reserved for cross-cutting items genuinely owned at GC level (programmes, head-contractor coordination — most of which fail the §3 gate anyway).

### BOD scope-exclusion mechanism (CONTEXT.md §5)

The BOD's own Landlord Response defines what is OUT of scope for this project ("N/A — no tape library requirement", "no meeting rooms / office / storage in scope", etc.). These rows go OUTSIDE under the disposition rule, not BORDERLINE.

**v0 mistake:** "Big Room meeting room" was flagged BORDERLINE. v1 should classify it OUTSIDE under the BOD scope-exclusion mechanism (Client confirmed no meeting/office/storage in scope).

### Negotiated-response rendering (CONTEXT.md §4)

**Locked: Option C (hybrid).** Visible marker (e.g. ` ⚠`) appended to `deliverables_summary`; full qualifier text lives in the `negotiated_response` flag context. Not embedded in summary text.

**v0 mistake:** Qualifier text was embedded in summary as `"... (Landlord deviation: '...')"`. v1 should use the marker + flag-context pattern.

### Cross-axis values (CONTEXT.md §5)

`DCS` is now both a trade and a service value. The schema supports a row with `trade=DCS` and `service=DCS / Controls` simultaneously. Other dual-axis values may emerge.

### Document state on the chiller spec

The OSE chiller spec is a global product spec, not project-specific. Its `document_state` of `100%` was a defensible heuristic for v0 (production-grade revision 11) but the build chat should consider: does `document_state` apply meaningfully to non-project documents at all? Possibly `state=null` for global specs is more honest. Worth a quick decision at v1.

---

## What the v0 demonstrated that should carry to v1

- The three-outcome gate (INSIDE / OUTSIDE / BORDERLINE) works cleanly. The OSE Scope-of-Work section had 12 candidates → 5 INSIDE, 7 OUTSIDE (audited), 0 BORDERLINE. The §3 exclusion list filtered process/service items correctly.
- Documentation deliverables were captured (O&M, BIM, EPD, warranty) per the unifying rule.
- The `definition_borderline` flag fired correctly on optional sub-assemblies (Adiabatic Cooler Option 2, Waterside Economiser Option 3).
- The `applicable_standards` discipline held — populated only where source explicitly cited the standard against the deliverable.
- The BOD disposition-rule + §3-gate two-stage filter is the right architecture: disposition handles "out of scope per Landlord", gate handles "in scope but not a deliverable".
- The `negotiated_response` flag is doing real semantic work — every qualified compliance row needs its qualifier preserved.
- Service mapping for new discipline categories (e.g. DCE Building Automation) correctly surfaced as a HITL approval prompt.
