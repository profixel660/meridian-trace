# Demarcation Schedule Prompt v1 — primary-reference scope+allocation extraction

**Version:** v1.0-draft
**Path:** Demarcation Schedule (responsibility / scope matrix — PDF or XLSX)
**Authority:** `CONTEXT.md` §5 ("Demarcation Schedules — primary reference for trade allocation"), §3 (deliverable definition + three-outcome gate), §4 (output schema), §8/§9 (flags + HITL).
**When invoked:** the per-document quality scan classifies the source as a demarcation schedule and the orchestrator dispatches here instead of `text_spec` or `bod_import`.
**Companion:** `PROMPT_V1_conflict_pass.md` does the downstream comparison of demarcation vs other sources (`scope_missing_from_demarcation`, `scope_extra_to_demarcation`, `responsibility` kind).

---

## Prompt body

```
You are extracting an AUTHORITATIVE scope+allocation register from a
Demarcation Schedule. The demarcation schedule's structural purpose
is to allocate scope items to responsible parties (Supplier /
Contractor / Vendor / Client / Cxa Agent / Lease Provider) in a
matrix. Each scope item paired with a responsible party is an
authoritative scope+allocation deliverable.

This is the PRIMARY-REFERENCE extraction path. Demarcation rows
become the authoritative scope+allocation rows in the project's
master register. Other sources (specs / drawings / BOD) are
COMPARED against demarcation rows downstream — they do not
overwrite or replace demarcation rows.

# DELIVERABLE DEFINITION (the test — same as text-spec path)

A deliverable is something that satisfies AT LEAST ONE of:
  (a) forms part of the COMPLETED building (permanent works); OR
  (b) is a PHYSICAL WORK required to realise the building, including
      temporary works (scaffolding, hoardings, propping); OR
  (c) is DOCUMENTATION with continuing operational value to the
      building or its operator after handover.

EXPLICITLY NOT DELIVERABLES (must be rejected — same exclusions
as text-spec path): RFIs, submittals workflow, programmes,
ITPs, process reports, meeting actions, training services,
attendance, off-site manufacture/commissioning AS PROCESS.

# THREE-OUTCOME GATE (apply per matrix row)

For each scope-item × responsible-party row:

  INSIDE     — clearly satisfies the §3 definition. Extract.
  OUTSIDE    — clearly fails (matches an explicit exclusion or
              unambiguously fails the unifying rule). Emit to audit.
  BORDERLINE — genuinely ambiguous. Extract with flag
              `definition_borderline` and one-sentence reasoning.

# RESPONSIBLE-PARTY → TRADE / VENDOR MAPPING

Demarcation schedules typically use party labels that map to the
locked trade taxonomy as follows:

  - "Equipment Vendor" / "OSE Vendor" / "<Equipment-Class> Vendor"
        → trade = "<Equipment-Class> Vendor" (e.g. "Chiller Vendor")
          if the equipment class is identified, else propose a new
          vendor entry and flag `taxonomy_new_value_proposed`.
  - "Mechanical Contractor" / "Mechanical Sub" → trade = "Mechanical"
  - "Electrical Contractor" / "Electrical Sub" → trade = "Electrical"
  - "Hydraulic" / "Plumbing" → trade = "Hydraulic"
  - "Fire Services" → trade = "Fire"
  - "Comms" / "ICT" / "Telecommunications" → trade = "Telecommunications"
  - "BMS" / "Controls" / "BAMC" / "DCS" → trade = "DCS"
  - "Security" → trade = "Security"
  - "Joinery" / "Carpentry" → trade = "Carpentry"
  - "Civil" / "Concrete Sub" → trade = "Concrete"
  - "Formwork Sub" → trade = "Formwork"
  - "Steelwork" → trade = "Steel"
  - "Principal Contractor" / "GC" / "Head Contractor" / "Builder"
        → trade = "General Contractor / Principal"
  - "Client" / "Owner" / "Lease Provider" → these are acquisition
        responsibilities, not delivery trades. Flag
        `responsibility_conflict` and pass through with
        trade = "General Contractor / Principal" and a note in
        flag_context.
  - "Cxa" / "Commissioning Agent" — typically commissioning is a
        process, not a deliverable; if the scope item itself is a
        commissioning activity (not the resulting equipment) it
        likely fails the §3 gate → OUTSIDE.

# OUTPUT SCHEMA — INSIDE / BORDERLINE rows

  source_document        - filename of source
  source_ref             - "p.{N} row {M}" or "Sheet '<sheet>' row {N}"
                           or finest available locator
  trade                  - WHO does the work, mapped per above
  service                - WHAT building system; null if not tied
  category               - design / procurement / delivery /
                           builders_works / null
  applicable_standards   - array of standards EXPLICITLY tied to the
                           specific scope item in the demarcation cell
                           (NOT inherited from headers)
  document_state         - per runtime metadata; demarcations may be
                           revisioned rather than maturity-graded —
                           if so, return null
  document_class         - "demarcation_schedule"
  confidence             - "high" / "medium" / "low"
  flags                  - array from controlled vocabulary
  flag_context           - object keyed by flag, payload (REQUIRED)
  deliverables_summary   - terse, present-tense noun phrase of the
                           scope item, NO embedded responsibility text
                           (the trade column carries that)

When multiple parties share responsibility for the same scope item,
emit MULTIPLE ROWS — one per (scope_item × trade) — per the multi-tag
rule. Use the same `extraction_group_id` across rows derived from the
same matrix cell.

# OUTPUT SCHEMA — OUTSIDE rows (audit array)

  source_ref         - same format as above
  candidate_text     - the matrix cell content (scope item)
  rejection_reason   - one sentence: which exclusion fired

# FLAG_CONTEXT POPULATION RULES (every flag must carry context)

  definition_borderline   → one short sentence stating WHY edge.
  responsibility_conflict → one short sentence naming the parties
      whose allocations conflict (within the demarcation itself —
      e.g. multiple parties claim the same scope without clear split).
  trade_inferred          → one short sentence stating basis.
  service_inferred        → one short sentence stating basis.
  unclear_language        → one short sentence quoting the wording.
  taxonomy_new_value_proposed → one short sentence with the proposed
      value and why it does not match an existing canonical entry.
  conflicts_with_source_<X> → DO NOT populate here; downstream pass.

# HARD RULES

  - Demarcation rows are AUTHORITATIVE SCOPE — each row is a
    scope+allocation deliverable, not a candidate to be re-tested
    against external taste. Apply the §3 gate (it is a definition
    test, not a scope test) but do not second-guess inclusions.
  - Multi-trade scope items become MULTIPLE ROWS, one per trade.
  - NEVER populate applicable_standards from headers, foreword,
    or general references. ONLY standards cited against the SPECIFIC
    matrix cell.
  - JSON only. Begin with `{`, end with `}`. No preamble. No
    markdown fencing.
  - EVERY flag must have a corresponding flag_context entry.

# OUTPUT FORMAT

Return a single JSON object:

  {
    "deliverables": [ ... INSIDE / BORDERLINE rows ... ],
    "audit":        [ ... OUTSIDE rows ... ],
    "questions":    [ ... HITL questions ... ]
  }

# SOURCE DOCUMENT METADATA (provided by the runtime)

filename:        {{ filename }}
document_class:  demarcation_schedule
document_state:  {{ document_state }}
revision:        {{ revision }}

# SOURCE TEXT (the demarcation matrix as extracted text)

{{ source_text }}
```

---

## Notes

- The demarcation prompt is structurally similar to `text_spec` but adds an explicit responsible-party → trade mapping and treats demarcation rows as authoritative scope rather than candidate scope.
- Cross-source comparison (missing/extra scope, allocation conflicts) is handled by `PROMPT_V1_conflict_pass.md`, not here.
- For PDF-only demarcation schedules (image-rendered tables), reliable text extraction depends on either pdf text being present or OCR being run first. Image-only PDFs without OCR will produce thin text and the prompt will likely return few rows — handled by quality-scan flagging `scan_quality = unreadable`.
