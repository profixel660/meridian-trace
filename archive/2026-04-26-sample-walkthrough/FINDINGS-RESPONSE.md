# Sample Walkthrough — Findings

**Project context:** AirTrunk SYD2 Data Centre, Shell C Warm Shell + D110.
**Sampled:** 8 documents covering all input formats and key edge cases.
**Method:** Text extraction via `pdftotext` (PDFs) and PowerShell COM (Excel/Word). Drawings inspected for title-block + annotation text only — visual content not accessible without vision rendering, which is exactly what the build will need.

---

## Per-document summary

| # | Doc | Format | Type | Verdict |
|---|---|---|---|---|
| 1 | Mech Services TR (`AT-GLOBAL-TR-000005`) | PDF text | Discipline-level technical requirements | **Extract fully.** Heavily delegates to OSE specs. |
| 2 | OSE Air-Cooled Chiller Spec (`AT-GLOBAL-OR-000103`) | PDF text | Equipment specification | **Extract fully.** Highest deliverable density seen. |
| 3 | Chiller Demarcation Schedule (`AT-GLOBAL-OR-000303`) | PDF text | **Responsibility matrix** | **Special handling.** Source of truth for trade allocation, not a spec. |
| 4 | BOD Excel (`SYD2CD_(SYD29EX2)`) | xlsx | **Negotiated requirements register** | **Special handling.** Already-structured deliverable rows; ingest, don't re-extract. |
| 5 | ACC Amendment Template | docx | Empty template (placeholders) | **Auto-exclude.** Template detection needed. |
| 6a | GA Plan L6 + Roof drawing | PDF (vector) | Architectural drawing | Vision-required. Title-block legible via text; spatial content is not. |
| 6b | Chilled Water Schematic | PDF (vector) | Mechanical schematic drawing | Vision-required. Status ("Not For Construction", "30% Design Development") clearly visible. |
| 7 | EHS Safety in Design Framework | PDF text | Methodology / framework document | **Excluded by deliverable definition.** No deliverables — process guidance. |

---

## Validations of locked decisions

The walkthrough confirms several CONTEXT.md decisions held up against real data:

1. **Source-ref granularity (page + section/clause)** — every text PDF has hierarchical section numbering (e.g. `5.1.1`, `2.4.10`, `5.12.1.1`) and page markers. Schema is appropriate.
2. **Trade vs Service N:M** — Demarcation Schedule literally has trade-role columns (Supplier / Contractor / Client/Consultant / Cxa Agent) with multi-trade allocations per scope item. Multi-row handling is the right call.
3. **Builders-works carve-out** — equipment plinths, structural openings, spatial coordination references are pervasive. The carve-out is necessary.
4. **Process artefact exclusion** — EHS Safety in Design Framework is a textbook example: methodology document with no deliverables. Exclusion rule fires correctly.
5. **OSE / procurement category emphasis** — fully validated. The corpus is *heavily* organised around OSE; procurement category will be a major output dimension.
6. **Vision required for drawings** — pdftotext output for both drawings is essentially unreadable line-noise. Confirms locked decision: drawings need vision-capable LLM processing, not OCR-then-text.
7. **Multi-document context strategy is URGENT, not optional.** Mech TR delegates almost every detail to OSE specs ("*Refer to AirTrunk Air cooled chiller OSE specification...*"). OSE chiller spec lists **23 referenced documents** (Section 2.3). The BOD references external Spec108/203. Nothing stands alone.

---

## New findings that should refine CONTEXT.md

These are genuine discoveries from the samples that the abstract discovery phase couldn't surface.

### F1 — Document state / lifecycle is a first-class attribute

Drawings carry explicit status: **"30% Design Development", "Not For Construction"**, "Issued For Construction" (IFC). Specs have revision (Rev 6, Rev 11). These are *different concepts*:
- **Revision** = which version of this document.
- **Design stage / status** = how mature/authoritative is its content.

A deliverable extracted from a "30% NFC" drawing is inherently provisional. The tool must:
- Detect document state during quality scan.
- Surface state at extraction time (e.g. new flag `provisional_design_stage`).
- Possibly weight low-state-confidence sources lower in cross-source conflict resolution.

Recommendation: add **document state** as a structured attribute on the source-doc record, separate from revision.

Response 1 - Decision: Add document state as recommended. 

### F2 — The Demarcation Schedule is a unique document type

It's not a spec; it's a **responsibility matrix**. Columns = trades/parties; rows = scope items; cells = "R" (responsible) or blank/"i" (involved). It IS the project's own representation of trade/service boundaries.

The tool should treat this as a **source of truth for trade allocation**, not just another doc to run extraction on. Two options:
- **(a)** Process via the standard extraction pipeline but with a specialised prompt that recognises the matrix structure.
- **(b)** Detect the doc type during ingestion and route to a dedicated parser that emits already-allocated deliverable rows.

Recommendation: (a) for v1 simplicity. Worth surfacing the type difference to the user during review.

Response 2 - Decision: surface the type difference to the user during the review.

### F3 — The BOD is a unique document type

The AirTrunk BOD Excel is a **negotiated requirements register** with structure:
`Req Id | Disc Section | Disc Subsection | Requirement | Landlord Response | Landlord Comment`

Each row is essentially an already-extracted deliverable with compliance status. The Discipline Section (e.g. "DCE Mechanical Engineering (ME)") maps to our service axis; the Landlord Response (Comply / Not Comply / Comply with conditions) determines whether the row enters the master register or is excluded.

Implication: rather than "extracting" deliverables from this doc, the tool should **import** structured rows. This is a different ingestion path that the data model can support cleanly because the schema already has all the needed columns.

Recommendation: support a "structured-import" code path alongside the LLM-extraction path. Detect during ingestion (heuristic: regular tabular structure with discipline + requirement columns).

Response 3 - Decision accept recommendation to support a "structured-import" code path alongside the LLM-extraction path.

### F4 — Document templates exist and must be auto-excluded

The ACC Amendment Template (Word) has placeholder content like *"Day date month, year"*, *"Name"*, *"EXAMPLE - Clause fsg"*, *"AT-SYD1p5"* (a different project). Running extraction on these would generate junk deliverables.

Recommendation: add a **template-detection step** during ingestion. New flag `template_detected` with default behaviour = auto-exclude from extraction. Heuristics: presence of placeholder strings, "EXAMPLE" labels, generic field names ("Name", "DATE:"), no project-specific identifiers.

Response 4 - Decision accept "template-detection step during ingestion as recommended.

### F5 — Documentation deliverables exist and the exclusion list needs nuancing

The OSE chiller spec Scope of Work (Section 2.2) includes:
- **Documentation** (shop drawings, O&M manuals, as-installed docs, **LOD300 BIM model**, **Type III EPD**)
- Operator training, maintenance, product warranty
- Site testing, commissioning

Some of these deserve to be deliverables:
- **O&M manuals, BIM model, EPD, as-installed docs, function descriptions** — *form part of the operational life of the building*. You can't operate the chiller without them. By our definition ("...part of the completed structure or its **operational services**") these qualify.
- **Training, warranty, attendance at meetings** — services / contractual obligations, not deliverables.

Current exclusion list says "Reports" and "Warranties" are excluded — needs nuancing:
- Excluded reports = process reports (RFI register, weekly status, meeting minutes).
- Documentation deliverables = those that become part of the operating building's documentation set.

Recommendation: tighten the exclusion language in CONTEXT.md §3 to draw this line explicitly. The test is *"does this artefact have continuing value to the operating building after handover?"* If yes → deliverable. If no → exclusion.

Response 5 - Decision to accept recommendation to tighten the exclusion language in the CONTEXT.md, agree with the heuristic for the deliverable does it have continuing value. However, warranty information has on-going value for a the warranty period so should be included as a deliverable.

### F6 — TBD items are explicit and structurally meaningful

The Chilled Water Schematic shows `IT Load TBC (6.5 MW)` directly on the drawing. Confirms the `tbd_placeholder` and `quantity_uncertain` flags will fire in real use. Worth emphasising in the prompt: TBD doesn't always mean "skip" — often the deliverable exists but a value is pending.

Response 6 - Decision yes agree this is worth emphasising in the prompt.

### F7 — External standard references are pervasive

"ASHRAE", "AS/NZS 1680", "Spec108/203", "AHJ", "Singapore standards", "GMS Global Minimum Standards" appear repeatedly. These are not deliverables but *interpretive context*.

Recommendation: capture standard references as a structured attribute on each deliverable (e.g. `applicable_standards: ["AS/NZS 1680", "ASHRAE 90.1"]`). Useful for compliance review downstream and for cross-deliverable consistency checks.

Response 7 - Decision: yes agree to capture references to standards, codes and guidelines to be included as a structured attribute on each deliverable. This could be in a separate column.

### F8 — Authority chain is more complex than just "revisions"

The corpus reveals a layered authority hierarchy:
1. **Customer requirements** (BOD) — sets the ceiling for what's in scope.
2. **Global TRs** (AT-GLOBAL-TR-*) — AirTrunk's global standards.
3. **Global OSE specs** (AT-GLOBAL-OR-*) — vendor equipment specs.
4. **Project Amendments** (filled-in templates) — modify global per project.
5. **Project Clarifications** — further modifications.
6. **Project drawings** (SYD2-*) — actual implementation, with status (NFC / IFC / xx% design).
7. **Project Demarcation Schedules** — responsibility allocation.

Our locked "revision authority" rule (latest rev wins, HITL on status conflict) handles **revision** but doesn't handle **source-type hierarchy**. A new TR might say one thing; a project Amendment might supersede it. Need to extend the authority logic to recognise this hierarchy.

Recommendation: introduce a **document class** attribute on each source doc (`customer_requirements`, `global_tr`, `global_ose_spec`, `project_amendment`, `project_clarification`, `drawing`, `demarcation_schedule`, etc.) and a class-level priority ordering for cross-source conflict resolution. Project-specific docs override global; amendments override base specs.

Response 8 Decision: yes include a document class attribute on each source doc but ensure that all conflicts are flagged and the most onerous requirement is called out to enable HITL conflict resolution.

---

## Implications for the build

These translate into concrete adjustments for the next phase:

**For CONTEXT.md (small refinements):**
- F1: add "document state / design maturity" as an explicit attribute and flag.
- F4: add template detection + auto-exclusion to the ingestion pipeline.
- F5: tighten the deliverable definition wording to clarify documentation deliverables vs process reports.
- F7: add `applicable_standards` as a structured attribute on deliverables.
- F8: extend authority logic to include source-type hierarchy alongside revision.

**For solution design (next phase):**
- F2 + F3: special-case extraction pipelines for Demarcation Schedules and BOD-style negotiated registers. Decide route (a) vs (b) per F2.
- F6: prompt design must call out TBD handling explicitly.
- The multi-document context strategy (CONTEXT.md §23 open item) needs concrete design. Sample evidence: extraction prompt for Mech TR should optionally include the relevant OSE spec and Demarcation Schedule as context.

**For prompt design (later):**
- Source-text quality is high (pdftotext output is clean for text PDFs, very poor for drawings). This validates the planned pipeline split: text PDFs → text extraction → LLM; drawing PDFs → vision LLM direct.
- The OSE chiller spec at ~1500 lines is well within Sonnet's context. Even multi-doc contexts (TR + OSE spec + Demarcation Schedule) fit comfortably.

---

## Open questions for Peter

These emerged from the samples and weren't captured in the discovery phase. Worth resolving before solution design.

1. **Document state / design maturity values** — what's the canonical list? Suggestion: `concept`, `30%`, `50%`, `90%`, `100%`, `IFC` (Issued For Construction), `as-built`. Or more granular AirTrunk-specific values?
Response - yes this is the hierarchy for maturity of documents but if there is a conflict this must be flagged with both documents identified and the most onerous highlighted for HITL conflict resolution. 

2. **F5 exclusion-line refinement** — confirm the test "*does this artefact have continuing value to the operating building after handover?*" is the right rule for distinguishing documentation deliverables from process reports.
Response - see about response

3. **F8 source-type hierarchy** — is the priority order I sketched correct? Specifically: does a project Amendment always override the underlying global OSE spec, or does it depend on the clause being amended?
Source-type hierarchy will not always apply where there is a conflict it must be flagged for HITL conflict resolution.

4. **BOD-style ingestion path** — confirm "structured-import" should be a real code path alongside LLM extraction, not collapsed into one approach.
Decision - yes structured-import see response above.

5. **Demarcation Schedule output** — when this doc is processed, should it produce one deliverable per scope-item (with multi-trade rows per the multi-row rule), or should it primarily be used as **context** when extracting from other docs?
Decision - primarily use this as the master and verify completeness and correctness using extractions from other documents, identify any missing scope items and add additional context for deliverables based on extractions from other documents.
---

## What didn't come up

For balance: things from CONTEXT.md that the samples didn't stress:
- Email parsing (no emails in this sample)
- Native DWG (only DWG exports as PDFs were sampled; native DWG parsing is unproven from this walkthrough)
- Cross-project portability (single project sampled)
- Performance under volume (8 docs is a fraction of 349)

These remain as build-phase concerns rather than design questions.
