# v1 hand-test — text-spec path against AT-GLOBAL-OR-000103_SPC-ACC-01[11].pdf

**Source:** `Samples/Contract-Documents/Approved-OSE-Specifications/Air-Cooled-Chiller/Air-Cooled-Chiller-Specifications/AT-GLOBAL-OR-000103_SPC-ACC-01[11].pdf`
**Sections covered:** §2.2 Scope of Work, §2.4.x General Requirements, §2.5 Documentation, §2.6 Spare Parts, §2.7 Training, §2.8 Safety in Design.
**Prompt version applied:** `PROMPT_V1_text_spec.md` v1.0-draft.
**Source-doc metadata:** `document_class = global_ose_spec`, `document_state = null` (per resolved fork — global, non-project spec), `revision = 11 (09/04/25)`.
**Method:** by hand, by the human author, walking the source against the v1 prompt rules. Not LLM output. Goal is to verify the prompt rules produce sensible behaviour and to surface any gaps before LLM trial runs.

---

## OUTPUT

### `deliverables` (INSIDE + BORDERLINE rows)

```jsonc
[
  {
    "source_document": "AT-GLOBAL-OR-000103_SPC-ACC-01[11].pdf",
    "source_ref": "p.6 §2.1, §2.2(a–l); pp.8–17 §2.4.1–§2.4.6, §2.4.10–§2.4.16",
    "trade": "Chiller Vendor",
    "service": "HVAC",
    "category": "procurement",
    "applicable_standards": ["ASTM B117"],
    "document_state": null,
    "document_class": "global_ose_spec",
    "confidence": "high",
    "flags": [],
    "flag_context": {},
    "deliverables_summary": "Air-cooled chiller assembly (unit casing, evaporator, condenser, fans, semi-hermetic two-stage VSD centrifugal compressors with magnetic bearings, refrigerant system, water pump and pipework, power input, unit control hardware, functional control, noise/vibration control, metering, control integration / Modbus RTU)"
  },

  {
    "source_document": "AT-GLOBAL-OR-000103_SPC-ACC-01[11].pdf",
    "source_ref": "p.10 §2.4.3.1",
    "trade": "Chiller Vendor",
    "service": "HVAC",
    "category": "procurement",
    "applicable_standards": [],
    "document_state": null,
    "document_class": "global_ose_spec",
    "confidence": "medium",
    "flags": ["definition_borderline"],
    "flag_context": {
      "definition_borderline": "Sub-option (Option 1 — extended condenser module). Inclusion is project-specific based on site ambient / performance requirements; reviewer to confirm whether SYD2 takes this option."
    },
    "deliverables_summary": "Extended condenser module (Option 1) for higher performance / hotter ambient"
  },

  {
    "source_document": "AT-GLOBAL-OR-000103_SPC-ACC-01[11].pdf",
    "source_ref": "p.10 §2.4.7",
    "trade": "Chiller Vendor",
    "service": "HVAC",
    "category": "procurement",
    "applicable_standards": [],
    "document_state": null,
    "document_class": "global_ose_spec",
    "confidence": "medium",
    "flags": ["definition_borderline"],
    "flag_context": {
      "definition_borderline": "Sub-option (Option 2 — adiabatic coolers). Inclusion is project-specific; reviewer to confirm whether SYD2 takes this option."
    },
    "deliverables_summary": "Adiabatic pre-cooling package (Option 2) — adiabatic pads, low-pressure non-misting nozzles, sump, sump pumps, water-quality controls, pulse water meter to CMS"
  },

  {
    "source_document": "AT-GLOBAL-OR-000103_SPC-ACC-01[11].pdf",
    "source_ref": "pp.10–11 §2.4.8",
    "trade": "Chiller Vendor",
    "service": "HVAC",
    "category": "procurement",
    "applicable_standards": [],
    "document_state": null,
    "document_class": "global_ose_spec",
    "confidence": "medium",
    "flags": ["definition_borderline"],
    "flag_context": {
      "definition_borderline": "Sub-option (Option 3 — waterside economiser). Inclusion is project-specific; reviewer to confirm whether SYD2 takes this option."
    },
    "deliverables_summary": "Waterside economiser package (Option 3) — proprietary economiser coil package, piping connections, balancing valves, control hardware/logic for free-cooling enable/disable and modulation"
  },

  {
    "source_document": "AT-GLOBAL-OR-000103_SPC-ACC-01[11].pdf",
    "source_ref": "p.11 §2.4.9",
    "trade": "Chiller Vendor",
    "service": "HVAC",
    "category": "procurement",
    "applicable_standards": [],
    "document_state": null,
    "document_class": "global_ose_spec",
    "confidence": "medium",
    "flags": ["definition_borderline"],
    "flag_context": {
      "definition_borderline": "Sub-option (Option 4 — glycol / anti-freeze protection of waterside economiser circuit). Inclusion is project-specific; reviewer to confirm. Note: glycol use requires AirTrunk approval."
    },
    "deliverables_summary": "Glycol / anti-freeze protection circuit (Option 4) for waterside economiser — secondary heat-recovery circuit and plate heat exchanger"
  },

  {
    "source_document": "AT-GLOBAL-OR-000103_SPC-ACC-01[11].pdf",
    "source_ref": "p.17 §2.4.17",
    "trade": "Chiller Vendor",
    "service": "HVAC",
    "category": "design",
    "applicable_standards": ["BCJ 2014 — Guidelines for Seismic Design and Construction of Building Equipment"],
    "document_state": null,
    "document_class": "global_ose_spec",
    "confidence": "medium",
    "flags": ["definition_borderline"],
    "flag_context": {
      "definition_borderline": "Conditional on Japan project. Equipment earthquake-proofing statement and anchor pull-out statement per BCJ 2014. SYD2 is Sydney — likely OUTSIDE for this project. Reviewer to confirm project-applicability filter."
    },
    "deliverables_summary": "Equipment earthquake-proofing statement and anchor pull-out statement per BCJ 2014 (Japan only)"
  },

  {
    "source_document": "AT-GLOBAL-OR-000103_SPC-ACC-01[11].pdf",
    "source_ref": "pp.18–19 §2.5.2",
    "trade": "Chiller Vendor",
    "service": "HVAC",
    "category": "design",
    "applicable_standards": [],
    "document_state": null,
    "document_class": "global_ose_spec",
    "confidence": "high",
    "flags": [],
    "flag_context": {},
    "deliverables_summary": "Chiller shop drawings — physical size and arrangement, elevations and sections, internal layouts, clearances, mechanical and electrical connection details, mounting details, mechanical selections and calculations, power-rating calculations, site interface drawings (general arrangement and schematic), recommended rigging diagrams, parts replacement strategy, cable termination diagrams, factory variable settings list"
  },

  {
    "source_document": "AT-GLOBAL-OR-000103_SPC-ACC-01[11].pdf",
    "source_ref": "p.18 §2.5.2(b)",
    "trade": "Chiller Vendor",
    "service": "HVAC",
    "category": "design",
    "applicable_standards": [],
    "document_state": null,
    "document_class": "global_ose_spec",
    "confidence": "high",
    "flags": [],
    "flag_context": {},
    "deliverables_summary": "Chiller technical datasheets (unit casing, magnetic-bearing compressor, evaporator, fans, refrigerant system, adiabatic coolers, waterside economiser, water pumps and pipework, unit controls, noise and vibration control, metering)"
  },

  {
    "source_document": "AT-GLOBAL-OR-000103_SPC-ACC-01[11].pdf",
    "source_ref": "p.19 §2.5.2(c)",
    "trade": "Chiller Vendor",
    "service": "HVAC",
    "category": "design",
    "applicable_standards": ["AT BIM Execution Plan (AT-GLOBAL-REF-00000)"],
    "document_state": null,
    "document_class": "global_ose_spec",
    "confidence": "high",
    "flags": [],
    "flag_context": {},
    "deliverables_summary": "Chiller LOD300 BIM model for project BIM integration"
  },

  {
    "source_document": "AT-GLOBAL-OR-000103_SPC-ACC-01[11].pdf",
    "source_ref": "p.19 §2.5.2(d)",
    "trade": "Chiller Vendor",
    "service": "HVAC",
    "category": "design",
    "applicable_standards": ["CIBSE TM65 (fallback if Type III EPD unavailable)"],
    "document_state": null,
    "document_class": "global_ose_spec",
    "confidence": "high",
    "flags": [],
    "flag_context": {},
    "deliverables_summary": "Type III Environmental Product Declaration (EPD); Type I EPD plus CIBSE TM65 embodied-carbon report as fallback"
  },

  {
    "source_document": "AT-GLOBAL-OR-000103_SPC-ACC-01[11].pdf",
    "source_ref": "p.19 §2.5.3",
    "trade": "Chiller Vendor",
    "service": "HVAC",
    "category": "delivery",
    "applicable_standards": ["AT-GLOBAL-REF-000012 O&M Requirements", "AT-GLOBAL-POL-000014 Operational Handover Policy"],
    "document_state": null,
    "document_class": "global_ose_spec",
    "confidence": "high",
    "flags": [],
    "flag_context": {},
    "deliverables_summary": "Chiller Operating and Maintenance (O&M) manual"
  },

  {
    "source_document": "AT-GLOBAL-OR-000103_SPC-ACC-01[11].pdf",
    "source_ref": "p.19 §2.5.4",
    "trade": "Chiller Vendor",
    "service": "HVAC",
    "category": "design",
    "applicable_standards": [],
    "document_state": null,
    "document_class": "global_ose_spec",
    "confidence": "high",
    "flags": [],
    "flag_context": {},
    "deliverables_summary": "Chiller Functional Description Specification — interfacing and operating descriptions covering integration of equipment to the project"
  },

  {
    "source_document": "AT-GLOBAL-OR-000103_SPC-ACC-01[11].pdf",
    "source_ref": "p.19 §2.5.5",
    "trade": "Chiller Vendor",
    "service": "HVAC",
    "category": "design",
    "applicable_standards": [],
    "document_state": null,
    "document_class": "global_ose_spec",
    "confidence": "medium",
    "flags": ["definition_borderline"],
    "flag_context": {
      "definition_borderline": "Reliability data (MTTR / MTBF / MTTF) for major components. Has continuing operational value (informs maintenance planning) — reads as INSIDE under §3(c). Flagged BORDERLINE because reliability submissions sometimes belong with the tender response process rather than as an operating-life document. Reviewer to confirm."
    },
    "deliverables_summary": "Reliability data (MTTR, MTBF, MTTF) for major chiller components — EC fans, evaporator, condenser, expansion valve, magflow meter, compressor, refrigerant system, economiser, adiabatic coolers, water pumps, UPS & controls, active harmonics filter"
  },

  {
    "source_document": "AT-GLOBAL-OR-000103_SPC-ACC-01[11].pdf",
    "source_ref": "p.20 §2.6",
    "trade": "Chiller Vendor",
    "service": "HVAC",
    "category": "delivery",
    "applicable_standards": [],
    "document_state": null,
    "document_class": "global_ose_spec",
    "confidence": "medium",
    "flags": ["definition_borderline"],
    "flag_context": {
      "definition_borderline": "Spare-parts list with stock levels and lead times. The list itself is operational documentation (INSIDE under §3(c)); the spares THEMSELVES are operational stock (INSIDE under §3(a/b) only if delivered to site). Reviewer to clarify: are the recommended spares delivered as part of OSE scope, or only the recommendations?"
    },
    "deliverables_summary": "Recommended spare-parts list with stock levels, lead times, and delivered prices"
  },

  {
    "source_document": "AT-GLOBAL-OR-000103_SPC-ACC-01[11].pdf",
    "source_ref": "p.18 §2.5.2(b)(vi) — adiabatic coolers; §2.5.2(b)(vii) — waterside economiser",
    "trade": "Chiller Vendor",
    "service": "HVAC",
    "category": "design",
    "applicable_standards": [],
    "document_state": null,
    "document_class": "global_ose_spec",
    "confidence": "medium",
    "flags": ["definition_borderline"],
    "flag_context": {
      "definition_borderline": "Datasheet entries for adiabatic coolers and waterside economiser presuppose Options 2 and 3 are taken. Should be auto-rejected if those options are not in scope for the project; flagged here pending project-option confirmation."
    },
    "deliverables_summary": "Datasheets for adiabatic coolers (Option 2) and waterside economiser (Option 3) — conditional on the project taking those options"
  }
]
```

### `audit` (OUTSIDE rows — logged not lost)

```jsonc
[
  {
    "source_ref": "p.6 §2.2(a)",
    "candidate_text": "Attendance at meetings during design and construction",
    "rejection_reason": "Matches explicit exclusion: Attendance at meetings."
  },
  {
    "source_ref": "p.6 §2.2(c)",
    "candidate_text": "Off-site manufacture and assembly",
    "rejection_reason": "Matches explicit exclusion: off-site manufacture as a process. The resulting equipment IS a deliverable (captured in the Air-cooled chiller assembly row); the process of manufacture is not."
  },
  {
    "source_ref": "p.6 §2.2(d)",
    "candidate_text": "Factory testing and certification",
    "rejection_reason": "Matches explicit exclusion: testing/commissioning as a process. Note: factory test results and equipment certification ARE deliverables under §3(c) and are captured in the chiller documentation rows (shop drawings / O&M)."
  },
  {
    "source_ref": "p.6 §2.2(e)",
    "candidate_text": "Delivery to site",
    "rejection_reason": "Logistical activity, not a deliverable. The delivered equipment is the deliverable, not the act of delivering it."
  },
  {
    "source_ref": "p.6 §2.2(f)",
    "candidate_text": "Site testing, commissioning and setting to work",
    "rejection_reason": "Matches explicit exclusion: commissioning as a process. Commissioning records (when produced) would be deliverables but the spec does not separately identify those here — they are typically a project-wide commissioning-records output, not in-scope of this OSE spec."
  },
  {
    "source_ref": "p.6 §2.2(h)",
    "candidate_text": "Operator training",
    "rejection_reason": "Matches explicit exclusion: Training services."
  },
  {
    "source_ref": "p.6 §2.2(i)",
    "candidate_text": "Maintenance",
    "rejection_reason": "Ongoing service activity, not a deliverable artefact. The O&M manual that supports maintenance IS a deliverable (captured in §2.5.3 row)."
  },
  {
    "source_ref": "p.6 §2.2(k)",
    "candidate_text": "Any re-assembly as a result of shipping",
    "rejection_reason": "Process activity, not a deliverable artefact. The reassembled equipment is the deliverable, captured in the chiller assembly row."
  },
  {
    "source_ref": "p.6 §2.2(b)",
    "candidate_text": "Detailed design, technical submissions and equipment certification",
    "rejection_reason": "The activity of producing the design is not a deliverable; the resulting documentation IS — captured in shop drawings, datasheets, BIM, EPD, O&M, FDS rows under §2.5."
  },
  {
    "source_ref": "p.7 §2.3",
    "candidate_text": "Reference Documentation list (RFP, Pricing Schedules, demarcation schedule, AT global refs, etc.)",
    "rejection_reason": "Document-wide reference list. Per the hard rule, document-wide standards/references are not extracted; they belong on the source-doc record. Each individual referenced doc is its own source, processed separately."
  },
  {
    "source_ref": "p.18 §2.5.1",
    "candidate_text": "Provide all requested information as part of the main RFP document",
    "rejection_reason": "RFP submission boilerplate, not a deliverable artefact with continuing operational value."
  },
  {
    "source_ref": "p.20 §2.7",
    "candidate_text": "Operator training delivered at completion of works",
    "rejection_reason": "Matches explicit exclusion: Training services."
  },
  {
    "source_ref": "p.20 §2.8",
    "candidate_text": "Coordination with GC for end-user safety during maintenance/operations/replacement",
    "rejection_reason": "General coordination task. The Safety in Design Register (where it exists as a separate doc) IS a deliverable; the coordination activity is not."
  }
]
```

### `questions` (batched HITL prompts)

```jsonc
[
  {
    "context": "§2.4.17 Seismic Coefficient for MEP Equipment is qualified '(For Japan)'. SYD2 is Sydney. The earthquake-proofing statement deliverable was extracted as BORDERLINE pending project-applicability filter.",
    "question": "Should geo-conditional sections in global OSE specs be auto-OUTSIDE for non-applicable jurisdictions (requires project-jurisdiction context), or always extracted as BORDERLINE for human filtering?",
    "candidate_source_refs": ["p.17 §2.4.17"]
  },
  {
    "context": "§2.4.7 / §2.4.8 / §2.4.9 are Options 2 / 3 / 4 — sub-options whose inclusion is project-specific. Each was extracted as a separate BORDERLINE row per the v1 rule. §2.4.3.1 Option 1 (extended condenser module) was treated the same way.",
    "question": "Confirm: for SYD2, which of Options 1-4 are taken? Once confirmed, the borderline rows for unselected options should be promoted to OUTSIDE in the audit log (not silently dropped).",
    "candidate_source_refs": ["p.10 §2.4.3.1, §2.4.7", "pp.10-11 §2.4.8", "p.11 §2.4.9"]
  },
  {
    "context": "§2.6 Spare Parts. The recommended spare-parts list is documentation; the spares themselves may or may not be delivered as part of OSE scope.",
    "question": "Is the recommended spare-parts STOCK delivered to site as part of the chiller OSE scope, or only the recommendations document? This affects whether to emit a second deliverable row for the physical spares.",
    "candidate_source_refs": ["p.20 §2.6"]
  }
]
```

---

## Verification — does v1 produce the corrections from `PROMPT_V0.md`?

| Correction | Outcome |
|---|---|
| **Locked trade names** (Mechanical / Hydraulic, not "HVAC contractor" / "Plumbing"). | ✓ All vendor scope tagged `Chiller Vendor`. No row uses old/incorrect trade names. |
| **Vendor-doc attribution** (chiller docs → `Chiller Vendor`, not GC). | ✓ All shop drawings, O&M, BIM, EPD, FDS, reliability data, and the Type III EPD are tagged `Chiller Vendor` — none defaulted to GC. |
| **OSE granularity rollup** (one assembly row, not per-component). | ✓ §2.4.1–§2.4.6 + §2.4.10–§2.4.16 collapsed into a single `Air-cooled chiller assembly` row. No separate rows for evaporator / condenser / fans / compressor / refrigerant / pump. |
| **Sub-options as BORDERLINE** (Options 1–4 each get their own row). | ✓ Four borderline rows, one per option, with one-sentence reasoning in `flag_context.definition_borderline`. |
| **`document_state = null` for global specs.** | ✓ Set null on all rows. (v0 had it as `100%`, which we agreed was less honest.) |
| **Hybrid Option C rendering** (` ⚠` marker + flag_context). | N/A here — no `negotiated_response` row in this source. Rendering convention exercised in the BOD hand-test. |
| **DCS dual-axis allowed.** | N/A here — the chiller is HVAC service throughout. Convention exercised in the BOD hand-test. |
| **Three-outcome gate operational** (INSIDE / OUTSIDE-with-audit / BORDERLINE). | ✓ 9 INSIDE rows (1 assembly + 8 documentation/parts), 6 BORDERLINE, 13 OUTSIDE in audit. v0 reported 5 INSIDE / 7 OUTSIDE / 0 BORDERLINE — v1 surfaces more BORDERLINE because we no longer collapse sub-options silently. This is the intended behaviour change. |
| **`applicable_standards` strictly cited, not document-wide.** | ✓ `ASTM B117` populated only on chiller assembly (cited at §2.4.3 against condenser coating); `BCJ 2014` only on the Japan seismic statement; `CIBSE TM65` only on the EPD row; AT-GLOBAL-REF refs only on O&M row. The 23-item §2.3 reference list is NOT inherited onto rows. |
| **`flag_context` carries reasoning, not summary text.** | ✓ All borderline reasoning lives in `flag_context.definition_borderline`; summaries stay terse and free of hedging/commentary. |

---

## Findings to surface for SME review

1. **Geo-conditional sections in global specs** — §2.4.17 (Japan seismic) demonstrates a real edge case. Three options:
   - (a) Project-jurisdiction is captured at project-create time and the prompt receives it as context; the LLM auto-OUTSIDEs non-applicable sections.
   - (b) BORDERLINE-then-human-filter (today's behaviour).
   - (c) Always INSIDE; downstream filter at review-queue level.
   - SME call. (b) is the safe v1 default; (a) is the better long-term answer.

2. **Sub-option promotion path.** When the SME confirms which Options apply, the unselected option rows shouldn't sit in the review queue forever — they should move to the audit (OUTSIDE) with reason "option not selected for this project". This is a queue-action UX detail; flagged for the UX flow design (kickoff step 5).

3. **Spare-parts ambiguity.** The spec is genuinely unclear whether spares stock is delivered. Worth surfacing as a `questions` entry as we have done — but also worth a one-line clarification in the v1 prompt about how to handle "list of X with delivery details" patterns where the deliverable could be the list, the items, or both.

4. **OSE Demarcation Schedule sits next door** (`AT-GLOBAL-OR-000303_SCH-ACC-01[4]_-_Chiller_OSE_Demarcation_Schedule..pdf`). Per CONTEXT.md §5, demarcation schedules are the primary reference for trade allocation and feed `responsibility_conflict` flags. Out of scope for this single-doc test but flagged: the chiller assembly row would need cross-checking against that demarcation. Worth a separate hand-test pass.

5. **Reliability data (§2.5.5)** classified BORDERLINE — could legitimately go INSIDE under §3(c). Borderline tag is the safe call; SME to confirm direction.

---

## Open prompt-rule gaps surfaced by this hand-test

- The prompt does not explicitly say what to do with **sub-option datasheets in §2.5.2(b)** — a datasheet for adiabatic coolers and waterside economiser presupposes Options 2 and 3 are taken. Treated here as a single conditional borderline row. A future v1.1 might tie sub-option deliverables together so that selecting/deselecting an option propagates to its documentation row automatically. v1 acceptable as is.
- The prompt's **OSE rollup rule** says components roll up "into a single assembly deliverable". It does not explicitly say "all standards cited against rolled-up components also roll up to the assembly row". In this test I made that judgement call (rolling ASTM B117 up). Worth a one-line clarification in the prompt for v1.1.
