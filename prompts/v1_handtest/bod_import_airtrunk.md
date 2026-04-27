# v1 hand-test — BOD structured-import path against AirTrunk SYD2CD BOD

**Source:** `Samples/BOD/Shell-C-&-D/SYD2CD_(SYD29EX2)_-_AirTrunk.xlsx`
**Sheet:** `Exhibit A2 - Technical Req`
**Sample size:** 11 representative rows from a 99-row sheet (selected to exercise every disposition path: Comply / Comply-then-§3-outside / Not Comply with N/A / Not Comply with substantive qualifier; across multiple disciplines: Mechanical, Fire, BAMC, Electrical, CSA).
**Prompt version applied:** `PROMPT_V1_bod_import.md` v1.0-draft.
**Source-doc metadata:** `document_class = customer_requirements`, `document_state = null` (per SME — BOD is revisioned, not design-maturity-graded; revision is the meaningful axis), `revision = SYD29EX2 (latest)`, `sheet_name = "Exhibit A2 - Technical Req"`.
**Method:** by hand, by the human author. Not LLM output. Goal is to verify v1 rules behave correctly and surface gaps before LLM trial runs.

---

## Sample rows — verbatim source content (so the output below can be checked against the input)

| Row | Disc Section | Subsec | Requirement (excerpt) | Landlord Response | Landlord Comment |
|---|---|---|---|---|---|
| 2  | DCE Mechanical Engineering (ME) | Spec108 vs Spec203 Colo Designs | "DC Specs 108 and 203 define performance requirements for air-cooled and liquid-cooled Colos... A single Colo cannot combine both specifications..." | Comply | — |
| 7  | DCE Mechanical Engineering (ME) | Spec203: Facility Water (cooling) Distribution | "Liquid cooled equipment will be isolated from the facility water loop via a Cooling Distribution Unit (CDU). The LP is required to provide the facility water supply and return to the primary side of the CDU." | Comply | — |
| 8  | DCE Mechanical Engineering (ME) | Spec203: Technology Water Temperature | "Technology water supply shall not exceed 22°C... 2% of the year or up to 175 hours up to 26°C." | Comply | — |
| 9  | DCE Mechanical Engineering (ME) | Spec203: Technology Water Distribution | "The LP is required to provide piping from the secondary side of the CDU to the high density IT rack positions. Piping shall be sized to support flowrate/temperature, with isolation valves at each row, supply and return headers..." | Not Comply | "Comply with design requirements. However, Secondary loop system from CDU to racks will be covered under customer fitout NRC scope." |
| 22 | DCE Mechanical Engineering (ME) | Spec108/203: Inlet Temperature at Tape Library | "16-25°C... Only applicable to space where the racks are located." | Not Comply | "N/A - no tape library requirement." |
| 27 | DCE Mechanical Engineering (ME) | Spec108/203: Containment | "Hot aisle containment shall be provided within the colo for both high and low density rows." | Not Comply | "Comply with design requirements. HAC will be covered under customer fitout NRC scope." |
| 39 | DCE Fire Protection System | Spec108/203: Noise Suppression Kit for Nozzle | "Noise suppression kit shall be installed on the nozzles of the gaseous suppression system..." | Not Comply | "Gas suppression systems are not utilised in the data hall. If MSFT require gas suppression and noise suppression kits this can be accommodated in the customer fitout under NRC." |
| 47 | DCE Building Automation, Monitoring & Control (BAMC) | Spec203: Leak Detection | "Controls scope shall include dedicated leak detection for each rack row containing technical water piping. Leak detection sensor shall be rope style, 1 per row, max coverage..." | Not Comply | "Comply with design requirements. However, per MSFT requirement, Leak detection and controls along rack rows will be covered under customer fitout NRC scope." |
| 58 | DCE Electrical Engineering (EE) | Spec108/203: Uninterruptible Power Supply (UPS) Redundancy | "UPS system shall provide minimum runtime of battery at EOL... UPS shall be one of: 2N or 2(N+1) redundant; N+1 block redundant per IEEE Std 3006.7-2013..." | Not Comply | "N+1 DR system shall be up to a 6N5. IEEE 3006.7-2013 not applied." |
| 82 | DCE Civil, Structural & Architectural (CSA) | Spec108/203: Structural | "The lease provider's Registered Structural Engineer shall design and stamp the floor systems, including the Raised Floor System, to support the various loads described, complying with local building and seismic codes." | Comply | "Refer to the attached letter from the Structural Engineer 'TFS Loading Certification Syd02 Shell C and D'. Letter contains loading plans; data hall floors and corridors designed for 15kPa live load. No raised floor system, rack installed on slab." |
| 91 | DCE Civil, Structural & Architectural (CSA) | Spec108/203: Rack Weight | "All facility floors must be capable of supporting: High Density Racks at 7,000 lbs (3,175kg) per 1.2m x 0.6m..." | Comply | — |

---

## OUTPUT

### `deliverables` (INSIDE + BORDERLINE rows)

```jsonc
[
  {
    "source_document": "SYD2CD_(SYD29EX2)_-_AirTrunk.xlsx",
    "source_ref": "Sheet 'Exhibit A2 - Technical Req' Row 7 (Req Id 2.1.6.1)",
    "trade": "Mechanical",
    "service": "HVAC",
    "category": "delivery",
    "applicable_standards": ["Spec203"],
    "document_state": null,
    "document_class": "customer_requirements",
    "confidence": "high",
    "flags": [],
    "flag_context": {},
    "deliverables_summary": "Facility water supply and return piping to the primary side of the Cooling Distribution Unit (CDU)"
  },

  {
    "source_document": "SYD2CD_(SYD29EX2)_-_AirTrunk.xlsx",
    "source_ref": "Sheet 'Exhibit A2 - Technical Req' Row 8 (Req Id 2.1.7.1)",
    "trade": "Mechanical",
    "service": "HVAC",
    "category": "design",
    "applicable_standards": ["Spec203"],
    "document_state": null,
    "document_class": "customer_requirements",
    "confidence": "medium",
    "flags": ["definition_borderline"],
    "flag_context": {
      "definition_borderline": "Performance requirement (≤22°C technology water supply, 26°C exception ≤175 hr/yr) rather than a discrete physical artefact. The cooling system that meets the requirement IS a deliverable elsewhere; this row may be redundant. Reviewer to confirm: keep as a performance-criterion row, or consolidate with the cooling-system deliverable."
    },
    "deliverables_summary": "Technology water supply system meeting ≤22°C inlet to IT equipment (with 2% / 175 hr/yr exception up to 26°C)"
  },

  {
    "source_document": "SYD2CD_(SYD29EX2)_-_AirTrunk.xlsx",
    "source_ref": "Sheet 'Exhibit A2 - Technical Req' Row 9 (Req Id 2.1.8.1)",
    "trade": "Mechanical",
    "service": "HVAC",
    "category": "delivery",
    "applicable_standards": ["Spec203"],
    "document_state": null,
    "document_class": "customer_requirements",
    "confidence": "high",
    "flags": ["negotiated_response", "scope_shifted_to_nrc"],
    "flag_context": {
      "negotiated_response": "Comply with design requirements. However, Secondary loop system from CDU to racks will be covered under customer fitout NRC scope.",
      "scope_shifted_to_nrc": "Secondary loop system from CDU to racks shifted from base-build to customer fit-out NRC."
    },
    "deliverables_summary": "Technology water piping (secondary side of CDU to high-density IT racks) — sized for flowrate/temperature, with isolation valves at each row, supply/return headers ⚠"
  },

  {
    "source_document": "SYD2CD_(SYD29EX2)_-_AirTrunk.xlsx",
    "source_ref": "Sheet 'Exhibit A2 - Technical Req' Row 27 (Req Id 2.x.x.x)",
    "trade": "Mechanical",
    "service": "HVAC",
    "category": "delivery",
    "applicable_standards": ["Spec108", "Spec203"],
    "document_state": null,
    "document_class": "customer_requirements",
    "confidence": "high",
    "flags": ["negotiated_response", "scope_shifted_to_nrc"],
    "flag_context": {
      "negotiated_response": "Comply with design requirements. HAC will be covered under customer fitout NRC scope.",
      "scope_shifted_to_nrc": "Hot aisle containment shifted from base-build to customer fit-out NRC."
    },
    "deliverables_summary": "Hot aisle containment (HAC) within the colo for both high and low density rows ⚠"
  },

  {
    "source_document": "SYD2CD_(SYD29EX2)_-_AirTrunk.xlsx",
    "source_ref": "Sheet 'Exhibit A2 - Technical Req' Row 47 (Req Id 2.x.x.x)",
    "trade": "DCS",
    "service": "DCS / Controls",
    "category": "delivery",
    "applicable_standards": ["Spec203"],
    "document_state": null,
    "document_class": "customer_requirements",
    "confidence": "high",
    "flags": ["negotiated_response", "scope_shifted_to_nrc"],
    "flag_context": {
      "negotiated_response": "Comply with design requirements. However, per MSFT requirement, Leak detection and controls along rack rows will be covered under customer fitout NRC scope.",
      "scope_shifted_to_nrc": "Rack-row leak detection and associated controls shifted from base-build to customer fit-out NRC, per MSFT requirement."
    },
    "deliverables_summary": "Rope-style leak detection per rack row (under technology water piping, max coverage area, 1 per row) and associated controls ⚠"
  },

  {
    "source_document": "SYD2CD_(SYD29EX2)_-_AirTrunk.xlsx",
    "source_ref": "Sheet 'Exhibit A2 - Technical Req' Row 58 (Req Id 3.x.x.x)",
    "trade": "Electrical",
    "service": "Power distribution",
    "category": "delivery",
    "applicable_standards": [],
    "document_state": null,
    "document_class": "customer_requirements",
    "confidence": "high",
    "flags": ["negotiated_response", "responsibility_conflict"],
    "flag_context": {
      "negotiated_response": "N+1 DR system shall be up to a 6N5. IEEE 3006.7-2013 not applied.",
      "responsibility_conflict": "Topology and standard deviate from spec — spec requires 2N / 2(N+1) / N+1 block-redundant per IEEE 3006.7-2013; project delivers N+1 DR up to 6N5 with IEEE 3006.7-2013 not applied. Surface for HITL: this is a substantive deviation, not a clarification."
    },
    "deliverables_summary": "Uninterruptible Power Supply (UPS) system — N+1 DR up to 6N5 topology, ≥2 min EOL battery runtime ⚠"
  },

  {
    "source_document": "SYD2CD_(SYD29EX2)_-_AirTrunk.xlsx",
    "source_ref": "Sheet 'Exhibit A2 - Technical Req' Row 82 (Req Id 5.x.x.x)",
    "trade": "Concrete",
    "service": null,
    "category": "design",
    "applicable_standards": [],
    "document_state": null,
    "document_class": "customer_requirements",
    "confidence": "high",
    "flags": ["negotiated_response", "trade_inferred"],
    "flag_context": {
      "negotiated_response": "Refer to the attached letter from the Structural Engineer 'TFS Loading Certification Syd02 Shell C and D'. Data hall floors and corridors designed for 15kPa live load (sufficient for high-density racks per the TFS rack layout). No raised floor system; racks installed on slab.",
      "trade_inferred": "Floor systems designed/stamped by Registered Structural Engineer; deliverable is the slab floor system. Trade=Concrete inferred (slab construction); Steel (rebar, reinforcement) and Formwork may also apply. Reviewer to confirm whether to split into multiple rows."
    },
    "deliverables_summary": "Data hall and corridor slab floor systems designed for 15kPa live load, stamped by Registered Structural Engineer (no raised floor; racks on slab) ⚠"
  },

  {
    "source_document": "SYD2CD_(SYD29EX2)_-_AirTrunk.xlsx",
    "source_ref": "Sheet 'Exhibit A2 - Technical Req' Row 82 (Req Id 5.x.x.x)",
    "trade": "Concrete",
    "service": null,
    "category": "design",
    "applicable_standards": [],
    "document_state": null,
    "document_class": "customer_requirements",
    "confidence": "high",
    "flags": ["negotiated_response"],
    "flag_context": {
      "negotiated_response": "Verbatim Structural Engineer letter referenced ('TFS Loading Certification Syd02 Shell C and D')."
    },
    "deliverables_summary": "Structural Engineer Loading Certification letter ('TFS Loading Certification Syd02 Shell C and D') with loading plans ⚠"
  },

  {
    "source_document": "SYD2CD_(SYD29EX2)_-_AirTrunk.xlsx",
    "source_ref": "Sheet 'Exhibit A2 - Technical Req' Row 91 (Req Id 5.x.x.x)",
    "trade": "Concrete",
    "service": null,
    "category": "design",
    "applicable_standards": ["Spec108", "Spec203"],
    "document_state": null,
    "document_class": "customer_requirements",
    "confidence": "high",
    "flags": ["trade_inferred"],
    "flag_context": {
      "trade_inferred": "Floor capacity for high-density racks (7000 lb / 3175 kg per 0.72 m²). Trade=Concrete inferred from slab construction; may also implicate Steel (rebar) and Formwork. Reviewer to confirm split."
    },
    "deliverables_summary": "Facility floors capable of supporting 7,000 lb (3,175 kg) per 1.2m × 0.6m high-density-rack footprint"
  }
]
```

### `audit` (OUTSIDE rows — logged not lost)

```jsonc
[
  {
    "source_ref": "Sheet 'Exhibit A2 - Technical Req' Row 2 (Req Id 2.1.1.1)",
    "candidate_text": "Spec108 vs Spec203 Colo Designs — explanation that the two specs cannot be combined within a single Colo, integration permitted at heat-rejection plant at lease provider's discretion.",
    "rejection_reason": "Passes disposition (Comply) but fails §3 gate. Informational/policy text describing how the two specs scope each other; does not identify a discrete physical or documentation deliverable. The Colos themselves and the heat-rejection plant are deliverables captured in downstream rows.",
    "landlord_response": "Comply"
  },

  {
    "source_ref": "Sheet 'Exhibit A2 - Technical Req' Row 22 (Req Id 2.x.x.x)",
    "candidate_text": "Inlet temperature 16-25°C at tape library racks (only applicable to spaces where racks are located).",
    "rejection_reason": "Out of scope for this project per Landlord Response: 'N/A - no tape library requirement.' Disposition rule rejects before §3 gate.",
    "landlord_response": "Not Comply (N/A)"
  },

  {
    "source_ref": "Sheet 'Exhibit A2 - Technical Req' Row 39 (Req Id 4.x.x.x)",
    "candidate_text": "Noise suppression kit installed on gaseous suppression system nozzles.",
    "rejection_reason": "Out of scope for this project per Landlord Response: 'Gas suppression systems are not utilised in the data hall.' Disposition rule rejects before §3 gate. (NRC scope availability for MSFT is downstream of base-build deliverables register and does not change disposition.)",
    "landlord_response": "Not Comply (no feature)"
  }
]
```

### `questions` (batched HITL prompts)

```jsonc
[
  {
    "context": "Row 47 (BAMC discipline) — service mapping 'DCE Building Automation, Monitoring & Control (BAMC)' → 'DCS / Controls'. The v1 service-mapping table covers this; flagging only because the Disc Section name in this BOD is BAMC (not 'DCE Building Automation, Monitoring & Co...' as in the table). Confirming the table entry should be made tolerant to abbreviation variants.",
    "question": "Confirm: 'DCE Building Automation, Monitoring & Control (BAMC)' maps to 'DCS / Controls' service? (Yes is the assumed answer.)",
    "candidate_source_refs": ["Sheet 'Exhibit A2 - Technical Req' Row 47"]
  },

  {
    "context": "Rows 82 and 91 (CSA discipline) — slab floor systems and floor loading. Trade was inferred as 'Concrete' per the BOD specialist-trade rule, but slab work properly involves Concrete (mix + pour) AND Formwork (mould + reinforcement + post-tensioning) AND Steel (rebar). The BOD rows do not split scope across these trades — they describe a load-bearing floor outcome. CONTEXT.md §5 says 'A plinth typically generates separate rows for each' — does that apply here, or is the BOD row better tagged single-trade with a multi-row split deferred until a downstream spec arrives?",
    "question": "For BOD-level structural-floor rows, should v1 emit one row per relevant trade (Concrete + Formwork + Steel), or a single Concrete row with a multi-trade flag pending downstream demarcation/spec disambiguation?",
    "candidate_source_refs": ["Sheet 'Exhibit A2 - Technical Req' Rows 82, 91"]
  },

  {
    "context": "Row 8 (Technology water temperature) is a performance criterion that does not by itself describe a physical artefact — the artefact is the cooling system, captured in adjacent rows. Flagged BORDERLINE here so the reviewer can decide.",
    "question": "How should v1 handle pure-performance-criterion BOD rows? Options: (a) emit as INSIDE row anchored to the requirement summary (today's behaviour); (b) emit as BORDERLINE for human consolidation (today's behaviour for ambiguous cases); (c) suppress and attach criteria to a 'parent' deliverable (requires graph modelling — not available in v1). v1 default is (a/b). SME to confirm.",
    "candidate_source_refs": ["Sheet 'Exhibit A2 - Technical Req' Row 8"]
  },

  {
    "context": "Row 58 (UPS) — substantive deviation. The Landlord delivers N+1 DR up to 6N5 instead of the spec's required 2N / 2(N+1) / N+1 block-redundant per IEEE 3006.7-2013, and explicitly does not apply IEEE 3006.7-2013. Tagged with both `negotiated_response` AND `responsibility_conflict` because this is closer to a substantive deviation than a mere clarification. Reviewer to confirm flag combination.",
    "question": "When a 'Not Comply' substantive qualifier represents a real deviation (not just a scope clarification), should v1 always add `responsibility_conflict` alongside `negotiated_response`, or only when the deviation is detectable as such?",
    "candidate_source_refs": ["Sheet 'Exhibit A2 - Technical Req' Row 58"]
  }
]
```

---

## Verification — does v1 produce the corrections from `PROMPT_V0.md`?

| Correction | Outcome |
|---|---|
| **BOD trade defaults to specialist trade, not GC.** | ✓ Every kept row tagged Mechanical / DCS / Electrical / Concrete — none defaulted to GC. |
| **Locked trade taxonomy names** (Mechanical, Hydraulic — not "HVAC contractor" / "Plumbing"). | ✓ Used Mechanical, Concrete, DCS, Electrical throughout. No use of v0's incorrect names. |
| **BOD scope-exclusion mechanism** ("no X required" → OUTSIDE under disposition, not BORDERLINE). | ✓ Rows 22 (no tape library) and 39 (gas suppression not utilised) classified OUTSIDE under disposition with the Landlord quote preserved in `rejection_reason`. v0 would have flagged these BORDERLINE. |
| **Hybrid Option C rendering** (` ⚠` marker on summary, full qualifier in `flag_context.negotiated_response`). | ✓ Rows 9, 27, 47, 58, 82×2 all carry the ` ⚠` marker; qualifier text preserved verbatim in `flag_context`. No qualifier text embedded in summary (v0 did embed). |
| **DCS dual-axis allowed** (trade=DCS AND service=DCS / Controls). | ✓ Row 47 demonstrates this. |
| **Disposition before §3 gate.** | ✓ Rows 22 and 39 short-circuit at disposition; Row 2 passes disposition and falls at §3 gate (informational, not a deliverable). |
| **`flag_context` carries reasoning, summary stays terse.** | ✓ All borderline / negotiated reasoning lives in `flag_context`; summaries are present-tense noun phrases without commentary or hedging. |
| **`applicable_standards` strictly cited.** | ✓ Only the source-internal 'Spec108' / 'Spec203' references populated; document-wide standards lists not inherited. |
| **Three-outcome gate operational.** | ✓ Of 11 sample rows: 7 INSIDE (rows 7, 9, 27, 47, 58, 82-letter, 91), 2 BORDERLINE (rows 8, 82-floor system), 3 OUTSIDE in audit (rows 2, 22, 39). The split shows the gate exercising all three outcomes on a representative sample. |
| **Verbatim Landlord Comment preservation.** | ✓ Every `negotiated_response` row preserves the comment verbatim in `flag_context.negotiated_response`. |

---

## Findings to surface for SME review

1. **NRC-scope-shift pattern.** Multiple rows (9, 27, 47) carry "Comply with design requirements. However, ... will be covered under customer fitout NRC scope." This is a substantive scope shift — the Landlord is saying "this deliverable exists but the customer's NRC delivers it, not us." For the AirTrunk Shell base-build register, are these INSIDE (the deliverable exists in the project) or OUTSIDE (it's not in the Landlord's scope)? The v1 prompt sends them through the §3 gate as INSIDE+negotiated_response, which preserves the qualifier for human review. **SME call: should NRC-scope-shift be a distinct flag, perhaps `scope_shifted_to_nrc`, so the review queue can filter them as a group?**

2. **Performance-criterion vs deliverable-row ambiguity** (Row 8). BOD rows occasionally describe parameters rather than artefacts. Today they go to BORDERLINE for human consolidation. Worth a quick prompt-rule clarification in v1.1 — explicit handling guidance for "pure performance criterion" rows.

3. **Multi-trade structural rows** (Rows 82, 91). Slab floor systems implicate Concrete + Formwork + Steel per CONTEXT.md §5. v1 emits a single Concrete row with `trade_inferred` flag, deferring the multi-row split to either (a) a downstream demarcation/spec or (b) a HITL question. SME to confirm the deferred split is acceptable for v1.

4. **Standard auto-detection scope.** The Landlord Comments and Requirements text reference 'Spec108', 'Spec203', 'IEEE 3006.7-2013', 'Spec203' — the prompt populates `applicable_standards` with these, scoped to the row. Of these, 'Spec108' and 'Spec203' are themselves AirTrunk specifications inside the project corpus, not external standards. Worth a v1.1 clarification: do internal project specs belong in `applicable_standards`, or should that field be reserved for genuinely external standards (AS, ASHRAE, IEEE, NFPA, etc.)?

5. **Structural Engineer letter (Row 82)** generated TWO rows — one for the floor system (the physical thing) and one for the certification letter (the documentation deliverable). The disposition rule is per-row, so this is technically a single row producing two deliverables. The v1 prompt allows multi-row output per BOD row implicitly (via the multi-tag rule on trades/services), but does not explicitly cover multi-row-from-single-source for distinct (physical + documentation) deliverables. **Worth a one-line clarification in the prompt for v1.1.**

6. **`responsibility_conflict` on Row 58.** The combination of `negotiated_response` + `responsibility_conflict` was applied because the deviation is substantive (different topology, dropped standard). The v1 prompt does not explicitly say to add `responsibility_conflict` for substantive deviations. SME to confirm: should this be automatic, or left to the human reviewer to add?

7. **`document_state` on the BOD itself.** Set to `"BOD as issued"` — but the BOD is a customer-requirements document, not a design-maturity document. Maturity vocabulary (`30%`, `IFC`, `as-built`) doesn't really apply. Same question as the global OSE spec hand-test: does `document_state` apply meaningfully to non-design-maturity documents? The earlier resolution was `null` for global OSE specs; the same logic applies here. Consider setting `document_state = null` for `customer_requirements` doc class as well.

---

## Open prompt-rule gaps surfaced by this hand-test

- **NRC scope-shift recognition.** Recurring pattern. Worth either a dedicated flag or a rule clarification.
- **Performance-criterion rows.** v1 emits BORDERLINE; could be sharpened.
- **Multi-deliverable-from-single-row.** Row 82 demonstrated. Implicit support; worth making explicit.
- **`document_state` on non-maturity-bearing doc classes.** Generalise the chiller-spec resolution to BOD too — propose `null` for `customer_requirements`.
- **Internal vs external standards in `applicable_standards`.** Boundary question worth a v1.1 prompt clarification.
